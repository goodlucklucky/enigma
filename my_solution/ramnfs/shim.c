// ramnfs LD_PRELOAD shim: redirects file I/O under RAMNFS_PREFIX to
// broker-held memfds, bypassing /tmp size limits while staying in the
// container's granted RAM budget.
//
// SQLite files (.db, .db-journal, .db-wal, .db-shm) are redirected to
// /tmp/cado-sqlite/ on the real filesystem (small, fits in 256 MB /tmp).
// All other ramnfs files use broker memfds for large data (GBs of relations).
//
// Environment:
//   RAMNFS_SOCK    broker socket path (default /tmp/ramnfs.sock)
//   RAMNFS_PREFIX  path prefix to intercept (default /ramwork)
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdarg.h>
#include <string.h>
#include <stdlib.h>
#include <stdio.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/types.h>
#include <sys/stat.h>
#include <linux/stat.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <dirent.h>
#include <errno.h>
#include <pthread.h>

static const char *g_sock   = NULL;
static const char *g_prefix = NULL;
static size_t      g_plen   = 0;
static int         g_debug  = 0;

static void dbg(const char *fmt, ...) {
    if (!g_debug) return;
    va_list ap; va_start(ap, fmt);
    vfprintf(stderr, fmt, ap);
    va_end(ap);
    fflush(stderr);
}

#define SQLITE_REAL_DIR "/tmp/cado-sqlite"
/* Real directory base for virtual CWD (chdir into ramnfs paths) */
#define CWD_REAL_PREFIX "/tmp/ramnfs_cwd"
#define PATHMAX 1024

#define MAXDIRS 2048
static char g_dirs[MAXDIRS][1024];
static int  g_ndirs = 0;
static pthread_mutex_t g_mu = PTHREAD_MUTEX_INITIALIZER;

__attribute__((constructor))
static void shim_init(void) {
    const char *s = getenv("RAMNFS_SOCK");
    const char *p = getenv("RAMNFS_PREFIX");
    g_sock   = s ? s : "/tmp/ramnfs.sock";
    g_prefix = p ? p : "/ramwork";
    g_plen   = strlen(g_prefix);
    g_debug  = getenv("RAMNFS_DEBUG") != NULL;
    // Create real directory for SQLite files
    mkdir(SQLITE_REAL_DIR, 0777);
}

/* Create a real directory tree (using real mkdir syscall via RTLD_NEXT). */
static void ensure_real_dir(const char *path) {
    static int (*real_mkdir)(const char *, mode_t) = NULL;
    if (!real_mkdir) real_mkdir = dlsym(RTLD_NEXT, "mkdir");
    char tmp[PATHMAX]; strncpy(tmp, path, PATHMAX-1); tmp[PATHMAX-1]=0;
    for (char *p = tmp+1; *p; p++) {
        if (*p == '/') { *p=0; real_mkdir(tmp, 0755); *p='/'; }
    }
    real_mkdir(tmp, 0755);
}

/* If the real CWD is under CWD_REAL_PREFIX, return the virtual ramnfs path.
   Uses a thread-local buffer to be thread-safe. */
static const char *real_to_virt_cwd(const char *real_cwd) {
    static const char *pfx = CWD_REAL_PREFIX;
    size_t plen = strlen(pfx);
    if (strncmp(real_cwd, pfx, plen) == 0
            && (real_cwd[plen] == '/' || real_cwd[plen] == '\0')) {
        static __thread char buf[PATHMAX];
        snprintf(buf, PATHMAX, "%s%s", g_prefix, real_cwd + plen);
        return buf;
    }
    return NULL;
}

/* For relative paths: compose the full ramnfs path if we're in a virtual CWD.
   Also handles "." (return virtual CWD) and normalizes "//" -> "/" and "/./" -> "/". */
static const char *resolve_relpath(const char *path) {
    static __thread char buf[PATHMAX];
    if (!path) return path;
    if (path[0] == '/') {
        /* Absolute: collapse duplicate slashes ("//ramwork" -> "/ramwork").
         * CADO joins paths in ways that yield "//ramwork/..."; the kernel treats
         * // as / on a real FS, so the same path must canonicalize here too, or
         * is_ramnfs() misses it and the op is wrongly sent to the real FS. */
        if (!strstr(path, "//")) return path;
        size_t j = 0;
        for (size_t i = 0; path[i] && j + 1 < PATHMAX; i++) {
            if (path[i] == '/' && j > 0 && buf[j-1] == '/') continue;
            buf[j++] = path[i];
        }
        buf[j] = 0;
        return buf;
    }
    char cwd[PATHMAX];
    if (!getcwd(cwd, sizeof cwd)) return path;
    const char *virt = real_to_virt_cwd(cwd);
    if (!virt) return path;
    if (path[0] == '.' && path[1] == '\0') {
        snprintf(buf, PATHMAX, "%s", virt);
    } else {
        snprintf(buf, PATHMAX, "%s/%s", virt, path);
    }
    /* Normalize: remove trailing "/." and "//" */
    size_t n = strlen(buf);
    while (n > 1 && buf[n-1] == '.' && buf[n-2] == '/') { n -= 2; buf[n] = '\0'; }
    while (n > 1 && buf[n-1] == '/') buf[--n] = '\0';
    return buf;
}

