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

import bittensor as bt
import os
import subprocess
import time
from typing import Optional, Tuple

from .exceptions.validation_errors import ValidationErrors

from qbittensor.validator.solution.challenge_inputs.challenge_setups import run_challenge_setup
from .milestones import assert_milestone_supported
from qbittensor.validator.solution.exceptions.invalid_solution import InvalidSolutionError
from qbittensor.database.db_connection import DBConnection

from .manage_files import setup
from .download_solution import download_zip
from .validate_zipfile import validate_zip
from .extract_solution_code import unzip
from .validate_code import validate_code
from .build_docker_image import build_image
from .validate_docker_image import reject_dockerfile, validate_image
from .run_solution import prepare_challenge_input_mount_dir, run_image_detached
from qbittensor.utils.solution_status import SolutionStatus
from qbittensor.utils.services.challenges import ChallengesClient
from qbittensor.utils.services.telemetry import TelemetryService
from .solution_context import SolutionExecution


def run_solution_management(
    db_conn: DBConnection,
    validator_label: str,
    download_url: str,
    challenge_milestone_id: str,
    challenge_validation_solution_id: str,
    tx_hash: str,
    miner_hotkey: str,
    submission_id: str | None,
    challenge_id: str,
    milestone_configuration: dict | None = None,
    platform_client: ChallengesClient | None = None,
    telemetry_service: TelemetryService | None = None,
    max_solution_runtime_seconds: int | None = None,
) -> Tuple[str | None, str | None, str | None]:
    """Run the full setup/download/build/run pipeline for a solution.

    The claim identifiers are passed individually up to (and including) the
    DB create step. A SolutionExecution (pure identity value object) is created
    *only after* we successfully obtain the stable solution_id from
    create_challenge_solution. It is then used for label generation etc.
    Runtime container/image/workspace details are kept in local variables.

    The appropriate handler (setup + validation) is selected by challenge_id.
    """

    image_name: str | None = None
    container_id: str | None = None
    container_name: str | None = None
    absolute_path_to_host_folder: str | None = None
    folder_name: str | None = None
    did_start_solution = False
    did_insert_solution = False
    execution: SolutionExecution | None = None

    bt.logging.info(f"🔧 run_solution_management starting for {challenge_validation_solution_id} (tx={tx_hash})")

    if telemetry_service:
        telemetry_service.record_event(
            "solution_execution_started",
            value=1,
            miner_hotkey=miner_hotkey,
            attributes={
                "submission_id": submission_id,
                "tx_hash": tx_hash,
                "challenge_milestone_id": challenge_milestone_id,
            },
        )

    try:
        bt.logging.info(f"Inserting pending challenge solution for miner with hotkey {miner_hotkey}")
        solution_id = db_conn.db_query.create_challenge_solution(
            challenge_validation_solution_id=challenge_validation_solution_id,
            challenge_milestone_id=challenge_milestone_id,
            submission_id=submission_id,
            solution_status=SolutionStatus.PENDING.value,
            tx_hash=tx_hash,
            miner_hotkey=miner_hotkey,
            challenge_id=challenge_id,
            max_solution_runtime_seconds=max_solution_runtime_seconds,
        )
        if not solution_id:
            bt.logging.error("Failed to insert pending challenge solution.")
            return None, None, None
        did_insert_solution = True

        execution = SolutionExecution.create(
            tx_hash=tx_hash,
            submission_id=submission_id or "",
            challenge_validation_solution_id=challenge_validation_solution_id,
            challenge_id=challenge_id,
            challenge_milestone_id=challenge_milestone_id,
            miner_hotkey=miner_hotkey,
            download_url=download_url,
            solution_id=solution_id,
        )

        # Setup
        bt.logging.info(f"Setting up folder for solution id: {challenge_validation_solution_id}")
        solution_tag, folder_name = setup(validator_label, challenge_validation_solution_id=challenge_validation_solution_id)
        absolute_path_to_host_folder = os.path.abspath(folder_name)

        # Download zip
        local_filepath = download_zip(url=download_url, folder_name=folder_name)
        if local_filepath is None:
            bt.logging.error("Failed to download zip file.")
            raise InvalidSolutionError(message=ValidationErrors.TARBALL_DOWNLOAD_FAILED.value)

        # Validate zip
        bt.logging.info("Validating zip...")
        valid_zipfile = validate_zip(local_filepath)
        if not valid_zipfile:
            bt.logging.error("Zip validation failed.")
            raise InvalidSolutionError(message=ValidationErrors.INVALID_TARBALL.value)

        # Extract code from zip
        bt.logging.info("Extracting code from zip...")
        unzip(folder_name=folder_name, source_filepath=local_filepath)

        # Validate code
        bt.logging.info("Validating code...")
        code_is_valid = validate_code(folder_name=folder_name)
        if not code_is_valid:
            bt.logging.error("Code validation failed.")
            raise InvalidSolutionError(message=ValidationErrors.INVALID_PROGRAM.value)

        # Validate Dockerfile security policy
        bt.logging.info("Validating Dockerfile policy...")
        if not reject_dockerfile(folder_name=folder_name):
            bt.logging.error("Dockerfile policy validation failed.")
            raise InvalidSolutionError(message=ValidationErrors.INVALID_PROGRAM.value)

        # Build docker image
        image_name = f"{solution_tag}_image".lower()  # Docker image names must be lowercase
        bt.logging.info(f"Building docker image: {image_name}")
        # build_image now raises InvalidSolutionError with rich diagnostics on any failure
        build_image(image_name=image_name, dockerfile_dir=f"{folder_name}/code")

        # Verify the image
        bt.logging.info(f"Validating docker image: {image_name}")
        # validate_image now raises InvalidSolutionError with diagnostics on failure
        validate_image(image_name=image_name)

        # Establish challenge input in a fresh read-only mount directory for this run.
        challenge_input_mount_dir = prepare_challenge_input_mount_dir(absolute_path_to_host_folder)
        run_challenge_setup(
            challenge_id=challenge_id,
            solution_folder_path=challenge_input_mount_dir,
            configuration=milestone_configuration,
        )

        # Run the image in a container, capture the container id
        # Pass the SolutionExecution (pure identity; used for labels via to_labels).
        container_name = f"{solution_tag}_container".lower()  # Docker container names must be lowercase
        bt.logging.info(f"Running docker container: {container_name}")
        container_id = run_image_detached(
            image_name=image_name,
            container_name=container_name,
            validator_label=validator_label,
            challenge_input_mount_dir=challenge_input_mount_dir,
            solution_execution=execution,
        )

        # Persist runtime fields via by-id update (no longer stored on the SolutionExecution).
        assert execution is not None
        bt.logging.info(f"Updating challenge solution for miner with hotkey {execution.miner_hotkey}")
        if not db_conn.db_query.update_challenge_solution_by_id(
            solution_id=execution.solution_id,
            container_id=container_id,
            container_name=container_name,
            image_id=image_name,
            absolute_path_to_solution=absolute_path_to_host_folder,
            solution_status=SolutionStatus.RUNNING.value,
        ):
            bt.logging.error("Failed to update challenge solution.")
            raise InvalidSolutionError(message=ValidationErrors.DOCKER_RUN_FAILED.value)
        did_start_solution = True
        bt.logging.info(f"✅ run_solution_management completed setup for {challenge_validation_solution_id}")

    except InvalidSolutionError as e:
        bt.logging.error(f"Invalid solution: {e.error_msg}")
        import traceback
        rich_reason = f"Invalid solution during processing: {e.error_msg}"
        tb = traceback.format_exc()
        if tb and "NoneType: None" not in tb:
            rich_reason += f"\n\nTraceback:\n{tb}"

        if did_insert_solution and not did_start_solution and execution is not None:
            # We constructed the SolutionExecution (with stable id) only on successful create.
            db_conn.db_query.update_challenge_solution_status_by_id(
                solution_id=execution.solution_id,
                solution_status=SolutionStatus.FAILED.value,
            )
        if platform_client:
            sub_for_report = execution.submission_id if execution is not None else (submission_id or "")
            platform_client.report_submission_status(
                submission_id=sub_for_report,
                status="Failure",
                reason=rich_reason[:2000],  # keep platform messages reasonable length
            )
        else:
            bt.logging.warning("No platform_client provided — could not report failure status to platform")
        return None, None, None
    finally:
        if not did_start_solution:
            clean_up_failed_solution(image_name=image_name, container_id=container_id, folder_name=absolute_path_to_host_folder)

    return image_name, container_id, folder_name


