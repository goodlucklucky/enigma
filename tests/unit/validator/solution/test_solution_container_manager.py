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
import subprocess
from datetime import timedelta
from unittest.mock import MagicMock, Mock, patch

import pytest

from qbittensor.validator.solution.solution_container_manager import (
    SolutionContainerManager,
    SolutionExecution,
    SolutionPostProcessInfo,
    is_docker_available,
)


@pytest.fixture
def container_manager():
    # Patch recovery during __init__ so that mgr creation (which calls recover_and_clean_on_startup
    # eagerly) does not execute real recovery against an unconfigured db_query mock. Recovery tests
    # explicitly invoke the real method after db_query has been replaced with a controllable Mock.
    with patch("qbittensor.validator.solution.solution_container_manager.Timer") as mock_timer, \
         patch.object(SolutionContainerManager, "recover_and_clean_on_startup", lambda self: None):
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
        exec = SolutionExecution.create(
            tx_hash="tx-123", submission_id="sub-1", challenge_validation_solution_id="cv-1",
            challenge_id="c1", challenge_milestone_id="m1", miner_hotkey="hk-1",
            download_url="", solution_id="sol-id-123",
        )
        infos = [SolutionPostProcessInfo(exec, "ctr_one", "/tmp/sol_workspace", "img1")]
        with patch.object(container_manager, "_container_has_validator_label", return_value=True):
            with patch(
                "qbittensor.validator.solution.solution_container_manager.extract_stdout_output",
                return_value=True,
            ) as mock_extract:
                container_manager._extract_outputs_from_completed_containers(infos)
        mock_extract.assert_called_once_with("ctr_one", "/tmp/sol_workspace")

    def test_skips_without_validator_label(self, container_manager):
        exec = SolutionExecution.create(
            tx_hash="tx-123", submission_id="sub-1", challenge_validation_solution_id="cv-1",
            challenge_id="c1", challenge_milestone_id="m1", miner_hotkey="hk-1",
            download_url="", solution_id="sol-id-123",
        )
        infos = [SolutionPostProcessInfo(exec, "ctr_one", "/tmp/sol_workspace", "img1")]
        with patch.object(container_manager, "_container_has_validator_label", return_value=False):
            with patch(
                "qbittensor.validator.solution.solution_container_manager.extract_stdout_output",
            ) as mock_extract:
                container_manager._extract_outputs_from_completed_containers(infos)
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


class TestIsDockerAvailable:
    def test_returns_true_when_docker_version_succeeds(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="Docker version 24.0.0\n", returncode=0)
            assert is_docker_available() is True
        mock_run.assert_called_once()
        assert "docker" in mock_run.call_args.args[0]

    def test_returns_false_on_nonzero_exit_and_logs_diagnostics(self, caplog):
        with patch("subprocess.run") as mock_run, patch("shutil.which", return_value="/usr/bin/docker"):
            mock_run.return_value = MagicMock(stdout="", stderr="permission denied", returncode=1)
            with patch.dict("os.environ", {"PATH": "/custom/path"}, clear=True):
                assert is_docker_available() is False
        assert "Docker CLI check failed" in caplog.text
        assert "permission denied" in caplog.text
        assert "/custom/path" in caplog.text
        assert "/usr/bin/docker" in caplog.text

    def test_returns_false_on_file_not_found_and_logs_pm2_style_diagnostics(self, caplog):
        with patch("subprocess.run", side_effect=FileNotFoundError("no docker")), \
             patch("shutil.which", return_value=None):
            with patch.dict("os.environ", {"PATH": "/minimal/pm2/path"}, clear=True):
                assert is_docker_available() is False
        assert "Docker CLI not found in PATH" in caplog.text
        assert "pm2" in caplog.text
        assert "/minimal/pm2/path" in caplog.text
        assert "None" in caplog.text  # from shutil.which

    def test_returns_false_on_timeout(self, caplog):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(["docker"], 10)):
            assert is_docker_available() is False
        assert "timed out" in caplog.text


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
        exec_a = SolutionExecution.create(
            tx_hash="tx-a", submission_id="sub-a", challenge_validation_solution_id="cv-a",
            challenge_id="c-a", challenge_milestone_id="m-a", miner_hotkey="hk-a",
            download_url="", solution_id="sol-id-a",
        )
        exec_b = SolutionExecution.create(
            tx_hash="tx-b", submission_id="sub-b", challenge_validation_solution_id="cv-b",
            challenge_id="c-b", challenge_milestone_id="m-b", miner_hotkey="hk-b",
            download_url="", solution_id="sol-id-b",
        )
        infos = [
            SolutionPostProcessInfo(exec_a, "ctr_a", "/path/a", "img-a"),
            SolutionPostProcessInfo(exec_b, "ctr_b", "/path/b", "img-b"),
        ]

        with patch.object(container_manager, "_find_completed_solutions", return_value=containers), \
                patch.object(container_manager, "_extract_outputs_from_completed_containers") as mock_extract, \
                patch.object(container_manager, "_collect_completed_solution_infos", return_value=infos), \
                patch.object(container_manager, "_validate_and_report_solutions") as mock_validate:

            container_manager.handle_completed_solutions()

            mock_extract.assert_called_once_with(infos)
            mock_validate.assert_called_once_with(infos)


