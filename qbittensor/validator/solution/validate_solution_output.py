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

import os
import tempfile
import zipfile

import requests

import bittensor as bt
from qbittensor.dto.challenge import ChallengeSubmissionVerifyUploadAddressResponse
from qbittensor.utils.solution_status import SolutionStatus
from qbittensor.validator.solution.constants import CONTAINER_OUTPUT_DIRNAME, CONTAINER_SOLUTION_DIRNAME
from qbittensor.validator.solution.solution_validations.solution_validator import validate_output
from qbittensor.database.db_connection import DBConnection
from qbittensor.utils.services.challenges import ChallengesClient


def validate_solution(solution_workspace_path: str, challenges_client: ChallengesClient, database_connection: DBConnection) -> str:
    """Validate the output of the solution

    Args:
        solution_workspace_path (str): Absolute path to the extracted solution workspace (DB
            ``absolute_path_to_solution``). The validator writes logs and extracted artifacts
            into the ``output`` subfolder after reading them from the container's stdout via
            ``docker logs``.

    Returns:
        Solution status string (e.g. ``SolutionStatus.SUCCESS``).
    """
    container_output_path = os.path.join(solution_workspace_path, CONTAINER_OUTPUT_DIRNAME)
    bt.logging.info(
        f"🛡️ Validating solution output workspace '{solution_workspace_path}' "
        f"(artifacts under '{container_output_path}')"
    )
    bt.logging.info("\t✅ Beginning validation of solution output")

    logs_data = establish_upload_locations_for_solution_data(solution_workspace_path, "solution_logs", challenges_client)
    solution_output_data = establish_upload_locations_for_solution_data(solution_workspace_path, "solution_output", challenges_client)

    if not logs_data or not solution_output_data:
        bt.logging.info("❌ Could not establish upload locations for solution output and logs")
        return SolutionStatus.FAILED_UPLOAD.value

    if not verify_upload_locations(solution_workspace_path, logs_data, solution_output_data, database_connection):
        bt.logging.info("❌ Could not resolve submission for upload locations.")
        return SolutionStatus.FAILED_UPLOAD.value

    upload_zip_to_platform(container_output_path, logs_data, "stdout.log")
    solution_status = perform_solution_output_validation(
        solution_workspace_path,
        container_output_path,
        logs_data,
        solution_output_data,
        challenges_client,
        database_connection,
    )
    return solution_status



def establish_upload_locations_for_solution_data(
    submission_location: str, output_type: str, challenges_client: ChallengesClient
) -> ChallengeSubmissionVerifyUploadAddressResponse | None:
    bt.logging.info(f"📢 Establishing platform upload location for {output_type} for submission at location {submission_location}")

    upload_location_data = challenges_client.create_verification_upload_url()
    if not upload_location_data:
        bt.logging.error(f"❌ Failed to acquire upload location for {output_type}")
        return None

    bt.logging.info(f"✅ Successfully acquired location for {output_type} output on platform. Received upload URL: {upload_location_data.url}, upload id: {upload_location_data.id}")

    return upload_location_data


def verify_upload_locations(
    location: str,
    logs_data: ChallengeSubmissionVerifyUploadAddressResponse,
    output_data: ChallengeSubmissionVerifyUploadAddressResponse,
    database_connection: DBConnection,
) -> bool:
    """
    Lightweight check that we can resolve a submission_id for this location.
    We no longer send a partial "Running + keys" update here.
    The keys will be sent together with the final status after uploads.
    """
    submission_id = database_connection.db_query.get_submission_id_by_solution_location(location)
    if not submission_id:
        bt.logging.error("❌ Could not find submission_id for location")
        return False

    bt.logging.info("\t✅ Upload locations resolved for reporting with final status")
    return True


