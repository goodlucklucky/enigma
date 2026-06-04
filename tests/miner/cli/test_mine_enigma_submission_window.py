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

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import click
import pytest

from qbittensor.cli.miner.mine_enigma import (
    _assert_milestone_allows_submission,
    _assert_milestone_status_incomplete,
    _assert_submission_window_open,
    _find_milestone,
    _format_milestone_status_display,
    _parse_api_datetime,
    submit_solution,
)


class TestParseApiDatetime:
    def test_parses_z_suffix_as_utc(self):
        dt = _parse_api_datetime("2026-05-27T12:00:00Z")
        assert dt == datetime(2026, 5, 27, 12, 0, tzinfo=timezone.utc)

    def test_parses_naive_datetime_as_utc(self):
        dt = _parse_api_datetime("2026-05-27T12:00:00")
        assert dt == datetime(2026, 5, 27, 12, 0, tzinfo=timezone.utc)


class TestFindMilestone:
    def test_finds_milestone_by_id(self):
        detail = {
            "id": "challenge-1",
            "milestones": [
                {"id": "m1", "name": "One"},
                {"id": "m2", "name": "Two"},
            ],
        }
        ms = _find_milestone(detail, "m2")
        assert ms["name"] == "Two"

    def test_raises_when_milestone_missing(self):
        detail = {"id": "challenge-1", "milestones": [{"id": "m1"}]}
        with pytest.raises(click.ClickException, match="Milestone m2 not found"):
            _find_milestone(detail, "m2")


class TestAssertSubmissionWindowOpen:
    def _milestone(self, start: str, end: str) -> dict:
        return {
            "id": "milestone-1",
            "status": "Incomplete",
            "start_date": start,
            "end_date": end,
        }

    def test_allows_submission_inside_window(self):
        now = datetime(2026, 5, 27, 12, 0, tzinfo=timezone.utc)
        milestone = self._milestone(
            "2026-05-27T00:00:00Z",
            "2026-05-27T23:59:59Z",
        )
        with patch("qbittensor.cli.miner.mine_enigma.timestamp", return_value=now):
            _assert_submission_window_open(milestone)

    def test_rejects_submission_before_start(self):
        now = datetime(2026, 5, 26, 23, 59, tzinfo=timezone.utc)
        milestone = self._milestone(
            "2026-05-27T00:00:00Z",
            "2026-05-27T23:59:59Z",
        )
        with patch("qbittensor.cli.miner.mine_enigma.timestamp", return_value=now):
            with pytest.raises(click.ClickException, match="not open yet"):
                _assert_submission_window_open(milestone)

    def test_rejects_submission_after_end(self):
        now = datetime(2026, 5, 28, 0, 0, 1, tzinfo=timezone.utc)
        milestone = self._milestone(
            "2026-05-27T00:00:00Z",
            "2026-05-27T23:59:59Z",
        )
        with patch("qbittensor.cli.miner.mine_enigma.timestamp", return_value=now):
            with pytest.raises(click.ClickException, match="has closed"):
                _assert_submission_window_open(milestone)

    def test_allows_when_both_dates_are_null(self):
        now = datetime(2026, 5, 27, 12, 0, tzinfo=timezone.utc)
        milestone = {"id": "m1", "status": "Incomplete"}
        with patch("qbittensor.cli.miner.mine_enigma.timestamp", return_value=now):
            _assert_submission_window_open(milestone)

    def test_allows_when_start_is_null_and_now_before_old_start(self):
        # start_date null means "open from the past"
        now = datetime(2026, 5, 26, 0, 0, tzinfo=timezone.utc)
        milestone = {
            "id": "m1",
            "status": "Incomplete",
            "end_date": "2026-05-27T23:59:59Z",
        }
        with patch("qbittensor.cli.miner.mine_enigma.timestamp", return_value=now):
            _assert_submission_window_open(milestone)

    def test_allows_when_end_is_null_and_now_after_old_end(self):
        # end_date null means "never closes"
        now = datetime(2026, 5, 30, 0, 0, tzinfo=timezone.utc)
        milestone = {
            "id": "m1",
            "status": "Incomplete",
            "start_date": "2026-05-27T00:00:00Z",
        }
        with patch("qbittensor.cli.miner.mine_enigma.timestamp", return_value=now):
            _assert_submission_window_open(milestone)

    def test_rejects_invalid_start_date_value(self):
        with pytest.raises(click.ClickException, match="invalid start_date"):
            _assert_submission_window_open(
                {
                    "id": "m1",
                    "status": "Incomplete",
                    "start_date": "not-a-date",
                    "end_date": "2026-05-27T23:59:59Z",
                }
            )

    def test_rejects_invalid_end_date_value(self):
        with pytest.raises(click.ClickException, match="invalid end_date"):
            _assert_submission_window_open(
                {
                    "id": "m1",
                    "status": "Incomplete",
                    "start_date": "2026-05-27T00:00:00Z",
                    "end_date": "garbage",
                }
            )

    def test_allows_null_start_with_valid_end_inside_window(self):
        now = datetime(2026, 5, 27, 12, 0, tzinfo=timezone.utc)
        milestone = {
            "id": "m1",
            "status": "Incomplete",
            "end_date": "2026-05-27T23:59:59Z",
        }
        with patch("qbittensor.cli.miner.mine_enigma.timestamp", return_value=now):
            _assert_submission_window_open(milestone)

    def test_rejects_when_end_is_set_and_now_after_it_even_if_start_null(self):
        now = datetime(2026, 5, 28, 1, 0, tzinfo=timezone.utc)
        milestone = {
            "id": "m1",
            "status": "Incomplete",
            "end_date": "2026-05-27T23:59:59Z",
        }
        with patch("qbittensor.cli.miner.mine_enigma.timestamp", return_value=now):
            with pytest.raises(click.ClickException, match="has closed"):
                _assert_submission_window_open(milestone)


