# Validator Onboarding Guide

To participate in Treasury Governance and cast votes on proposals, validators must associate an EVM wallet with their Bittensor hotkey. This allows the governance smart contracts to verify your voting power.

## 1. Environment Setup

Before you begin, you need to set up your environment with the necessary tools and dependencies. All commands should be run from the root of this repository.

### Python Dependencies

Follow the [Installation & Setup](README.md#installation--setup-validator-only) section of the main README to install the package (including `bittensor[cli]`, which provides the `btcli` command).

### Foundry Toolchain

The `cast` command-line tool, part of the Foundry suite, is required for creating your wallet.

```bash
curl -L https://foundry.paradigm.xyz | bash
export PATH="$HOME/.foundry/bin:$PATH"
foundryup
```
*Note: You may need to add the `export` command to your shell's configuration file (e.g., `~/.bashrc` or `~/.zshrc`) and restart your terminal for the `cast` command to be available system-wide.*

### Network Access

The treasury scripts default to the public lite endpoint `https://lite.chain.opentensor.ai`. For higher reliability or if you hit rate limits during busy periods, you can obtain a dedicated RPC URL from a provider like [Nodies](https://www.nodies.app) and pass it via the `--rpc` / `--rpc-url` flag.

### NVIDIA GPU
To run the validator on a NVIDIA GPU, you need to have the NVIDIA Container Toolkit installed and the NVIDIA driver installed. See [GPU_README.md](qbittensor/validator/utils/gpu_verification/GPU_README.md) for more details.

## 2. Create an EVM Wallet

You need a dedicated EVM wallet to interact with the Treasury smart contracts. You can create a new wallet using `cast`:

```bash
cast wallet new
```

**Important:** Save the `Address` and `Private Key` in a secure location. You will need the private key to sign transactions when voting.

## 3. Fund Your EVM Wallet

To cast votes, your EVM wallet must have TAO to pay for gas fees. We recommend maintaining a balance of **0.5–1 TAO** for regular voting.

First, convert your new EVM address to its corresponding Bittensor SS58 address:

```bash
python3 treasury/scripts/evm_to_ss58.py <YOUR_EVM_ADDRESS>
```

Next, transfer TAO from your coldkey to the generated SS58 address:

```bash
btcli wallet transfer --wallet.name <YOUR_COLDKEY> --dest <THE_SS58_ADDRESS> --amount 1
```

## 4. Associate the EVM Wallet with Your Hotkey

You must cryptographically link your EVM wallet to your Bittensor hotkey so the smart contract recognizes your voting power.

Run the `associate_evm.py` script:

```bash
python3 treasury/scripts/associate_evm.py \
  --private-key <YOUR_EVM_PRIVATE_KEY> \
  --hotkey <YOUR_HOTKEY_SS58_ADDRESS> \
  --hotkey-private-key "<YOUR_HOTKEY_PRIVATE_KEY>" \
  --netuid <SUBNET_ID>
```

The script defaults to `https://lite.chain.opentensor.ai`. Pass `--rpc-url YOUR_RPC` if needed.

*Note: The script requires your hotkey's seed phrase to sign a message proving ownership of the hotkey, and your EVM private key to submit the transaction to the EVM network.*

## 5. Notify the Treasury Admin

Once your EVM wallet is created and associated, provide your **EVM Address** (the `0x...` public address, NOT your private key) to the Treasury Admin.

The Admin will need to create a governance proposal to add your EVM address to the Trusted Validators whitelist. Once that proposal passes and is executed, you will be authorized to cast votes on future proposals.
