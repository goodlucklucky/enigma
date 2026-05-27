# Miner

The miner has two cooperating parts:

- A CLI (`cli/mine_enigma.py`) that lets an operator browse challenges, upload a `.zip`, pay the TAO transfer fee, and store a submission record locally.
- A miner neuron (`neurons/miner.py`) that serves validators over Bittensor synapses by reading that local submission record and returning a `SolutionCandidate` plus transfer proof data.

## What The CLI Does

The `mine_enigma` CLI is an operator workflow for preparing a submission that the running miner can later serve to validators.

High-level flow:

1. Load `.env` values (and optional CLI overrides).
2. Query challenge data from the Engima Challenges API.
3. Let the user pick a challenge/milestone and `.zip` file.
4. Request an upload slot from `POST /v1/submissions/upload` (JWT-authenticated via `RequestManager`).
5. Send the TAO fee transfer on-chain (Balances `transfer_keep_alive`).
6. Upload the zip to the presigned upload URL returned by the API.
7. Upsert a row into the local SQLite miner DB (`miner_submissions_<hotkey_prefix>.db`).

## CLI Usage

From the repository root, run:

```bash
mine-enigma
```

(You may need to reactivate your virtualenv or run `hash -r` for the new command to appear.)

During interactive submission, the CLI asks for:

- Miner hotkey SS58 (or `MINER_HOTKEY_SS58` from `.env`)
- Path to `.zip` solution
- Source coldkey SS58 to send transfer from (or `MINER_SOURCE_COLDKEY_SS58`)
- Source mnemonic/seed phrase (or `MINER_SOURCE_COLDKEY_MNEMONIC`)

## .env Configuration

Create a `.env` file in the repository root (never commit it). The miner CLI loads variables via `python-dotenv` (through `get_api_config()` at import time).

### Challenges API Authentication (Signing Wallet)

The CLI uses a Bittensor wallet to sign requests when obtaining upload slots and submitting solutions. This is typically a separate "buy" wallet from your mining hotkey:

- `BUY_WALLET_COLDKEY`
- `BUY_WALLET_HOTKEY`

These can be overridden on the command line with `--wallet-name` and `--wallet-hotkey`.

- `NETWORK` — Bittensor network label passed to `RequestManager` and the signing flow (default: `finney`).

### Non-interactive / Automation Helpers

These variables allow `mine-enigma` to run without interactive prompts:

- `MINER_HOTKEY_SS58` — The hotkey SS58 that will serve the solution on-chain.
- `MINER_SOURCE_COLDKEY_SS58` — Coldkey that will pay the TAO submission fee.
- `MINER_SOURCE_COLDKEY_MNEMONIC` — Mnemonic/seed for the source coldkey (used to sign the on-chain transfer).

### Advanced

- `MINER_KEYFILE_PATH` — Direct path to a hotkey keyfile. When set and valid, the CLI will use this keypair directly for signing Challenges API requests instead of loading a wallet via `bt.Wallet`.

See `qbittensor/cli/miner/mine_enigma.py`, `_resolve_cli_api_auth()`, and `qbittensor/utils/request/request_manager.py` for the exact lookup logic.

## Local Database

The miner stores submission state in SQLite under `data/`:

- Path pattern: `data/miner_submissions_<first5_of_hotkey>.db`
- Table: `miner_submissions`
- Primary key: `challenge_milestone_id`

Key columns:

- `challenge_milestone_id`: milestone identity (PK)
- `upload_id`: upload endpoint id returned by `/submissions/upload`
- `miner_hotkey`: miner SS58 hotkey
- `tx_hash`: transfer extrinsic hash
- `transfer_block_hash`: inclusion block hash for transfer proof
- `transfer_from_ss58`: coldkey that paid
- `transfer_to_ss58`: destination ss58 (fee recipient)
- `transfer_amount_rao`: transfer amount (string RAO)
- `validators`: JSON list of validator hotkeys that already received this row
- `submitted_at`/`created_at`/`updated_at`: lifecycle timestamps

## Miner Submission Lifecycle

A submission follows this flow:

1. **Upload (CLI)**  
   `mine_enigma` obtains an upload slot from the platform, performs the required TAO transfer (with on-chain proof), uploads the `.zip`, and writes a row to the local `miner_submissions` table. At this point `submitted_at` is `NULL`.

2. **Serving (running miner neuron)**  
   `neurons/miner.py` continuously polls the local DB via `SolutionPoller`.  
   - It prefers rows that have never been served to any validator (`submitted_at IS NULL`).  
   - Once served to a validator, it sets `submitted_at` (the row moves down the priority queue but remains eligible to be served to *other* validators).  
   - Every time the solution is returned in a synapse, the miner attaches a fresh signature over the transfer proof data.

3. **Validator claim & execution**  
   On the validator side the `ResponseProcessor` verifies the transfer proof. If valid, it claims the work with the platform via `ChallengesClient.submit_solution`. The platform decides whether the validator should actually execute the solution or whether the work should be offered via the `/submissions/next` cross-check endpoint instead.

4. **Current lifetime policy**  
   A submission remains offerable indefinitely (it will keep being returned to validators that query the miner, subject to the priority ordering above). There is currently **no automatic expiration or TTL** on miner submissions. Old submissions are only removed if an operator manually deletes them or wipes the DB.

   Future policy changes (e.g. “un-serve after 7 days” or “after N distinct validators have seen it”) will be implemented in the miner DB layer and documented here.

## Miner Synapse Workflow

`neurons/miner.py` handles incoming `SolutionSynapse` requests:

1. Read validator hotkey from `synapse.dendrite.hotkey`.
2. If `synapse.validator_busy` is true, return early.
3. Query local DB for next row not yet sent to this validator (`get_next_miner_submission`).
4. Convert DB row -> `SolutionCandidate`.
5. Build transfer proof message and sign it with miner hotkey.
6. Populate synapse fields:
   - `solution_candidate`
   - `tx_hash`
   - `transfer_block_hash`
   - `transfer_from_ss58`
   - `transfer_to_ss58`
   - `transfer_amount_rao`
   - `transfer_proof_message`
   - `transfer_proof_signature_hex`
7. Mark row submitted for that validator (`mark_miner_submission_as_submitted`) so the same validator does not get duplicate delivery.

On validator side, `ResponseProcessor` verifies transfer proof data (message integrity, signature, hotkey/coldkey ownership, transfer destination/amount, and on-chain extrinsic inclusion) before accepting the candidate.

## End-to-End Operator Workflow

1. Configure `.env`.
2. Run `mine_enigma` and upload your milestone `.zip`.
3. CLI performs:
   - challenge API interaction
   - TAO fee transfer
   - submission upsert to local DB
4. Run the miner neuron:

```bash
python neurons/miner.py
```

5. As validators query your miner, it serves DB-backed submissions over synapses.