static int is_ramnfs(const char *path) {
    if (!path || !g_prefix || g_plen == 0) return 0;
    return strncmp(path, g_prefix, g_plen) == 0
        && (path[g_plen] == '/' || path[g_plen] == '\0');
}

/* Gate helper: normalize an absolute path (collapse //) before the is_ramnfs
   test, so "//ramwork/..." is recognized. The forwarded interposer re-normalizes
   the original path, so callers pass the original path on through. */
static int looks_ramnfs(const char *path) {
    if (!path) return 0;
    if (path[0] == '/') path = resolve_relpath(path);
    return is_ramnfs(path);
}

// Returns 1 if path is a SQLite-managed file (should use real /tmp filesystem)
static int is_sqlite_file(const char *path) {
    if (!path) return 0;
    size_t n = strlen(path);
    // Match: .db  .db-journal  .db-wal  .db-shm
    if (n >= 3 && strcmp(path + n - 3, ".db") == 0) return 1;
    if (n >= 11 && strcmp(path + n - 11, ".db-journal") == 0) return 1;
    if (n >= 7 && strcmp(path + n - 7, ".db-wal") == 0) return 1;
    if (n >= 7 && strcmp(path + n - 7, ".db-shm") == 0) return 1;
    return 0;
}

// Map a ramnfs path to the real /tmp path for SQLite files.
// e.g. /ramwork/factor.work/c140.db -> /tmp/cado-sqlite/factor.work/c140.db
// Returns a static buffer (not thread-safe for concurrent calls, but SQLite
// calls are single-threaded in CADO-NFS).
static const char *sqlite_real_path(const char *ramnfs_path) {
    static char buf[PATHMAX];
    // ramnfs_path = g_prefix + "/" + rest
    const char *rest = ramnfs_path + g_plen; // "/factor.work/c140.db"
    snprintf(buf, sizeof buf, SQLITE_REAL_DIR "%s", rest);
    // Ensure parent directory exists
    char parent[PATHMAX];
    strncpy(parent, buf, sizeof parent - 1);
    char *slash = strrchr(parent, '/');
    if (slash && slash != parent) {
        *slash = 0;
        // Recursively create parent dirs (simple: just try both levels)
        // This is good enough since CADO-NFS workdir is only 2 levels deep.
        static int (*real_mkdir)(const char*, mode_t) = NULL;
        if (!real_mkdir) real_mkdir = dlsym(RTLD_NEXT, "mkdir");
        if (real_mkdir) {
            // Create up to 3 levels of intermediate dirs
            char tmp[PATHMAX];
            strncpy(tmp, SQLITE_REAL_DIR, sizeof tmp - 1);
            const char *p = rest + 1; // skip leading /
            while (p && *p) {
                const char *next = strchr(p, '/');
                if (!next) break;
                int len = next - rest; // from start of rest
                snprintf(tmp, sizeof tmp, SQLITE_REAL_DIR "%.*s", len, rest);
                real_mkdir(tmp, 0777);
                p = next + 1;
            }
            real_mkdir(parent, 0777);
        }
    }
    return buf;
}

static int readn(int fd, void *buf, int n) {
    int g = 0;
    while (g < n) { int r = read(fd,(char*)buf+g,n-g); if(r<=0) return g; g+=r; }
    return g;
}

static int broker_connect(void) {
    // Retry transient failures: under the 16-client metadata storm, socket()
    // (EMFILE) and connect() (backlog full) fail intermittently. A failed connect
    // must NOT be reported as "file not found" — that makes CADO abort spuriously.
    for (int attempt = 0; attempt < 200; attempt++) {
        int s = socket(AF_UNIX, SOCK_STREAM, 0);
        if (s >= 0) {
            struct sockaddr_un a = {0}; a.sun_family = AF_UNIX;
            strncpy(a.sun_path, g_sock, sizeof a.sun_path - 1);
            if (connect(s, (struct sockaddr *)&a, sizeof a) == 0) return s;
            close(s);
        }
        usleep(attempt < 20 ? 500 : 3000);
    }
    return -1;
}

