// ramnfs broker: holds RAM-backed memfds for multi-process pipelines.
// Non-root, no mount, no privileges — uses memfd_create + SCM_RIGHTS.
//
// Protocol (unix stream, one op per connection):
//   'O' open/create  [op:1][len:2][path:len]  -> SCM_RIGHTS fd + [status:1]
//   'S' stat         [op:1][len:2][path:len]  -> [status:1][size:8LE]
//   'U' unlink       [op:1][len:2][path:len]  -> [status:1]
//   'L' listdir      [op:1][len:2][prefix:len]-> [count:4LE]([namelen:2LE][name:namelen])*
//       (returns direct-child basenames only)
//
// Thread-per-connection; global table guarded by mutex.
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <sys/mman.h>
#include <pthread.h>
#include <errno.h>
#include <signal.h>

#define MAXF    16384
#define PATHMAX 1024

static char            g_paths[MAXF][PATHMAX];
static int             g_fds[MAXF];
static int             g_nf = 0;
static pthread_mutex_t g_mu = PTHREAD_MUTEX_INITIALIZER;
static int             g_dbg = 0;
static int             g_keep = 0;

static int idx_of(const char *p) {
    for (int i = 0; i < g_nf; i++)
        if (g_fds[i] >= 0 && strcmp(g_paths[i], p) == 0) return i;
    return -1;
}

static int find_or_create(const char *path) {
    int i = idx_of(path);
    if (i >= 0) return g_fds[i];
    if (g_nf >= MAXF) return -1;
    int fd = memfd_create("ramnfs", 0);
    if (fd < 0) return -1;
    strncpy(g_paths[g_nf], path, PATHMAX - 1);
    g_fds[g_nf] = fd;
    g_nf++;
    return fd;
}

static int send_fd(int sock, int fd, char status) {
    struct msghdr msg = {0};
    char b = status;
    struct iovec io = {.iov_base = &b, .iov_len = 1};
    msg.msg_iov = &io; msg.msg_iovlen = 1;
    char cbuf[CMSG_SPACE(sizeof(int))];
    memset(cbuf, 0, sizeof cbuf);
    if (fd >= 0) {
        msg.msg_control = cbuf; msg.msg_controllen = sizeof cbuf;
        struct cmsghdr *c = CMSG_FIRSTHDR(&msg);
        c->cmsg_level = SOL_SOCKET; c->cmsg_type = SCM_RIGHTS;
        c->cmsg_len = CMSG_LEN(sizeof(int));
        memcpy(CMSG_DATA(c), &fd, sizeof(int));
    }
    return sendmsg(sock, &msg, 0);
}

static int readn(int fd, void *buf, int n) {
    int g = 0;
    while (g < n) {
        int r = read(fd, (char *)buf + g, n - g);
        if (r <= 0) return g;
        g += r;
    }
    return g;
}

