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

import base64
import binascii
import os
import shutil
import subprocess
import time
import zipfile

import bittensor as bt

from qbittensor.validator.solution.exceptions.invalid_solution import InvalidSolutionError
from qbittensor.validator.solution.exceptions.validation_errors import ValidationErrors
from .constants import (
    CHALLENGE_INPUT_DIRNAME,
    CONTAINER_CHALLENGE_INPUT_PATH,
    CONTAINER_OUTPUT_DIRNAME,
    CONTAINER_SOLUTION_DIRNAME,
    SOLUTION_LOG_FILENAME,
    SOLUTION_OUTPUT_SEPARATOR,
    SOLUTION_OUTPUT_ZIP_FILENAME,
    SOLUTION_STDOUT_MAX_BYTES_DEFAULT,
    SOLUTION_STDOUT_MAX_BYTES_ENV,
    VALIDATOR_DOCKER_CAP_DROP_DEFAULT,
    VALIDATOR_DOCKER_CAP_DROP_ENV,
    VALIDATOR_DOCKER_CPU_LIMIT_DEFAULT,
    VALIDATOR_DOCKER_CPU_LIMIT_ENV,
    VALIDATOR_DOCKER_MINER_USER_DEFAULT,
    VALIDATOR_DOCKER_MINER_USER_ENV,
    VALIDATOR_DOCKER_NO_NEW_PRIVILEGES_DEFAULT,
    VALIDATOR_DOCKER_NO_NEW_PRIVILEGES_ENV,
    VALIDATOR_DOCKER_PIDS_LIMIT_DEFAULT,
    VALIDATOR_DOCKER_PIDS_LIMIT_ENV,
    VALIDATOR_DOCKER_READ_ONLY_DEFAULT,
    VALIDATOR_DOCKER_READ_ONLY_ENV,
    VALIDATOR_DOCKER_TMPFS_DEFAULT,
    VALIDATOR_DOCKER_TMPFS_ENV,
    VALIDATOR_DOCKER_ULIMIT_NOFILE_DEFAULT,
    VALIDATOR_DOCKER_ULIMIT_NOFILE_ENV,
    VALIDATOR_MEMORY_LIMIT_DEFAULT,
    VALIDATOR_MEMORY_LIMIT_ENV,
    VALIDATOR_CONTAINER_STOP_TIMEOUT_DEFAULT,
    VALIDATOR_CONTAINER_STOP_TIMEOUT_ENV,
)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _stdout_max_bytes() -> int:
    raw = os.getenv(SOLUTION_STDOUT_MAX_BYTES_ENV, str(SOLUTION_STDOUT_MAX_BYTES_DEFAULT)).strip()
    try:
        return int(raw)
    except ValueError:
        bt.logging.warning(
            f"Invalid {SOLUTION_STDOUT_MAX_BYTES_ENV}={raw!r}; "
            f"using default {SOLUTION_STDOUT_MAX_BYTES_DEFAULT} bytes."
        )
        return SOLUTION_STDOUT_MAX_BYTES_DEFAULT


def prepare_challenge_input_mount_dir(workspace: str) -> str:
    """
    Create a fresh host directory for this run's challenge input (mounted ``:ro``).
    Challenge setup should write files only under this path.
    """
    mount_dir = os.path.join(os.path.abspath(workspace), CHALLENGE_INPUT_DIRNAME)
    if os.path.isdir(mount_dir):
        bt.logging.info(f"🗑️ Removing previous challenge input mount dir: {mount_dir}")
        try:
            shutil.rmtree(mount_dir)
            bt.logging.info(f"🗑️ Removed previous challenge input mount dir: {mount_dir}")
        except Exception as e:
            bt.logging.warning(f"⚠️ Failed to remove previous mount dir {mount_dir}: {e}")
    os.makedirs(mount_dir, mode=0o755)
    return mount_dir


