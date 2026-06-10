// Breaking RSA solver — all-C/C++ orchestrator.
//
// Drives CADO-NFS (GNFS) with a RAM-backed working directory, verifies the
// factorization, and emits the solution-output protocol (logs, magic separator,
// base64 zip of result.json + solve_info.json) — all without Python on our side.
//
// The RAM scratch is provided by the ramnfs broker; CADO's I/O reaches it either
// via the LD_PRELOAD shim (default) or, once the CADO source patch lands, via
// CADO's own memfd-aware I/O (set RAMNFS_MODE=patched). Both keep scratch in the
// 85 GB RAM, bypassing the validator's 1 GB /tmp.
//
// Env: WALL_TIME, DEADLINE_MARGIN, CADO_NFS, CADO_THREADS,
//      RAMNFS_BROKER, RAMNFS_SHIM, RAMNFS_SOCK, RAMNFS_WORKDIR,
//      RAMNFS_MODE (shim|patched).

#include <gmpxx.h>
#include <zlib.h>

#include <cctype>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <string>
#include <vector>

#include <fcntl.h>
#include <signal.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

static const char *SEP =
    "\n----- ENIGMA-SOLUTION-OUTPUT-BEGIN-a8c7f3e2-9d4b-4c5a-8f1e-2b6d3a4e5f7c -----\n";

// --- logging ----------------------------------------------------------------

static void logmsg(const std::string &m) {
    char ts[32];
    time_t t = time(nullptr);
    struct tm g;
    gmtime_r(&t, &g);
    strftime(ts, sizeof ts, "%Y-%m-%d %H:%M:%S", &g);
    fprintf(stdout, "[%s UTC] %s\n", ts, m.c_str());
    fflush(stdout);
}

static long env_long(const char *k, long def) {
    const char *v = getenv(k);
    if (!v || !*v) return def;
    char *e = nullptr;
    long r = strtol(v, &e, 10);
    return (e && *e == 0) ? r : def;
}

static std::string env_str(const char *k, const std::string &def) {
    const char *v = getenv(k);
    return (v && *v) ? std::string(v) : def;
}

// --- tiny tolerant JSON field extraction ------------------------------------
// The input is a flat object {"difficulty":N,"num":BIG,"num_bits":M}; pull the
// integer value following a given key without a full JSON parser.

static bool json_int_field(const std::string &s, const std::string &key, std::string &out) {
    std::string pat = "\"" + key + "\"";
    size_t i = s.find(pat);
    if (i == std::string::npos) return false;
    i += pat.size();
    while (i < s.size() && (s[i] == ' ' || s[i] == ':' || s[i] == '\t' || s[i] == '"')) i++;
    size_t j = i;
    while (j < s.size() && (isdigit((unsigned char)s[j]) || (j == i && s[j] == '-'))) j++;
    if (j == i) return false;
    out = s.substr(i, j - i);
    return true;
}

static bool is_prime(const mpz_class &x) { return mpz_probab_prime_p(x.get_mpz_t(), 30) != 0; }

// --- run a child process, capture stdout ------------------------------------

struct Proc { pid_t pid = -1; int out_fd = -1; };

// Fork/exec argv with optional extra env vars (key=value); child gets its own
// session so we can kill the whole group. With capture_out=true the child's
// stdout+stderr are piped back via p.out_fd (the caller MUST drain it, or the
// 64 KB pipe buffer fills and blocks the child in write()). With
// capture_out=false they go to /dev/null — use this for long-running children
// whose output nobody reads (e.g. the ramnfs broker), mirroring the Python
// reference's stdout=DEVNULL/stderr=DEVNULL.
static Proc spawn(const std::vector<std::string> &argv,
                  const std::vector<std::string> &extra_env,
                  bool capture_out = true) {
    Proc p;
    int pipefd[2] = {-1, -1};
    if (capture_out && pipe(pipefd) != 0) return p;
    pid_t pid = fork();
    if (pid < 0) { if (capture_out) { close(pipefd[0]); close(pipefd[1]); } return p; }
    if (pid == 0) {
        // child
        setsid();
        if (capture_out) {
            dup2(pipefd[1], STDOUT_FILENO);
            dup2(pipefd[1], STDERR_FILENO);
            close(pipefd[0]); close(pipefd[1]);
        } else {
            int dn = open("/dev/null", O_RDWR);
            if (dn >= 0) { dup2(dn, STDOUT_FILENO); dup2(dn, STDERR_FILENO); if (dn > 2) close(dn); }
        }
        for (auto &e : extra_env) putenv(strdup(e.c_str()));
        std::vector<char *> cargv;
        for (auto &a : argv) cargv.push_back(const_cast<char *>(a.c_str()));
        cargv.push_back(nullptr);
        execvp(cargv[0], cargv.data());
        _exit(127);
    }
    if (capture_out) { close(pipefd[1]); p.out_fd = pipefd[0]; }
    p.pid = pid;
    return p;
}