static void broker_send_req(int s, char op, const char *path) {
    unsigned short len = (unsigned short)strlen(path);
    write(s, &op, 1); write(s, &len, 2); write(s, path, len);
}

/* Send a diagnostic line to the broker, which prints it to its (visible)
   stderr. Used to surface shim decisions from processes whose stderr CADO
   redirects into a RAM file (e.g. dup1). */
static void blog(const char *fmt, ...) {
    if (!g_debug) return;
    char buf[1200];
    va_list ap; va_start(ap, fmt);
    vsnprintf(buf, sizeof buf, fmt, ap);
    va_end(ap);
    int s = broker_connect();
    if (s < 0) return;
    broker_send_req(s, 'P', buf);
    close(s);
}

static int broker_open_fd(const char *path) {
    int s = broker_connect(); if (s < 0) return -1;
    broker_send_req(s, 'O', path);
    char status = 0;
    struct iovec iov = {.iov_base=&status,.iov_len=1};
    char cbuf[CMSG_SPACE(sizeof(int))]; memset(cbuf,0,sizeof cbuf);
    struct msghdr msg = {.msg_iov=&iov,.msg_iovlen=1,
                         .msg_control=cbuf,.msg_controllen=sizeof cbuf};
    int fd = -1;
    if (recvmsg(s, &msg, 0) >= 1) {
        struct cmsghdr *c = CMSG_FIRSTHDR(&msg);
        if (c && c->cmsg_type == SCM_RIGHTS) memcpy(&fd, CMSG_DATA(c), sizeof(int));
    }
    close(s);
    return (status == 'K') ? fd : (fd >= 0 ? (close(fd),-1) : -1);
}

static int broker_stat(const char *path, long long *sz_out) {
    int s = broker_connect(); if (s < 0) return 0;
    broker_send_req(s, 'S', path);
    char st = 0; long long sz = 0;
    readn(s, &st, 1); readn(s, &sz, 8); close(s);
    if (sz_out) *sz_out = sz;
    return st == 'K';
}

static void broker_unlink_path(const char *path) {
    int s = broker_connect(); if (s < 0) return;
    broker_send_req(s, 'U', path);
    char st; readn(s, &st, 1); close(s);
}

static char **broker_listdir(const char *dir, int *count_out) {
    *count_out = 0;
    int s = broker_connect(); if (s < 0) return NULL;
    broker_send_req(s, 'L', dir);
    int cnt = 0; readn(s, &cnt, 4);
    if (cnt <= 0) { close(s); return NULL; }
    char **names = calloc(cnt, sizeof(char *));
    for (int i = 0; i < cnt; i++) {
        unsigned short nl = 0; readn(s, &nl, 2);
        names[i] = malloc(nl + 1);
        readn(s, names[i], nl); names[i][nl] = 0;
    }
    close(s);
    *count_out = cnt;
    return names;
}

static void record_dir(const char *path) {
    pthread_mutex_lock(&g_mu);
    for (int i = 0; i < g_ndirs; i++)
        if (strcmp(g_dirs[i], path) == 0) { pthread_mutex_unlock(&g_mu); return; }
    if (g_ndirs < MAXDIRS) strncpy(g_dirs[g_ndirs++], path, 1023);
    pthread_mutex_unlock(&g_mu);
}

static int is_known_dir(const char *path) {
    if (strcmp(path, g_prefix) == 0) return 1;
    pthread_mutex_lock(&g_mu);
    for (int i = 0; i < g_ndirs; i++)
        if (strcmp(g_dirs[i], path) == 0) { pthread_mutex_unlock(&g_mu); return 1; }
    pthread_mutex_unlock(&g_mu);
    return 0;
}

// ---- open / open64 / openat / openat64 ----

int open(const char *path, int flags, ...) {
    static int (*real)(const char*,int,...) = NULL;
    if (!real) real = dlsym(RTLD_NEXT, "open");
    path = resolve_relpath(path);
    if (is_ramnfs(path)) {
        if (is_sqlite_file(path)) {
            // Route SQLite files to real /tmp filesystem
            const char *rpath = sqlite_real_path(path);
            mode_t m = 0644;
            if (flags & O_CREAT) { va_list v; va_start(v,flags); m=va_arg(v,mode_t); va_end(v); }
            return real(rpath, flags, m);
        }
        /* Path ending with '/' is always a directory */
        size_t plen = strlen(path);
        if (plen > g_plen && path[plen-1] == '/') { errno = EISDIR; return -1; }
        if (is_known_dir(path)) { errno = EISDIR; return -1; }
        if ((flags & O_CREAT) == 0) {
            long long sz;
            if (!broker_stat(path, &sz)) {
                dbg("[shim] open FAIL path=%s flags=%d (ENOENT, not in broker)\n", path, flags);
                errno = ENOENT; return -1;
            }
        }
        int fd = broker_open_fd(path);
        if (fd < 0) { dbg("[shim] open FAIL path=%s (EIO broker)\n", path); errno = EIO; return -1; }
        if (flags & O_TRUNC) ftruncate(fd, 0);
        if (flags & O_APPEND) lseek(fd, 0, SEEK_END);
        else lseek(fd, 0, SEEK_SET);
        return fd;
    }
    mode_t m = 0;
    if (flags & O_CREAT) { va_list v; va_start(v,flags); m=va_arg(v,mode_t); va_end(v); }
    return real(path, flags, m);
}