static void *handle_conn(void *arg) {
    int c = (int)(intptr_t)arg;
    char op = 0;
    if (readn(c, &op, 1) != 1) goto done;
    unsigned short len = 0;
    if (readn(c, &len, 2) != 2) goto done;
    if (len >= PATHMAX) len = PATHMAX - 1;
    char path[PATHMAX] = {0};
    if (readn(c, path, len) != (int)len) goto done;
    path[len] = 0;

    if (op == 'O') {
        pthread_mutex_lock(&g_mu);
        int existed = idx_of(path) >= 0;
        int memfd = find_or_create(path);
        pthread_mutex_unlock(&g_mu);
        if (g_dbg) fprintf(stderr, "broker: OPEN %s %s\n", existed ? "existing" : "CREATE", path);
        if (memfd < 0) { send_fd(c, -1, 'E'); goto done; }
        char proc[64];
        snprintf(proc, sizeof proc, "/proc/self/fd/%d", memfd);
        int sendable = open(proc, O_RDWR);
        send_fd(c, sendable, sendable >= 0 ? 'K' : 'E');
        if (sendable >= 0) close(sendable);

    } else if (op == 'S') {
        pthread_mutex_lock(&g_mu);
        int i = idx_of(path);
        long long sz = 0; char st = 'E';
        if (i >= 0) {
            struct stat stt;
            if (fstat(g_fds[i], &stt) == 0) { sz = stt.st_size; st = 'K'; }
        }
        pthread_mutex_unlock(&g_mu);
        if (g_dbg && st == 'E') fprintf(stderr, "broker: STAT MISS %s\n", path);
        write(c, &st, 1); write(c, &sz, 8);

    } else if (op == 'U') {
        // Retain relation files (.gz) on unlink: CADO deletes them after upload,
        // but the filtering step (dup1) still needs to read them. Everything else
        // (polyselect candidates, temporaries) is freed so re-uploads/overwrites
        // keep working and RAM is reclaimed.
        size_t pl = strlen(path);
        int is_rel = (pl >= 3 && strcmp(path + pl - 3, ".gz") == 0);
        int retain = g_keep || is_rel;
        pthread_mutex_lock(&g_mu);
        int i = idx_of(path);
        if (g_dbg) fprintf(stderr, "broker: UNLINK %s %s%s\n",
                           i >= 0 ? "hit" : "miss", path, (i >= 0 && retain) ? " (RETAINED)" : "");
        if (i >= 0 && !retain) { close(g_fds[i]); g_fds[i] = -1; g_paths[i][0] = 0; }
        pthread_mutex_unlock(&g_mu);
        char st = 'K'; write(c, &st, 1);

    } else if (op == 'L') {
        // List direct children of directory 'path' (strip trailing slash)
        char pfx[PATHMAX]; strncpy(pfx, path, PATHMAX - 1); pfx[PATHMAX-1] = 0;
        size_t pl = strlen(pfx);
        if (pl > 0 && pfx[pl-1] == '/') pfx[--pl] = 0;

        char **names = NULL; int cnt = 0;
        pthread_mutex_lock(&g_mu);
        for (int i = 0; i < g_nf; i++) {
            if (g_fds[i] < 0) continue;
            const char *p = g_paths[i];
            if (strncmp(p, pfx, pl) != 0 || p[pl] != '/') continue;
            const char *rest = p + pl + 1;
            if (strchr(rest, '/') != NULL) continue;
            names = realloc(names, (cnt + 1) * sizeof(char *));
            names[cnt++] = strdup(rest);
        }
        pthread_mutex_unlock(&g_mu);

        write(c, &cnt, 4);
        for (int i = 0; i < cnt; i++) {
            unsigned short nl = (unsigned short)strlen(names[i]);
            write(c, &nl, 2); write(c, names[i], nl);
            free(names[i]);
        }
        free(names);

    } else if (op == 'P') {
        fprintf(stderr, "shimlog: %s\n", path);
    }
done:
    close(c);
    return NULL;
}

static void dump_table(int sig) {
    (void)sig;
    fprintf(stderr, "=== BROKER DUMP: %d slots ===\n", g_nf);
    for (int i = 0; i < g_nf; i++) {
        if (g_fds[i] < 0) continue;
        if (strstr(g_paths[i], "filelist") || strstr(g_paths[i], "stderr") || strstr(g_paths[i], "stdout")) {
            fprintf(stderr, "DUMP %s CONTENT:\n", g_paths[i]);
            off_t cur = lseek(g_fds[i], 0, SEEK_CUR);
            lseek(g_fds[i], 0, SEEK_SET);
            char buf[16384]; int r;
            while ((r = read(g_fds[i], buf, sizeof buf)) > 0) fwrite(buf, 1, r, stderr);
            lseek(g_fds[i], cur, SEEK_SET);
            fprintf(stderr, "--- end filelist ---\n");
        } else {
            fprintf(stderr, "FILE %s\n", g_paths[i]);
        }
    }
    fprintf(stderr, "=== END DUMP ===\n");
}

int main(int argc, char **argv) {
    const char *sp = argc > 1 ? argv[1] : "/tmp/ramnfs.sock";
    signal(SIGUSR1, dump_table);
    g_dbg = getenv("RAMNFS_DEBUG") != NULL;
    g_keep = getenv("RAMNFS_KEEP") != NULL;
    unlink(sp);
    int s = socket(AF_UNIX, SOCK_STREAM, 0);
    struct sockaddr_un a = {0}; a.sun_family = AF_UNIX;
    strncpy(a.sun_path, sp, sizeof a.sun_path - 1);
    if (bind(s, (struct sockaddr *)&a, sizeof a) < 0) { perror("bind"); return 1; }
    listen(s, 4096);
    fprintf(stderr, "broker: listening on %s (pid %d)\n", sp, getpid());
    for (;;) {
        int c = accept(s, 0, 0);
        if (c < 0) continue;
        pthread_t t; pthread_attr_t attr;
        pthread_attr_init(&attr);
        pthread_attr_setdetachstate(&attr, PTHREAD_CREATE_DETACHED);
        if (pthread_create(&t, &attr, handle_conn, (void *)(intptr_t)c) != 0)
            handle_conn((void *)(intptr_t)c);
        pthread_attr_destroy(&attr);
    }
}
