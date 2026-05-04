# Treasury Wallet

## Implementation

The Treasury Wallet utilizes a dual-contract pattern: the `TreasuryController` acts as the governance brain handling proposals, voting, and limits, while the `TreasuryVault` acts as the timelocked executor and asset holder. Parts of these contracts are based upon the Church of Rao Treasury Contract (https://github.com/bittensor-church/treasury-contract).

### 1. Roles and Access Control
* **Treasury Admin:** A centrally appointed address that possesses the exclusive right to *create* proposals (whitelist updates, native transfers, ERC20 transfers, and Alpha stake transfers) and move stake between hotkeys to allow wallet consolidation.
* **Trusted Validators:** A whitelisted set of addresses. To participate in governance (voting or canceling proposals), an address must be on this whitelist **and** possess an active validator status on the target subnet (`TARGET_NETUID`). This prevents voting by validators that are not running subnet code (weight copiers).
* **Proposers/Executors (Vault):** Defined during the `TreasuryVault` initialization. The `TreasuryController` will be granted proposer and executor rights on the vault to move proposals through the timelock lifecycle.

### 2. Proposal Mechanisms and Lifecycle
Standard OpenZeppelin Governor proposal functions are explicitly disabled in favor of strictly typed proposal pathways.
* **Permitted Proposal Types:**
    * `Whitelist Update`: Adding or removing addresses from the `_trustedValidators` set.
    * `Native Transfer`: Moving native gas tokens (TAO) out of the vault.
    * `ERC20 Transfer`: Moving standard ERC20 tokens out of the vault.
    * `Alpha Transfer`: Transferring staked Alpha between hotkeys/coldkeys and subnets using the Bittensor Staking V2 precompile (`0x...805`).
* **Lifecycle Overrides:** Proposals must sequentially pass through custom `propose`, `queue`, and `execute` wrappers specific to their type.
* **Cancellation:** Proposals in a `Pending` or `Queued` state can be canceled by either the Treasury Admin or any currently active Trusted Validator.
* **Expiration:** Proposals include a custom expiration state. If a successful proposal is not executed within `proposalExpirationBlocks` after its deadline, it becomes `Expired`.

### 3. Voting and Quorum Logic
Governance is heavily integrated with Bittensor's specific consensus and metagraph state.
* **Stake-Weighted Voting:** Voting power is not determined by an ERC20 token, but by dynamically querying the `BITTENSOR_VOTES_ADDRESS` precompile (`0x...80D`). A voter's total weight is the sum of the voting power of all hotkeys linked to their EVM address via the UID lookup.
* **Dynamic Quorum:** Quorum is not a static number. It is calculated dynamically based on the total voting power of all *currently active* trusted validators on the `TARGET_NETUID`, multiplied by a support threshold (`SUPPORT_THRESHOLD_NUMERATOR / 10000`).
* **Success Threshold:** For a vote to pass, the `forVotes` must meet or exceed a specific percentage of the total votes cast, defined by `SUCCESS_THRESHOLD_BPS` (measured in basis points).

### 4. Rate Limiting and Spending Controls
The `TreasuryController` enforces strict, time-based spending limits to mitigate the risk of draining the vault if a malicious proposal passes.
* **Asset-Specific Limits:** Separate spending caps exist for Native tokens (`TAO_LIMIT`), Alpha tokens (`ALPHA_LIMIT`), and ERC20 tokens (`ERC20_LIMIT`).
* **Time Periods:** Limits reset according to a configured `LIMIT_RESET_PERIOD` (converted from minutes to seconds during initialization). 
* **Execution-Time Enforcement:** Spending limits are checked and updated during the `execute` phase, not the `propose` phase, ensuring that queued but unexecuted proposals don't artificially lock up the period's budget.

### 5. Vault Operations & Native Integrations
The `TreasuryVault` handles the ultimate execution of timelocked payloads and includes custom logic for interacting with Bittensor network mechanics.
* **Neuron Registration:** The vault includes a custom `registerNeuron` function allowing it to interact directly with the `NEURON_PRECOMPILE` (`0x...804`). 
* **Safe Limit Price Handling:** When registering a neuron, it calculates the Rao limit, ensures it prevents `uint64` overflow, tracks the exact amount of native token burned by the precompile, and safely refunds any unspent msg.value back to the caller.

## Testing

### Prerequisites

Localnet must be running:

To run a local Bittensor network for development and testing:

```bash
# Start the localnet
docker run --rm -it \
  --name bittensor-localnet \
  -p 9944:9944 \
  -p 9933:9933 \
  -p 30333:30333 \
  ghcr.io/opentensor/subtensor-localnet:devnet-ready
```

### Localnet Setup

- Subnet must be created and have the following options:
  - `enable_voting_power_tracking` set to `true`
  - `commit_reveal_weights_enabled` set to `false`

- Create wallets, add stake:
  - Validator needs to be registered, EVM wallet created, and Hotkey associated with EVM wallet
  - Miner needs to be registered
  - Malicious miner needs to be registered
  - Create deployment EVM wallet
  - Fund deployment EVM wallet
- Deployment:
  - Deploy contract
  - Associate vault EVM wallet with Hotkey
- Set weights to Vault

### Tests

The `test_treasury_e2e.py` script provides a automated test suite to verify the security boundaries, asset transfers, and lifecycle mechanics of the Treasury Governance EVM contract. It interacts directly with the localnet blockchain to simulate both administrative actions and malicious attack vectors.

```bash
# Run full suite against localnet (default)
python test_treasury_e2e.py
```

## Deployment

### Prerequisites

The subnet must have:
- `enable_voting_power_tracking` set to `true`
- `commit_reveal_weights_enabled` set to `false`

1) Create the EVM wallet for deployment:

```bash
cast wallet new
```

*__Note:__ You must save the private key in a safe place for future operations to work.*

2) Convert public address to ss58

```bash
python ./scripts/evm_to_ss58.py 0x...
```

3) Fund wallet with TAO for deployment cost (0.1 TAO)

```bash
btcli wallet transfer --wallet.name <YOUR_COLDKEY_NAME> --dest <THE_SS58_ALIAS_FROM_STEP_2> --amount <AMOUNT_IN_TAO>
```

4) Deploy the contract

```bash
python deploy_mainnet_treasury.py --rpc-url YOUR_RPC_ENDPOINT --private-key YOUR_KEY --netuid YOUR_NETUID --gov-name CONTRACT_NAME
```