def perform_solution_output_validation(
    solution_workspace_path: str,
    container_output_path: str,
    logs_data: ChallengeSubmissionVerifyUploadAddressResponse,
    solution_output_data: ChallengeSubmissionVerifyUploadAddressResponse,
    challenges_client: ChallengesClient,
    database_connection: DBConnection,
) -> str:
    """Perform the actual validation of the solution output

    Args:
        solution_workspace_path: Extracted solution root (matches DB ``absolute_path_to_solution``).
        container_output_path: Local directory where the validator wrote stdout logs and
            extracted artifacts (populated from ``docker logs`` after the container exits).
    """
    challenge_milestone_id = database_connection.db_query.get_challenge_milestone_id_by_file_path(solution_workspace_path)
    submission_id = database_connection.db_query.get_submission_id_by_solution_location(solution_workspace_path)

    if challenge_milestone_id is None:
        bt.logging.error("❌ No challenge milestone ID found for output path.")
        return SolutionStatus.FAILED.value

    bt.logging.info(f"🛡️ Performing validation of solution output at '{container_output_path}'")
    solution_folder_path = os.path.join(container_output_path, CONTAINER_SOLUTION_DIRNAME)
    success: bool = validate_output(solution_folder_path, challenge_milestone_id)

    if success:
        bt.logging.info("\t✅ Solution output valid")
        upload_zip_to_platform(
            solution_folder_path, solution_output_data, "solution_output", zip_entire_directory=True
        )
        # Report final status together with the data keys
        if challenges_client.report_submission_status(
            submission_id,
            "Success",
            log_data_key=logs_data.id,
            output_data_key=solution_output_data.id,
        ):
            bt.logging.info("\t✅ Successfully updated platform with successful validation status")
            return SolutionStatus.SUCCESS.value
        else:
            return SolutionStatus.FAILED_UPLOAD.value
    else:
        bt.logging.info("\t❌ Solution output invalid")
        if challenges_client.report_submission_status(
            submission_id,
            "Failure",
            log_data_key=logs_data.id,
            output_data_key=solution_output_data.id,
        ):
            bt.logging.info("\t✅ Successfully updated platform with failed validation status")
            return SolutionStatus.FAILED.value
        else:
            return SolutionStatus.FAILED_UPLOAD.value

def upload_zip_to_platform(
    output_file_path: str,
    platform_data: ChallengeSubmissionVerifyUploadAddressResponse,
    file_name: str,
    *,
    zip_entire_directory: bool = False,
) -> None:
    """Zip either a single file under output_file_path or the whole output directory, then PUT to platform_data.url."""
    base_name = os.path.splitext(file_name)[0]

    if zip_entire_directory:
        if not os.path.isdir(output_file_path):
            bt.logging.error(
                f"❌ Unable to upload directory: output path is not a directory '{output_file_path}'"
            )
            return
        zip_fd, zip_path = tempfile.mkstemp(suffix=".zip", prefix=f"{base_name}_")
        os.close(zip_fd)
        try:
            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zip_file:
                for root, _, files in os.walk(output_file_path):
                    for f in files:
                        full_path = os.path.join(root, f)
                        arcname = os.path.relpath(full_path, output_file_path)
                        zip_file.write(full_path, arcname=arcname)
        except Exception as e:
            bt.logging.error(f"❌ Failed to zip output directory: {e}")
            try:
                os.unlink(zip_path)
            except OSError:
                pass
            return
    else:
        output_txt_path = os.path.join(output_file_path, file_name)
        if not os.path.isfile(output_txt_path):
            bt.logging.error(
                f"❌ Unable to upload {file_name}: output file not found at '{output_txt_path}'"
            )
            return

        zip_path = os.path.join(output_file_path, f"{base_name}.zip")
        try:
            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zip_file:
                zip_file.write(output_txt_path, arcname=file_name)
        except Exception as e:
            bt.logging.error(f"❌ Failed to zip {file_name}: {e}")
            return

    try:
        with open(zip_path, "rb") as zip_file_obj:
            response = requests.put(
                platform_data.url,
                data=zip_file_obj,
                headers={"Content-Type": "application/zip"},
                timeout=30,
            )

        if response.status_code < 200 or response.status_code > 299:
            bt.logging.error(
                f"❌ Failed to upload zip. Status code: {response.status_code}, Response: {response.text}"
            )
            return

        bt.logging.info(f"✅ Uploaded {base_name} zip to platform from '{zip_path}'")
    except Exception as e:
        bt.logging.error(f"❌ Failed to zip and upload {base_name}: {e}")
    finally:
        if zip_entire_directory and os.path.isfile(zip_path):
            try:
                os.unlink(zip_path)
            except OSError:
                pass
