# Breaking RSA — YAFU factor() solution

Automated SOTA factoring via **YAFU** (Buhrow): trial division → rho → p-1 →
ECM → **SIQS**. Turnkey and fast for smaller milestones (AVX SIQS + ECM).

```
breaking_rsa.py     # runs `yafu factor(N)` (expression fed via stdin), parses + self-verifies
Dockerfile          # python:3.12-slim + GMP-ECM + YAFU
enigma_challenges/  # vendored platform interface (required)
```

## Scope / limits
- **Wins sizes YAFU handles in budget** — roughly ≤ ~95 digits, where YAFU uses
  SIQS (its qs/gnfs crossover is 95 digits). Verified: c80 factored in ~14 s
  under the validator sandbox.
- **NFS not enabled.** Above ~95 digits YAFU wants NFS, which needs the external
  **GGNFS lattice sievers** (`ggnfs_dir` in yafu.ini). GGNFS's assembly does not
  build on modern toolchains without a patched fork. Even with it, YAFU-NFS ≈
  CADO-NFS at 460 bits (~1–3 days) — so it does **not** clear a 460-bit number
  in 4 h. No factoring engine does on a single machine.

## Build & test
```bash
docker build -t enigma-yafu .
docker run --rm --read-only --tmpfs /tmp:noexec,nosuid,size=256m --user miner --network none \
  --memory 85g --cpus 16 enigma-yafu cid '{"difficulty":266,"num":<c80 N>,"num_bits":266}'
```

Env: `YAFU_BIN`, `YAFU_THREADS`, `YAFU_WORKDIR`, `YAFU_TIMEOUT`.