def clean_up_failed_solution(image_name: str | None, container_id: str | None, folder_name: str | None) -> None:
    bt.logging.info(f"🧹 Starting cleanup of failed solution (image={image_name}, container={container_id}, folder={folder_name})")
    cleaned = 0
    total = sum(1 for x in (container_id, image_name, folder_name) if x)

    if container_id is not None:
        remove_container_result = subprocess.run(["docker", "rm", "-fv", container_id], check=False, capture_output=True, text=True)
        if remove_container_result.returncode == 0:
            bt.logging.info(f"🗑️ Removed container {container_id}")
            cleaned += 1
        else:
            bt.logging.warning(
                f"⚠️ Failed to remove container {container_id}: "
                f"{(remove_container_result.stderr or remove_container_result.stdout).strip()}"
            )

    if image_name is not None:
        remove_image_result = subprocess.run(["docker", "rmi", "-f", image_name], check=False, capture_output=True, text=True)
        if remove_image_result.returncode == 0:
            bt.logging.info(f"🗑️ Removed image {image_name}")
            cleaned += 1
        else:
            bt.logging.warning(
                f"⚠️ Failed to remove image {image_name}: "
                f"{(remove_image_result.stderr or remove_image_result.stdout).strip()}"
            )

    if folder_name is not None:
        remove_folder_result = subprocess.run(["rm", "-rf", folder_name], check=False, capture_output=True, text=True)
        if remove_folder_result.returncode == 0:
            bt.logging.info(f"🗑️ Removed folder {folder_name}")
            cleaned += 1
        else:
            bt.logging.warning(
                f"⚠️ Failed to remove folder {folder_name}: "
                f"{(remove_folder_result.stderr or remove_folder_result.stdout).strip()}"
            )

    if total > 0:
        bt.logging.info(f"🧹 Failed solution cleanup complete: {cleaned}/{total} items removed")


