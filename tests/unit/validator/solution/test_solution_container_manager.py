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

import json
from datetime import timedelta
from unittest.mock import MagicMock, Mock, patch

import pytest

from qbittensor.validator.solution.solution_container_manager import SolutionContainerManager


@pytest.fixture
def container_manager():
    with patch("qbittensor.validator.solution.solution_container_manager.Timer") as mock_timer:
        mock_timer.return_value = Mock()
        mgr = SolutionContainerManager(
            platform_client=Mock(),
            database_connection=Mock(),
            validator_label="val_label",
        )
    mgr.database_connection.db_query = Mock()
    return mgr


class TestImageRefOwnedByValidator:
    @pytest.mark.parametrize(
        "image_ref,expected",
        [
            ("val_label_abc_image", True),
            ("val_label_abc_image:latest", True),
            ("val_label_abc_image@sha256:deadbeef", True),
            ("other_label_image", False),
            ("", False),
        ],
    )
    def test_image_ref_ownership(self, container_manager, image_ref, expected):
        assert container_manager._image_ref_owned_by_validator(image_ref) is expected


class TestContainerHasValidatorLabel:
    def test_valid_label(self, container_manager):
        labels = json.dumps({"val_label": "val_label"})
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=labels, returncode=0)
            assert container_manager._container_has_validator_label("ctr") is True

    def test_missing_label(self, container_manager):
        labels = json.dumps({"other": "x"})
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=labels, returncode=0)
            assert container_manager._container_has_validator_label("ctr") is False

    def test_inspect_failure_returns_false(self, container_manager):
        with patch("subprocess.run", side_effect=OSError("docker down")):
            assert container_manager._container_has_validator_label("ctr") is False


class TestValidatorIsBusy:
    def test_busy_when_at_max(self, container_manager):
        with patch.object(container_manager, "_get_number_of_running_solutions", return_value=999):
            with patch("qbittensor.validator.solution.solution_container_manager.MAX_SOLUTIONS", 999):
                assert container_manager.validator_is_busy() is True

    def test_not_busy_below_max(self, container_manager):
        with patch.object(container_manager, "_get_number_of_running_solutions", return_value=0):
            assert container_manager.validator_is_busy() is False


class TestExtractOutputsFromCompletedContainers:
    def test_extracts_when_solution_location_known(self, container_manager):
        container_manager.database_connection.db_query.get_challenge_solution_location.return_value = Mock(
            absolute_path_to_solution="/tmp/sol_workspace"
        )
        with patch.object(container_manager, "_container_has_validator_label", return_value=True):
            with patch(
                "qbittensor.validator.solution.solution_container_manager.extract_stdout_output",
                return_value=True,
            ) as mock_extract:
                container_manager._extract_outputs_from_completed_containers(["ctr_one"])
        mock_extract.assert_called_once_with("ctr_one", "/tmp/sol_workspace")

    def test_skips_without_validator_label(self, container_manager):
        with patch.object(container_manager, "_container_has_validator_label", return_value=False):
            with patch(
                "qbittensor.validator.solution.solution_container_manager.extract_stdout_output",
            ) as mock_extract:
                container_manager._extract_outputs_from_completed_containers(["ctr_one"])
        mock_extract.assert_not_called()


class TestFindCompletedSolutions:
    def test_parses_docker_output(self, container_manager):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="ctr_one\nctr_two\n", returncode=0)
            names = container_manager._find_completed_solutions()
        assert names == ["ctr_one", "ctr_two"]

    def test_docker_error_returns_empty(self, container_manager):
        with patch("subprocess.run", side_effect=OSError("fail")):
            assert container_manager._find_completed_solutions() == []


# =============================================================================
# Core Orchestration Tests (high value per README)
# =============================================================================

class TestRun:
    def test_run_executes_full_orchestration(self, container_manager):
        with patch.object(container_manager, "handle_completed_solutions") as mock_handle, \
                patch.object(container_manager, "_get_number_of_running_solutions", return_value=1), \
                patch.object(container_manager, "_get_overdue_containers", return_value=["ctr1"]), \
                patch.object(container_manager, "_terminate_overdue_containers") as mock_term, \
                patch.object(container_manager.database_connection.db_query, "prune_old_solutions") as mock_prune, \
                patch.object(container_manager, "_prune_containers") as mock_prune_ctrs:

            container_manager.run()

            mock_handle.assert_called_once()
            mock_term.assert_called_once_with(["ctr1"])
            mock_prune.assert_called_once()
            mock_prune_ctrs.assert_called_once()


class TestHandleCompletedSolutions:
    def test_early_return_when_no_completed(self, container_manager):
        with patch.object(container_manager, "_find_completed_solutions", return_value=[]):
            container_manager.handle_completed_solutions()  # should not blow up

    def test_full_happy_path(self, container_manager):
        containers = ["ctr_a", "ctr_b"]
        locations = ["/path/a", "/path/b"]

        with patch.object(container_manager, "_find_completed_solutions", return_value=containers), \
                patch.object(container_manager, "_extract_outputs_from_completed_containers") as mock_extract, \
                patch.object(container_manager, "_find_location_of_completed_solutions", return_value=locations), \
                patch.object(container_manager, "_validate_and_report_solutions") as mock_validate:

            container_manager.handle_completed_solutions()

            mock_extract.assert_called_once_with(containers)
            mock_validate.assert_called_once_with(locations)


