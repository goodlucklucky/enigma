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
// Scalability (idea #1): the file table is a SHARDED hash map. Each path hashes
// to one of NSTRIPE independent shards, each with its own mutex and hash
// buckets. Open/Stat/Unlink are therefore O(1) and only contend with other ops
// on the *same* shard — so N sieve clients on N cores no longer serialize on a
// single global lock doing an O(files) strcmp scan (the old design's quadratic
// blow-up that made the broker the bottleneck at high core counts). Entries are
// heap-allocated, so there is no fixed MAXF ceiling either.
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <unistd.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <sys/mman.h>
#include <pthread.h>
#include <errno.h>
#include <signal.h>
#include <sys/resource.h>

#define PATHMAX  1024
#define NSTRIPE  256          // lock/hash shards (power of two)
#define SBUCKETS 4096         // hash buckets per shard (power of two)

typedef struct entry {
    struct entry *next;
    int           fd;
    size_t        plen;
    char          path[];     // flexible array, NUL-terminated, exact length
} entry_t;

typedef struct {
    pthread_mutex_t mu;
    entry_t        *buckets[SBUCKETS];
} stripe_t;

static stripe_t g_str[NSTRIPE];
static int      g_dbg  = 0;
static int      g_keep = 0;

// FNV-1a; split into shard index (low bits) and bucket index (high bits).
static uint64_t hash_path(const char *p, size_t n) {
    uint64_t h = 1469598103934665603ULL;
    for (size_t i = 0; i < n; i++) { h ^= (unsigned char)p[i]; h *= 1099511628211ULL; }
    return h;
}

static entry_t *bucket_find(entry_t *head, const char *p, size_t n) {
    for (entry_t *e = head; e; e = e->next)
        if (e->fd >= 0 && e->plen == n && memcmp(e->path, p, n) == 0) return e;
    return NULL;
}

