// SPDX-License-Identifier: MIT

/*
The MIT License (MIT)
Copyright © 2026 qBitTensor Labs

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
documentation files (the “Software”), to deal in the Software without restriction, including without limitation
the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software,
and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of
the Software.

THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO
THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
DEALINGS IN THE SOFTWARE.

Parts of this script are based upon the Church of Rao Treasury Contract (https://github.com/bittensor-church/treasury-contract).
*/

pragma solidity ^0.8.24;

import { Script } from "forge-std/Script.sol";
import { console } from "forge-std/console.sol";
import { TreasuryVault } from "../src/vault/TreasuryVault.sol";
import { TreasuryController } from "../src/controller/TreasuryController.sol";

contract DeployGovernance is Script {
    function run() external {
        uint256 deployerPrivateKey = vm.envUint("PRIVATE_KEY");
        address deployerAddress = vm.addr(deployerPrivateKey);

        uint256 minDelay = vm.envOr("MIN_DELAY", uint256(1));
        uint256 netuid = vm.envOr("NETUID", uint256(2));
        uint256 proposalExpirationBlocks = vm.envOr("PROPOSAL_EXPIRATION", uint256(1000));

        string memory govName = vm.envOr("GOV_NAME", string("BittensorDAO"));
        uint256 votingDelayEnv = vm.envOr("VOTING_DELAY", uint256(0));
        uint256 votingPeriodEnv = vm.envOr("VOTING_PERIOD", uint256(5));
        uint256 proposalThresholdEnv = vm.envOr("PROPOSAL_THRESHOLD", uint256(0));
        uint256 quorumNumeratorEnv = vm.envOr("QUORUM_BPS", uint256(100));
        uint256 successThresholdEnv = vm.envOr("SUCCESS_THRESHOLD_BPS", uint256(6000));

        uint256 taoLimit = vm.envOr("TAO_LIMIT", uint256(1000 ether));
        uint256 alphaLimit = vm.envOr("ALPHA_LIMIT", uint256(5000 * 1e9)); // 9 decimals instead of ether
        uint256 erc20Limit = vm.envOr("ERC20_LIMIT", uint256(10000 ether));
        uint256 resetPeriod = vm.envOr("LIMIT_RESET_PERIOD_MIN", uint256(10080)); // Default: 1 week

        // forge-lint: disable-next-line(unsafe-typecast)
        uint48 votingDelay = uint48(votingDelayEnv);
        // forge-lint: disable-next-line(unsafe-typecast)
        uint32 votingPeriod = uint32(votingPeriodEnv);

        console.log("Starting deployment...");
        console.log("Deployer address:", deployerAddress);
        console.log("NetUID:", netuid);
        console.log("TAO Limit:", taoLimit);
        console.log("Alpha Limit:", alphaLimit);
        console.log("ERC20 Limit:", erc20Limit);
        console.log("Reset Period (Min):", resetPeriod);

        vm.startBroadcast(deployerPrivateKey);

        address[] memory proposers = new address[](0);
        address[] memory executors = new address[](1);
        executors[0] = address(0);

        address adminAddress = vm.envOr("TREASURY_ADMIN", deployerAddress);

        // We use dynamic sizing so we can pass 0 validators if we want to start empty
        address envValidator = vm.envOr("INITIAL_VALIDATOR", address(0));
        address[] memory initialTrustedValidators;

        if (envValidator != address(0)) {
            initialTrustedValidators = new address[](1);
            initialTrustedValidators[0] = envValidator;
        } else {
            initialTrustedValidators = new address[](0);
        }

        TreasuryVault vault = new TreasuryVault(minDelay, proposers, executors, deployerAddress);
        TreasuryController governor = new TreasuryController(
            vault,
            // forge-lint: disable-next-line(unsafe-typecast)
            uint16(netuid),
            govName,
            votingDelay,
            votingPeriod,
            proposalThresholdEnv,
            quorumNumeratorEnv,
            successThresholdEnv,
            proposalExpirationBlocks,
            taoLimit,
            alphaLimit,
            erc20Limit,
            resetPeriod,
            adminAddress,
            initialTrustedValidators
        );

        bytes32 cancellerRole = vault.CANCELLER_ROLE();
        vault.grantRole(cancellerRole, address(governor));

        bytes32 proposerRole = vault.PROPOSER_ROLE();
        vault.grantRole(proposerRole, address(governor));

        bytes32 adminRole = vault.DEFAULT_ADMIN_ROLE();
        vault.renounceRole(adminRole, deployerAddress);

        vm.stopBroadcast();

        console.log("--------------------------------------------------");
        console.log("Vault deployed at:    ", address(vault));
        console.log("Governor deployed at: ", address(governor));
        console.log("--------------------------------------------------");
    }
}
