#!/usr/bin/env python3
# The MIT License (MIT)
# Copyright © 2026 qBitTensor Labs
#
# Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
# documentation files (the “Software”), to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all copies or substantial portions of
# the Software.
#
# THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO
# THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
# THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.

"""Minimal proof-of-concept: confirm the container can see and use the host GPU."""

from __future__ import annotations

import subprocess
import sys

import numpy as np
from numba import cuda


@cuda.jit
def _add_kernel(a, b, out):
    i = cuda.grid(1)
    if i < out.size:
        out[i] = a[i] + b[i]


def main() -> int:
    print("=== nvidia-smi ===")
    try:
        subprocess.run(["nvidia-smi"], check=True)
    except FileNotFoundError:
        print("nvidia-smi not found (CUDA driver tools may still work via Numba)")
    except subprocess.CalledProcessError as exc:
        print(f"nvidia-smi failed with exit code {exc.returncode}")

    print("\n=== CUDA runtime (Numba) ===")
    if not cuda.is_available():
        print("FAIL: cuda.is_available() is False")
        return 1

    print(f"GPU count: {len(cuda.gpus)}")
    device = cuda.get_current_device()
    name = device.name.decode() if isinstance(device.name, bytes) else str(device.name)
    cc = device.compute_capability
    print(f"Device 0: {name} (compute {cc[0]}.{cc[1]})")

    n = 4
    a = np.array([1.0, 2.0, 3.0, 4.0], dtype=np.float32)
    b = np.array([10.0, 20.0, 30.0, 40.0], dtype=np.float32)
    out = np.zeros(n, dtype=np.float32)
    _add_kernel[1, n](a, b, out)

    expected = a + b
    if not np.allclose(out, expected):
        print(f"FAIL: kernel result {out} != expected {expected}")
        return 1

    print(f"Vector add on GPU OK: {out.tolist()}")
    print("\nSUCCESS: GPU is accessible from this container.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
