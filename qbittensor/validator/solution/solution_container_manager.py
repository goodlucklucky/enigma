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
from datetime import datetime, timezone
import json
import subprocess
from qbittensor.utils.timer import Timer
from qbittensor.database.db_connection import DBConnection
from qbittensor.validator.solution.run_solution import extract_stdout_output
from qbittensor.validator.solution.validate_solution_output import validate_solution
from qbittensor.utils.services.challenges import ChallengesClient
from qbittensor.constants import SOLUTION_CONTAINER_MANAGER_TIMEOUT, MAX_SOLUTIONS
# Fallback only - real value should come from milestone configuration via the API
from datetime import timedelta
DEFAULT_MAX_SOLUTION_RUNTIME = timedelta(minutes=30)
import bittensor as bt


class SolutionContainerManager:

    def __init__(self, platform_client: ChallengesClient, database_connection: DBConnection, validator_label: str):
        self.platform_client = platform_client
        self.timer: Timer = Timer(timeout=SOLUTION_CONTAINER_MANAGER_TIMEOUT, run=self.run, run_on_start=True)
        self.database_connection = database_connection
        self.LABEL = validator_label
        self._runtime_cache: dict[str, timedelta] = {}  # key: "challenge_id:milestone_id" -> timedelta

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

        # Pull each completed container's stdout via ``docker logs`` and split it into
        # the run's log file and the run's solution-output zip before validation reads
        # them; the container can be safely removed afterwards because nothing on disk
        # was shared with the validator (no /output volume or bind mount).
        self._extract_outputs_from_completed_containers(completed_containers)

        solution_locations = self._find_location_of_completed_solutions(completed_containers)
        bt.logging.info(f"📂 Found {len(solution_locations)} output folders for completed solutions")

        self._validate_and_report_solutions(solution_locations)

    def _extract_outputs_from_completed_containers(self, container_names: List[str]) -> None:
        """
        Pull each completed container's stdout via ``docker logs`` and split it into
        the run's log file and the solution-output zip on the host workspace.
        """
        for name in container_names:
            if not self._container_has_validator_label(name):
                bt.logging.warning(
                    f"⚠️ Skipping output extraction for container {name}; "
                    f"validator label {self.LABEL} not found"
                )
                continue
            solution = self.database_connection.db_query.get_challenge_solution_location(container_name=name)
            if not solution:
                bt.logging.warning(
                    f"⚠️ Skipping output extraction for container {name}; "
                    f"no solution location in database"
                )
                continue
            extract_stdout_output(name, solution.absolute_path_to_solution)

    def _validate_and_report_solutions(self, solution_locations: List[str]) -> None:
        """Validate the outputs of completed solutions and report results to the platform"""
        for location in solution_locations:
            bt.logging.info(f"🔍 Validating solution output at {location}")
            solution_status = validate_solution(location, self.platform_client, self.database_connection)
            self.database_connection.db_query.update_solution_status_in_db(solution_location=location, solution_status=solution_status)

        self._clean_up_solutions(solution_locations)

    def _clean_up_solutions(self, solution_locations: List[str]) -> None:
        cleaned = 0
        for location in solution_locations:
            bt.logging.info(f"🧹 Cleaning up solution at {location}")

            container_name = str(self.database_connection.db_query.get_container_name_by_solution_location(location))
            image_id = self.database_connection.db_query.get_image_id_from_solution_location(location)

            try:
                if container_name is not None:
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
                else:
                    bt.logging.warning(f"⚠️ Failed to remove folder {location}")

            except Exception as e:
                bt.logging.error(f"❌ Failed to clean up solution at {location}: {e}")

        if solution_locations:
            bt.logging.info(f"🧹 Cleaned up {cleaned}/{len(solution_locations)} solution locations")

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

    def _find_location_of_completed_solutions(self, container_names: List[str]) -> List[str]:
        """Find the host folder locations of completed solutions"""
        solution_locations: List[str] = []
        for name in container_names:
            solution = self.database_connection.db_query.get_challenge_solution_location(container_name=name)
            if solution:
                bt.logging.info(f"📂 Found solution location for container {name}: {solution.absolute_path_to_solution}")
                solution_locations.append(solution.absolute_path_to_solution)  # type: ignore
            else:
                bt.logging.warning(f"⚠️ No solution location found for container {name}")
                self._clean_up_orphaned_solutions(name)
        return solution_locations

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
        Resolve the max allowed runtime for the milestone associated with this container
        by looking up the solution record in the local DB and then querying the
        Challenges API for that milestone's configuration.max_solution_runtime.
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

            # Primary lookup by container_name
            solution = self.database_connection.db_query.get_challenge_solution_location(
                container_name=container_name
            )

            if solution and hasattr(solution, "challenge_milestone_id"):
                milestone_id = solution.challenge_milestone_id
                challenge_id = getattr(solution, "challenge_id", None)

                cache_key = f"{challenge_id or ''}:{milestone_id}"
                if cache_key in self._runtime_cache:
                    return self._runtime_cache[cache_key]

                if self.platform_client:
                    runtime = self.platform_client.get_milestone_max_solution_runtime(
                        challenge_id=challenge_id or "",
                        milestone_id=milestone_id,
                    )
                    self._runtime_cache[cache_key] = runtime
                    bt.logging.debug(
                        f"Resolved max_solution_runtime={runtime} for milestone {milestone_id}"
                    )
                    return runtime

            bt.logging.debug(
                f"Could not resolve milestone for container {container_identifier}. "
                f"Using default max runtime."
            )
            return DEFAULT_MAX_SOLUTION_RUNTIME

        except Exception as e:
            bt.logging.warning(f"Failed to resolve max runtime for container {container_identifier}: {e}")
            return DEFAULT_MAX_SOLUTION_RUNTIME

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
            db_image = self.database_connection.db_query.get_image_id_by_container_id(cid)
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

            # clean up image as well, if we can find it in the db
            image_id = self.database_connection.db_query.get_image_id_by_container_name(container_name)
            if image_id and self._image_ref_owned_by_validator(image_id):
                bt.logging.info(f"🗑️ Removing orphan image {image_id}")
                rmi_res = subprocess.run(["docker", "rmi", "-f", image_id], check=False, capture_output=True, text=True)
                if rmi_res.returncode == 0:
                    bt.logging.info(f"🗑️ Removed orphan image {image_id}")
                else:
                    bt.logging.warning(f"⚠️ Failed to remove orphan image {image_id}")
            elif image_id:
                bt.logging.warning(
                    f"⚠️ Refusing to remove orphan image {image_id}; name does not match validator label prefix"
                )

            self.database_connection.db_query.remove_solution_from_db_by_conainer_name(container_name=container_name)

        except Exception as e:
            bt.logging.error(f"❌ Failed to clean up orphaned solution associated to container: {container_name}: {e}")
