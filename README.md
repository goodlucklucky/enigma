<div align="center">

<img src="./logo.png"/>

# **Enigma** (SN 63) <!-- omit in toc -->
[![Discord Chat](https://img.shields.io/discord/1395424987816661103)](https://discord.gg/Gfr2mhft)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

---

## Decentralized Challenge Platform on Bittensor

[Discord](https://discord.gg/xJ9JKPMJQD) • [Network](https://taostats.io/subnets/63) • [Website](https://www.qbittensorlabs.com/enigma) • [GitHub](https://github.com/qbittensor-labs/enigma)

</div>

---

**Enigma** is Subnet 63 on Bittensor — a decentralized platform for pressure-testing foundational technologies through open challenges with real prize pools. It incentivizes the global community (researchers, hackers, engineers, and students) to break cryptographic systems, AI safeguards, post-quantum protocols, and other deep-tech targets.

By leveraging Bittensor's incentive layer, Enigma turns critical security research into a transparent, competitive, and publicly verifiable process. Winners drain prize pools (including the current Treasury Wallet challenge), and successful exploits are open-sourced to advance the ecosystem.

> **Current Focus**: Breaking today to build a better tomorrow. The first challenge — Breaking RSA — is live. The subnet pioneers Bittensor's Treasury Wallet feature to fund challenge prize pools.

---

## How Enigma Works

1. **Challenges are posted** with funded prize pools in SN63 Alpha.
2. **Participants compete** by submitting verified solutions (code + exploits). Unlimited resubmissions allowed. See [Miner Setup](#miner-setup) for how to submit.
3. **Validators** score and verify solutions. A dedicated validator sets weights to the treasury wallet to distribute emissions and rewards.
4. **Winner takes all** — the first valid solution drains the prize. Proof is on-chain.
5. **Code is published** as open source after verification.

---

## Treasury Wallet

- **Details**

  - Vault Contract Address: `0xB291C87759E2BAf678734C45A44121091d999220`
  - Vault SS58 Cold Key: `5EgP27pkachXDvWpYGfFjatQkWkGXfaoGkP35jdXC4xwPmtZ`
  - Vault SS58 Hot Key: `5DCLafsAKaLeZwm9hjMHvrQNjtucSwBhKyTLYnYmMvhxF2Uc`
  - Governor Contract Address: `0x41a1BE0a7408717877DE25e2c62c2Fb71a04D8A9`
  - Details (verify with `treasury/scripts/list_proposals.py`):
    ```
    ====================================================================================================
    🔍 Contract Configuration:
      Name:                  Enigma-Treasury-v1.1-20260505
      Target NetUID:         63
      Treasury Admin:        0xa5ACB66F2e1e5307cd536F7fd346b0301b7bC0Ca
      TAO Limit:             1,000.0000 TAO
      Alpha Limit:           25,000.0000 Alpha
      ERC20 Limit:           10,000.0000 Tokens
      Limit Reset Period:    172800 seconds (~2 days 0 hrs)
      Success Threshold:     6000 BPS (60.0%)
      Quorum:                5000 BPS (50.0%)
      Proposal Expiration:   14400 blocks (~2 days 0 hrs)
      Voting Delay:          900 blocks (~3 hrs 0 mins)
      Voting Period:         21600 blocks (~3 days 0 hrs)
      Timelock Delay:        86400 seconds (~1 days 0 hrs)
    ====================================================================================================
    ```

The Treasury Wallet is a core component of Subnet 63, implemented as a smart contract on the EVM layer of Bittensor. It serves as the primary funding mechanism for challenges and ecosystem development.

- **Funding**: All miner emissions are directed to the Treasury Wallet, accumulating SN63 Alpha tokens.
- **Governance**: Managed through a Governor contract with timelock delays, voting periods, and quorum requirements to ensure secure fund management.
- **Purpose**: Funds are used to sponsor challenges, reward participants, and support the subnet's growth.
- **Technical Details**: For deployment instructions and contract specifications, see [treasury/README.md](treasury/).

Challenges and prizes will be paid to the winner based upon a proposal and vote by validators.

For current treasury balance and active prize pools, visit the [Enigma page](https://www.qbittensorlabs.com/enigma).

---

## Challenges

### Breaking RSA — Live

Factor large semiprimes to break RSA encryption. Solutions run in Docker containers on validator hardware (NVIDIA RTX PRO 6000, 24 vCPU, 85 GB RAM, `--network none`, `linux/amd64`) with a 4-hour wall time.

See the [Breaking RSA challenge README](workbench/challenges/breaking_rsa/README.md) for details, and the [Miner Guide](qbittensor/miner/README.md) for submission instructions.

### Hardening Quantum Proof — Live

Given a quantum circuit, find the peaked state — the output with a disproportionately high measurement probability. Peaked circuits can serve as quantum proofs: verifiable tests that a real quantum computer can solve but a classical system cannot. If classical solvers can crack them, the proof doesn't hold.

See the [Hardening Quantum Proof challenge README](workbench/challenges/hardening_quantum_proof/README.md) for details, and the [Miner Guide](qbittensor/miner/README.md) for submission instructions.

For current prize pools, milestones, and additional details, visit the [Enigma page](https://www.qbittensorlabs.com/enigma).

---

## Voting Setup

To participate in Treasury Governance and cast votes on proposals, validators must follow the [Validator Onboarding Guide](VALIDATORS.md) which provides setup instructions for an EVM wallet.

- [Validator Voting Guide](VOTING.md)

---

## Validator Setup

For complete validator operator instructions (environment variables, Docker requirements, local database, high-level operation, etc.) see the dedicated guide:

**→ [qbittensor/validator/README.md](qbittensor/validator/README.md)**

### Quick Launch

#### With PM2 (recommended)

```bash
# 1. Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 2. Clone the repository
git clone https://github.com/qbittensor-labs/enigma.git
cd enigma
pip install -e .

# 3. Run the validator with PM2
pm2 start --interpreter .venv/bin/python --name enigma-validator neurons/validator.py -- --netuid 63 --logging.info --wallet.name <your_wallet_name> --wallet.hotkey <your_hotkey>
```

**Note**: Replace `<your_wallet_name>` and `<your_hotkey>` with your Bittensor wallet details (defaults to 'default' if not specified). For localnet testing, add `--subtensor.network local`.

#### Running Without PM2

```bash
python neurons/validator.py --netuid 63 --logging.info --wallet.name <your_wallet_name> --wallet.hotkey <your_hotkey>
```

### GPU Device

To bind the validator to a specific GPU, use the `--neuron.device` flag (see the validator README for details).

## Miner Setup

See the dedicated miner operator guide:

**→ [qbittensor/miner/README.md](qbittensor/miner/README.md)**

## Minimum Compute Requirements

**Validator** (high requirements):
- GPU: RTX PRO 6000 96 GB VRAM
- CPU: 26 cores at 2.5 GHz+
- RAM: 96 GB

**Miner** (lightweight):
- GPU: Not required
- CPU: 1 core minimum at 2.0 GHz+
- RAM: 8 GB minimum

Full details (including storage, OS, and network recommendations) are in [`min_compute.yml`](min_compute.yml).

## Development

For contributor setup, development workflow, testing, and linting instructions (including the required `pip install -e .` step and `pip install -r requirements-dev.txt`), see [CONTRIBUTING.md](CONTRIBUTING.md).