def extract_stdout_output(container_ref: str, host_workspace: str) -> bool:
    """
    Pull a stopped miner container's stdout via ``docker logs`` and split it into
    the run's log file and the run's solution-output zip.

    The miner contract is:

        <text logs...>
        <SOLUTION_OUTPUT_SEPARATOR>
        <base64-encoded zip of the solution_artifacts directory>

    Docker's json-file logging driver treats stdout as UTF-8 text and corrupts
    raw binary bytes; base64 encoding survives the round-trip intact.

    Everything before the first occurrence of the separator is written verbatim to
    ``<host_workspace>/output/stdout.log``. The base64 payload after the separator
    is decoded, written to ``<host_workspace>/output/solution_artifacts.zip``, and
    (when valid) extracted into ``<host_workspace>/output/solution_artifacts/``.

    Returns ``True`` when at least the log file was produced; the function still
    returns ``True`` if the separator was missing (whole stdout → logs, no
    artifacts). Returns ``False`` when ``docker logs`` fails, the payload is not
    valid base64, or the decoded bytes are not a valid zip.
    """
    host_output = os.path.join(os.path.abspath(host_workspace), CONTAINER_OUTPUT_DIRNAME)
    os.makedirs(host_output, exist_ok=True)

    artifacts_dir = os.path.join(host_output, CONTAINER_SOLUTION_DIRNAME)
    os.makedirs(artifacts_dir, exist_ok=True)

    max_bytes = _stdout_max_bytes()
    try:
        result = subprocess.run(
            ["docker", "logs", container_ref],
            capture_output=True,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        stderr = ((e.stderr or e.stdout or b"").decode("utf-8", errors="replace")).strip()
        bt.logging.error(
            f"❌ Failed to read stdout from '{container_ref}': {stderr}"
        )
        _write_extraction_diagnostics(artifacts_dir, container_ref, f"docker logs failed: {stderr}")
        return False
    except OSError as e:
        bt.logging.error(f"❌ Failed to invoke docker logs for '{container_ref}': {e}")
        _write_extraction_diagnostics(artifacts_dir, container_ref, f"Failed to invoke docker logs: {e}")
        return False

    raw_stdout = result.stdout or b""
    if len(raw_stdout) > max_bytes:
        bt.logging.error(
            f"❌ Container '{container_ref}' produced {len(raw_stdout)} bytes of stdout, "
            f"exceeding the {max_bytes} byte cap; truncating and refusing to extract artifacts."
        )
        truncated = raw_stdout[:max_bytes]
        _write_log_file(host_output, truncated)
        _write_extraction_diagnostics(
            artifacts_dir, container_ref,
            f"stdout exceeded cap ({len(raw_stdout)} > {max_bytes} bytes). Truncated logs written; no artifacts extracted."
        )
        return False

    logs_bytes, payload_b64, separator_found = _split_on_separator(raw_stdout)
    _write_log_file(host_output, logs_bytes)

    if not separator_found:
        bt.logging.warning(
            f"⚠️ No solution-output separator found in stdout of '{container_ref}'; "
            f"treating the entire stdout as logs and skipping artifact extraction."
        )
        _write_extraction_diagnostics(
            artifacts_dir, container_ref,
            "No SOLUTION_OUTPUT_SEPARATOR found in container stdout.\n"
            "The solution must write logs, then exactly this line on its own line:\n"
            f"{SOLUTION_OUTPUT_SEPARATOR.decode('utf-8', errors='replace')!r}\n"
            "then base64-encoded zip of result.json + output.txt.\n"
            "See the mock_solution.py example for the required contract."
        )
        return True

    try:
        payload_bytes = base64.b64decode(payload_b64.strip(), validate=True)
    except (binascii.Error, ValueError) as e:
        bt.logging.error(
            f"❌ Solution payload from '{container_ref}' is not valid base64: {e}"
        )
        _write_extraction_diagnostics(artifacts_dir, container_ref, f"Base64 decode of solution payload failed: {e}")
        return False

    zip_path = os.path.join(host_output, SOLUTION_OUTPUT_ZIP_FILENAME)
    try:
        with open(zip_path, "wb") as f:
            f.write(payload_bytes)
    except OSError as e:
        bt.logging.error(
            f"❌ Failed to write solution zip for '{container_ref}' to '{zip_path}': {e}"
        )
        _write_extraction_diagnostics(artifacts_dir, container_ref, f"Failed to write decoded solution zip: {e}")
        return False

    if os.path.isdir(artifacts_dir):
        shutil.rmtree(artifacts_dir)
    os.makedirs(artifacts_dir, exist_ok=True)

    if not payload_bytes:
        bt.logging.warning(
            f"⚠️ Solution-output payload from '{container_ref}' is empty after the separator; "
            f"leaving '{artifacts_dir}' empty."
        )
        _write_extraction_diagnostics(
            artifacts_dir, container_ref,
            "Separator was present but the base64 payload after it was empty. "
            "The solution wrote the separator line but no (or zero-length) zip data after it."
        )
        return True

    if not zipfile.is_zipfile(zip_path):
        bt.logging.error(
            f"❌ Solution-output payload from '{container_ref}' at '{zip_path}' "
            f"is not a valid zip archive; skipping artifact extraction."
        )
        _write_extraction_diagnostics(
            artifacts_dir, container_ref,
            f"Decoded payload after separator is not a valid zip file (path: {zip_path}). "
            "Common causes: logging after the separator, wrong base64, or the solution wrote raw binary instead of base64."
        )
        return False

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            _safe_extract_zip(zf, artifacts_dir)
    except zipfile.BadZipFile as e:
        bt.logging.error(
            f"❌ Failed to read solution zip for '{container_ref}' at '{zip_path}': {e}"
        )
        _write_extraction_diagnostics(artifacts_dir, container_ref, f"BadZipFile when extracting: {e}")
        return False
    except (OSError, RuntimeError) as e:
        bt.logging.error(
            f"❌ Failed to extract solution zip for '{container_ref}' to '{artifacts_dir}': {e}"
        )
        _write_extraction_diagnostics(artifacts_dir, container_ref, f"Failed to extract solution zip: {e}")
        return False

    bt.logging.info(
        f"✅ Extracted solution output from '{container_ref}' to '{artifacts_dir}'"
    )
    return True


def _split_on_separator(raw_stdout: bytes) -> tuple[bytes, bytes, bool]:
    """
    Split a stdout byte stream around the first occurrence of
    :data:`SOLUTION_OUTPUT_SEPARATOR`.

    Returns ``(logs_bytes, payload_bytes, separator_found)``. The separator itself
    is consumed (it appears in neither the logs nor the payload).
    """
    idx = raw_stdout.find(SOLUTION_OUTPUT_SEPARATOR)
    if idx == -1:
        return raw_stdout, b"", False
    return raw_stdout[:idx], raw_stdout[idx + len(SOLUTION_OUTPUT_SEPARATOR):], True


def _write_log_file(host_output_dir: str, logs_bytes: bytes) -> None:
    log_path = os.path.join(host_output_dir, SOLUTION_LOG_FILENAME)
    try:
        with open(log_path, "wb") as f:
            f.write(logs_bytes)
    except OSError as e:
        bt.logging.error(f"❌ Failed to write log file '{log_path}': {e}")


def _write_extraction_diagnostics(artifacts_dir: str, container_ref: str, message: str) -> None:
    """Write a small diagnostics file inside the solution artifacts directory.

    This ensures that even on extraction failures, useful diagnostic information
    (e.g. missing separator, bad base64, docker logs failure, etc.) is included
    when the artifacts directory is zipped and uploaded on the failure path.
    """
    os.makedirs(artifacts_dir, exist_ok=True)
    diag_path = os.path.join(artifacts_dir, "extraction_diagnostics.txt")
    try:
        with open(diag_path, "w", encoding="utf-8") as f:
            f.write(f"Container: {container_ref}\n")
            f.write(f"Time: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n\n")
            f.write(message)
            f.write("\n")
    except Exception as e:
        bt.logging.warning(f"⚠️ Failed to write extraction diagnostics file: {e}")


def _safe_extract_zip(zf: zipfile.ZipFile, destination: str) -> None:
    """Extract a zip while rejecting absolute paths and ``..`` traversal."""
    destination_abs = os.path.abspath(destination)
    for member in zf.infolist():
        member_path = os.path.normpath(os.path.join(destination_abs, member.filename))
        if not (
            member_path == destination_abs
            or member_path.startswith(destination_abs + os.sep)
        ):
            raise RuntimeError(
                f"Refusing to extract '{member.filename}': escapes destination directory"
            )
    zf.extractall(destination_abs)


def _container_run_user() -> str | None:
    """Return the ``docker run --user`` value, or None if non-root enforcement is disabled."""
    raw = os.getenv(
        VALIDATOR_DOCKER_MINER_USER_ENV,
        VALIDATOR_DOCKER_MINER_USER_DEFAULT,
    )
    if raw is None:
        return VALIDATOR_DOCKER_MINER_USER_DEFAULT or None
    user = raw.strip()
    return user or None


def docker_run_security_args() -> list[str]:
    """
    Extra ``docker run`` flags that harden miner solution containers.
    Each setting is controlled by an environment variable (see constants.py).
    """
    args: list[str] = []

    stop_timeout = os.getenv(VALIDATOR_CONTAINER_STOP_TIMEOUT_ENV, VALIDATOR_CONTAINER_STOP_TIMEOUT_DEFAULT).strip()
    if stop_timeout:
        args.extend(["--stop-timeout", stop_timeout])

    pids_limit = os.getenv(VALIDATOR_DOCKER_PIDS_LIMIT_ENV, VALIDATOR_DOCKER_PIDS_LIMIT_DEFAULT).strip()
    if pids_limit:
        args.extend(["--pids-limit", pids_limit])

    ulimit_nofile = os.getenv(
        VALIDATOR_DOCKER_ULIMIT_NOFILE_ENV, VALIDATOR_DOCKER_ULIMIT_NOFILE_DEFAULT
    ).strip()
    if ulimit_nofile:
        args.extend(["--ulimit", f"nofile={ulimit_nofile}"])

    cap_drop = os.getenv(VALIDATOR_DOCKER_CAP_DROP_ENV, VALIDATOR_DOCKER_CAP_DROP_DEFAULT).strip()
    if cap_drop:
        args.extend(["--cap-drop", cap_drop])

    if _env_bool(VALIDATOR_DOCKER_NO_NEW_PRIVILEGES_ENV, VALIDATOR_DOCKER_NO_NEW_PRIVILEGES_DEFAULT):
        args.extend(["--security-opt", "no-new-privileges:true"])

    if _env_bool(VALIDATOR_DOCKER_READ_ONLY_ENV, VALIDATOR_DOCKER_READ_ONLY_DEFAULT):
        args.append("--read-only")

    tmpfs = os.getenv(VALIDATOR_DOCKER_TMPFS_ENV, VALIDATOR_DOCKER_TMPFS_DEFAULT).strip()
    if tmpfs:
        args.extend(["--tmpfs", tmpfs])

    cpus = os.getenv(VALIDATOR_DOCKER_CPU_LIMIT_ENV, VALIDATOR_DOCKER_CPU_LIMIT_DEFAULT).strip()
    if cpus:
        args.extend(["--cpus", cpus])

    memory = os.getenv(VALIDATOR_MEMORY_LIMIT_ENV, VALIDATOR_MEMORY_LIMIT_DEFAULT).strip()
    if memory:
        args.extend(["--memory", memory, "--memory-swap", memory])

    run_user = _container_run_user()
    if run_user:
        args.extend(["--user", run_user])

    return args


def _run_docker_command(
    cmd: list[str],
    *,
    description: str = "docker command",
    check: bool = True,
) -> subprocess.CompletedProcess:
    """
    Centralized helper for running Docker CLI commands.

    - Always captures stdout/stderr as text.
    - On failure (non-zero exit or 'docker' not found), raises InvalidSolutionError
      with a rich, platform-friendly message containing the full command,
      exit code, stderr, and stdout.
    - This ensures we always get actionable diagnostics sent to the cloud.
    """
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=check,
        )
        return result

    except FileNotFoundError as e:
        msg = (
            f"Docker CLI not found while running {description}. "
            "The 'docker' executable is not available in the PATH of the validator process. "
            "Is Docker installed and properly integrated (especially on WSL)?"
        )
        bt.logging.error(f"❌ {msg} | command={' '.join(cmd)} | {e}")
        raise InvalidSolutionError(message=msg) from e

    except subprocess.CalledProcessError as e:
        returncode = e.returncode
        stderr = (e.stderr or "").strip() or "(no stderr captured)"
        stdout = (e.stdout or "").strip() or "(no stdout captured)"

        if returncode == 127:
            detail = f"Docker command not found (exit status 127) while running {description}"
        else:
            detail = f"{description} failed with exit code {returncode}"

        bt.logging.error(
            f"❌ {detail}\n"
            f"   Command: {' '.join(cmd)}\n"
            f"   stderr:\n{stderr}\n"
            f"   stdout:\n{stdout}"
        )

        platform_msg = (
            f"{detail}\n\n"
            f"Command: {' '.join(cmd)}\n"
            f"Exit code: {returncode}\n\n"
            f"stderr:\n{stderr}\n\n"
            f"stdout:\n{stdout}"
        )
        raise InvalidSolutionError(message=platform_msg) from e