# =============================================================================
# Shared Execution Entry Point (Normal + Cross-check paths)
# =============================================================================

def execute_verified_solution(
    db_conn: DBConnection,
    platform_client: ChallengesClient | None,
    validator_label: str,
    download_url: str,
    challenge_milestone_id: str,
    challenge_validation_solution_id: str,
    submission_id: str,
    tx_hash: str,
    miner_hotkey: str,
    challenge_id: str,
    # Optional: include these when the logs and solution output have been uploaded
    # so they can be reported together with the final status (as required by the platform).
    log_data_key: Optional[str] = None,
    output_data_key: Optional[str] = None,
    telemetry_service: TelemetryService | None = None,
) -> Tuple[str | None, str | None, str | None]:
    """
    Common execution path for running a solution that has already been verified
    (transfer proof passed) and for which the maintenance incentive has been recorded.

    This function is intended to be called by both:
      - The normal synapse processing path (ResponseProcessor)
      - The cross-check path (SolutionCrossChecker)

    Claim identifiers are passed as individual values. A SolutionExecution
    (the stable identity value object with solution_id etc.) is created
    *internally* inside run_solution_management only after create_challenge_solution
    succeeds. Runtime details are not part of SolutionExecution.

    The handler (setup for challenge inputs + output validation) is looked up
    by challenge_id (see MILESTONE_REGISTRY).


    On failure it reports via report_submission_status (including keys if provided).
    On success, the caller is responsible for any final Success report (with keys if applicable).
    """
    bt.logging.info(
        f"🧪 Beginning verified solution execution pipeline for submission={submission_id} "
        f"(miner {miner_hotkey}, tx {tx_hash}, milestone {challenge_milestone_id})"
    )

    # Fail fast if this challenge id does not have registered setup + validation handlers.
    # Per design, we cannot execute solutions for unsupported challenges.
    # Handlers are registered/looked up directly by challenge_id.
    assert_milestone_supported(challenge_id)

    # Fetch milestone configuration from the platform API (contains difficulty, runtime, etc.)
    milestone_configuration = {}
    max_solution_runtime_seconds: int | None = None
    if platform_client:
        milestone_configuration = platform_client.get_milestone_configuration(
            challenge_id, challenge_milestone_id
        )
        bt.logging.info(f"📋 Milestone configuration: {milestone_configuration}")
        raw_runtime = milestone_configuration.get("max_solution_runtime") if isinstance(milestone_configuration, dict) else None
        if raw_runtime is not None:
            try:
                secs = int(raw_runtime)
                if secs > 0:
                    max_solution_runtime_seconds = secs
            except (TypeError, ValueError):
                pass

    if max_solution_runtime_seconds is None:
        bt.logging.error(
            f"❌ Missing or invalid max_solution_runtime for milestone {challenge_milestone_id} "
            f"(challenge {challenge_id}). Refusing to run solution per policy: runtime limit must come from milestone config."
        )
        if platform_client:
            platform_client.report_submission_status(
                submission_id=submission_id,
                status="Failure",
                reason="Milestone is missing required max_solution_runtime configuration; validator cannot enforce timeout.",
            )
        else:
            bt.logging.warning("No platform_client provided — could not report failure for missing max_solution_runtime")
        return None, None, None

    start_ts = time.time()

    image_name, container_id, folder_name = run_solution_management(
        db_conn=db_conn,
        validator_label=validator_label,
        download_url=download_url,
        challenge_milestone_id=challenge_milestone_id,
        challenge_validation_solution_id=challenge_validation_solution_id,
        submission_id=submission_id,
        tx_hash=tx_hash,
        miner_hotkey=miner_hotkey,
        challenge_id=challenge_id,
        milestone_configuration=milestone_configuration,
        platform_client=platform_client,
        telemetry_service=telemetry_service,
        max_solution_runtime_seconds=max_solution_runtime_seconds,
    )

    elapsed = time.time() - start_ts
    if image_name and container_id:
        bt.logging.info(
            f"🏁 Execution pipeline finished successfully in {elapsed:.1f}s "
            f"(image={image_name}, container={container_id})"
        )

        if telemetry_service:
            telemetry_service.record_event(
                "solution_execution_completed",
                value=elapsed,
                miner_hotkey=miner_hotkey,
                attributes={
                    "submission_id": submission_id,
                    "tx_hash": tx_hash,
                    "outcome": "success",
                    "image": image_name,
                    "container": container_id,
                },
            )
    else:
        bt.logging.warning(
            f"🏁 Execution pipeline finished (with failures) in {elapsed:.1f}s "
            f"for tx {tx_hash}"
        )

        if telemetry_service:
            telemetry_service.record_event(
                "solution_execution_completed",
                value=elapsed,
                miner_hotkey=miner_hotkey,
                attributes={
                    "submission_id": submission_id,
                    "tx_hash": tx_hash,
                    "outcome": "failure",
                },
            )

    if image_name is None or container_id is None or folder_name is None:
        bt.logging.error(f"❌ Failed to execute verified solution for tx_hash {tx_hash}")
        if platform_client:
            bt.logging.info(
                f"📤 Reporting Failure to platform (submission_id={submission_id}) "
                f"with log_data_key={'present' if log_data_key else 'None'} "
                f"and output_data_key={'present' if output_data_key else 'None'}"
            )
            platform_client.report_submission_status(
                submission_id=submission_id,
                status="Failure",
                reason="Execution pipeline failed before container could produce output. Check validator logs around the submission timestamp for details (download/build/start phase).",
                log_data_key=log_data_key,
                output_data_key=output_data_key,
            )
        return None, None, None

    bt.logging.info(
        f"✨ Executed verified solution {submission_id} "
        f"(milestone {challenge_milestone_id}) with image {image_name}"
    )
    return image_name, container_id, folder_name