int open64(const char *path, int flags, ...) {
    mode_t m = 0;
    if (flags & O_CREAT) { va_list v; va_start(v,flags); m=va_arg(v,mode_t); va_end(v); }
    return open(path, flags, m);
}

int openat(int dirfd, const char *path, int flags, ...) {
    static int (*real)(int,const char*,int,...) = NULL;
    if (!real) real = dlsym(RTLD_NEXT, "openat");
    /* Normalize absolute paths (collapse //) and AT_FDCWD-relative paths; leave
       paths relative to a real dirfd for the kernel. */
    if (path && (path[0] == '/' || dirfd == AT_FDCWD))
        path = resolve_relpath(path);
    if (path && path[0]=='/' && is_ramnfs(path)) {
        mode_t m = 0;
        if (flags & O_CREAT) { va_list v; va_start(v,flags); m=va_arg(v,mode_t); va_end(v); }
        return open(path, flags, m);
    }
    mode_t m = 0;
    if (flags & O_CREAT) { va_list v; va_start(v,flags); m=va_arg(v,mode_t); va_end(v); }
    return real ? real(dirfd, path, flags, m) : (errno=ENOSYS,-1);
}

int openat64(int dirfd, const char *path, int flags, ...) {
    mode_t m = 0;
    if (flags & O_CREAT) { va_list v; va_start(v,flags); m=va_arg(v,mode_t); va_end(v); }
    return openat(dirfd, path, flags, m);
}

// ---- fopen / fopen64 ----

FILE *fopen(const char *path, const char *mode) {
    static FILE *(*real)(const char*,const char*) = NULL;
    if (!real) real = dlsym(RTLD_NEXT, "fopen");
    path = resolve_relpath(path);
    if (is_ramnfs(path)) {
        if (is_sqlite_file(path)) {
            return real(sqlite_real_path(path), mode);
        }
        int flags;
        if (mode[0]=='r' && !strchr(mode,'+'))       flags = O_RDONLY;
        else if (mode[0]=='w' && !strchr(mode,'+'))  flags = O_WRONLY|O_CREAT|O_TRUNC;
        else if (mode[0]=='a')                        flags = O_WRONLY|O_CREAT|O_APPEND;
        else                                          flags = O_RDWR|O_CREAT;
        int fd = open(path, flags, 0666);
        if (fd < 0) return NULL;
        char m2[8]={0}; strncpy(m2, mode, 7);
        FILE *f = fdopen(fd, m2);
        if (!f) close(fd);
        return f;
    }
    return real(path, mode);
}

FILE *fopen64(const char *path, const char *mode) { return fopen(path, mode); }

// ---- creat ----

int creat(const char *path, mode_t mode) {
    return open(path, O_WRONLY|O_CREAT|O_TRUNC, mode);
}

// ---- stat / lstat / __xstat variants ----

static void fill_stat_dir(struct stat *buf) {
    memset(buf, 0, sizeof *buf);
    buf->st_mode = S_IFDIR | 0755; buf->st_nlink = 2;
}
static void fill_stat_file(struct stat *buf, long long sz) {
    memset(buf, 0, sizeof *buf);
    buf->st_mode = S_IFREG | 0755; buf->st_nlink = 1; buf->st_size = sz;
}

int stat(const char *path, struct stat *buf) {
    static int (*real)(const char*,struct stat*) = NULL;
    if (!real) real = dlsym(RTLD_NEXT, "stat");
    path = resolve_relpath(path);
    if (is_ramnfs(path)) {
        if (is_sqlite_file(path)) return real(sqlite_real_path(path), buf);
        if (is_known_dir(path)) { fill_stat_dir(buf); return 0; }
        long long sz = 0;
        if (broker_stat(path, &sz)) { fill_stat_file(buf, sz); return 0; }
        errno = ENOENT; return -1;
    }
    return real(path, buf);
}

int lstat(const char *path, struct stat *buf) { return stat(resolve_relpath(path), buf); }