class TestValidateAndReportSolutions:
    def test_calls_validate_and_updates_status_then_cleans(self, container_manager):
        exec1 = SolutionExecution.create(
            tx_hash="tx-1", submission_id="sub1", challenge_validation_solution_id="cv-1",
            challenge_id="cid1", challenge_milestone_id="m1", miner_hotkey="hk-1",
            download_url="", solution_id="sol-id-1",
        )
        exec2 = SolutionExecution.create(
            tx_hash="tx-2", submission_id="sub2", challenge_validation_solution_id="cv-2",
            challenge_id="cid2", challenge_milestone_id="m2", miner_hotkey="hk-2",
            download_url="", solution_id="sol-id-2",
        )
        infos = [
            SolutionPostProcessInfo(exec1, "c1", "/loc1", "img1"),
            SolutionPostProcessInfo(exec2, "c2", "/loc2", "img2"),
        ]
        mock_validate = Mock(return_value="Success")

        with patch("qbittensor.validator.solution.solution_container_manager.validate_solution", mock_validate), \
                patch.object(container_manager.database_connection.db_query, "update_solution_status_by_id") as mock_update, \
                patch.object(container_manager, "_clean_up_solutions") as mock_clean:

            container_manager._validate_and_report_solutions(infos)

            assert mock_validate.call_count == 2
            assert mock_update.call_count == 2
            mock_clean.assert_called_once_with(infos)


class TestCleanUpSolutions:
    def test_stops_removes_and_deletes(self, container_manager):
        exec = SolutionExecution.create(
            tx_hash="tx-1", submission_id="sub1", challenge_validation_solution_id="cv-1",
            challenge_id="c1", challenge_milestone_id="m1", miner_hotkey="hk-1",
            download_url="", solution_id="sol-id-1",
        )
        infos = [SolutionPostProcessInfo(exec, "ctr1", "/ws/solution1", "img1")]

        with patch("subprocess.run") as mock_run, \
                patch.object(container_manager, "_container_has_validator_label", return_value=True), \
                patch.object(container_manager, "_image_ref_owned_by_validator", return_value=True):

            container_manager._clean_up_solutions(infos)

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
        container_manager.database_connection.db_query.get_challenge_solution_by_id.return_value = None

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
    def test_collect_infos_triggers_orphan_cleanup_when_missing(self, container_manager):
        with patch.object(container_manager, "_get_stable_keys_from_container", return_value={"submission_id": None, "tx_hash": None}), \
                patch.object(container_manager, "_clean_up_orphaned_solutions") as mock_orphan:
            container_manager._collect_completed_solution_infos(["orphan_ctr"])
            mock_orphan.assert_called_once_with("orphan_ctr")

    def test_clean_up_orphaned_respects_label_and_cleans(self, container_manager):
        with patch.object(container_manager, "_container_has_validator_label", return_value=True), \
                patch.object(container_manager, "_image_ref_owned_by_validator", return_value=True), \
                patch("subprocess.run") as mock_run:

            container_manager._clean_up_orphaned_solutions("orphan_ctr")

            # Should stop, rm container, rmi image (via inspect), and remove from DB
            calls = [str(c) for c in mock_run.call_args_list]
            assert any("stop" in c for c in calls)
            assert any("rm" in c for c in calls)


# =============================================================================
# Startup recovery (recover_and_clean_on_startup) - must not kill running solutions
# =============================================================================

from types import SimpleNamespace

from qbittensor.utils.solution_status import SolutionStatus