def run_image_detached(
    image_name: str,
    container_name: str,
    validator_label: str,
    challenge_input_mount_dir: str,
) -> str:
    """
    Run a Docker container in detached mode with hardened mounts.

    - Challenge input: fresh host dir per run, read-only at ``/challenge_input``.
    - Solution output: nothing is mounted into the container. The miner writes
      text logs + a :data:`SOLUTION_OUTPUT_SEPARATOR` line + a base64-encoded
      zip of artifacts to stdout. The validator captures this after the container
      exits using ``docker logs`` (see :func:`extract_stdout_output`).
    """
    challenge_mount = os.path.abspath(challenge_input_mount_dir)

    cmd = [
        "docker",
        "run",
        "-d",
        "--network",
        "none",
        *docker_run_security_args(),
        "--name",
        container_name,
        "-v", f"{challenge_mount}:{CONTAINER_CHALLENGE_INPUT_PATH}:ro",
        "--label",
        validator_label,
        image_name,
    ]

    try:
        result = _run_docker_command(
            cmd,
            description=f"docker run for container {container_name}",
        )
        container_id = result.stdout.strip()

        bt.logging.info(
            f"🚀 Container '{container_name}' started in background (ID: {container_id}) "
            f"with label '{validator_label}'"
        )
        return container_id

    except InvalidSolutionError:
        # Already has rich diagnostics from the helper
        raise

    except Exception as e:
        bt.logging.error(f"❌ An unexpected error occurred while starting the container: {e}")
        raise InvalidSolutionError(
            message=f"Unexpected error starting Docker container: {e}. "
                    "Check validator logs for the full docker command and any additional context."
        ) from e