int stat64(const char *path, struct stat64 *buf) {
    static int (*real)(const char*,struct stat64*) = NULL;
    if (!real) real = dlsym(RTLD_NEXT, "stat64");
    path = resolve_relpath(path);
    if (is_ramnfs(path)) {
        if (is_sqlite_file(path)) return real(sqlite_real_path(path), buf);
        if (is_known_dir(path)) {
            memset(buf, 0, sizeof *buf);
            buf->st_mode = S_IFDIR | 0755; buf->st_nlink = 2; return 0;
        }
        long long sz = 0;
        if (broker_stat(path, &sz)) {
            memset(buf, 0, sizeof *buf);
            buf->st_mode = S_IFREG | 0644; buf->st_nlink = 1;
            buf->st_size = (off64_t)sz; return 0;
        }
        errno = ENOENT; return -1;
    }
    return real(path, buf);
}
int lstat64(const char *path, struct stat64 *buf) { return stat64(resolve_relpath(path), buf); }

int fstatat(int dirfd, const char *path, struct stat *buf, int flags) {
    static int (*real)(int,const char*,struct stat*,int) = NULL;
    if (!real) real = dlsym(RTLD_NEXT, "fstatat");
    if (path && (path[0] == '/' || dirfd == AT_FDCWD)) path = resolve_relpath(path);
    if (path && path[0]=='/' && is_ramnfs(path)) {
        if (is_sqlite_file(path)) return real(AT_FDCWD, sqlite_real_path(path), buf, flags);
        return stat(path, buf);
    }
    return real ? real(dirfd, path, buf, flags) : (errno=ENOSYS,-1);
}

int fstatat64(int dirfd, const char *path, struct stat64 *buf, int flags) {
    static int (*real)(int,const char*,struct stat64*,int) = NULL;
    if (!real) real = dlsym(RTLD_NEXT, "fstatat64");
    if (path && (path[0] == '/' || dirfd == AT_FDCWD)) path = resolve_relpath(path);
    if (path && path[0]=='/' && is_ramnfs(path)) {
        if (is_sqlite_file(path)) return real(AT_FDCWD, sqlite_real_path(path), buf, flags);
        if (is_known_dir(path)) {
            memset(buf, 0, sizeof *buf);
            buf->st_mode = S_IFDIR | 0755; buf->st_nlink = 2;
            return 0;
        }
        long long sz = 0;
        if (broker_stat(path, &sz)) {
            memset(buf, 0, sizeof *buf);
            buf->st_mode = S_IFREG | 0644; buf->st_nlink = 1;
            buf->st_size = (off64_t)sz;
            return 0;
        }
        errno = ENOENT; return -1;
    }
    return real ? real(dirfd, path, buf, flags) : (errno=ENOSYS,-1);
}

int statx(int dirfd, const char *path, int flags, unsigned int mask,
          struct statx *stxbuf) {
    static int (*real)(int,const char*,int,unsigned,struct statx*) = NULL;
    if (!real) real = dlsym(RTLD_NEXT, "statx");
    if (path && (path[0] == '/' || dirfd == AT_FDCWD)) path = resolve_relpath(path);
    if (path && path[0]=='/' && is_ramnfs(path)) {
        if (is_sqlite_file(path)) return real(AT_FDCWD, sqlite_real_path(path), flags, mask, stxbuf);
        if (is_known_dir(path)) {
            memset(stxbuf, 0, sizeof *stxbuf);
            stxbuf->stx_mode  = S_IFDIR | 0755;
            stxbuf->stx_nlink = 2;
            stxbuf->stx_mask  = STATX_BASIC_STATS;
            return 0;
        }
        long long sz = 0;
        if (broker_stat(path, &sz)) {
            memset(stxbuf, 0, sizeof *stxbuf);
            stxbuf->stx_mode  = S_IFREG | 0644;
            stxbuf->stx_nlink = 1;
            stxbuf->stx_size  = (unsigned long long)sz;
            stxbuf->stx_mask  = STATX_BASIC_STATS;
            return 0;
        }
        errno = ENOENT; return -1;
    }
    return real ? real(dirfd, path, flags, mask, stxbuf) : (errno=ENOSYS,-1);
}

int __xstat(int ver, const char *path, struct stat *buf) {
    static int (*real)(int,const char*,struct stat*) = NULL;
    if (!real) real = dlsym(RTLD_NEXT, "__xstat");
    if (looks_ramnfs(path)) return stat(path, buf);
    return real ? real(ver, path, buf) : (errno=ENOSYS,-1);
}
int __lxstat(int ver, const char *path, struct stat *buf) { return __xstat(ver,path,buf); }
int __xstat64(int ver, const char *path, struct stat *buf) { return __xstat(ver,path,buf); }
int __lxstat64(int ver, const char *path, struct stat *buf) { return __xstat(ver,path,buf); }