// Open-or-create the memfd for `path`. Returns the fd (>=0) or -1.
// On *existed (out), reports whether the path already had a memfd.
static int find_or_create(const char *path, int *existed) {
    size_t n = strlen(path);
    uint64_t h = hash_path(path, n);
    stripe_t *s = &g_str[h & (NSTRIPE - 1)];
    size_t b = (h >> 16) & (SBUCKETS - 1);
    pthread_mutex_lock(&s->mu);
    entry_t *e = bucket_find(s->buckets[b], path, n);
    if (e) { if (existed) *existed = 1; int fd = e->fd; pthread_mutex_unlock(&s->mu); return fd; }
    if (existed) *existed = 0;
    int fd = memfd_create("ramnfs", 0);
    if (fd < 0) { pthread_mutex_unlock(&s->mu); return -1; }
    e = malloc(sizeof(entry_t) + n + 1);
    if (!e) { close(fd); pthread_mutex_unlock(&s->mu); return -1; }
    e->fd = fd; e->plen = n; memcpy(e->path, path, n + 1);
    e->next = s->buckets[b]; s->buckets[b] = e;
    pthread_mutex_unlock(&s->mu);
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
        int existed = 0;
        int memfd = find_or_create(path, &existed);
        if (g_dbg) fprintf(stderr, "broker: OPEN %s %s\n", existed ? "existing" : "CREATE", path);
        if (memfd < 0) { send_fd(c, -1, 'E'); goto done; }
        char proc[64];
        snprintf(proc, sizeof proc, "/proc/self/fd/%d", memfd);
        int sendable = open(proc, O_RDWR);
        send_fd(c, sendable, sendable >= 0 ? 'K' : 'E');
        if (sendable >= 0) close(sendable);

    } else if (op == 'S') {
        size_t n = strlen(path);
        uint64_t h = hash_path(path, n);
        stripe_t *s = &g_str[h & (NSTRIPE - 1)];
        size_t b = (h >> 16) & (SBUCKETS - 1);
        long long sz = 0; char st = 'E';
        pthread_mutex_lock(&s->mu);
        entry_t *e = bucket_find(s->buckets[b], path, n);
        if (e) {
            struct stat stt;
            if (fstat(e->fd, &stt) == 0) { sz = stt.st_size; st = 'K'; }
        }
        pthread_mutex_unlock(&s->mu);
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
        uint64_t h = hash_path(path, pl);
        stripe_t *s = &g_str[h & (NSTRIPE - 1)];
        size_t b = (h >> 16) & (SBUCKETS - 1);
        pthread_mutex_lock(&s->mu);
        entry_t **pp = &s->buckets[b], *e = NULL;
        for (; *pp; pp = &(*pp)->next)
            if ((*pp)->fd >= 0 && (*pp)->plen == pl && memcmp((*pp)->path, path, pl) == 0) { e = *pp; break; }
        if (g_dbg) fprintf(stderr, "broker: UNLINK %s %s%s\n",
                           e ? "hit" : "miss", path, (e && retain) ? " (RETAINED)" : "");
        if (e && !retain) { close(e->fd); *pp = e->next; free(e); }
        pthread_mutex_unlock(&s->mu);
        char st = 'K'; write(c, &st, 1);

    } else if (op == 'L') {
        // List direct children of directory 'path' (strip trailing slash).
        // Rare op; scans every shard (each under its own lock).
        char pfx[PATHMAX]; strncpy(pfx, path, PATHMAX - 1); pfx[PATHMAX-1] = 0;
        size_t pl = strlen(pfx);
        if (pl > 0 && pfx[pl-1] == '/') pfx[--pl] = 0;

        char **names = NULL; int cnt = 0;
        for (int si = 0; si < NSTRIPE; si++) {
            stripe_t *s = &g_str[si];
            pthread_mutex_lock(&s->mu);
            for (int bi = 0; bi < SBUCKETS; bi++) {
                for (entry_t *e = s->buckets[bi]; e; e = e->next) {
                    if (e->fd < 0) continue;
                    const char *p = e->path;
                    if (strncmp(p, pfx, pl) != 0 || p[pl] != '/') continue;
                    const char *rest = p + pl + 1;
                    if (strchr(rest, '/') != NULL) continue;
                    names = realloc(names, (cnt + 1) * sizeof(char *));
                    names[cnt++] = strdup(rest);
                }
            }
            pthread_mutex_unlock(&s->mu);
        }

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
    int total = 0;
    for (int si = 0; si < NSTRIPE; si++)
        for (int bi = 0; bi < SBUCKETS; bi++)
            for (entry_t *e = g_str[si].buckets[bi]; e; e = e->next)
                if (e->fd >= 0) total++;
    fprintf(stderr, "=== BROKER DUMP: %d files ===\n", total);
    for (int si = 0; si < NSTRIPE; si++) {
        for (int bi = 0; bi < SBUCKETS; bi++) {
            for (entry_t *e = g_str[si].buckets[bi]; e; e = e->next) {
                if (e->fd < 0) continue;
                if (strstr(e->path, "filelist") || strstr(e->path, "stderr") || strstr(e->path, "stdout")) {
                    fprintf(stderr, "DUMP %s CONTENT:\n", e->path);
                    off_t cur = lseek(e->fd, 0, SEEK_CUR);
                    lseek(e->fd, 0, SEEK_SET);
                    char buf[16384]; int r;
                    while ((r = read(e->fd, buf, sizeof buf)) > 0) fwrite(buf, 1, r, stderr);
                    lseek(e->fd, cur, SEEK_SET);
                    fprintf(stderr, "--- end filelist ---\n");
                } else {
                    fprintf(stderr, "FILE %s\n", e->path);
                }
            }
        }
    }
    fprintf(stderr, "=== END DUMP ===\n");
}

int main(int argc, char **argv) {
    const char *sp = argc > 1 ? argv[1] : "/tmp/ramnfs.sock";
    signal(SIGUSR1, dump_table);
    // A client that disconnects mid-reply (timeout, kill, OOM-killed sieve
    // worker) must NOT take the broker — and thus the whole job — down. Without
    // this, the next write/sendmsg to that dead socket raises SIGPIPE whose
    // default action terminates the process. At high core counts, with dozens of
    // clients churning, one ill-timed disconnect would otherwise be fatal.
    signal(SIGPIPE, SIG_IGN);
    // Hold one memfd per RAM file; never let a low inherited soft limit cap how
    // many files (workunits/relations) we can keep at high core counts.
    struct rlimit rl;
    if (getrlimit(RLIMIT_NOFILE, &rl) == 0) {
        rl.rlim_cur = rl.rlim_max;
        setrlimit(RLIMIT_NOFILE, &rl);
    }
    g_dbg = getenv("RAMNFS_DEBUG") != NULL;
    g_keep = getenv("RAMNFS_KEEP") != NULL;
    for (int i = 0; i < NSTRIPE; i++)
        pthread_mutex_init(&g_str[i].mu, NULL);
    unlink(sp);
    int s = socket(AF_UNIX, SOCK_STREAM, 0);
    struct sockaddr_un a = {0}; a.sun_family = AF_UNIX;
    strncpy(a.sun_path, sp, sizeof a.sun_path - 1);
    if (bind(s, (struct sockaddr *)&a, sizeof a) < 0) { perror("bind"); return 1; }
    listen(s, 4096);
    fprintf(stderr, "broker: listening on %s (pid %d, %d shards)\n", sp, getpid(), NSTRIPE);
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