class TestValidateAndReportSolutions:
    def test_calls_validate_and_updates_status_then_cleans(self, container_manager):
        locations = ["/loc1", "/loc2"]
        mock_validate = Mock(return_value="Success")

        with patch("qbittensor.validator.solution.solution_container_manager.validate_solution", mock_validate), \
                patch.object(container_manager.database_connection.db_query, "update_solution_status_in_db") as mock_update, \
                patch.object(container_manager, "_clean_up_solutions") as mock_clean:

            container_manager._validate_and_report_solutions(locations)

            assert mock_validate.call_count == 2
            assert mock_update.call_count == 2
            mock_clean.assert_called_once_with(locations)


class TestCleanUpSolutions:
    def test_stops_removes_and_deletes(self, container_manager):
        locations = ["/ws/solution1"]
        container_manager.database_connection.db_query.get_container_name_by_solution_location.return_value = "ctr1"
        container_manager.database_connection.db_query.get_image_id_from_solution_location.return_value = "img1"

        with patch("subprocess.run") as mock_run, \
                patch.object(container_manager, "_container_has_validator_label", return_value=True), \
                patch.object(container_manager, "_image_ref_owned_by_validator", return_value=True):

            container_manager._clean_up_solutions(locations)

            # Should have called docker stop, rm, rmi, and rm -rf on the folder
            assert any("stop" in str(c) for c in mock_run.call_args_list)
            assert any("rm" in str(c) for c in mock_run.call_args_list)


class TestOverdueContainers:
    def test_get_overdue_filters_by_runtime(self, container_manager):
        container = "val_label_ctr_overdue"

        with patch.object(container_manager, "_get_running_containers", return_value=[container]), \
                patch.object(container_manager, "_container_has_validator_label", return_value=True), \
                patch.object(container_manager, "_get_max_runtime_for_container", return_value=timedelta(minutes=1)), \
                patch("subprocess.run") as mock_run:

            # Return a very old started time
            mock_run.return_value = MagicMock(stdout="2020-01-01T00:00:00Z", returncode=0)

            overdue = container_manager._get_overdue_containers()
            assert container in overdue

    def test_terminate_overdue_respects_label_and_cleans(self, container_manager):
        overdue = ["ctr1"]
        container_manager.database_connection.db_query.get_image_id_by_container_id.return_value = None

        with patch.object(container_manager, "_container_has_validator_label", return_value=True), \
                patch.object(container_manager, "_inspect_container_config_image", return_value="val_label_img"), \
                patch.object(container_manager, "_image_ref_owned_by_validator", return_value=True), \
                patch("subprocess.run") as mock_run:

            container_manager._terminate_overdue_containers(overdue)

            assert any("stop" in str(c) for c in mock_run.call_args_list)
            assert any("rm" in str(c) for c in mock_run.call_args_list)


class TestPruneContainers:
    def test_prune_removes_exited_validator_containers(self, container_manager):
        with patch("subprocess.run") as mock_run, \
                patch.object(container_manager, "_container_has_validator_label", return_value=True), \
                patch.object(container_manager, "_inspect_container_config_image", return_value="val_label_img"), \
                patch.object(container_manager, "_image_ref_owned_by_validator", return_value=True):

            mock_run.return_value = MagicMock(stdout="ctr_exited\n", returncode=0)

            container_manager._prune_containers()

            # Should attempt docker rm and rmi
            calls = [str(c) for c in mock_run.call_args_list]
            assert any("rm" in c for c in calls)


class TestOrphanedAndLocationHandling:
    def test_find_location_triggers_orphan_cleanup_when_missing(self, container_manager):
        container_manager.database_connection.db_query.get_challenge_solution_location.return_value = None

        with patch.object(container_manager, "_clean_up_orphaned_solutions") as mock_orphan:
            container_manager._find_location_of_completed_solutions(["orphan_ctr"])
            mock_orphan.assert_called_once_with("orphan_ctr")

    def test_clean_up_orphaned_respects_label_and_cleans(self, container_manager):
        container_manager.database_connection.db_query.get_image_id_by_container_name.return_value = "val_label_img"

        with patch.object(container_manager, "_container_has_validator_label", return_value=True), \
                patch.object(container_manager, "_image_ref_owned_by_validator", return_value=True), \
                patch("subprocess.run") as mock_run:

            container_manager._clean_up_orphaned_solutions("orphan_ctr")

            # Should stop, rm container, rmi image, and remove from DB
            calls = [str(c) for c in mock_run.call_args_list]
            assert any("stop" in c for c in calls)
            assert any("rm" in c for c in calls)