// ---- access / faccessat ----

int access(const char *path, int mode) {
    static int (*real)(const char*,int) = NULL;
    if (!real) real = dlsym(RTLD_NEXT, "access");
    path = resolve_relpath(path);
    if (is_ramnfs(path)) {
        if (is_sqlite_file(path)) return real(sqlite_real_path(path), mode);
        if (is_known_dir(path)) return 0;
        if (broker_stat(path, NULL)) return 0;
        blog("ACCESS ramnfs MISS '%s' mode=%d", path, mode);
        errno = ENOENT; return -1;
    }
    if (strstr(path, ".gz")) blog("ACCESS non-ramnfs gz '%s'", path);
    int r = real(path, mode);
    if (r != 0 && strstr(path, ".gz")) blog("ACCESS real FAIL '%s' errno=%d", path, errno);
    return r;
}

int faccessat(int dirfd, const char *path, int mode, int flags) {
    static int (*real)(int,const char*,int,int) = NULL;
    if (!real) real = dlsym(RTLD_NEXT, "faccessat");
    if (looks_ramnfs(path)) return access(path, mode);
    return real ? real(dirfd, path, mode, flags) : (errno=ENOSYS,-1);
}

// ---- unlink / unlinkat ----

int unlink(const char *path) {
    static int (*real)(const char*) = NULL;
    if (!real) real = dlsym(RTLD_NEXT, "unlink");
    path = resolve_relpath(path);
    if (is_ramnfs(path)) {
        if (is_sqlite_file(path)) return real(sqlite_real_path(path));
        broker_unlink_path(path); return 0;
    }
    return real(path);
}

int unlinkat(int dirfd, const char *path, int flags) {
    static int (*real)(int,const char*,int) = NULL;
    if (!real) real = dlsym(RTLD_NEXT, "unlinkat");
    if (looks_ramnfs(path)) return unlink(path);
    return real ? real(dirfd, path, flags) : (errno=ENOSYS,-1);
}

// ---- rename / renameat ----

int rename(const char *oldp, const char *newp) {
    static int (*real)(const char*,const char*) = NULL;
    if (!real) real = dlsym(RTLD_NEXT, "rename");
    /* resolve_relpath uses a single thread-local buffer; save before second call */
    char old_buf[PATHMAX], new_buf[PATHMAX];
    strncpy(old_buf, resolve_relpath(oldp), PATHMAX-1); old_buf[PATHMAX-1]=0; oldp=old_buf;
    strncpy(new_buf, resolve_relpath(newp), PATHMAX-1); new_buf[PATHMAX-1]=0; newp=new_buf;
    if (is_ramnfs(oldp) || is_ramnfs(newp)) {
        // SQLite files: use real rename
        if (is_sqlite_file(oldp) || is_sqlite_file(newp)) {
            const char *ro = is_sqlite_file(oldp) ? sqlite_real_path(oldp) : oldp;
            const char *rn = is_sqlite_file(newp) ? sqlite_real_path(newp) : newp;
            return real(ro, rn);
        }
        int src = broker_open_fd(oldp);
        int dst = broker_open_fd(newp);
        if (src < 0 || dst < 0) {
            if (src>=0) close(src); if (dst>=0) close(dst);
            errno = EIO; return -1;
        }
        ftruncate(dst, 0); lseek(src, 0, SEEK_SET);
        char buf[131072]; ssize_t r;
        while ((r = read(src, buf, sizeof buf)) > 0) write(dst, buf, r);
        close(src); close(dst);
        broker_unlink_path(oldp);
        return 0;
    }
    return real(oldp, newp);
}

int renameat(int od, const char *op, int nd, const char *np) {
    if (looks_ramnfs(op)) return rename(op, np);
    static int (*real)(int,const char*,int,const char*) = NULL;
    if (!real) real = dlsym(RTLD_NEXT, "renameat");
    return real ? real(od, op, nd, np) : (errno=ENOSYS,-1);
}

// ---- symlink / symlinkat ----
// bwc.pl creates W as: symlink("W.sols0-64", "/ramwork/.../W")
// Since /ramwork is virtual, the kernel symlink() would fail.
// We implement it as a file copy within ramnfs.

