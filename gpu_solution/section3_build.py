#!/usr/bin/env python3
# Build msieve Section-III inputs from a finished CADO-NFS workdir.
#
# Run UNDER the ramnfs shim (LD_PRELOAD), because every input/output path is
# under /ramwork (the broker's RAM files). Produces, in <workdir>:
#   <name>.fb       msieve factor base (polynomial, msieve format)
#   <name>          msieve savefile: "N\n" + raw ggnfs relations in purged order
#   <name>.purged   msieve purge index: count line + one 0-based index per relation
#
# msieve's cado_filter cycle file references relations by purge-line number, so
# savefile line i must be exactly the i-th relation of <name>.purged.gz. We map
# each purged (a,b) back to its raw relation; CADO free relations (b==0) are not
# in the sieve files and are reconstructed from <name>.freerel.gz (which gives
# the number of algebraic ideals above each free prime).

import glob
import gzip
import os
import re
import sys


def _open(path):
    return gzip.open(path, "rt") if path.endswith(".gz") else open(path)


def write_fb(workdir, cado_name, msieve_name, n):
    poly = os.path.join(workdir, f"{cado_name}.poly")
    coeffs, skew = {}, None
    with open(poly) as f:
        for line in f:
            m = re.match(r'^(c\d+|Y\d+|skew):\s*(\S+)', line)
            if m:
                if m.group(1) == "skew":
                    skew = m.group(2)
                else:
                    coeffs[m.group(1)] = m.group(2)
    deg = max((int(k[1:]) for k in coeffs if k.startswith("c")), default=0)
    with open(os.path.join(workdir, f"{msieve_name}.fb"), "w") as f:
        f.write(f"N {n}\n")
        if skew:
            f.write(f"SKEW {skew}\n")
        f.write(f"R0 {coeffs['Y0']}\n")
        f.write(f"R1 {coeffs['Y1']}\n")
        for i in range(deg + 1):
            f.write(f"A{i} {coeffs.get('c' + str(i), '0')}\n")
    return abs(int(coeffs.get(f"c{deg}", "1")))   # algebraic leading coefficient


def filter_cycles(cyc_path, bad_indices):
    """Drop cycles that reference a 'bad' relation index, in place.

    .cyc binary layout (msieve dump_cycles): uint32 ncols, then per cycle
    uint32 count followed by count uint32 relation indices."""
    import struct
    with open(cyc_path, "rb") as f:
        data = f.read()
    if len(data) < 4:
        return 0
    (ncols,) = struct.unpack_from("<I", data, 0)
    off = 4
    kept = []
    dropped = 0
    for _ in range(ncols):
        (cnt,) = struct.unpack_from("<I", data, off)
        idxs = struct.unpack_from(f"<{cnt}I", data, off + 4)
        rec = data[off:off + 4 + 4 * cnt]
        off += 4 + 4 * cnt
        if bad_indices.isdisjoint(idxs):
            kept.append(rec)
        else:
            dropped += 1
    if dropped:
        with open(cyc_path, "wb") as f:
            f.write(struct.pack("<I", len(kept)))
            f.write(b"".join(kept))
    return dropped


def freerel_root_counts(workdir, name):
    """Map free-relation prime -> number of algebraic ideals (roots) above it."""
    counts = {}
    fr = os.path.join(workdir, f"{name}.freerel.gz")
    if not os.path.exists(fr):
        return counts
    with _open(fr) as f:
        for line in f:
            if line.startswith("#") or ":" not in line:
                continue
            head, rest = line.split(":", 1)
            try:
                a_hex, b = head.split(",")
                if b != "0":
                    continue
                p = int(a_hex, 16)
            except ValueError:
                continue
            nideals = len([x for x in rest.strip().split(",") if x])
            counts[p] = max(1, nideals - 1)  # minus the single rational ideal
    return counts


