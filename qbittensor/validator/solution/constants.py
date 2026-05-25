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

CHALLENGE_SOLTION_PREFIX: str = "sn63solution"

# Host subfolder under each per-solution workspace where the validator materializes
# the miner's stdout-delivered payload (see run_solution.extract_stdout_output):
#   <workspace>/output/stdout.log              — text logs (everything before the separator)
#   <workspace>/output/solution_artifacts.zip  — base64-decoded zip emitted after the separator
#   <workspace>/output/solution_artifacts/     — zip contents extracted for validation/upload
CONTAINER_OUTPUT_DIRNAME: str = "output"
CONTAINER_SOLUTION_DIRNAME: str = "solution_artifacts"
SOLUTION_LOG_FILENAME: str = "stdout.log"
SOLUTION_OUTPUT_ZIP_FILENAME: str = "solution_artifacts.zip"

# Paths inside the miner container (must match miner solution conventions).
CONTAINER_CHALLENGE_INPUT_PATH: str = "/challenge_input"

# Magic line written by the miner on stdout to delimit text logs (everything before)
# from a base64-encoded zip of the solution artifacts (everything after). Docker's
# json-file logging driver treats stdout as UTF-8 text and corrupts raw binary, so
# the zip MUST be base64-encoded by the miner and is base64-decoded by the validator.
# Picked to be unique enough that miners are unlikely to log it accidentally — keep
# this in sync with workbench/challenges/.../mock_solution.py.
SOLUTION_OUTPUT_SEPARATOR: bytes = (
    b"\n----- ENIGMA-SOLUTION-OUTPUT-BEGIN-a8c7f3e2-9d4b-4c5a-8f1e-2b6d3a4e5f7c -----\n"
)

# Cap on how many bytes of stdout the validator will read from a single solution
# container. Anything larger than this means the miner is misbehaving / blasting
# the log stream and the run is treated as failed. 256 MiB comfortably exceeds the
# 1 GiB uncompressed zip budget once you account for typical compression ratios
# while still bounding memory use on the validator.
SOLUTION_STDOUT_MAX_BYTES_ENV: str = "VALIDATOR_SOLUTION_STDOUT_MAX_BYTES"
SOLUTION_STDOUT_MAX_BYTES_DEFAULT: int = 256 * 1024**2  # 256 MiB

# Fresh per-run host dir, mounted read-only at CONTAINER_CHALLENGE_INPUT_PATH.
CHALLENGE_INPUT_DIRNAME: str = "challenge_input_mount"

# Docker run hardening for miner solution containers (see run_solution.docker_run_security_args).
VALIDATOR_DOCKER_PIDS_LIMIT_ENV: str = "VALIDATOR_DOCKER_PIDS_LIMIT"
VALIDATOR_DOCKER_PIDS_LIMIT_DEFAULT: str = "128"

VALIDATOR_DOCKER_ULIMIT_NOFILE_ENV: str = "VALIDATOR_DOCKER_ULIMIT_NOFILE"
VALIDATOR_DOCKER_ULIMIT_NOFILE_DEFAULT: str = "1024:1024"

VALIDATOR_DOCKER_CAP_DROP_ENV: str = "VALIDATOR_DOCKER_CAP_DROP"
VALIDATOR_DOCKER_CAP_DROP_DEFAULT: str = "ALL"

VALIDATOR_DOCKER_NO_NEW_PRIVILEGES_ENV: str = "VALIDATOR_DOCKER_NO_NEW_PRIVILEGES"
VALIDATOR_DOCKER_NO_NEW_PRIVILEGES_DEFAULT: bool = True

VALIDATOR_DOCKER_READ_ONLY_ENV: str = "VALIDATOR_DOCKER_READ_ONLY"
VALIDATOR_DOCKER_READ_ONLY_DEFAULT: bool = True

VALIDATOR_DOCKER_TMPFS_ENV: str = "VALIDATOR_DOCKER_TMPFS"
VALIDATOR_DOCKER_TMPFS_DEFAULT: str = "/tmp:noexec,nosuid,size=256m"

# Force non-root at ``docker run`` (overrides image USER/CMD default). Miner Dockerfiles
# must define this account (``USER miner``) or pass ``uid[:gid]``. Empty disables.
VALIDATOR_DOCKER_MINER_USER_ENV: str = "VALIDATOR_DOCKER_MINER_USER"
VALIDATOR_DOCKER_MINER_USER_DEFAULT: str = "miner"

# Max total uncompressed size allowed when validating miner solution zips (bytes).
VALIDATOR_ZIP_MAX_UNCOMPRESSED_BYTES_ENV: str = "VALIDATOR_ZIP_MAX_UNCOMPRESSED_BYTES"
VALIDATOR_ZIP_MAX_UNCOMPRESSED_BYTES_DEFAULT: int = 1 * 1024**3  # 1 GiB

# ``docker run --memory`` cap. Default is bytes; env may use Docker suffixes (e.g. 2g, 512m).
# Empty disables. When set, ``--memory-swap`` is set to the same value (no extra swap).
VALIDATOR_MEMORY_LIMIT_ENV: str = "VALIDATOR_MEMORY_LIMIT"
VALIDATOR_MEMORY_LIMIT_DEFAULT: str = str(2 * 1024**3)  # 2 GiB

# ``docker run --cpus`` limit in CPU cores (fractional allowed, e.g. 0.5). Empty disables.
VALIDATOR_DOCKER_CPU_LIMIT_ENV: str = "VALIDATOR_DOCKER_CPU_LIMIT"
VALIDATOR_DOCKER_CPU_LIMIT_DEFAULT: str = "2"

# Max built miner solution Docker image size (``docker image inspect --format='{{.Size}}'``, bytes).
MAX_SOLUTION_DOCKER_IMAGE_SIZE_BYTES_ENV: str = "MAX_SOLUTION_DOCKER_IMAGE_SIZE_BYTES"
MAX_SOLUTION_DOCKER_IMAGE_SIZE_BYTES: int = 10 * 1024**3  # 10 GiB

VALIDATOR_CONTAINER_STOP_TIMEOUT_ENV: str = "VALIDATOR_CONTAINER_STOP_TIMEOUT"
VALIDATOR_CONTAINER_STOP_TIMEOUT_DEFAULT: str = "30"