static void kill_proc(Proc &p) {
    if (p.pid > 0) {
        kill(-p.pid, SIGTERM);
        for (int i = 0; i < 50; i++) { if (waitpid(p.pid, nullptr, WNOHANG) == p.pid) { p.pid = -1; break; } usleep(100000); }
        if (p.pid > 0) { kill(-p.pid, SIGKILL); waitpid(p.pid, nullptr, 0); p.pid = -1; }
    }
    if (p.out_fd >= 0) { close(p.out_fd); p.out_fd = -1; }
}

// --- CADO-NFS via the RAM-backed workdir ------------------------------------

static bool scan_factor_line(const std::string &line, const mpz_class &n, mpz_class &p, mpz_class &q) {
    // CADO prints "p q" — two space-separated decimal integers whose product is n.
    std::vector<std::string> toks; std::string cur;
    for (char c : line) { if (isspace((unsigned char)c)) { if (!cur.empty()) { toks.push_back(cur); cur.clear(); } } else if (isdigit((unsigned char)c)) cur += c; else { cur.clear(); toks.clear(); break; } }
    if (!cur.empty()) toks.push_back(cur);
    if (toks.size() < 2) return false;
    mpz_class prod = 1;
    for (auto &t : toks) prod *= mpz_class(t);
    if (prod != n) return false;
    for (auto &t : toks) if (!is_prime(mpz_class(t))) return false;
    mpz_class a(toks[0]), b(toks[1]);
    if (a <= b) { p = a; q = b; } else { p = b; q = a; }
    return true;
}

static bool start_broker(const std::string &sock, Proc &broker) {
    std::string bin = env_str("RAMNFS_BROKER", "/opt/ramnfs/broker");
    if (access(bin.c_str(), X_OK) != 0) { logmsg("ramnfs: broker not found"); return false; }
    unlink(sock.c_str());
    // Broker runs for the whole multi-hour CADO job and nobody reads its
    // stdout/stderr; send it to /dev/null so its output can never fill an
    // unread 64 KB pipe and back-pressure/deadlock the broker mid-run.
    broker = spawn({bin, sock}, {}, /*capture_out=*/false);
    if (broker.pid < 0) return false;
    for (int i = 0; i < 50; i++) { if (access(sock.c_str(), F_OK) == 0) { logmsg("ramnfs: broker started"); return true; } usleep(100000); }
    kill_proc(broker);
    return false;
}

static int cpu_count() {
    long n = sysconf(_SC_NPROCESSORS_ONLN);
    if (n <= 0) n = 8;
    // honor cgroup quota (docker --cpus): cgroup v2 first
    FILE *f = fopen("/sys/fs/cgroup/cpu.max", "r");
    if (f) { char a[64]; long period; if (fscanf(f, "%63s %ld", a, &period) == 2 && strcmp(a, "max") != 0 && period > 0) { long quota = atol(a); long q = quota / period; if (q >= 1 && q < n) n = q; } fclose(f); }
    else {
        // cgroup v1 fallback (cpu.cfs_quota_us / cpu.cfs_period_us), matching the
        // Python _cpu_count() so a v1 host does not over-subscribe sieve clients.
        FILE *fq = fopen("/sys/fs/cgroup/cpu/cpu.cfs_quota_us", "r");
        FILE *fp = fopen("/sys/fs/cgroup/cpu/cpu.cfs_period_us", "r");
        if (fq && fp) { long quota, period; if (fscanf(fq, "%ld", &quota) == 1 && fscanf(fp, "%ld", &period) == 1 && quota > 0 && period > 0) { long q = quota / period; if (q >= 1 && q < n) n = q; } }
        if (fq) fclose(fq);
        if (fp) fclose(fp);
    }
    return (int)(n > 0 ? n : 8);
}

