#!/usr/bin/env python3
"""Run the real solver entrypoint on a QASM and check it recovers the planted peak.

    python tools/test_local.py --qasm /tmp/peaked.qasm

Reads the ground-truth peak from <qasm>.peak.json, runs hardening_quantum_proof.py as a
subprocess (the actual entrypoint, in OUTPUT_DIR/direct mode), then compares the
emitted bitstring against the peak — reporting whether it matched directly or only
when reversed (to catch any bit-ordering convention mismatch).
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOLVER_DIR = HERE.parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--qasm", required=True)
    ap.add_argument("--sidecar", default=None)
    ap.add_argument("--wall", type=int, default=1200)
    ap.add_argument("--allow-cpu", action="store_true")
    args = ap.parse_args()

    sidecar = Path(args.sidecar or (args.qasm + ".peak.json"))
    truth = json.loads(sidecar.read_text()) if sidecar.is_file() else {}
    peak = truth.get("peak")

    outdir = tempfile.mkdtemp(prefix="peaked_out_")
    env = dict(os.environ)
    env["OUTPUT_DIR"] = outdir
    env["WALL_TIME"] = str(args.wall)
    env["DEADLINE_MARGIN"] = "60"
    if args.allow_cpu:
        env["ALLOW_CPU"] = "1"

    t0 = time.time()
    proc = subprocess.run(
        [sys.executable, str(SOLVER_DIR / "hardening_quantum_proof.py"), args.qasm],
        cwd=str(SOLVER_DIR), env=env, capture_output=True, text=True,
    )
    dt = time.time() - t0

    # Show the human log portion (before the separator)
    sep = "ENIGMA-SOLUTION-OUTPUT-BEGIN"
    log = proc.stdout.split(sep)[0]
    print(log[-4000:])
    if proc.returncode not in (0, 1):
        print(f"[stderr]\n{proc.stderr[-2000:]}")

    result_path = Path(outdir, "result.json")
    if not result_path.is_file():
        print(f"FAIL: no result.json (exit {proc.returncode}) in {outdir}")
        sys.exit(1)
    result = json.loads(result_path.read_text())
    got = result.get("peaked_state")
    info = {}
    sip = Path(outdir, "solve_info.json")
    if sip.is_file():
        info = json.loads(sip.read_text())

    print("\n================ RESULT ================")
    print(f"qasm        : {args.qasm}")
    print(f"truth peak  : {peak}  (weight={truth.get('peak_weight')}, verified={truth.get('verified_by_statevector')})")
    print(f"got         : {got}")
    print(f"solve_time  : {dt:.1f}s   exit={proc.returncode}")
    print(f"decision    : {info.get('diagnostics', {}).get('reason')}")
    print(f"best_amp2   : {info.get('diagnostics', {}).get('best_amp2')}  baseline_2^-n={info.get('diagnostics', {}).get('baseline_2_minus_n')}")

    if peak is None:
        print("VERDICT: (no ground truth to compare)")
        return
    if got == peak:
        print("VERDICT: PASS  ✅  (exact match)")
    elif got and got[::-1] == peak:
        print("VERDICT: PASS-but-REVERSED  ⚠️  (bit order is flipped vs ground truth — fix convention)")
    else:
        hd = sum(a != b for a, b in zip(got or "", peak)) if got else len(peak)
        print(f"VERDICT: FAIL  ❌  (Hamming distance {hd}/{len(peak)})")


if __name__ == "__main__":
    main()
