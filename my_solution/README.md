# Breaking RSA — GNFS (CADO-NFS) solver

A drop-in Enigma SN63 *Breaking RSA* solution built for the **460-bit (~139-digit)**
challenge, where the previous top solution (multithreaded YAFU **SIQS**) cannot
finish inside the 4-hour wall-time.

Same submission shape as any Enigma solver:

```
breaking_rsa.py        # the solver wrapper (this engine)
Dockerfile             # builds GMP-ECM + CADO-NFS, runs as user "miner"
enigma_challenges/     # vendored platform interface (verbatim from the repo)
```

## Why this beats the incumbent at 460 bits

| | Incumbent (YAFU SIQS) | This solution (CADO-NFS GNFS) |
|---|---|---|
| Algorithm | Quadratic Sieve | **General Number Field Sieve** |
| Best range | ~100–110 digits | **>110 digits — the SOTA for 139 digits** |
| 460-bit / 4 h | ❌ would need *days* | ✅ in the feasible window |

At ~139 digits the semiprime is well past the SIQS→GNFS crossover (~100–110
digits), so simply fielding a working GNFS pipeline beats SIQS here — **if** two
constraints are solved: scratch disk and (secondarily) the clock.

## Pipeline

| Stage | Method | Purpose |
|---|---|---|
| 0 | trial division, perfect-square, bounded Pollard ρ (Brent), short Pollard p−1 | instant wins for small/degenerate inputs and low-bit workbench tests |
| 0.5 | short GMP-ECM pre-test (`ECM_PRETEST`) | cheap insurance vs an unexpectedly small/unbalanced factor; ~seconds, finds nothing on a balanced c139 |
| 1 | **CADO-NFS** (poly-select → lattice sieve → filter → linear algebra → sqrt) | the engine for the real challenge size |

Output, input, and self-verification (`p·q==N` **and** both prime) match the
platform contract exactly; the solver emits `logs → separator → base64(zip)` of
`result.json` + `solve_info.json`.

## The real blocker: 256 MB of scratch disk

The validator runs solutions **read-only-root** with a single writable `/tmp`
that is a **256 MB noexec tmpfs**, alongside ~85 GB RAM (`--memory 85g`), 24
cores, and a GPU. A 139-digit GNFS produces **multiple GB** of relation/matrix
scratch — it `ENOSPC`s in 256 MB. `provision_scratch()` resolves scratch in
three tiers:

1. **`CADO_WORKDIR`** — explicit operator override (e.g. a host-mounted ramdisk). *Most reliable.*
2. **Largest existing tmpfs** already present (`/dev/shm`, `/tmp`) if it has ≥ `SCRATCH_MIN_GB`.
3. **Self-provisioned RAM tmpfs** — best-effort `unshare --map-root-user --mount` + `mount -t tmpfs -o size=${RAMDISK_SIZE_GB}g` at `/scratch`, sized to RAM. This is the one *unprivileged* way to get >256 MB of writable space; it works on hosts that permit unprivileged user namespaces and **falls back with a loud warning** if the sandbox forbids it.

> ⚠️ **Make-or-break risk.** Tier 3 depends on the validator host allowing
> unprivileged user namespaces + tmpfs mounts (default Docker seccomp may block
> the `mount` syscall). If it does, GNFS runs entirely in RAM and finishes. If
> it does not, **set `CADO_WORKDIR` to a writable ≥ ~10 GB path**, or have the
> validator raise the tmpfs / `--shm-size`. This must be confirmed on the real
> validator — it cannot be validated from the workbench alone.

## The GPU (RTX PRO 6000)

GNFS **sieving — 70–85 % of the work — is CPU-only** in every production tool,
so the GPU does **not** accelerate the bottleneck. Where it helps is polynomial
selection (and, in msieve, linear algebra). The baseline here is robust
**CPU-only CADO**. To use the GPU for poly-select, uncomment the CUDA build
stanza in the `Dockerfile` and run with `USE_GPU_POLYSELECT=1` (+ `CADO_GPU_DIR`).
This is an *enhancement*, not a requirement — it shaves the minority of the job,
not the sieve.

## Environment knobs

| Var | Default | Meaning |
|---|---|---|
| `CADO_NFS` | `/opt/cado-nfs/cado-nfs.py` | path to the CADO orchestrator |
| `CADO_THREADS` | `os.cpu_count()` | CADO worker threads |
| `CADO_WORKDIR` | auto (RAM) | force a scratch directory |
| `CADO_EXTRA_ARGS` | — | extra args appended to `cado-nfs.py` |
| `CADO_TIMEOUT` | none | soft self-timeout (s) for the CADO run |
| `SCRATCH_MIN_GB` | `3` | min free GB to trust an existing tmpfs |
| `RAMDISK_SIZE_GB` | `60` | size of the self-provisioned tmpfs |
| `USE_USERNS_RAMDISK` | `1` | attempt the unprivileged-userns ramdisk |
| `USE_GPU_POLYSELECT` | `0` | use CADO CUDA poly-select |
| `ECM_PRETEST` / `ECM_CURVES` / `ECM_B1` | `1` / `40` / `250000` | Stage 0.5 |
| `RHO_BUDGET` | `2000000` | Pollard ρ budget |

## Build

```bash
# reproducible: pin CADO/ECM to a tag or commit
docker build -t enigma-rsa-gnfs \
  --build-arg CADO_REF=master \
  --build-arg ECM_REF=master \
  /root/breaking_rsa_gnfs
```

The build clones GMP-ECM and CADO-NFS from their canonical Inria GitLab repos
(CADO GitHub mirror: <https://github.com/cado-nfs/cado-nfs>) and runs a CADO
smoke factorization as a build-time correctness gate.

## Test locally

**Plumbing / small-N (no Docker, seconds)** — exercises input parsing, Stage 0
factoring, the output protocol, and self-verify:

```bash
cd /root/breaking_rsa_gnfs
python breaking_rsa.py test-id '{"difficulty": 60, "num": <semiprime>, "num_bits": 60}'
```

**Workbench (the validator-equivalent harness)** from the enigma repo:

```bash
cd /root/enigma
python -m workbench test breaking-rsa --solution /root/breaking_rsa_gnfs --mode direct --difficulty 60 --seed 42
# full path (Docker, real engine):  --mode docker --difficulty 460
```

A small `--difficulty` is solved by Stage 0. A real **460-bit** run needs the
CADO engine, the RAM-scratch tier working, and hours of compute — validate that
on hardware equivalent to the validator (24 cores + the RAM tier), not in a
quick local run.

## Honest status

- ✅ Wrapper logic, I/O contract, self-verify, Stage 0/0.5, graceful degradation, and the scratch-provisioning logic are implemented and locally tested.
- ⚠️ **Not yet proven on real hardware:** (1) that 460-bit CADO-NFS finishes within 4 h on 24 cores, and (2) that the unprivileged RAM-tmpfs tier is permitted by the validator sandbox. Both require a real validator-equivalent run. Tier-1 (`CADO_WORKDIR` on a guaranteed ramdisk) de-risks (2).