static bool run_cado(const mpz_class &n, double deadline, mpz_class &p, mpz_class &q) {
    std::string cado = env_str("CADO_NFS", "/opt/cado-nfs/build/release/cado-nfs.py");
    if (access(cado.c_str(), F_OK) != 0) { logmsg("Stage 2: CADO not found"); return false; }
    std::string sock = env_str("RAMNFS_SOCK", "/tmp/ramnfs.sock");
    std::string workdir = env_str("RAMNFS_WORKDIR", "/ramwork/factor.work");
    std::string shim = env_str("RAMNFS_SHIM", "/opt/ramnfs/shim.so");
    std::string mode = env_str("RAMNFS_MODE", "shim");
    int threads = (int)env_long("CADO_THREADS", 0); if (threads <= 0) threads = cpu_count();

    // Start every run from a clean slate. CADO resumes from a SQLite state DB; the
    // ramnfs shim keeps that DB on the REAL filesystem (/tmp/cado-sqlite) while the
    // bulk data lives in the ephemeral broker. A DB left over from a prior run makes
    // CADO skip steps whose data files no longer exist in the fresh broker and abort
    // (e.g. "freerel.gz does not exist"). A fresh container never sees this, but
    // clearing the stale CADO scratch makes every invocation idempotent.
    // (/tmp/cado-sqlite mirrors SQLITE_REAL_DIR in ramnfs/shim.c.)
    { std::string tmpdir = env_str("TMPDIR", "/tmp");
      if (system(("rm -rf /tmp/cado-sqlite " + tmpdir + "/cado_run 2>/dev/null") .c_str())) { /* best effort */ } }

    // Shim mode needs the LD_PRELOAD .so to actually exist to virtualize the
    // /ramwork workdir. If it is missing/renamed, glibc silently ignores the
    // bad LD_PRELOAD and CADO would write real files to a nonexistent /ramwork
    // and fail. Mirror the Python guard: with no usable shim, skip the broker
    // entirely and fall back to a /tmp workdir.
    bool shim_ok = (mode != "shim") || access(shim.c_str(), R_OK) == 0;
    if (!shim_ok) logmsg("ramnfs: shim not found at " + shim + "; falling back to /tmp");

    Proc broker;
    bool have_broker = shim_ok && start_broker(sock, broker);
    if (!have_broker) { workdir = env_str("TMPDIR", "/tmp") + "/cado_run"; mkdir(workdir.c_str(), 0777); }

    // Build CADO argv. One single-threaded sieve client per core: las is the
    // dominant GNFS phase and the HTTP server / ramnfs broker are near-idle during
    // it, so every core should be sieving.
    std::string bindir = cado.substr(0, cado.find_last_of('/'));
    int ndigits = (int)n.get_str().size();
    int n_clients = threads;

    std::vector<std::string> argv = {
        "python3", cado, n.get_str(),
        "tasks.workdir=" + workdir,
        "tasks.threads=" + std::to_string(threads),
        "server.address=localhost", "server.port=0", "server.threaded=1",
        "slaves.nrclients=" + std::to_string(n_clients),
        "slaves.cado_nfs_client.bindir=" + bindir,
        "tasks.linalg.bwc.threads=" + std::to_string(threads),
        "tasks.sieve.las.threads=1",
        // NOTE: do NOT set tasks.sieve.adjust_strategy. Benchmarked value 2 made the
        // sieve ~5x slower on c60 (sieve CPU 30s -> 164s, total 22s -> 51s). CADO's
        // params.cNNN ship it commented out (default 0) for the production range, so
        // the calibrated lim/lpb/mfb/I assume strategy 0. Leave it at the default.
    };
    // Let CADO pick the size-appropriate polynomial-selection parameters (degree,
    // admin, admax, P, nq, lim/lpb/mfb, ...) from its calibrated params.cNNN files.
    // We deliberately do NOT force degree or admax: forcing degree=5 yields zero
    // polynomials for c<100 (crash), and capping admax below a size's admin (e.g.
    // 2000 < c145's admin=2100) gives an empty search range — a guaranteed failure
    // on the production target — while saving only ~5% of wall time. CADO_ADMAX /
    // CADO_DEGREE remain manual escape hatches (appended last so they win).
    if (const char *e = getenv("CADO_ADMAX"); e && *e)
        argv.push_back(std::string("tasks.polyselect.admax=") + e);
    if (const char *e = getenv("CADO_DEGREE"); e && *e)
        argv.push_back(std::string("tasks.polyselect.degree=") + e);

    std::vector<std::string> env = {"HOME=/tmp", "TMPDIR=/tmp"};
    if (have_broker && mode == "shim") {
        env.push_back("LD_PRELOAD=" + shim);
        env.push_back("RAMNFS_SOCK=" + sock);
        env.push_back("RAMNFS_PREFIX=/ramwork");
    } else if (have_broker) { // patched CADO: it reads the broker directly
        env.push_back("RAMNFS_SOCK=" + sock);
        env.push_back("RAMNFS_PREFIX=/ramwork");
    }
    logmsg("Stage 2: CADO c" + std::to_string(ndigits) + " threads=" +
           std::to_string(threads) + " clients=" + std::to_string(n_clients) +
           " workdir=" + workdir + " ram_shim=" +
           (have_broker && mode == "shim" ? "on" : (have_broker ? "patched" : "off")));

    Proc cado_p = spawn(argv, env);
    if (cado_p.pid < 0) { if (have_broker) kill_proc(broker); return false; }

    // stream stdout line by line, scan for the factor line
    std::string acc; char buf[8192]; bool found = false; double t0 = (double)time(nullptr); double last = 0;
    fcntl(cado_p.out_fd, F_SETFL, O_NONBLOCK);
    while (true) {
        ssize_t r = read(cado_p.out_fd, buf, sizeof buf);
        if (r > 0) {
            acc.append(buf, r);
            size_t nl;
            while ((nl = acc.find('\n')) != std::string::npos) {
                std::string line = acc.substr(0, nl); acc.erase(0, nl + 1);
                if (scan_factor_line(line, n, p, q)) { logmsg("Stage 2: CADO found factors"); found = true; break; }
            }
            if (found) break;
        } else if (r == 0) {
            break; // EOF
        } else {
            usleep(200000);
        }
        double now = (double)time(nullptr);
        if (now - last > 120) { logmsg("Stage 2: CADO running ... " + std::to_string((int)(now - t0)) + "s elapsed"); last = now; }
        if (now >= deadline) { logmsg("Stage 2: CADO hit deadline"); break; }
        if (waitpid(cado_p.pid, nullptr, WNOHANG) == cado_p.pid) { cado_p.pid = -1; // drain remaining
            ssize_t rr; while ((rr = read(cado_p.out_fd, buf, sizeof buf)) > 0) { acc.append(buf, rr); size_t nl; while ((nl = acc.find('\n')) != std::string::npos) { std::string line = acc.substr(0, nl); acc.erase(0, nl + 1); if (scan_factor_line(line, n, p, q)) { found = true; break; } } if (found) break; }
            break; }
    }
    kill_proc(cado_p);
    if (have_broker) kill_proc(broker);
    return found;
}

