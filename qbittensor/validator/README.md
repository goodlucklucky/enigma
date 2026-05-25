# Validator

The validator (`neurons/validator.py`) **queries miners** for solution candidates, **verifies TAO transfer proof** on the synapse, **registers work with the qbittensor Challenges API**, **runs submitted solutions in Docker**, and **reports validation outcomes** back to the platform. A separate timer-driven path **cross-checks** work assigned by the platform to help reach consensus across validators.

For implementation details (internal components, solution pipeline, weight setting, etc.), see the source under `qbittensor/validator/`.

## High-level operation

The validator periodically:
- Queries active miners via Bittensor synapses.
- Verifies on-chain TAO transfer proofs for submissions.
- Downloads and runs submitted solutions in Docker.
- Reports results back to the Challenges API.
- Periodically cross-checks work assigned by the platform for consensus.

Full details on the solution pipeline, Docker handling, output contract, and internal components live in the code under `qbittensor/validator/`.

## Local database

- **Path pattern**: `data/challenge_solutions_<first5_of_validator_hotkey>.db`
- **Tables** include:
  - **`challenge_solutions`**: one row per run (container, paths, submission id, status, milestone, …).
  - **`miner_maintenance_incentives`**: rows for weight setting / incentive eligibility (unique `tx_hash`).

## Operational requirements

- **Docker** available on the host (build, run, list, stop, rm) with permissions for the validator process.
- Sufficient disk for download/extract/solution output paths.

## Entry point

```bash
python neurons/validator.py
```

(Use your usual bittensor config flags / wallet as for any subnet validator.)
