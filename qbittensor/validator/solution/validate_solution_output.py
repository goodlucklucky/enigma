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
from qbittensor.validator.solution.constants import (
    CONTAINER_OUTPUT_DIRNAME,
    CONTAINER_SOLUTION_DIRNAME,
    DOCKER_BUILD_LOG_FILENAME,
    SOLUTION_LOG_FILENAME,
)
from qbittensor.validator.solution.solution_validations.solution_validator import validate_output
from qbittensor.database.db_connection import DBConnection
from qbittensor.utils.services.challenges import ChallengesClient


def validate_solution(
    solution_workspace_path: str,
    challenges_client: ChallengesClient,
    submission_id: str,
    challenge_milestone_id: str,
    challenge_id: str,
) -> str:
    """Validate the output of the solution.

    Args:
        solution_workspace_path (str): Absolute path to the extracted solution workspace (DB
            ``absolute_path_to_solution``). The validator writes logs and extracted artifacts
            into the ``output`` subfolder after reading them from the container's stdout via
            ``docker logs``.
        submission_id: The cloud submission ID (always present by the time output
            validation runs, because the platform submission happened before execution).
        challenge_milestone_id, challenge_id: These stable values (from the
            SolutionPostProcessInfo.execution) are always present for completed solutions.

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

    bt.logging.info("📤 Uploading validator logs package (docker build + container stdout) to platform")
    logs_uploaded = upload_logs_package(container_output_path, logs_data)
    if logs_uploaded:
        bt.logging.info("✅ Logs package upload completed successfully")
    else:
        bt.logging.warning("⚠️ Logs package upload did not succeed (will still attempt to report status)")

    solution_status = perform_solution_output_validation(
        container_output_path,
        logs_data,
        solution_output_data,
        challenges_client,
        logs_uploaded=logs_uploaded,
        submission_id=submission_id,
        challenge_milestone_id=challenge_milestone_id,
        challenge_id=challenge_id,
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


def perform_solution_output_validation(
    container_output_path: str,
    logs_data: ChallengeSubmissionVerifyUploadAddressResponse,
    solution_output_data: ChallengeSubmissionVerifyUploadAddressResponse,
    challenges_client: ChallengesClient,
    submission_id: str,
    challenge_milestone_id: str,
    challenge_id: str,
    logs_uploaded: bool = True,
) -> str:
    """
    Perform the actual validation of the solution output.

    Attempts to upload solution artifacts on both success and failure paths
    (best effort). Only includes the corresponding data keys in the final
    report if the upload actually succeeded.

    The caller must supply the stable identifiers (submission_id,
    challenge_milestone_id, challenge_id) from the SolutionPostProcessInfo
    (via its embedded SolutionExecution or the convenience properties).
    No DB lookups (path-based or otherwise) are performed inside this function.
    """

    bt.logging.info(f"🛡️ Performing validation of solution output at '{container_output_path}'")
    solution_folder_path = os.path.join(container_output_path, CONTAINER_SOLUTION_DIRNAME)
    success, validation_failure_reason = validate_output(solution_folder_path, challenge_id)

    output_uploaded = False

    if success:
        bt.logging.info("\t✅ Solution output valid")
        bt.logging.info("📤 Attempting upload of solution output artifacts (validation passed)...")
        output_uploaded = upload_zip_to_platform(
            solution_folder_path, solution_output_data, "solution_output", zip_entire_directory=True
        )

        report_payload = {
            "status": "Success",
            "log_data_key": logs_data.id if logs_uploaded else None,
            "output_data_key": solution_output_data.id if output_uploaded else None,
        }
        if challenges_client.report_submission_status(
            submission_id,
            report_payload["status"],
            log_data_key=report_payload["log_data_key"],
            output_data_key=report_payload["output_data_key"],
        ):
            bt.logging.info("\t✅ Successfully updated platform with successful validation status")
            return SolutionStatus.SUCCESS.value
        else:
            return SolutionStatus.FAILED_UPLOAD.value

    else:
        bt.logging.info("\t❌ Solution output invalid")

        bt.logging.info("📤 Attempting upload of solution output artifacts (validation failed) for diagnostics...")
        try:
            output_uploaded = upload_zip_to_platform(
                solution_folder_path, solution_output_data, "solution_output", zip_entire_directory=True
            )
        except Exception as upload_exc:
            bt.logging.warning(f"⚠️ Failed to upload solution artifacts on failure path: {upload_exc}")
            output_uploaded = False

        failure_message = validation_failure_reason or "Output validation failed (see uploaded stdout.log and solution_output artifacts for details)"
        failure_message = f"Milestone {challenge_milestone_id}: {failure_message}"

        report_payload = {
            "status": "Failure",
            "log_data_key": logs_data.id if logs_uploaded else None,
            "output_data_key": solution_output_data.id if output_uploaded else None,
        }
        if challenges_client.report_submission_status(
            submission_id,
            report_payload["status"],
            reason=failure_message,
            log_data_key=report_payload["log_data_key"],
            output_data_key=report_payload["output_data_key"],
        ):
            bt.logging.info("\t✅ Successfully updated platform with failed validation status")
            return SolutionStatus.FAILED.value
        else:
            return SolutionStatus.FAILED_UPLOAD.value


def upload_zip_to_platform(
    output_file_path: str,
    platform_data: ChallengeSubmissionVerifyUploadAddressResponse,
    file_name: str,
    zip_entire_directory: bool = False,
) -> bool:
    """
    Zip either a single file under output_file_path or the whole output directory,
    then PUT it to the platform using the provided presigned URL.

    Returns True if the upload succeeded (2xx response), False otherwise.
    """
    base_name = os.path.splitext(file_name)[0]
    bt.logging.info(f"📤 Attempting upload of '{file_name}' ({'directory' if zip_entire_directory else 'file'}) to platform...")

    if zip_entire_directory:
        if not os.path.isdir(output_file_path):
            bt.logging.error(
                f"❌ Unable to upload directory: output path is not a directory '{output_file_path}'"
            )
            return False
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
            return False
    else:
        output_txt_path = os.path.join(output_file_path, file_name)
        if not os.path.isfile(output_txt_path):
            bt.logging.error(
                f"❌ Unable to upload {file_name}: output file not found at '{output_txt_path}'"
            )
            return False

        zip_path = os.path.join(output_file_path, f"{base_name}.zip")
        try:
            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zip_file:
                zip_file.write(output_txt_path, arcname=file_name)
        except Exception as e:
            bt.logging.error(f"❌ Failed to zip {file_name}: {e}")
            return False

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
                f"❌ Failed to upload '{file_name}'. Status code: {response.status_code}, Response: {response.text}"
            )
            return False

        bt.logging.info(f"✅ Successfully uploaded '{file_name}' to platform (id={platform_data.id})")
        return True
    except Exception as e:
        bt.logging.error(f"❌ Exception during upload of '{file_name}': {e}")
        return False
    finally:
        if zip_entire_directory and os.path.isfile(zip_path):
            try:
                os.unlink(zip_path)
            except OSError:
                pass


def upload_logs_package(
    container_output_path: str,
    platform_data: ChallengeSubmissionVerifyUploadAddressResponse,
) -> bool:
    """
    Create a zip containing the validator-produced logs for this submission and
    upload it using the provided presigned platform upload slot.

    The package intentionally includes:
      - docker_build.log (always written with --progress=plain before/during build)
      - stdout.log (container run logs, if the solution container produced any)
      - Any *diagnostics*.txt files (e.g. extraction failures)

    This becomes the artifact referenced by ``log_data_key`` so that build logs
    and run logs are available for diagnostics on both success and failure paths.
    """
    import tempfile
    import zipfile

    bt.logging.info("📤 Building logs package (docker_build.log + stdout.log + diagnostics)...")

    if not os.path.isdir(container_output_path):
        bt.logging.error(
            f"❌ Unable to build logs package: output path is not a directory '{container_output_path}'"
        )
        return False

    # Collect candidate log files (order is not critical)
    candidates = [
        DOCKER_BUILD_LOG_FILENAME,
        SOLUTION_LOG_FILENAME,
    ]
    # Also pick up any extraction or other diagnostics
    try:
        for name in os.listdir(container_output_path):
            if name.endswith("_diagnostics.txt") or name == "extraction_diagnostics.txt":
                if name not in candidates:
                    candidates.append(name)
    except OSError:
        pass

    log_files_to_zip: list[tuple[str, str]] = []  # (abs_path, arcname)
    for fname in candidates:
        fpath = os.path.join(container_output_path, fname)
        if os.path.isfile(fpath):
            log_files_to_zip.append((fpath, fname))

    if not log_files_to_zip:
        bt.logging.warning(
            f"⚠️ No log files found under '{container_output_path}' to upload as logs package "
            f"(looked for {candidates})"
        )
        # Still attempt to create an empty-ish diagnostic note? For now, treat as nothing to upload.
        return False

    # Create a temp zip containing only the selected log files at the root of the archive
    zip_fd, zip_path = tempfile.mkstemp(suffix=".zip", prefix="validator_logs_")
    os.close(zip_fd)
    try:
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for abs_path, arcname in log_files_to_zip:
                zf.write(abs_path, arcname=arcname)

        with open(zip_path, "rb") as zip_file_obj:
            response = requests.put(
                platform_data.url,
                data=zip_file_obj,
                headers={"Content-Type": "application/zip"},
                timeout=60,
            )

        if response.status_code < 200 or response.status_code > 299:
            bt.logging.error(
                f"❌ Failed to upload logs package. Status code: {response.status_code}, Response: {response.text}"
            )
            return False

        names = ", ".join(arc for _, arc in log_files_to_zip)
        bt.logging.info(f"✅ Successfully uploaded logs package ({names}) to platform (id={platform_data.id})")
        return True
    except Exception as e:
        bt.logging.error(f"❌ Exception during logs package upload: {e}")
        return False
    finally:
        try:
            if os.path.isfile(zip_path):
                os.unlink(zip_path)
        except OSError:
            pass