// --- output: STORED zip + base64 + protocol ---------------------------------

static void put16(std::string &b, unsigned v) { b.push_back(v & 0xff); b.push_back((v >> 8) & 0xff); }
static void put32(std::string &b, unsigned long v) { for (int i = 0; i < 4; i++) b.push_back((v >> (8 * i)) & 0xff); }

static std::string make_zip(const std::vector<std::pair<std::string, std::string>> &files) {
    std::string out, central;
    unsigned long offset = 0;
    for (auto &f : files) {
        const std::string &name = f.first, &data = f.second;
        unsigned long crc = crc32(0L, (const Bytef *)data.data(), data.size());
        unsigned long off = offset;
        // local file header
        std::string lh;
        put32(lh, 0x04034b50); put16(lh, 20); put16(lh, 0); put16(lh, 0); // sig, ver, flags, method(stored)
        put16(lh, 0); put16(lh, 0x21); // modtime, moddate (arbitrary)
        put32(lh, crc); put32(lh, data.size()); put32(lh, data.size());
        put16(lh, name.size()); put16(lh, 0);
        lh += name;
        out += lh; out += data;
        offset += lh.size() + data.size();
        // central directory entry
        put32(central, 0x02014b50); put16(central, 20); put16(central, 20); put16(central, 0); put16(central, 0);
        put16(central, 0); put16(central, 0x21);
        put32(central, crc); put32(central, data.size()); put32(central, data.size());
        put16(central, name.size()); put16(central, 0); put16(central, 0); put16(central, 0); put16(central, 0);
        put32(central, 0); put32(central, off);
        central += name;
    }
    unsigned long cdoff = out.size();
    out += central;
    // end of central directory
    put32(out, 0x06054b50); put16(out, 0); put16(out, 0);
    put16(out, files.size()); put16(out, files.size());
    put32(out, central.size()); put32(out, cdoff); put16(out, 0);
    return out;
}

