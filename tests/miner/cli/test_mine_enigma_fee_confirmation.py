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

from unittest.mock import MagicMock, patch

import click
import pytest
from rich.console import Console

from qbittensor.cli.miner.mine_enigma import (
    _confirm_fee_amount_before_unlock,
    CliApiAuth,
)


def make_console() -> Console:
    """Create a Console that captures output for assertions."""
    return Console(record=True, width=120, force_terminal=True, no_color=True)


def make_challenges_client(price_tao: float = 0.12) -> MagicMock:
    client = MagicMock()
    client.get_milestone_price_tao.return_value = price_tao
    return client


class TestConfirmFeeAmountBeforeUnlock:
    def test_returns_price_when_user_confirms(self):
        console = make_console()
        client = make_challenges_client(price_tao=0.25)

        with patch("qbittensor.cli.miner.mine_enigma.Prompt.ask", return_value="y"):
            result = _confirm_fee_amount_before_unlock(
                console=console,
                challenges_client=client,
                milestone_id="m1",
                challenge_id="ch1",
            )

        assert result == 0.25
        client.get_milestone_price_tao.assert_called_once_with(
            challenge_id="ch1", milestone_id="m1"
        )

    def test_returns_none_when_user_declines(self):
        console = make_console()
        client = make_challenges_client(price_tao=0.5)

        with patch("qbittensor.cli.miner.mine_enigma.Prompt.ask", return_value="n"):
            result = _confirm_fee_amount_before_unlock(
                console=console,
                challenges_client=client,
                milestone_id="m1",
                challenge_id="ch1",
            )

        assert result is None

    def test_raises_click_exception_on_price_fetch_failure(self):
        console = make_console()
        client = MagicMock()
        client.get_milestone_price_tao.side_effect = RuntimeError("network down")

        with pytest.raises(click.ClickException, match="Failed to fetch fee amount"):
            _confirm_fee_amount_before_unlock(
                console=console,
                challenges_client=client,
                milestone_id="m1",
                challenge_id="ch1",
            )

    def test_accepts_yes_variants(self):
        console = make_console()
        client = make_challenges_client(0.1)

        for answer in ("y", "Y", "yes", "YES", " Yes "):
            with patch("qbittensor.cli.miner.mine_enigma.Prompt.ask", return_value=answer):
                result = _confirm_fee_amount_before_unlock(
                    console=console,
                    challenges_client=client,
                    milestone_id="m1",
                    challenge_id="ch1",
                )
                assert result == 0.1