int symlink(const char *target, const char *linkpath) {
    static int (*real)(const char*,const char*) = NULL;
    if (!real) real = dlsym(RTLD_NEXT, "symlink");
    // Resolve linkpath (the new path being created)
    char lp_buf[PATHMAX];
    strncpy(lp_buf, resolve_relpath(linkpath), PATHMAX-1); lp_buf[PATHMAX-1]=0;
    if (is_ramnfs(lp_buf)) {
        // Resolve target relative to linkpath's directory
        char src_path[PATHMAX];
        if (target[0] == '/') {
            strncpy(src_path, target, PATHMAX-1); src_path[PATHMAX-1]=0;
        } else {
            // target is relative to the directory containing linkpath
            strncpy(src_path, lp_buf, PATHMAX-1);
            char *slash = strrchr(src_path, '/');
            if (slash) { slash[1] = '\0'; strncat(src_path, target, PATHMAX-1-strlen(src_path)); }
            else strncpy(src_path, target, PATHMAX-1);
        }
        if (!is_ramnfs(src_path)) { errno = EXDEV; return -1; }
        // Copy content: open src, open dst, copy
        int src = broker_open_fd(src_path);
        int dst = broker_open_fd(lp_buf);
        if (src < 0 || dst < 0) {
            if (src>=0) close(src); if (dst>=0) close(dst);
            errno = ENOENT; return -1;
        }
        ftruncate(dst, 0); lseek(src, 0, SEEK_SET);
        char buf[131072]; ssize_t r;
        while ((r = read(src, buf, sizeof buf)) > 0) write(dst, buf, r);
        close(src); close(dst);
        return 0;
    }
    return real ? real(target, linkpath) : (errno=ENOSYS,-1);
}

int symlinkat(const char *target, int newdirfd, const char *linkpath) {
    if (looks_ramnfs(linkpath))
        return symlink(target, linkpath);
    static int (*real)(const char*,int,const char*) = NULL;
    if (!real) real = dlsym(RTLD_NEXT, "symlinkat");
    return real ? real(target, newdirfd, linkpath) : (errno=ENOSYS,-1);
}


// ---- mkdir / mkdirat / rmdir ----

int mkdir(const char *path, mode_t mode) {
    static int (*real)(const char*,mode_t) = NULL;
    if (!real) real = dlsym(RTLD_NEXT, "mkdir");
    path = resolve_relpath(path);
    if (is_ramnfs(path)) { record_dir(path); return 0; }
    return real(path, mode);
}

int mkdirat(int dirfd, const char *path, mode_t mode) {
    static int (*real)(int,const char*,mode_t) = NULL;
    if (!real) real = dlsym(RTLD_NEXT, "mkdirat");
    if (looks_ramnfs(path)) return mkdir(path, mode);
    return real ? real(dirfd, path, mode) : (errno=ENOSYS,-1);
}

int rmdir(const char *path) {
    static int (*real)(const char*) = NULL;
    if (!real) real = dlsym(RTLD_NEXT, "rmdir");
    if (looks_ramnfs(path)) return 0;
    return real(path);
}

// ---- chdir / fchdir ----
// BWC binaries call chdir(wdir) then access files with relative paths.
// We create a real directory under /tmp/ramnfs_cwd/ as a placeholder
// for the virtual CWD, then resolve_relpath() maps relative accesses back.

int chdir(const char *path) {
    static int (*real)(const char*) = NULL;
    if (!real) real = dlsym(RTLD_NEXT, "chdir");
    if (path && path[0]=='/') path = resolve_relpath(path);
    if (is_ramnfs(path)) {
        char real_dir[PATHMAX];
        const char *rest = path + g_plen;
        snprintf(real_dir, PATHMAX, CWD_REAL_PREFIX "%s", rest);
        ensure_real_dir(real_dir);
        return real(real_dir);
    }
    return real(path);
}

// ---- chmod / chown ----

int chmod(const char *path, mode_t mode) {
    static int (*real)(const char*,mode_t) = NULL;
    if (!real) real = dlsym(RTLD_NEXT, "chmod");
    path = resolve_relpath(path);
    if (is_ramnfs(path)) {
        if (is_sqlite_file(path)) return real(sqlite_real_path(path), mode);
        return 0;
    }
    return real(path, mode);
}

int lchmod(const char *path, mode_t mode) { return chmod(path, mode); }

int fchmodat(int dirfd, const char *path, mode_t mode, int flags) {
    static int (*real)(int,const char*,mode_t,int) = NULL;
    if (!real) real = dlsym(RTLD_NEXT, "fchmodat");
    if (looks_ramnfs(path)) return chmod(path, mode);
    return real ? real(dirfd, path, mode, flags) : (errno=ENOSYS,-1);
}

int chown(const char *path, uid_t uid, gid_t gid) {
    static int (*real)(const char*,uid_t,gid_t) = NULL;
    if (!real) real = dlsym(RTLD_NEXT, "chown");
    if (looks_ramnfs(path)) return 0;
    return real(path, uid, gid);
}