class TestFormatMilestoneStatusDisplay:
    def test_shows_incomplete_for_incomplete_status(self):
        assert _format_milestone_status_display({"status": "Incomplete"}) == "Incomplete"
        assert _format_milestone_status_display({"status": "incomplete"}) == "Incomplete"
        assert _format_milestone_status_display({}) == "Incomplete"

    def test_shows_other_status_values(self):
        assert _format_milestone_status_display({"status": "Complete"}) == "Complete"
        assert _format_milestone_status_display({"status": "InReview"}) == "InReview"


class TestAssertMilestoneStatusIncomplete:
    def test_allows_incomplete_status(self):
        _assert_milestone_status_incomplete({"id": "m1", "status": "Incomplete"})

    def test_allows_incomplete_status_case_insensitive(self):
        _assert_milestone_status_incomplete({"id": "m1", "status": "incomplete"})

    def test_rejects_complete_status(self):
        with pytest.raises(
            click.ClickException,
            match="successfully solved",
        ):
            _assert_milestone_status_incomplete({"id": "m1", "status": "Complete"})

    def test_rejects_inreview_status_with_retry_guidance(self):
        with pytest.raises(
            click.ClickException,
            match="potential solution is currently in review",
        ):
            _assert_milestone_status_incomplete({"id": "m1", "status": "InReview"})

    def test_rejects_missing_status(self):
        with pytest.raises(click.ClickException, match="missing a status"):
            _assert_milestone_status_incomplete({"id": "m1"})


class TestAssertMilestoneAllowsSubmission:
    def test_rejects_when_status_is_not_incomplete(self):
        milestone = {
            "id": "m1",
            "status": "Complete",
            "start_date": "2026-05-27T00:00:00Z",
            "end_date": "2026-05-27T23:59:59Z",
        }
        with pytest.raises(click.ClickException, match="not open for submissions"):
            _assert_milestone_allows_submission(milestone)

    def test_allows_when_status_and_window_are_valid(self):
        now = datetime(2026, 5, 27, 12, 0, tzinfo=timezone.utc)
        milestone = {
            "id": "m1",
            "status": "Incomplete",
            "start_date": "2026-05-27T00:00:00Z",
            "end_date": "2026-05-27T23:59:59Z",
        }
        with patch("qbittensor.cli.miner.mine_enigma.timestamp", return_value=now):
            _assert_milestone_allows_submission(milestone)


class TestSubmitSolutionEarlyStatusGuard:
    """Verify that bad milestone status short-circuits submit_solution *before*
    any balance checks, zip inspection, upload slot requests, actual uploads,
    or fee transfers/payments. This is the core guarantee after status changes
    on the cloud (e.g. InReview instead of Validating).
    """

    def test_rejects_inreview_before_any_balance_or_upload_or_pay(self):
        console = MagicMock()
        client = MagicMock()
        client.get_challenge.return_value = {
            "id": "ch1",
            "milestones": [
                {"id": "m1", "status": "InReview", "priceTao": 0.25}
            ],
        }
        fee_keypair = MagicMock()
        fee_keypair.ss58_address = "5FeePayer"

        with patch(
            "qbittensor.cli.miner.mine_enigma.ensure_sufficient_balance_for_fee"
        ) as mock_balance, patch(
            "qbittensor.cli.miner.mine_enigma._upload_solution_zip"
        ) as mock_upload, patch(
            "qbittensor.cli.miner.mine_enigma.transfer_tao_for_submission"
        ) as mock_transfer:
            with pytest.raises(
                click.ClickException, match="in review"
            ):
                submit_solution(
                    console=console,
                    milestone_id="m1",
                    solution_path="/tmp/does-not-need-to-exist.zip",
                    challenges_client=client,
                    miner_hotkey="5MinerHot",
                    source_ss58="5Source",
                    fee_keypair=fee_keypair,
                    network="finney",
                    challenge_id="ch1",
                    fee_tao=0.25,  # pre-supplied, as in normal caller path
                )

            # These must never have been reached
            mock_balance.assert_not_called()
            mock_upload.assert_not_called()
            mock_transfer.assert_not_called()
            # We did consult the platform for status (early)
            client.get_challenge.assert_called_once_with("ch1")

    def test_rejects_complete_before_any_balance_or_upload_or_pay(self):
        console = MagicMock()
        client = MagicMock()
        client.get_challenge.return_value = {
            "id": "ch1",
            "milestones": [
                {"id": "m1", "status": "Complete", "priceTao": 0.1}
            ],
        }
        fee_keypair = MagicMock()
        fee_keypair.ss58_address = "5FeePayer"

        with patch(
            "qbittensor.cli.miner.mine_enigma.ensure_sufficient_balance_for_fee"
        ) as mock_balance, patch(
            "qbittensor.cli.miner.mine_enigma._upload_solution_zip"
        ) as mock_upload, patch(
            "qbittensor.cli.miner.mine_enigma.transfer_tao_for_submission"
        ) as mock_transfer:
            with pytest.raises(
                click.ClickException, match="successfully solved"
            ):
                submit_solution(
                    console=console,
                    milestone_id="m1",
                    solution_path="/tmp/does-not-need-to-exist.zip",
                    challenges_client=client,
                    miner_hotkey="5MinerHot",
                    source_ss58="5Source",
                    fee_keypair=fee_keypair,
                    network="finney",
                    challenge_id="ch1",
                    # fee_tao omitted -> will still resolve from the fetched detail
                )

            mock_balance.assert_not_called()
            mock_upload.assert_not_called()
            mock_transfer.assert_not_called()
            client.get_challenge.assert_called_once_with("ch1")
