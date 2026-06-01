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

from datetime import timedelta

# --- Cross-Check ---
CROSS_CHECK_TIMEOUT: timedelta = timedelta(seconds=30)
CROSS_CHECK_MAX_BATCH_SIZE: int = 3

# --- Solutions Container Management ---
# How often the SolutionContainerManager runs its periodic check.
# This controls how quickly we detect completed solutions, prune exited
# containers/images, and kill overdue ones.
SOLUTION_CONTAINER_MANAGER_TIMEOUT: timedelta = timedelta(minutes=5)

# Maximum allowed runtime for a single solution container before it is
# forcibly terminated. The container manager will catch these on its next tick.
MAX_SOLUTION_RUNTIME: timedelta = timedelta(minutes=30)

MAX_SOLUTIONS: int = 1
