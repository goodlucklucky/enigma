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

import pytest

from qbittensor.utils.solution_status import SolutionStatus


@pytest.fixture
def validator_query(validator_db):
    return validator_db.db_query


def _insert_solution(query, **overrides):
    defaults = dict(
        challenge_validation_solution_id="cv-1",
        container_id="cid-abc123",
        container_name="ctr_name",
        image_id="val_label_sol_image",
        challenge_id="ch-1",
        challenge_milestone_id="milestone-1",
        absolute_path_to_solution="/tmp/solution/path",
        submission_id="sub-1",
        solution_status=SolutionStatus.RUNNING.value,
        tx_hash="0xabc",
        miner_hotkey="5Miner",
    )
    defaults.update(overrides)
    assert query.insert_challenge_solution(**defaults) is True
    return defaults


class TestDBQuery:
    def test_insert_and_get_by_container_name(self, validator_query):
        data = _insert_solution(validator_query)
        row = validator_query.get_challenge_solution_location(data["container_name"])
        assert row is not None
        assert row.absolute_path_to_solution == data["absolute_path_to_solution"]

    def test_update_solution_status(self, validator_query):
        data = _insert_solution(validator_query)
        ok = validator_query.update_solution_status_in_db(
            data["absolute_path_to_solution"], SolutionStatus.SUCCESS.value
        )
        assert ok is True
        row = validator_query.get_challenge_solution_location(data["container_name"])
        assert row.solution_status == SolutionStatus.SUCCESS.value

    def test_insert_early_and_update_challenge_solution(self, validator_query):
        assert validator_query.create_challenge_solution(
            challenge_validation_solution_id="cv-early",
            challenge_milestone_id="milestone-1",
            submission_id="sub-early",
            solution_status=SolutionStatus.PENDING.value,
            tx_hash="0xearly",
            miner_hotkey="5Miner",
            challenge_id="ch-1",
        )
        row = validator_query.get_challenge_solution_location(
            validator_query._pending_placeholder("0xearly", "container_name")
        )
        assert row is not None
        assert row.solution_status == SolutionStatus.PENDING.value

        assert validator_query.update_challenge_solution(
            tx_hash="0xearly",
            container_id="cid-final",
            container_name="ctr_final",
            image_id="img_final",
            absolute_path_to_solution="/tmp/final/path",
            solution_status=SolutionStatus.RUNNING.value,
        )
        row = validator_query.get_challenge_solution_location("ctr_final")
        assert row.container_id == "cid-final"
        assert row.absolute_path_to_solution == "/tmp/final/path"
        assert row.solution_status == SolutionStatus.RUNNING.value

    def test_create_challenge_solution_without_challenge_id(self, validator_query):
        assert validator_query.create_challenge_solution(
            challenge_validation_solution_id="cv-cross",
            challenge_milestone_id="milestone-1",
            submission_id="sub-cross",
            solution_status=SolutionStatus.PENDING.value,
            tx_hash="0xcross",
            miner_hotkey="5Miner",
            challenge_id=None,
        )
        row = validator_query.get_challenge_solution_location(
            validator_query._pending_placeholder("0xcross", "container_name")
        )
        assert row is not None
        assert row.challenge_id is None

    def test_get_container_name_by_solution_location(self, validator_query):
        data = _insert_solution(validator_query)
        name = validator_query.get_container_name_by_solution_location(
            data["absolute_path_to_solution"]
        )
        assert name == data["container_name"]

    def test_get_image_id_lookups(self, validator_query):
        data = _insert_solution(validator_query)
        assert validator_query.get_image_id_from_solution_location(
            data["absolute_path_to_solution"]
        ) == data["image_id"]
        assert validator_query.get_image_id_by_container_name(data["container_name"]) == data["image_id"]
        assert validator_query.get_image_id_by_container_id(data["container_id"]) == data["image_id"]
        assert validator_query.get_image_id_by_container_id("cid-abc") == data["image_id"]

    def test_get_submission_and_milestone_ids(self, validator_query):
        data = _insert_solution(validator_query)
        assert validator_query.get_submission_id_by_solution_location(
            data["absolute_path_to_solution"]
        ) == data["submission_id"]
        assert validator_query.get_challenge_milestone_id_by_file_path(
            data["absolute_path_to_solution"]
        ) == data["challenge_milestone_id"]

    def test_remove_by_container_name(self, validator_query):
        data = _insert_solution(validator_query)
        assert validator_query.remove_solution_from_db_by_conainer_name(data["container_name"]) is True
        assert validator_query.get_challenge_solution_location(data["container_name"]) is None

    def test_insert_for_maintenance_incentive_and_get_active(self, validator_query):
        assert validator_query.insert_for_maintenance_incentive("5Miner", "m1", "0xincentive") is True
        # duplicate tx_hash is ignored
        assert validator_query.insert_for_maintenance_incentive("5Miner", "m1", "0xincentive") is True
        miners = validator_query.get_active_miners()
        assert "5Miner" in miners

    def test_get_miner_submission_statuses(self, validator_query):
        data = _insert_solution(validator_query)
        statuses = validator_query.get_miner_submission_statuses(data["miner_hotkey"])
        assert len(statuses) == 1
        assert statuses[0].tx_hash == data["tx_hash"]

    def test_remove_challenge_solution(self, validator_query):
        data = _insert_solution(validator_query, challenge_validation_solution_id="cv-remove")
        assert validator_query.remove_challenge_solution("cv-remove") is True
        assert validator_query.get_challenge_solution_location(data["container_name"]) is None

    def test_missing_rows_return_none_or_empty(self, validator_query):
        assert validator_query.get_challenge_solution_location("missing") is None
        assert validator_query.get_miner_submission_statuses("5Nobody") == []
