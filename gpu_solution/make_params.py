#!/usr/bin/env python3
# Generate right-sized CADO params for the c136-c139 gap by linearly
# interpolating CADO's hand-tuned (Mike Curtis) params.c135 and params.c140.
# CADO picks the NEAREST params file (no interpolation), so without these a
# c138/c139 (RSA-460) job runs on oversized c140 params. Interpolating between
# two expert-tuned endpoints is safe and right-sizes lim/qmin/rels_wanted etc.
import os, re, sys

SRC = sys.argv[1] if len(sys.argv) > 1 else "/opt/cado-nfs/parameters/factor"
OUT = sys.argv[2] if len(sys.argv) > 2 else SRC

def load(path):
    d = {}
    for line in open(path):
        m = re.match(r'^\s*([\w.]+)\s*=\s*(\S+)', line)
        if m:
            d[m.group(1)] = m.group(2)
    return d

c135 = load(os.path.join(SRC, "params.c135"))
c140 = load(os.path.join(SRC, "params.c140"))

# params interpolated as floats then rounded; integers kept integer.
INT_KEYS = {"tasks.polyselect.nq", "tasks.polyselect.nrkeep", "tasks.lim0",
            "tasks.lim1", "tasks.lpb0", "tasks.lpb1", "tasks.sieve.mfb0",
            "tasks.sieve.mfb1", "tasks.sieve.ncurves0", "tasks.sieve.ncurves1",
            "tasks.qmin", "tasks.sieve.rels_wanted", "tasks.filter.purge.keep",
            "tasks.linalg.bwc.interval", "tasks.polyselect.P"}

def num(v):
    # handles "9e4", "1.89", "9000000"
    return float(v)

def interp(k, w):
    a, b = c135.get(k), c140.get(k)
    if a is None or b is None:
        return None
    if a == b:
        return a                                   # constant across both
    va, vb = num(a), num(b)
    x = va + w * (vb - va)
    if k in INT_KEYS:
        return str(int(round(x)))
    return f"{x:.3f}".rstrip("0").rstrip(".")

# union of keys, preserve c140 order
keys = list(c140.keys())
for digits in (136, 137, 138, 139):
    w = (digits - 135) / 5.0                        # 0.2 .. 0.8
    lines = [f"# Right-sized for c{digits}: linear interpolation of CADO's",
             f"# hand-tuned params.c135 and params.c140 (w={w:.1f} toward c140).",
             f"name = c{digits}"]
    for k in keys:
        if k == "name":
            continue
        v = interp(k, w)
        if v is not None:
            lines.append(f"{k} = {v}")
    path = os.path.join(OUT, f"params.c{digits}")
    open(path, "w").write("\n".join(lines) + "\n")
    print(f"wrote {path}: lim1={interp('tasks.lim1',w)} qmin={interp('tasks.qmin',w)} "
          f"rels_wanted={interp('tasks.sieve.rels_wanted',w)} ropteffort={interp('tasks.polyselect.ropteffort',w)}")
