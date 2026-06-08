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

from typing import List
from datetime import datetime, timedelta, timezone
import json
import os
import shutil
import subprocess
from qbittensor.utils.timer import Timer
from qbittensor.database.db_connection import DBConnection
from qbittensor.validator.solution.run_solution import extract_stdout_output
from qbittensor.validator.solution.validate_solution_output import validate_solution
from qbittensor.utils.services.challenges import ChallengesClient
from qbittensor.constants import (
    SOLUTION_CONTAINER_MANAGER_TIMEOUT,
    MAX_SOLUTIONS,
    DOCKER_RESOURCE_PRUNE_INTERVAL,
    DOCKER_BUILDER_PRUNE_UNTIL,
    DOCKER_IMAGE_PRUNE_UNTIL,
)
from qbittensor.utils.solution_status import SolutionStatus
import bittensor as bt

from .solution_context import SolutionExecution, SolutionPostProcessInfo


def is_docker_available() -> bool:
    """Check whether the Docker CLI is available and responsive.

    This is called at startup to give early, clear feedback instead of
    failing on the first solution that needs to be built/run.
    """
    try:
        result = subprocess.run(
            ["docker", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            version = result.stdout.strip()
            bt.logging.info(f"🐳 Docker CLI detected: {version}")
            return True
        else:
            docker_in_path = shutil.which("docker")
            bt.logging.error(
                "❌ Docker CLI check failed.\n"
                f"   Command: docker --version\n"
                f"   Exit code: {result.returncode}\n"
                f"   stderr: {result.stderr.strip() or '(empty)'}\n\n"
                f"   shutil.which('docker') returned: {docker_in_path or 'None'}\n"
                "   Current PATH seen by the process (pm2 and similar tools often use a minimal one):\n"
                f"   {os.environ.get('PATH', '(not set)')}\n\n"
                "   The validator will not be able to build or run solution containers."
            )
            return False
    except FileNotFoundError:
        docker_in_path = shutil.which("docker")
        bt.logging.error(
            "❌ Docker CLI not found in PATH.\n"
            "   The 'docker' command could not be located by the validator process.\n"
            "   This commonly happens when running under process managers like pm2,\n"
            "   because they start with a minimal environment and do not load your\n"
            "   shell profile (.bashrc, .profile, etc.).\n\n"
            f"   shutil.which('docker') returned: {docker_in_path or 'None'}\n"
            "   Current PATH seen by the process:\n"
            f"   {os.environ.get('PATH', '(not set)')}\n\n"
            "   Please ensure the directory containing the 'docker' binary is present\n"
            "   in the PATH used by pm2 (use an ecosystem file with an explicit `env` or `PATH`)."
        )
        return False
    except subprocess.TimeoutExpired:
        bt.logging.error("❌ Docker CLI check timed out. Docker may be unresponsive.")
        return False
    except Exception as e:
        bt.logging.error(f"❌ Unexpected error while checking Docker availability: {e}")
        return False


class SolutionContainerManager:

    def __init__(self, platform_client: ChallengesClient, database_connection: DBConnection, validator_label: str):
        self.platform_client = platform_client
        self.timer: Timer = Timer(timeout=SOLUTION_CONTAINER_MANAGER_TIMEOUT, run=self.run, run_on_start=True)
        self.docker_prune_timer: Timer = Timer(
            timeout=DOCKER_RESOURCE_PRUNE_INTERVAL,
            run=self._prune_docker_resources,
            run_on_start=False,  # let the first real tick happen after an interval
        )
        self.database_connection = database_connection
        self.LABEL = validator_label

        # Early Docker availability check (non-fatal, but very loud)
        is_docker_available()

        # Recover from previous run: process completed (for proper validation/reporting),
        # leave any still-running solutions untouched (so they continue and are monitored
        # to completion), and only remnant-clean truly lost/orphaned ones.
        self.recover_and_clean_on_startup()

    def run(self) -> None:
        bt.logging.info("🐳 Starting periodic solution container check (pruning, completed, overdue)")

        self.handle_completed_solutions()

        number_of_running_solutions: int = self._get_number_of_running_solutions()
        bt.logging.info(f"🏗️ Found {number_of_running_solutions} containers still running")

        overdue_containers: List[str] = self._get_overdue_containers()
        if len(overdue_containers) > 0:
            bt.logging.info(f"⏰ Found {len(overdue_containers)} overdue containers to terminate")
            self._terminate_overdue_containers(overdue_containers)
        else:
            bt.logging.debug("No overdue containers found this check")

        self.database_connection.db_query.prune_old_solutions()

        self._prune_containers()

        # Periodic conservative Docker disk cleanup (build cache + old unused images).
        # Throttled by its own timer (see DOCKER_RESOURCE_PRUNE_INTERVAL).
        self.docker_prune_timer.check_timer()

    def handle_completed_solutions(self) -> None:
        """Find completed solution containers, validate their output, and report results to the platform"""
        completed_containers = self._find_completed_solutions()
        bt.logging.info(f"✅ Found {len(completed_containers)} completed containers")
        if len(completed_containers) == 0:
            return

        # Log per-container exit details for visibility
        for name in completed_containers:
            details = self._get_container_exit_details(name)
            exit_msg = f"exit_code={details['exit_code']}"
            if details.get("error"):
                exit_msg += f", error='{details['error']}'"
            if details.get("finished_at"):
                exit_msg += f", finished_at={details['finished_at']}"
            bt.logging.info(f"📦 Container '{name}' has exited ({exit_msg})")

        infos = self._collect_completed_solution_infos(completed_containers)
        bt.logging.info(f"📂 Found {len(infos)} completed solutions for output validation + cleanup")

        self._extract_outputs_from_completed_containers(infos)

        self._validate_and_report_solutions(infos)

    def _extract_outputs_from_completed_containers(self, solutions: List[SolutionPostProcessInfo]) -> None:
        """
        Pull each completed container's stdout via ``docker logs`` and split it into
        the run's log file and the solution-output zip on the host workspace.
        """
        for info in solutions:
            name = info.container_name
            if not self._container_has_validator_label(name):
                bt.logging.warning(
                    f"⚠️ Skipping output extraction for container {name}; "
                    f"validator label {self.LABEL} not found"
                )
                continue
            # We already have the path from the collected info; no extra lookup needed.
            extract_stdout_output(name, info.workspace_path)

    def _validate_and_report_solutions(self, solutions: List[SolutionPostProcessInfo]) -> None:
        """Validate the outputs of completed solutions and report results to the platform.

        Uses stable identifiers from the collected info objects (delegated via
        the embedded SolutionExecution for the identity fields).
        """
        for info in solutions:
            bt.logging.info(f"🔍 Validating solution output at {info.workspace_path}")
            solution_status = validate_solution(
                info.workspace_path,
                self.platform_client,
                submission_id=info.submission_id,
                challenge_milestone_id=info.challenge_milestone_id,
                challenge_id=info.challenge_id,
            )
            self.database_connection.db_query.update_solution_status_by_id(
                info.id, solution_status
            )

        self._clean_up_solutions(solutions)

    def _clean_up_solutions(self, solutions: List[SolutionPostProcessInfo]) -> None:
        cleaned = 0
        for info in solutions:
            bt.logging.info(f"🧹 Cleaning up solution at {info.workspace_path}")

            # Use carried values from stable-key row lookup; never look up by path
            container_name = info.container_name
            image_id = info.image_id
            location = info.workspace_path

            if not os.path.exists(location):
                bt.logging.info(f"    FS path does not exist for {info.id}, marking as cleaned")
                self.database_connection.db_query.mark_solution_cleaned(info.id)
                # still attempt container/image cleanup below if names present

            try:
                if container_name:
                    if not self._container_has_validator_label(container_name):
                        bt.logging.warning(
                            f"⚠️ Refusing to clean up container {container_name}; validator label {self.LABEL} not found"
                        )
                        continue
                    bt.logging.info(f"🛑 Stopping container {container_name}")
                    stop_res = subprocess.run(["docker", "stop", container_name], check=False, capture_output=True, text=True)
                    if stop_res.returncode == 0:
                        bt.logging.info(f"🛑 Stopped container {container_name}")
                    else:
                        bt.logging.warning(f"⚠️ Stop may have failed for {container_name}")

                    bt.logging.info(f"🗑️ Removing container {container_name}")
                    rm_res = subprocess.run(["docker", "rm", "-v", container_name], check=False, capture_output=True, text=True)
                    if rm_res.returncode == 0:
                        bt.logging.info(f"🗑️ Removed container {container_name}")
                    else:
                        bt.logging.warning(f"⚠️ Failed to remove container {container_name}: {(rm_res.stderr or rm_res.stdout).strip()}")

                if image_id:
                    if not self._image_ref_owned_by_validator(image_id):
                        bt.logging.warning(
                            f"⚠️ Refusing to remove image {image_id}; name does not match validator label prefix"
                        )
                    else:
                        bt.logging.info(f"🗑️ Removing image {image_id}")
                        rmi_res = subprocess.run(["docker", "rmi", "-f", image_id], check=False, capture_output=True, text=True)
                        if rmi_res.returncode == 0:
                            bt.logging.info(f"🗑️ Removed image {image_id}")
                        else:
                            bt.logging.warning(f"⚠️ Failed to remove image {image_id}: {(rmi_res.stderr or rmi_res.stdout).strip()}")

                bt.logging.info(f"🗑️ Removing solution folder {location}")
                rmf_res = subprocess.run(["rm", "-rf", location], check=False, capture_output=True, text=True)
                if rmf_res.returncode == 0:
                    bt.logging.info(f"🗑️ Removed solution folder {location}")
                    cleaned += 1
                    self.database_connection.db_query.mark_solution_cleaned(info.id)
                else:
                    bt.logging.warning(f"⚠️ Failed to remove folder {location}")

            except Exception as e:
                bt.logging.error(f"❌ Failed to clean up solution at {location}: {e}")

        if solutions:
            bt.logging.info(f"🧹 Cleaned up {cleaned}/{len(solutions)} solution locations")

    def _find_completed_solutions(self) -> List[str]:
        """Find containers that have completed their run and are ready for output validation"""

        try:
            result = subprocess.run(
                ["docker", "ps", "-a", "--filter", f"label={self.LABEL}", "--filter", "status=exited", "--format", "{{.Names}}"],
                capture_output=True,
                text=True,
                check=True,
            )
            names = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            return names
        except Exception as e:
            bt.logging.error(f"❌ Failed to list completed containers: {e}")
            return []

    def _get_container_exit_details(self, container_name: str) -> dict:
        """Inspect a container to get exit code and error reason (best effort)."""
        try:
            result = subprocess.run(
                ["docker", "inspect", "--format", "{{.State.ExitCode}}|{{.State.Error}}|{{.State.FinishedAt}}", container_name],
                capture_output=True,
                text=True,
                check=True,
            )
            parts = result.stdout.strip().split("|", 2)
            exit_code = int(parts[0]) if parts[0].isdigit() else -1
            error = parts[1] if len(parts) > 1 else ""
            finished_at = parts[2] if len(parts) > 2 else ""
            return {"exit_code": exit_code, "error": error, "finished_at": finished_at}
        except Exception:
            return {"exit_code": -1, "error": "", "finished_at": ""}

    def _get_container_state(self, container_identifier: str) -> str | None:
        """Return the docker State.Status for a container (e.g. 'running', 'exited', 'created')
        or None if the container does not exist or inspect fails.
        Used by recovery to decide whether a solution's container is still alive.
        """
        if not container_identifier:
            return None
        try:
            result = subprocess.run(
                ["docker", "inspect", "--format", "{{.State.Status}}", container_identifier],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                status = result.stdout.strip()
                return status or None
            return None
        except Exception:
            return None

    def _get_stable_keys_from_container(self, container_name: str) -> dict[str, str | None]:
        """Inspect the container's labels (attached at `docker run` time) to extract
        stable identifiers (primarily solution_id, plus others for diagnostics).
        This allows DB correlation via get_challenge_solution_by_id.
        """
        try:
            result = subprocess.run(
                ["docker", "inspect", container_name, "--format", "{{json .Config.Labels}}"],
                capture_output=True,
                text=True,
                check=True,
            )
            labels = json.loads(result.stdout.strip() or "{}") or {}
            return {
                "submission_id": labels.get("submission_id"),
                "tx_hash": labels.get("tx_hash"),
                "challenge_validation_solution_id": labels.get("challenge_validation_solution_id"),
                "solution_id": labels.get("solution_id"),
            }
        except Exception as e:
            bt.logging.debug(f"Could not extract stable labels from container {container_name}: {e}")
            return {"submission_id": None, "tx_hash": None, "challenge_validation_solution_id": None, "solution_id": None}

    def _collect_completed_solution_infos(self, container_names: List[str]) -> List[SolutionPostProcessInfo]:
        """Collect post-process info for completed solutions.

        We extract the solution_id (and other stable identifiers) from Docker labels
        attached at `docker run --label` time. When present, we look up the DB row
        using get_challenge_solution_by_id (preferred). We build a SolutionExecution
        (for identity) + SolutionPostProcessInfo (embedding it + runtime details).
        The info carries the internal `id` (via .id / .execution.solution_id) for
        all subsequent by-id operations.
        """
        infos: List[SolutionPostProcessInfo] = []
        for name in container_names:
            stable = self._get_stable_keys_from_container(name)
            solution = None
            if stable.get("solution_id"):
                solution = self.database_connection.db_query.get_challenge_solution_by_id(stable["solution_id"])
            else:
                # No solution_id label — treat as no DB row (orphan cleanup will handle docker side).
                # All containers are expected to carry the solution_id label.
                solution = None

            if solution:
                bt.logging.info(f"📂 Found solution for container {name}: {solution.absolute_path_to_solution}")
                # Reconstruct a SolutionExecution for the identity fields we have from the DB row.
                # download_url is not stored in the row (it is transient to the initial execution);
                # we use a placeholder since it is not needed for post-processing.
                exec_identity = SolutionExecution.create(
                    tx_hash=solution.tx_hash,
                    submission_id=solution.submission_id,
                    challenge_validation_solution_id=solution.challenge_validation_solution_id or "",
                    challenge_id=solution.challenge_id or "",
                    challenge_milestone_id=solution.challenge_milestone_id,
                    miner_hotkey=solution.miner_hotkey,
                    download_url="",
                    solution_id=solution.id,
                )
                infos.append(
                    SolutionPostProcessInfo(
                        execution=exec_identity,
                        container_name=name,
                        workspace_path=solution.absolute_path_to_solution,
                        image_id=solution.image_id,
                    )
                )
            else:
                bt.logging.warning(f"⚠️ No solution row found for container {name}")
                self._clean_up_orphaned_solutions(name)
        return infos

    def validator_is_busy(self) -> bool:
        """Check if the validator is running the max number of solutions"""
        running_solutions: int = self._get_number_of_running_solutions()
        return running_solutions >= MAX_SOLUTIONS

    def _get_number_of_running_solutions(self) -> int:
        """Get the number of currently running containers that match our solution label"""
        containers = self._get_running_containers()
        return len(containers)

    def _get_running_containers(self) -> List[str]:
        """Use the Docker CLI to find all running container ids that match our solution label"""
        try:
            result = subprocess.run(
                ["docker", "ps", "--filter", f"label={self.LABEL}", "--format", "{{.ID}}"],
                capture_output=True,
                text=True,
                check=True,
            )
            ids = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            return ids
        except Exception as e:
            bt.logging.error(f"❌ Failed to list running containers: {e}")
            return []

    def _container_has_validator_label(self, container_identifier: str) -> bool:
        """Verify a container is owned by this validator via Docker labels."""
        try:
            result = subprocess.run(
                ["docker", "inspect", "--format", "{{json .Config.Labels}}", container_identifier],
                capture_output=True,
                text=True,
                check=True,
            )
            labels_raw = result.stdout.strip()
            labels = json.loads(labels_raw) if labels_raw else {}
            return isinstance(labels, dict) and self.LABEL in labels
        except Exception as e:
            bt.logging.error(
                f"❌ Failed to inspect labels for container {container_identifier}: {e}"
            )
            return False

    def _inspect_container_config_image(self, container_identifier: str) -> str | None:
        """Return the image reference the container was created from (.Config.Image)."""
        try:
            result = subprocess.run(
                ["docker", "inspect", "--format", "{{.Config.Image}}", container_identifier],
                capture_output=True,
                text=True,
                check=True,
            )
            ref = result.stdout.strip()
            return ref or None
        except Exception as e:
            bt.logging.error(
                f"❌ Failed to read Config.Image for container {container_identifier}: {e}"
            )
            return None

    def _image_ref_owned_by_validator(self, image_ref: str) -> bool:
        """
        True if the image reference looks like a solution image for this validator
        (folder/image tag starts with ``{LABEL}_``, matching manage_files.setup / run.py).
        """
        if not image_ref or not isinstance(image_ref, str):
            return False
        name = image_ref.strip().split("@", 1)[0]
        if ":" in name:
            name = name.rsplit(":", 1)[0]
        name = name.lower()
        prefix = f"{self.LABEL.lower()}_"
        return name.startswith(prefix)

    def _get_max_runtime_for_container(self, container_identifier: str) -> timedelta:
        """
        Return the max allowed runtime for the milestone of this container.

        The value is read exclusively from the max_solution_runtime_seconds stored
        on the ChallengeSolution DB row (recorded at start time from the milestone
        configuration). Containers are only started after the early fetch + fail-fast
        check, so a valid positive value is expected.

        The solution row is located via the stable solution_id label on the container.

        If no valid stored runtime can be obtained for a container we own (via our
        validator label), we return 0s. This causes the container to be treated as
        immediately overdue and terminated. No API re-query is performed, and there
        are no hardcoded fallbacks.
        """
        try:
            container_name = container_identifier

            # If we were given a container ID (from docker ps), resolve the human-readable name
            if not container_identifier.startswith(self.LABEL.lower()):
                try:
                    name_res = subprocess.run(
                        ["docker", "inspect", "--format", "{{.Name}}", container_identifier],
                        capture_output=True,
                        text=True,
                        check=True,
                    )
                    raw_name = name_res.stdout.strip().lstrip("/")
                    if raw_name:
                        container_name = raw_name
                except Exception:
                    pass  # fall back to using the identifier as-is

            # Use solution_id label (attached at docker run) for stable lookup.
            # We require the DB row to have the runtime limit recorded.
            stable = self._get_stable_keys_from_container(container_name)
            solution = None
            if stable.get("solution_id"):
                solution = self.database_connection.db_query.get_challenge_solution_by_id(stable["solution_id"])

            if solution:
                milestone_id = solution.challenge_milestone_id

                stored = solution.max_solution_runtime_seconds
                if stored is not None:
                    try:
                        secs = int(stored)
                        if secs > 0:
                            runtime = timedelta(seconds=secs)
                            bt.logging.debug(
                                f"Using stored max_solution_runtime={runtime} for milestone {milestone_id}"
                            )
                            return runtime
                    except (TypeError, ValueError):
                        pass

                # Row exists but has no valid runtime recorded. This violates the
                # "started only after early fetch" rule. Kill it (no limit = terminate).
                bt.logging.error(
                    f"❌ Container {container_identifier} (solution_id={stable.get('solution_id')}) "
                    f"has no valid max_solution_runtime_seconds on its DB row "
                    f"(milestone={milestone_id}). Terminating as unenforced."
                )
                return timedelta(0)

            # No solution row (missing solution_id label, or label present but row gone).
            # We only manage containers we started with labels + DB rows. Kill it.
            bt.logging.error(
                f"❌ Could not resolve DB solution row (and thus runtime limit) for container "
                f"{container_identifier} via labels. Terminating as unenforced (no solution_id label or no row)."
            )
            return timedelta(0)

        except Exception as e:
            bt.logging.error(
                f"❌ Error resolving max runtime for container {container_identifier}: {e}. "
                "Terminating as unenforced."
            )
            return timedelta(0)

    def _get_overdue_containers(self) -> List[str]:
        """
        Get all of the containers that match our solution label that have been running for too long and need to be terminated
        """
        overdue: List[str] = []
        running = self._get_running_containers()
        now = datetime.now(timezone.utc)

        def _parse_started_at(started_at: str) -> datetime:
            # Docker returns e.g. 2024-02-21T12:34:56.123456789Z
            s = started_at
            if s.endswith('Z'):
                s = s[:-1] + '+00:00'
            if '.' in s:
                # Truncate fractional seconds to microseconds (6 digits)
                date_part, rest = s.split('.', 1)
                tz_index = rest.find('+') if '+' in rest else rest.find('-')
                if tz_index != -1:
                    frac = rest[:tz_index]
                    tz = rest[tz_index:]
                else:
                    frac = rest
                    tz = ''
                frac = (frac + '000000')[:6]
                s = f"{date_part}.{frac}{tz}"
            return datetime.fromisoformat(s)

        for cid in running:
            if not self._container_has_validator_label(cid):
                bt.logging.warning(
                    f"⚠️ Skipping container {cid} because it is missing validator label {self.LABEL}"
                )
                continue
            try:
                res = subprocess.run(
                    ["docker", "inspect", "--format", "{{.State.StartedAt}}", cid],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                started_at_raw = res.stdout.strip()
                if not started_at_raw:
                    continue
                started_at = _parse_started_at(started_at_raw)
                runtime = now - started_at
                max_runtime = self._get_max_runtime_for_container(cid)
                if runtime > max_runtime:
                    overdue.append(cid)
            except Exception as e:
                bt.logging.error(f"❌ Failed to inspect container {cid}: {e}")

        return overdue

    def _terminate_overdue_containers(self, overdue_containers: List[str]) -> None:
        """Terminate all overdue containers"""
        bt.logging.info(
            f"🛑 Starting termination of {len(overdue_containers)} overdue solutions "
            f"(runtime exceeded the milestone's configured max_solution_runtime)"
        )
        terminated = 0
        images_removed = 0
        for cid in overdue_containers:
            if not self._container_has_validator_label(cid):
                bt.logging.warning(
                    f"⚠️ Refusing to terminate container {cid}; validator label {self.LABEL} not found"
                )
                continue
            config_image = self._inspect_container_config_image(cid)
            # Use stable solution_id from labels (attached at docker run time) to look up the DB row
            # and its image_id. No fallback needed; all containers created after labeling change
            # will have the solution_id label.
            stable = self._get_stable_keys_from_container(cid)
            db_image = None
            if stable.get("solution_id"):
                sol = self.database_connection.db_query.get_challenge_solution_by_id(stable["solution_id"])
                db_image = sol.image_id if sol else None
            image_to_remove: str | None = None
            if config_image and self._image_ref_owned_by_validator(config_image):
                image_to_remove = config_image
            elif db_image and self._image_ref_owned_by_validator(db_image):
                image_to_remove = db_image
            if not image_to_remove:
                bt.logging.warning(
                    f"⚠️ Skipping image removal for overdue container {cid}; "
                    f"no image reference matched validator-owned naming (config={config_image!r}, db={db_image!r})"
                )
            try:
                bt.logging.info(f"🛑 Stopping overdue container {cid}")
                stop_result = subprocess.run(["docker", "stop", cid], check=False, capture_output=True, text=True)
                if stop_result.returncode == 0:
                    bt.logging.info(f"🛑 Stopped overdue container {cid}")
                else:
                    bt.logging.warning(f"⚠️ Stop may have failed for {cid}: {(stop_result.stderr or stop_result.stdout).strip()}")
            except Exception as e:
                bt.logging.error(f"❌ Error stopping container {cid}: {e}")
            try:
                bt.logging.info(f"🗑️ Removing overdue container {cid}")
                rm_result = subprocess.run(["docker", "rm", "-v", cid], check=False, capture_output=True, text=True)
                if rm_result.returncode == 0:
                    bt.logging.info(f"🗑️ Removed overdue container {cid}")
                    terminated += 1
                else:
                    bt.logging.warning(f"⚠️ Failed to remove container {cid}: {(rm_result.stderr or rm_result.stdout).strip()}")
            except Exception as e:
                bt.logging.error(f"❌ Error removing container {cid}: {e}")
            if image_to_remove:
                try:
                    bt.logging.info(f"🗑️ Removing overdue solution image {image_to_remove}")
                    rmi_result = subprocess.run(["docker", "rmi", "-f", image_to_remove], check=False, capture_output=True, text=True)
                    if rmi_result.returncode == 0:
                        bt.logging.info(f"🗑️ Removed overdue image {image_to_remove}")
                        images_removed += 1
                    else:
                        bt.logging.info(f"❌ Failed to remove image {image_to_remove}: {(rmi_result.stderr or rmi_result.stdout).strip()}")
                except Exception as e:
                    bt.logging.error(f"❌ Error removing image {image_to_remove}: {e}")

        bt.logging.info(f"🛑 Overdue termination complete: {terminated} containers terminated, {images_removed} images removed")

    def _prune_containers(self) -> None:
        """Remove exited containers with our label and their images when image names match this validator."""
        bt.logging.info("🗑️ Starting prune of exited solution containers and validator-owned images")
        try:
            cmd = [
                "docker",
                "ps",
                "-a",
                "--filter",
                f"label={self.LABEL}",
                "--filter",
                "status=exited",
                "--format",
                "{{.ID}}",
            ]
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
            )
            exited = [line.strip() for line in res.stdout.splitlines() if line.strip()]
            bt.logging.info(f"🗑️ Found {len(exited)} exited containers to consider for pruning")

            pruned_containers = 0
            pruned_images = 0
            for cid in exited:
                if not self._container_has_validator_label(cid):
                    bt.logging.warning(
                        f"⚠️ Skipping prune for container {cid}; validator label {self.LABEL} not found"
                    )
                    continue
                config_image = self._inspect_container_config_image(cid)
                try:
                    rm_result = subprocess.run(["docker", "rm", "-v", cid], check=False, capture_output=True, text=True)
                    if rm_result.returncode == 0:
                        bt.logging.info(f"🗑️ Pruned container {cid}")
                        pruned_containers += 1
                    else:
                        bt.logging.warning(f"⚠️ Failed to prune container {cid}: {(rm_result.stderr or rm_result.stdout).strip()}")
                except Exception as e:
                    bt.logging.error(f"❌ Failed to remove container {cid}: {e}")

                if not config_image or not self._image_ref_owned_by_validator(config_image):
                    bt.logging.warning(
                        f"⚠️ Skipping image removal for pruned container {cid}; "
                        f"Config.Image not validator-owned (config_image={config_image!r})"
                    )
                    continue
                try:
                    rmi_result = subprocess.run(["docker", "rmi", "-f", config_image], check=False, capture_output=True, text=True)
                    if rmi_result.returncode == 0:
                        bt.logging.info(f"🗑️ Pruned image {config_image}")
                        pruned_images += 1
                    else:
                        bt.logging.info(f"❌ Failed to prune image {config_image}: {(rmi_result.stderr or rmi_result.stdout).strip()}")
                except Exception as e:
                    bt.logging.info(f"❌ Failed to remove image {config_image}: {e}")

            bt.logging.info(f"🗑️ Prune complete: {pruned_containers} containers, {pruned_images} images removed")
        except Exception as e:
            bt.logging.error(f"❌ Failed to prune containers/images: {e}")

    def _prune_docker_resources(self) -> None:
        """Conservative periodic Docker disk cleanup (build cache + unused images).

        This implements the recommended "Option A" hygiene:
            docker builder prune -f --filter "until=48h"
            docker image prune -a -f --filter "until=72h"

        The time-based filters keep recent build cache (useful while actively
        building/running challenges) and any images created or used in the last
        ~3 days, while reclaiming space from old solution images, test images,
        stale base image layers (e.g. old nvidia/cuda), and accumulated build
        cache.

        This is safe to run while a challenge solution container is active:
        - Any image referenced by a running (or recently created) container is
          considered "used" and will not be removed by `image prune -a`.
        - The currently-executing challenge's image stays resident.
        - Only truly unreferenced + aged-out images and cache entries are deleted.

        Runs on its own throttled timer (DOCKER_RESOURCE_PRUNE_INTERVAL) so we
        don't spam prune commands on every 5-minute container check.
        """
        bt.logging.info("🧹 Starting periodic Docker resource prune (build cache + unused images)")

        prune_jobs = [
            (
                ["docker", "builder", "prune", "-f", "--filter", f"until={DOCKER_BUILDER_PRUNE_UNTIL}"],
                "build cache",
            ),
            (
                ["docker", "image", "prune", "-a", "-f", "--filter", f"until={DOCKER_IMAGE_PRUNE_UNTIL}"],
                "unused images",
            ),
        ]

        for cmd, label in prune_jobs:
            try:
                bt.logging.debug(f"   → {' '.join(cmd)}")
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=300,  # generous; large dirty caches can take a bit
                )
                if result.returncode == 0:
                    out = (result.stdout or "").strip()
                    if out:
                        # Docker prints a summary; show the most relevant tail
                        lines = [ln for ln in out.splitlines() if ln.strip()]
                        tail = "\n".join(lines[-8:]) if len(lines) > 8 else out
                        bt.logging.info(f"   ✅ Pruned {label}:\n{tail}")
                    else:
                        bt.logging.info(f"   ✅ Pruned {label}: nothing eligible within filter window")
                else:
                    err = (result.stderr or result.stdout or "(no output)").strip()[:400]
                    bt.logging.warning(f"   ⚠️ {label} prune returned {result.returncode}: {err}")
            except subprocess.TimeoutExpired:
                bt.logging.warning(f"   ⚠️ Timed out while pruning {label}")
            except Exception as e:
                bt.logging.warning(f"   ⚠️ Error pruning {label}: {e}")

        bt.logging.info("🧹 Docker resource prune complete")

    def _clean_up_orphaned_solutions(self, container_name: str) -> None:
        """If we find a container that has no associated solution location, we should remove it to free up resources"""
        bt.logging.info(f"🧹 Cleaning up orphaned container {container_name} (no DB location)")
        try:
            if not self._container_has_validator_label(container_name):
                bt.logging.warning(
                    f"⚠️ Refusing to clean up orphaned container {container_name}; "
                    f"validator label {self.LABEL} not found"
                )
                return
            bt.logging.info(f"🛑 Stopping orphaned container {container_name}")
            stop_res = subprocess.run(["docker", "stop", container_name], check=False, capture_output=True, text=True)
            if stop_res.returncode == 0:
                bt.logging.info(f"🛑 Stopped orphaned container {container_name}")
            else:
                bt.logging.warning(f"⚠️ Stop may have failed for orphaned {container_name}")

            bt.logging.info(f"🗑️ Removing orphaned container {container_name}")
            rm_res = subprocess.run(["docker", "rm", "-v", container_name], check=False, capture_output=True, text=True)
            if rm_res.returncode == 0:
                bt.logging.info(f"🗑️ Removed orphaned container {container_name}")
            else:
                bt.logging.warning(f"⚠️ Failed to remove orphaned container {container_name}")

            # Clean up the image directly via inspect (no DB needed for orphans).
            # If the container had a DB row we would have the image_id in the info,
            # but for true orphans we still want to remove our images if present.
            image_ref = self._inspect_container_config_image(container_name)
            if image_ref and self._image_ref_owned_by_validator(image_ref):
                bt.logging.info(f"🗑️ Removing orphan image {image_ref}")
                rmi_res = subprocess.run(["docker", "rmi", "-f", image_ref], check=False, capture_output=True, text=True)
                if rmi_res.returncode == 0:
                    bt.logging.info(f"🗑️ Removed orphan image {image_ref}")
                else:
                    bt.logging.warning(f"⚠️ Failed to remove orphan image {image_ref}")
            elif image_ref:
                bt.logging.warning(
                    f"⚠️ Refusing to remove orphan image {image_ref}; name does not match validator label prefix"
                )

            # Remove any stale DB row preferring by solution id from labels.
            # (We no longer fall back to container_name lookup; for pre-labeling orphan
            # containers we simply leave any stale DB row -- it will be aged out by prune
            # or is harmless. This keeps all DB mutations by stable id only.)
            stable = self._get_stable_keys_from_container(container_name)
            if stable.get("solution_id"):
                self.database_connection.db_query.remove_solution_by_id(stable["solution_id"])
            else:
                bt.logging.debug(
                    f"No solution_id label on orphan container {container_name}; "
                    "skipping DB row removal (no fragile container_name lookup)"
                )

        except Exception as e:
            bt.logging.error(f"❌ Failed to clean up orphaned solution associated to container: {container_name}: {e}")

    def recover_and_clean_on_startup(self) -> None:
        """On validator startup, recover from prior runs (including abrupt shutdowns).

        Policy (per design: just recover and monitor; never forcefully exit a running solution):
        - First, delegate to handle_completed_solutions() so any containers that exited around
          shutdown get the full normal path: extract stdout/artifacts, validate output,
          report status to platform, and clean + mark_cleaned. This prevents force-failing
          solutions that actually completed.
        - Re-query uncleaned rows.
        - For each still-uncleaned solution:
          - If it has a container that is *currently running* and carries our validator label:
            - Log that we recovered/adopted a live solution.
            - Leave the container completely untouched (no stop, no rm).
            - Leave the DB row uncleaned (status unchanged).
            - Future periodic runs will see it via _get_overdue_containers (for max-runtime)
              and handle_completed_solutions (once it exits).
          - Otherwise (no container, container exited/gone, or not owned):
            - Clean any remnants (container if present, image if owned, FS path if present).
            - mark_solution_cleaned(id)
            - If the prior status was RUNNING or PENDING, set FAILED (lost in-flight work).
        - Always guard docker mutations with label checks.
        - Rows with missing paths/containers are marked cleaned so they are not re-processed
          on future restarts.
        """
        bt.logging.info("🔄 Recovering uncleaned solutions from previous validator run (if any)")
        try:
            uncleaned = self.database_connection.db_query.get_uncleaned_solutions()
            if not uncleaned:
                bt.logging.info("✅ No uncleaned solutions to recover")
                return

            # Proactively process any *exited* containers first. This lets solutions that
            # completed just before shutdown go through the proper extraction/validation/
            # reporting path instead of being force-cleaned+failed here.
            self.handle_completed_solutions()

            # Re-fetch: exited ones that had rows should now be marked cleaned by the normal path.
            # Remaining will be running containers we must not touch, plus lost/orphaned ones.
            still_uncleaned = self.database_connection.db_query.get_uncleaned_solutions()
            if not still_uncleaned:
                bt.logging.info("✅ No uncleaned solutions left after processing completed ones")
                return

            recovered = 0
            for sol in still_uncleaned:
                bt.logging.info(f"  Recovering solution id={sol.id} name={sol.container_name} path={sol.absolute_path_to_solution} status={sol.solution_status}")
                cleaned_something = False

                container_name = sol.container_name
                state = self._get_container_state(container_name) if container_name else None

                if container_name and state == "running" and self._container_has_validator_label(container_name):
                    bt.logging.info(
                        f"    🔄 Recovered still-running solution id={sol.id} container={container_name}; "
                        "leaving container running and row uncleaned so normal monitoring "
                        "(handle_completed_solutions + _get_overdue_containers) will observe completion. "
                        "Not stopping, not marking cleaned, not forcing FAILED."
                    )
                    continue  # <--- key: do not kill it, do not mark, do not fail

                # No owned+running container for this uncleaned row -> safe to clean remnants.
                # (covers: container never existed, exited+already-handled, exited+rm'd externally, lost, etc.)

                # Try to remove container if it still exists (but we already know it's not a live running one we own)
                if container_name and self._container_has_validator_label(container_name):
                    try:
                        # stop is harmless for exited/dead containers
                        stop_res = subprocess.run(["docker", "stop", container_name], check=False, capture_output=True, text=True)
                        if stop_res.returncode == 0:
                            bt.logging.info(f"    Stopped container {container_name}")
                        rm_res = subprocess.run(["docker", "rm", "-v", container_name], check=False, capture_output=True, text=True)
                        if rm_res.returncode == 0:
                            bt.logging.info(f"    Removed container {container_name}")
                            cleaned_something = True
                    except Exception as e:
                        bt.logging.warning(f"    Error cleaning container {container_name}: {e}")

                # Remove image if we have it and it's ours (best effort)
                if sol.image_id and self._image_ref_owned_by_validator(sol.image_id):
                    try:
                        rmi_res = subprocess.run(["docker", "rmi", "-f", sol.image_id], check=False, capture_output=True, text=True)
                        if rmi_res.returncode == 0:
                            bt.logging.info(f"    Removed image {sol.image_id}")
                            cleaned_something = True
                    except Exception as e:
                        bt.logging.warning(f"    Error removing image {sol.image_id}: {e}")

                # Remove FS path if it exists (or note it is already gone)
                if sol.absolute_path_to_solution:
                    if os.path.exists(sol.absolute_path_to_solution):
                        try:
                            shutil.rmtree(sol.absolute_path_to_solution, ignore_errors=True)
                            bt.logging.info(f"    Removed FS path {sol.absolute_path_to_solution}")
                            cleaned_something = True
                        except Exception as e:
                            bt.logging.warning(f"    Error removing path {sol.absolute_path_to_solution}: {e}")
                    else:
                        bt.logging.info(f"    FS path already gone for {sol.id}, marking cleaned")
                        cleaned_something = True

                # Mark cleaned for the orphaned/lost case; also fail in-flight statuses
                if cleaned_something:
                    self.database_connection.db_query.mark_solution_cleaned(sol.id)
                    if sol.solution_status in (SolutionStatus.RUNNING.value, SolutionStatus.PENDING.value):
                        self.database_connection.db_query.update_solution_status_by_id(
                            sol.id, SolutionStatus.FAILED.value
                        )
                    recovered += 1
                else:
                    bt.logging.warning(f"    Could not clean anything for {sol.id}, leaving uncleaned for now")

            bt.logging.info(f"✅ Startup recovery complete: processed {len(uncleaned)} uncleaned, marked {recovered} cleaned")
        except Exception as e:
            bt.logging.error(f"❌ Error during startup recovery of solutions: {e}")