int lchown(const char *path, uid_t uid, gid_t gid) { return chown(path, uid, gid); }

// ---- truncate ----

int truncate(const char *path, off_t len) {
    static int (*real)(const char*,off_t) = NULL;
    if (!real) real = dlsym(RTLD_NEXT, "truncate");
    path = resolve_relpath(path);
    if (is_ramnfs(path)) {
        if (is_sqlite_file(path)) return real(sqlite_real_path(path), len);
        int fd = broker_open_fd(path);
        if (fd < 0) { errno = ENOENT; return -1; }
        int r = ftruncate(fd, len); close(fd); return r;
    }
    return real(path, len);
}

// ---- opendir / readdir / readdir64 / closedir ----

#define FAKEDIR_MAGIC 0x52414d46U

typedef struct {
    unsigned int magic;
    char       **names;
    int          count;
    int          idx;
    struct dirent64 de64;
    struct dirent   de;
} FakeDir;

DIR *opendir(const char *name) {
    static DIR *(*real)(const char*) = NULL;
    if (!real) real = dlsym(RTLD_NEXT, "opendir");
    name = resolve_relpath(name);
    if (is_ramnfs(name)) {
        long long sz;
        if (broker_stat(name, &sz)) { errno = ENOTDIR; return NULL; }
        record_dir(name);
        int cnt = 0;
        char **names = broker_listdir(name, &cnt);
        FakeDir *fd = calloc(1, sizeof(FakeDir));
        fd->magic = FAKEDIR_MAGIC;
        fd->names = names; fd->count = cnt; fd->idx = 0;
        return (DIR *)fd;
    }
    return real(name);
}

struct dirent *readdir(DIR *dirp) {
    static struct dirent *(*real)(DIR*) = NULL;
    if (!real) real = dlsym(RTLD_NEXT, "readdir");
    if (dirp && ((FakeDir *)dirp)->magic == FAKEDIR_MAGIC) {
        FakeDir *fd = (FakeDir *)dirp;
        if (fd->idx >= fd->count) return NULL;
        memset(&fd->de, 0, sizeof fd->de);
        strncpy(fd->de.d_name, fd->names[fd->idx++], sizeof fd->de.d_name - 1);
        fd->de.d_type = DT_REG;
        return &fd->de;
    }
    return real(dirp);
}

struct dirent64 *readdir64(DIR *dirp) {
    static struct dirent64 *(*real)(DIR*) = NULL;
    if (!real) real = dlsym(RTLD_NEXT, "readdir64");
    if (dirp && ((FakeDir *)dirp)->magic == FAKEDIR_MAGIC) {
        FakeDir *fd = (FakeDir *)dirp;
        if (fd->idx >= fd->count) return NULL;
        memset(&fd->de64, 0, sizeof fd->de64);
        strncpy(fd->de64.d_name, fd->names[fd->idx++], sizeof fd->de64.d_name - 1);
        fd->de64.d_type = DT_REG;
        return &fd->de64;
    }
    return real(dirp);
}

int closedir(DIR *dirp) {
    static int (*real)(DIR*) = NULL;
    if (!real) real = dlsym(RTLD_NEXT, "closedir");
    if (dirp && ((FakeDir *)dirp)->magic == FAKEDIR_MAGIC) {
        FakeDir *fd = (FakeDir *)dirp;
        for (int i = 0; i < fd->count; i++) free(fd->names[i]);
        free(fd->names); free(fd);
        return 0;
    }
    return real(dirp);
}

// ---- fstat / fstat64 ----
// memfd_create files always have st_nlink=0 (no directory entry).
// SQLite checks fstat(fd).st_nlink and refuses writes when nlink<1.
// Fix: report nlink=1 for anonymous regular files (nlink==0 && S_ISREG).
// SQLite db files are now routed to real /tmp so this mainly covers
// large ramnfs data files opened by other tools.

int fstat(int fd, struct stat *buf) {
    static int (*real)(int, struct stat*) = NULL;
    if (!real) real = dlsym(RTLD_NEXT, "fstat");
    int rc = real(fd, buf);
    if (rc == 0 && S_ISREG(buf->st_mode) && buf->st_nlink == 0)
        buf->st_nlink = 1;
    return rc;
}

int fstat64(int fd, struct stat64 *buf) {
    static int (*real)(int, struct stat64*) = NULL;
    if (!real) real = dlsym(RTLD_NEXT, "fstat64");
    int rc = real(fd, buf);
    if (rc == 0 && S_ISREG(buf->st_mode) && buf->st_nlink == 0)
        buf->st_nlink = 1;
    return rc;
}