class TestStartupRecovery:
    def _make_sol(self, *, sol_id="sol-xyz", container_name="val_ctr_1", path="/tmp/ws1", status=SolutionStatus.RUNNING.value, image_id="val_img_1"):
        return SimpleNamespace(
            id=sol_id,
            container_name=container_name,
            absolute_path_to_solution=path,
            solution_status=status,
            image_id=image_id,
        )

    def test_no_uncleaned_does_nothing(self, container_manager):
        container_manager.database_connection.db_query.get_uncleaned_solutions.return_value = []
        with patch.object(container_manager, "handle_completed_solutions") as mock_handle:
            container_manager.recover_and_clean_on_startup()
        mock_handle.assert_not_called()  # early return before calling it when initial list empty
        container_manager.database_connection.db_query.mark_solution_cleaned.assert_not_called()
        container_manager.database_connection.db_query.update_solution_status_by_id.assert_not_called()

    def test_calls_handle_completed_then_leaves_running_solution_alone(self, container_manager, caplog):
        running_sol = self._make_sol(sol_id="live-1", container_name="val_live_ctr", path="/live/path", status=SolutionStatus.RUNNING.value)
        container_manager.database_connection.db_query.get_uncleaned_solutions.side_effect = [
            [running_sol],  # initial
            [running_sol],  # after handle_completed (still present because running)
        ]
        with patch.object(container_manager, "handle_completed_solutions") as mock_handle, \
             patch.object(container_manager, "_get_container_state", return_value="running") as mock_state, \
             patch.object(container_manager, "_container_has_validator_label", return_value=True) as mock_label, \
             patch("subprocess.run") as mock_run, \
             patch("shutil.rmtree") as mock_rmtree:
            container_manager.recover_and_clean_on_startup()

        mock_handle.assert_called_once()
        mock_state.assert_called_once_with("val_live_ctr")
        mock_label.assert_called_with("val_live_ctr")
        # No docker mutations for the live one
        run_calls = [str(c) for c in mock_run.call_args_list]
        assert not any("stop" in c or "rm " in c for c in run_calls)
        mock_rmtree.assert_not_called()
        container_manager.database_connection.db_query.mark_solution_cleaned.assert_not_called()
        container_manager.database_connection.db_query.update_solution_status_by_id.assert_not_called()
        # Note: bt.logging.info may not surface in caplog (bittensor/loguru); behavior is verified via mocks above.

    def test_non_running_or_lost_container_gets_remnant_cleanup_and_failed(self, container_manager):
        lost_sol = self._make_sol(sol_id="lost-9", container_name="val_lost", path="/lost/ws", status=SolutionStatus.RUNNING.value, image_id="val_lost_img")
        container_manager.database_connection.db_query.get_uncleaned_solutions.side_effect = [
            [lost_sol],
            [lost_sol],  # still uncleaned after handle (because no container -> handle wouldn't have seen it)
        ]
        with patch.object(container_manager, "handle_completed_solutions") as mock_handle, \
             patch.object(container_manager, "_get_container_state", return_value=None) as mock_state, \
             patch.object(container_manager, "_container_has_validator_label", return_value=True), \
             patch.object(container_manager, "_image_ref_owned_by_validator", return_value=True), \
             patch("subprocess.run") as mock_run, \
             patch("shutil.rmtree") as mock_rmtree, \
             patch("qbittensor.validator.solution.solution_container_manager.os.path.exists", return_value=True):
            container_manager.recover_and_clean_on_startup()

        mock_handle.assert_called_once()
        # Should have attempted container rm (even with state=None), rmi, and rmtree
        calls = [str(c) for c in mock_run.call_args_list]
        assert any("rm " in c or "rmi" in c for c in calls)
        mock_rmtree.assert_called()
        container_manager.database_connection.db_query.mark_solution_cleaned.assert_called_once_with("lost-9")
        container_manager.database_connection.db_query.update_solution_status_by_id.assert_called_once_with("lost-9", SolutionStatus.FAILED.value)

    def test_exited_but_uncleaned_gets_handled_via_handle_completed(self, container_manager):
        # After handle_completed processes an exited, the re-query returns empty for uncleaned -> nothing further
        exited_sol = self._make_sol(sol_id="exited-2", container_name="val_exited", path="/ex/ws", status=SolutionStatus.RUNNING.value)
        container_manager.database_connection.db_query.get_uncleaned_solutions.side_effect = [
            [exited_sol],  # initial
            [],            # after handle_completed the row is now cleaned by normal path
        ]
        with patch.object(container_manager, "handle_completed_solutions") as mock_handle, \
             patch.object(container_manager, "_get_container_state") as mock_state, \
             patch("subprocess.run"), \
             patch("shutil.rmtree"):
            container_manager.recover_and_clean_on_startup()

        mock_handle.assert_called_once()
        # Because re-query was empty, recovery did not reach state checks or mark for this one
        mock_state.assert_not_called()
        container_manager.database_connection.db_query.mark_solution_cleaned.assert_not_called()

    def test_path_gone_but_no_container_still_marks_cleaned(self, container_manager):
        orphan_sol = self._make_sol(sol_id="orphan-p", container_name=None, path="/gone/path", status=SolutionStatus.PENDING.value, image_id=None)
        container_manager.database_connection.db_query.get_uncleaned_solutions.side_effect = [[orphan_sol], [orphan_sol]]
        with patch.object(container_manager, "handle_completed_solutions"), \
             patch.object(container_manager, "_get_container_state", return_value=None), \
             patch.object(container_manager, "_container_has_validator_label", return_value=False), \
             patch("subprocess.run") as mock_run, \
             patch("shutil.rmtree") as mock_rmtree, \
             patch("qbittensor.validator.solution.solution_container_manager.os.path.exists", return_value=False):
            container_manager.recover_and_clean_on_startup()

        # No docker work (no container, no image)
        assert mock_run.call_count == 0
        mock_rmtree.assert_not_called()
        container_manager.database_connection.db_query.mark_solution_cleaned.assert_called_once_with("orphan-p")
        container_manager.database_connection.db_query.update_solution_status_by_id.assert_called_once_with("orphan-p", SolutionStatus.FAILED.value)
