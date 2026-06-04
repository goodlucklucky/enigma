# Validator Voting Guide

Validators must vote on two different types of proposals:

- ### Whitelist

    When it has been determined that a validator will be running Enigma challenge milestones, it will be able to participate in the voting process when a challenge milestone is met. This is controlled by the validator whitelist in the treasury contract.

    Existing validators in the whitelist will be notified via Discord and expected to vote within the voting period (36 hours).

    The `list_proposals.py` script can be used to view the proposal information and which validator(s) are being added or removed.

    Example:
    ```bash
    python treasury/scripts/list_proposals.py --contract CONTRACT --id PROPOSAL_ID
    ```

    The scripts default to the public lite endpoint (`https://lite.chain.opentensor.ai`). Use `--rpc YOUR_RPC` if you need a dedicated endpoint.

    The output will show one or more addresses. The EVM address if properly associated with the validator hotkey will be decoded as well. Ensure before voting that this is a true validator on the subnet. Additions to the whitelist will have a trusted status of `true` and removals will have a trusted status of `false`.

    Sample Output:
    ```
    ID:       0xf1690580ada20746a1621f225961ae035686e8600ab39bf0ca264bfad21ef8d6
    State:    7 (Executed)
    Timing:   Snapshot: 8090996 | Deadline: 8091896
    Target:   0x61216B0F3b92f59Edd67CCd446ad3A3C1371CdE6
    Value:    0.0000 TAO
    DescHash: 0x9eb54f09b4df7ef666dea3b56032583bae525395e2ffb6000aab5f350c981267
    Payload:  Type: updateTrustedValidators
      Validators: [0x28fB25B95ABC7569BB55Fe08808363B0EbF884da (SS58: 5HfV9mhBmALgishMY6s3F3jQDa2LHrQ1C99KUi8wAsxHTUGU) [Hotkeys: 5EZ52JMq4S7PYqzmLAggYahyDirMx3p1f1uBtLQgx6fk7kR8]]
      Trusted Status: [true]
    ```

    Voting should then be completed with the script:

    ```bash
    python treasury/scripts/vote_whitelist.py --contract <treasury governor address> --proposal-id <proposal id> --support <true/false> --pk <validator evm private key>
    ```

- ### Payout

    When it has been determined that all validators have validated an Enigma challenge milestone successfully, a proposal will be made to the miner hotkey that submitted the challenge milestone. Currently, checking the validation status and voting on a proposal will be done manually.

    Existing validators in the whitelist will be notified via Discord and expected to vote within the voting period (36 hours).

    **Validator Check Script**

    After your validator has processed a milestone, you can inspect its local database using the `check-validation` CLI (available after `pip install -e .`):

    ```bash
    check-validation --submission <submission_id> [--hotkey <your_validator_hotkey_ss58>]
    ```

    This queries your local validator database (`~/.enigma/challenge_solutions_*.db`) and shows the current status of the submission (e.g. `validated`, `cross_checked`, etc.), the associated milestone ID, miner hotkey, transaction hash, and timestamps.

    Example:
    ```bash
    check-validation --submission sn63solution_abc123...
    ```

    Use this before voting on payout proposals to confirm that the milestone was successfully validated (and cross-checked) by your validator.

    Voting should then be completed with the script:

    ```bash
    python treasury/scripts/vote_payout.py --contract <treasury governor address> --netuid 63 --vault-hotkey <vault hotkey> --support <true/false> --pk <validator evm private key>
    ```

    You will be prompted for the challenge and milestone being completed and the proposal will be looked up from the hashed data.

    *__Note:__ During the case of a failed vote, there may be a necessity to retry. Since all proposal hashes must be unique, there is a `--retry n` flag that must be passed in order to indicate intentionally a new proposal / vote.*

    When listing proposals with `list_proposals.py`, payouts are able to be viewed as well.

    Sample Output:
    ```
    ID:       0x443cdf69765b17a8a9a491469c368ada902a3814fadd74f320b08a4d3be00545
    State:    1 (Active)
    Timing:   Voting ends in ~792 blocks (~2 hrs 38 mins) (at block 8113038)
    Target:   0x0000000000000000000000000000000000000805
    Value:    0.0000 TAO
    DescHash: 0x8fc3f7af7d0f28e5c8b483f27f0555002afb74b53edaa8ec61c777ffda9f772d
    Payload:  Type: transferStake
      Destination Coldkey: 0xb06b962c7145a78b735941cdd606e22f5d556f6f6570a697529704e334a6d968 (SS58: 5G42F9f1sd3z1UnBLT9yThnvUfQXzz5kJsgsdW4DUqcfGY8n)
      Source Hotkey: 0x4a7648a38df10029bf41d9548d5a789a94ebdf467998f773013a54e101c35c26 (SS58: 5DkLYAtptcS1nuPXW3YnxsybYQgZxoJc3edqevZkkyzTvpSr)
      Origin Netuid: 63
      Destination Netuid: 63
      Amount (Alpha): 4.125 Alpha (Raw: 4250000000)
    ```

### Recommended EVM Wallet Balances

| Account | Recommended TAO Balance | Reason |
| --- | --- | --- |
| Each Trusted Validator* | 0.5–1 TAO* | Needs to vote regularly |

### Gas Estimates

| Action | Who Pays Gas | Approx. Gas Used | Realistic TAO Cost* | Recommended Buffer |
| --- | --- | --- | --- | --- |
| castVote()* | Each Validator* | 95,000 – 135,000* | 0.012 – 0.018 TAO* | 0.03 TAO per vote |

_* Assumes ~100 Gwei gas price. Bittensor EVM gas prices can fluctuate between 50–200 Gwei._
