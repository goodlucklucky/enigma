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


def run_solution_management(
    db_conn: DBConnection,
    validator_label: str,
    download_url: str,
    challenge_milestone_id: str,
    challenge_validation_solution_id: str,
    tx_hash: str,
    miner_hotkey: str,
    submission_id: str | None,
    challenge_id: str | None = None,
    platform_client: ChallengesClient | None = None,
    telemetry_service: TelemetryService | None = None,
) -> Tuple[str | None, str | None, str | None]:

    image_name: str | None = None
    container_id: str | None = None
    container_name: str | None = None
    absolute_path_to_host_folder: str | None = None
    folder_name: str | None = None
    did_start_solution = False
    did_insert_solution = False

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
        # 0. insert challenge solution
        bt.logging.info(f"Inserting pending challenge solution for miner with hotkey {miner_hotkey}")
        if not db_conn.db_query.create_challenge_solution(
            challenge_validation_solution_id=challenge_validation_solution_id,
            challenge_milestone_id=challenge_milestone_id,
            submission_id=submission_id,
            solution_status=SolutionStatus.PENDING.value,
            tx_hash=tx_hash,
            miner_hotkey=miner_hotkey,
            challenge_id=challenge_id,
        ):
            bt.logging.error("Failed to insert pending challenge solution.")
            return None, None, None
        did_insert_solution = True

        # 1. Setup
        bt.logging.info(f"Setting up folder for solution id: {challenge_validation_solution_id}")
        solution_tag, folder_name = setup(validator_label, challenge_validation_solution_id=challenge_validation_solution_id)
        absolute_path_to_host_folder = os.path.abspath(folder_name)

        # 2. Download zip
        local_filepath = download_zip(url=download_url, folder_name=folder_name)
        if local_filepath is None:
            bt.logging.error("Failed to download zip file.")
            raise InvalidSolutionError(message=ValidationErrors.TARBALL_DOWNLOAD_FAILED.value)

        # 3. Validate zip
        bt.logging.info("Validating zip...")
        valid_zipfile = validate_zip(local_filepath)
        if not valid_zipfile:
            bt.logging.error("Zip validation failed.")
            raise InvalidSolutionError(message=ValidationErrors.INVALID_TARBALL.value)

        # 4. Extract code from zip
        bt.logging.info("Extracting code from zip...")
        unzip(folder_name=folder_name, source_filepath=local_filepath)

        # 5. Validate code
        bt.logging.info("Validating code...")
        code_is_valid = validate_code(folder_name=folder_name)
        if not code_is_valid:
            bt.logging.error("Code validation failed.")
            raise InvalidSolutionError(message=ValidationErrors.INVALID_PROGRAM.value)

        # 5. Validate Dockerfile security policy
        bt.logging.info("Validating Dockerfile policy...")
        if not reject_dockerfile(folder_name=folder_name):
            bt.logging.error("Dockerfile policy validation failed.")
            raise InvalidSolutionError(message=ValidationErrors.INVALID_PROGRAM.value)

        # 6. Build docker image
        image_name = f"{solution_tag}_image".lower()  # Docker image names must be lowercase
        bt.logging.info(f"Building docker image: {image_name}")
        build_success = build_image(image_name=image_name, dockerfile_dir=f"{folder_name}/code")
        if not build_success:
            bt.logging.error("Docker image build failed.")
            raise InvalidSolutionError(message=ValidationErrors.DOCKER_BUILD_FAILED.value)

        # 7. Verify the image
        bt.logging.info(f"Validating docker image: {image_name}")
        image_valid: bool = validate_image(image_name=image_name)
        if not image_valid:
            bt.logging.error("Docker image validation failed.")
            raise InvalidSolutionError(message=ValidationErrors.DOCKER_IMAGE_VALIDATION_FAILED.value)

        # Establish challenge input in a fresh read-only mount directory for this run.
        challenge_input_mount_dir = prepare_challenge_input_mount_dir(absolute_path_to_host_folder)
        run_challenge_setup(
            challenge_milestone_id=challenge_milestone_id,
            solution_folder_path=challenge_input_mount_dir,
        )

        # 8. Run the image in a container, capture the container id
        container_name = f"{solution_tag}_container".lower()  # Docker container names must be lowercase
        bt.logging.info(f"Running docker container: {container_name}")
        container_id = run_image_detached(
            image_name=image_name,
            container_name=container_name,
            validator_label=validator_label,
            challenge_input_mount_dir=challenge_input_mount_dir,
        )

        # 9. Update challenge solution with container/runtime fields
        bt.logging.info(f"Updating challenge solution for miner with hotkey {miner_hotkey}")
        if not db_conn.db_query.update_challenge_solution(
            tx_hash=tx_hash,
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
        if did_insert_solution and not did_start_solution:
            db_conn.db_query.update_challenge_solution_status(
                tx_hash=tx_hash,
                solution_status=SolutionStatus.FAILED.value,
            )
        if platform_client:
            platform_client.report_submission_status(
                submission_id=submission_id or "",
                status="Failure",
                reason="Invalid solution during processing",
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
    *,
    db_conn: DBConnection,
    platform_client: ChallengesClient | None,
    validator_label: str,
    download_url: str,
    challenge_milestone_id: str,
    challenge_validation_solution_id: str,
    submission_id: str,
    tx_hash: str,
    miner_hotkey: str,
    challenge_id: str | None = None,
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

    On failure it reports via report_submission_status (including keys if provided).
    On success, the caller is responsible for any final Success report (with keys if applicable).
    """
    bt.logging.info(
        f"🧪 Beginning verified solution execution pipeline for submission={submission_id} "
        f"(miner {miner_hotkey}, tx {tx_hash}, milestone {challenge_milestone_id})"
    )
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
        platform_client=platform_client,
        telemetry_service=telemetry_service,
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
                reason="RunFailed",
                log_data_key=log_data_key,
                output_data_key=output_data_key,
            )
        return None, None, None

    bt.logging.info(
        f"✨ Executed verified solution {submission_id} "
        f"(milestone {challenge_milestone_id}) with image {image_name}"
    )
    return image_name, container_id, folder_name
