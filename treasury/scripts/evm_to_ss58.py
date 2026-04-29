# The MIT License (MIT)
# Copyright © 2026 qBitTensor Labs

# Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
# documentation files (the “Software”), to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all copies or substantial portions of
# the Software.

# THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO
# THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
# THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.

from substrateinterface.utils.hasher import blake2_256
from substrateinterface import Keypair
import sys
addr_str = (sys.argv[1] or "").strip()
if addr_str.startswith("0x") or addr_str.startswith("0X"):
    addr_str = addr_str[2:]
if not addr_str or len(addr_str) != 40:
    raise SystemExit("Usage: python3 evm_to_ss58.py <0x-prefixed 20-byte hex address>")
evm_addr = bytes.fromhex(addr_str)

prefix = b'evm:'
account_id = blake2_256(prefix + evm_addr)
ss58 = Keypair(public_key=account_id, ss58_format=42).ss58_address
print(f'SS58: {ss58}')