static std::string b64(const std::string &in) {
    static const char *T = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    std::string o; int val = 0, bits = -6;
    for (unsigned char c : in) { val = (val << 8) + c; bits += 8; while (bits >= 0) { o.push_back(T[(val >> bits) & 0x3f]); bits -= 6; } }
    if (bits > -6) o.push_back(T[((val << 8) >> (bits + 8)) & 0x3f]);
    while (o.size() % 4) o.push_back('=');
    return o;
}

static void emit(const std::string &status, const mpz_class *p, const mpz_class *q,
                 const std::string &cid, double solve_time, long num_bits) {
    std::string result = "{\"status\": \"" + status + "\", \"p\": " +
        (p ? p->get_str() : "null") + ", \"q\": " + (q ? q->get_str() : "null") + "}";
    char st[64]; snprintf(st, sizeof st, "%.2f", solve_time);
    std::string info = "{\"solution_status\": \"" + status + "\", \"challenge_id\": \"" + cid +
        "\", \"solve_time_seconds\": " + st + ", \"method\": \"cado_gnfs\", \"num_bits\": " +
        std::to_string(num_bits) + ", \"orchestrator\": \"cpp\"}";
    std::string zip = make_zip({{"result.json", result}, {"solve_info.json", info}});
    fflush(stdout); fflush(stderr);
    fwrite(SEP, 1, strlen(SEP), stdout);
    std::string e = b64(zip);
    fwrite(e.data(), 1, e.size(), stdout);
    fputc('\n', stdout);
    fflush(stdout);
}

// --- read whole file --------------------------------------------------------

static bool read_file(const char *path, std::string &out) {
    FILE *f = fopen(path, "rb"); if (!f) return false;
    char buf[8192]; size_t r; while ((r = fread(buf, 1, sizeof buf, f)) > 0) out.append(buf, r); fclose(f);
    return true;
}

int main(int argc, char **argv) {
    long wall = env_long("WALL_TIME", 14400);
    long margin = env_long("DEADLINE_MARGIN", 120);
    double start = (double)time(nullptr);
    double deadline = start + wall - margin;

    std::string cid = "challenge", json;
    std::string cif = "/challenge_input/challenge_input.json";
    if (access(cif.c_str(), F_OK) == 0 && read_file(cif.c_str(), json)) {
        if (argc > 1) cid = argv[1];
    } else if (argc == 3) {
        cid = argv[1]; json = argv[2];
    } else {
        fprintf(stderr, "No problem input\n"); return 1;
    }

    std::string num_s, bits_s;
    if (!json_int_field(json, "num", num_s)) { fprintf(stderr, "no num field\n"); return 1; }
    json_int_field(json, "num_bits", bits_s);
    mpz_class n(num_s);
    long num_bits = bits_s.empty() ? (long)mpz_sizeinbase(n.get_mpz_t(), 2) : atol(bits_s.c_str());
    if (n < 6) { fprintf(stderr, "n too small\n"); return 1; }

    logmsg("Starting Breaking RSA challenge: " + cid);
    {
        std::string ns = n.get_str();
        logmsg("N = " + ns.substr(0, 40) + (ns.size() > 40 ? "..." : "") + " (" + std::to_string(num_bits) + " bits, " + std::to_string(ns.size()) + " digits)");
    }

    mpz_class p, q; bool ok = false; std::string method;

    // Stage 2: CADO GNFS
    if (run_cado(n, deadline, p, q)) { ok = true; method = "cado_gnfs"; }

    double solve_time = (double)time(nullptr) - start;
    // final verification
    if (ok) ok = (p * q == n) && is_prime(p) && is_prime(q);

    if (ok) {
        logmsg("SUCCESS via " + method + " in " + std::to_string(solve_time) + "s");
        emit("success", &p, &q, cid, solve_time, num_bits);
        return 0;
    }
    logmsg("FAILED after " + std::to_string(solve_time) + "s");
    emit("failed", nullptr, nullptr, cid, solve_time, num_bits);
    return 1;
}