def relation_files(workdir, name):
    """Raw two-sided sieve relation files (a,b:rational:algebraic).

    msieve needs the two-colon ggnfs format with the rational and algebraic
    primes kept on separate sides. That is CADO's *upload* (las output) format;
    CADO's dup1 files merge both sides into one list (one colon) and are NOT
    usable here. We therefore read the upload files directly. Each is globbed at
    a single directory level (the ramnfs broker lists direct children fine);
    duplicate (a,b) across files simply overwrite the same survivor entry."""
    out = set()
    for pat in (os.path.join(workdir, f"{name}.upload", "*.gz"),
                os.path.join(workdir, "*.gz")):
        out.update(glob.glob(pat))
    skip = re.compile(r"\.(purged|history|index|freerel|roots\d*|poly|renumber|"
                      r"dup\d|nodup|kp)\b")
    return sorted(p for p in out if not skip.search(os.path.basename(p)))


def main():
    workdir, cado_name, msieve_name, n = (sys.argv[1], sys.argv[2],
                                          sys.argv[3], sys.argv[4])
    lead_coeff = write_fb(workdir, cado_name, msieve_name, n)
    with open(os.path.join(workdir, f"{msieve_name}.todo"), "w") as f:
        f.write(f"{n}\n")              # msieve reads N from here (-i)

    purged = os.path.join(workdir, f"{cado_name}.purged.gz")

    # Pass 1: purged (a,b) order; record which are free (b==0) and which raw
    # (a,b) we must recover from the sieve files.
    order = []          # list of (a_int, b_int)
    needed = set()
    with _open(purged) as f:
        f.readline()    # "# count ..."
        for line in f:
            if line.startswith("#") or ":" not in line:
                continue
            ab = line.split(":", 1)[0]
            try:
                a_s, b_s = ab.split(",")
                key = (int(a_s, 16), int(b_s, 16))
            except ValueError:
                continue
            order.append(key)
            if key[1] != 0:
                needed.add(key)

    # Pass 2: stream raw relation files, keep only the survivors we need.
    raw = {}
    rel_re = re.compile(r"^(-?\d+),(\d+):[0-9a-fA-F,]*:[0-9a-fA-F,]*\s*$")
    for rf in relation_files(workdir, cado_name):
        try:
            with _open(rf) as fin:
                for line in fin:
                    if line.startswith("#") or line.count(":") != 2:
                        continue
                    m = rel_re.match(line)
                    if not m:
                        continue
                    key = (int(m.group(1)), int(m.group(2)))
                    if key in needed and key not in raw:
                        raw[key] = line.rstrip("\n")
        except Exception:  # noqa: BLE001
            continue

    # Free-relation reconstruction (generic, from CADO root counts).
    roots = freerel_root_counts(workdir, cado_name)

    # Emit savefile + .purged in purged order.
    save = os.path.join(workdir, msieve_name)
    purge_out = os.path.join(workdir, f"{msieve_name}.purged")
    miss = 0
    with open(save, "w") as sf, open(purge_out, "w") as pf:
        sf.write(f"N {n}\n")
        pf.write(f"{len(order)}\n")
        bad_idx = set()
        for i, (a, b) in enumerate(order):
            if b == 0:                              # free relation
                p_hex = format(a, "x")
                k = roots.get(a, 1)
                rel = f"{a},0:{p_hex}:{','.join([p_hex] * k)}"
                # msieve recomputes a free relation's roots from the polynomial
                # and rejects it when the prime divides the algebraic leading
                # coefficient (projective root: msieve counts < degree affine
                # roots). CADO still emits such relations, so mark them to be
                # excised from the cycle list below.
                if lead_coeff and a and lead_coeff % a == 0:
                    bad_idx.add(i)
            else:
                rel = raw.get((a, b))
                if rel is None:
                    miss += 1
                    rel = f"{a},{b}::"              # placeholder keeps alignment
            sf.write(rel + "\n")
            pf.write(f"{i}\n")

    # Drop cycles that reference a msieve-incompatible free relation. These are
    # rare (only primes dividing the leading coefficient) so the matrix keeps
    # ample excess.
    dropped = 0
    if bad_idx:
        dropped = filter_cycles(os.path.join(workdir, f"{msieve_name}.cyc"),
                                bad_idx)
    sys.stderr.write(f"section3: {len(order)} relations, {miss} unrecovered, "
                     f"{len(bad_idx)} bad free rels, {dropped} cycles dropped\n")
    # A handful of unrecovered relations would corrupt the matrix; fail loudly so
    # the caller falls back to CPU linear algebra.
    sys.exit(1 if miss else 0)


if __name__ == "__main__":
    main()
