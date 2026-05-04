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

Parts of this contract are based upon the Church of Rao Treasury Contract (https://github.com/bittensor-church/treasury-contract).
*/

pragma solidity ^0.8.24;

import { Governor } from "@openzeppelin/contracts/governance/Governor.sol";
import { GovernorSettings } from "@openzeppelin/contracts/governance/extensions/GovernorSettings.sol";
import { GovernorStorage } from "@openzeppelin/contracts/governance/extensions/GovernorStorage.sol";
import { GovernorTimelockControl } from "@openzeppelin/contracts/governance/extensions/GovernorTimelockControl.sol";
import { TimelockController } from "@openzeppelin/contracts/governance/TimelockController.sol";
import { IERC20 } from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import { IGovernor } from "@openzeppelin/contracts/governance/IGovernor.sol";
import { EnumerableSet } from "@openzeppelin/contracts/utils/structs/EnumerableSet.sol";

enum ProposalType {
    Whitelist,
    Transfer
}

struct LookupItem {
    uint16 uid;
    uint64 blockAssociated;
}

interface IUidLookup {
    function uidLookup(uint16 netuid, address evmAddress, uint16 limit) external view returns (LookupItem[] memory);
}

interface IMetagraph {
    function getValidatorStatus(uint16 netuid, uint16 uid) external view returns (bool);
    function getHotkey(uint16 netuid, uint16 uid) external view returns (bytes32);
}

interface IBittensorVotes {
    function getVotingPower(uint16 netuid, bytes32 hotkey) external view returns (uint256);
}

interface IStakingV2 {
    function transferStake(
        bytes32 destinationColdkey,
        bytes32 hotkey,
        uint256 originNetuid,
        uint256 destinationNetuid,
        uint256 amountAlpha
    ) external;
}

contract TreasuryController is Governor, GovernorSettings, GovernorTimelockControl, GovernorStorage {
    address constant BITTENSOR_VOTES_ADDRESS = 0x000000000000000000000000000000000000080D;
    address constant METAGRAPH_ADDRESS = 0x0000000000000000000000000000000000000802;
    address constant UID_LOOKUP_ADDRESS = 0x0000000000000000000000000000000000000806;
    address constant STAKING_V2_ADDRESS = 0x0000000000000000000000000000000000000805;

    uint16 public immutable TARGET_NETUID;
    uint256 public immutable SUPPORT_THRESHOLD_NUMERATOR;
    uint256 public immutable SUCCESS_THRESHOLD_BPS;

    uint256 public immutable TAO_LIMIT;
    uint256 public immutable ALPHA_LIMIT;
    uint256 public immutable ERC20_LIMIT;
    uint256 public immutable LIMIT_RESET_PERIOD;

    uint256 public proposalExpirationBlocks;
    address public immutable treasuryAdmin;

    mapping(uint256 => ProposalType) public proposalTypes;

    struct ProposalTallies {
        uint256 forVotes;
        uint256 againstVotes;
        mapping(address => bool) hasVoted; 
    }

    mapping(uint256 => ProposalTallies) private _proposalTallies;
    mapping(uint256 => mapping(bytes32 => uint256)) public periodSpent;

    using EnumerableSet for EnumerableSet.AddressSet;
    EnumerableSet.AddressSet private _trustedValidators;

    event TrustedValidatorUpdated(address indexed validator, bool trusted);

    modifier onlyTreasuryAdmin() {
        require(msg.sender == treasuryAdmin, "Only the treasury admin can propose");
        _;
    }

    modifier onlyAdminOrWhitelisted() {
        require(
            msg.sender == treasuryAdmin || 
            (_trustedValidators.contains(msg.sender) && _hasActiveValidatorStatus(msg.sender)), 
            "Not admin or whitelisted/active"
        );
        _;
    }

    constructor(
        TimelockController _timelock,
        uint16 _netuid,
        string memory _name,
        uint48 _initialVotingDelay,
        uint32 _initialVotingPeriod,
        uint256 _initialProposalThreshold,
        uint256 _supportThresholdNumerator,
        uint256 _successThresholdBps,
        uint256 _proposalExpirationBlocks,
        uint256 _taoLimit,
        uint256 _alphaLimit,
        uint256 _erc20Limit,
        uint256 _limitResetPeriodMinutes,
        address _admin,
        address[] memory _initialTrustedValidators
    )
        Governor(_name)
        GovernorSettings(_initialVotingDelay, _initialVotingPeriod, _initialProposalThreshold)
        GovernorTimelockControl(_timelock)
    {
        treasuryAdmin = _admin;
        TARGET_NETUID = _netuid;
        SUPPORT_THRESHOLD_NUMERATOR = _supportThresholdNumerator;
        SUCCESS_THRESHOLD_BPS = _successThresholdBps;
        proposalExpirationBlocks = _proposalExpirationBlocks;

        TAO_LIMIT = _taoLimit;
        ALPHA_LIMIT = _alphaLimit;
        ERC20_LIMIT = _erc20Limit;
        LIMIT_RESET_PERIOD = _limitResetPeriodMinutes * 60;

        for (uint256 i = 0; i < _initialTrustedValidators.length; i++) {
            if (_trustedValidators.add(_initialTrustedValidators[i])) {
                emit TrustedValidatorUpdated(_initialTrustedValidators[i], true);
            }
        }
    }

    // ==========================================
    // PROPOSAL FUNCTIONS
    // ==========================================

    function proposeUpdateTrustedValidators(
        address[] memory validators,
        bool[] memory trusted,
        string memory description
    ) external onlyTreasuryAdmin returns (uint256) {
        require(validators.length == trusted.length, "Length mismatch");

        address[] memory targets = new address[](1);
        uint256[] memory values = new uint256[](1);
        bytes[] memory calldatas = new bytes[](1);

        targets[0] = address(this);
        values[0] = 0;
        calldatas[0] = abi.encodeWithSelector(this.updateTrustedValidators.selector, validators, trusted);

        uint256 proposalId = super.propose(targets, values, calldatas, description);
        proposalTypes[proposalId] = ProposalType.Whitelist;
        return proposalId;
    }

    function proposeNativeTransfer(address recipient, uint256 amount, string memory description)
        external onlyTreasuryAdmin returns (uint256)
    {
        (address[] memory targets, uint256[] memory values, bytes[] memory calldatas) = _buildNativePayload(recipient, amount);
        uint256 proposalId = super.propose(targets, values, calldatas, description);
        proposalTypes[proposalId] = ProposalType.Transfer;
        return proposalId;
    }

    function proposeERC20Transfer(address token, address recipient, uint256 amount, string memory description)
        external onlyTreasuryAdmin returns (uint256)
    {
        (address[] memory targets, uint256[] memory values, bytes[] memory calldatas) = _buildERC20Payload(token, recipient, amount);
        uint256 proposalId = super.propose(targets, values, calldatas, description);
        proposalTypes[proposalId] = ProposalType.Transfer;
        return proposalId;
    }

    function proposeAlphaTransfer(
        bytes32 destinationColdkey, bytes32 hotkey, uint16 originNetuid, uint16 destinationNetuid, uint256 amount, string memory description
    ) external onlyTreasuryAdmin returns (uint256) {
        (address[] memory targets, uint256[] memory values, bytes[] memory calldatas) =
            _buildAlphaPayload(destinationColdkey, hotkey, originNetuid, destinationNetuid, amount);
        uint256 proposalId = super.propose(targets, values, calldatas, description);
        proposalTypes[proposalId] = ProposalType.Transfer;
        return proposalId;
    }

    // ==========================================
    // QUEUE WRAPPERS
    // ==========================================

    function queueWhitelistUpdate(address[] memory validators, bool[] memory trusted, string memory description) 
        external returns (uint256) 
    {
        address[] memory targets = new address[](1);
        uint256[] memory values = new uint256[](1);
        bytes[] memory calldatas = new bytes[](1);
        targets[0] = address(this);
        values[0] = 0;
        calldatas[0] = abi.encodeWithSelector(this.updateTrustedValidators.selector, validators, trusted);
        return super.queue(targets, values, calldatas, keccak256(bytes(description)));
    }

    function queueNativeTransfer(address recipient, uint256 amount, string memory description) external returns (uint256) {
        (address[] memory targets, uint256[] memory values, bytes[] memory calldatas) = _buildNativePayload(recipient, amount);
        return super.queue(targets, values, calldatas, keccak256(bytes(description)));
    }

    function queueERC20Transfer(address token, address recipient, uint256 amount, string memory description) external returns (uint256) {
        (address[] memory targets, uint256[] memory values, bytes[] memory calldatas) = _buildERC20Payload(token, recipient, amount);
        return super.queue(targets, values, calldatas, keccak256(bytes(description)));
    }

    function queueAlphaTransfer(
        bytes32 destinationColdkey, bytes32 hotkey, uint16 originNetuid, uint16 destinationNetuid, uint256 amount, string memory description
    ) external returns (uint256) {
        (address[] memory targets, uint256[] memory values, bytes[] memory calldatas) = 
            _buildAlphaPayload(destinationColdkey, hotkey, originNetuid, destinationNetuid, amount);
        return super.queue(targets, values, calldatas, keccak256(bytes(description)));
    }

    // ==========================================
    // EXECUTE WRAPPERS
    // ==========================================

    function executeWhitelistUpdate(address[] memory validators, bool[] memory trusted, string memory description) 
        external payable returns (uint256) 
    {
        address[] memory targets = new address[](1);
        uint256[] memory values = new uint256[](1);
        bytes[] memory calldatas = new bytes[](1);
        targets[0] = address(this);
        values[0] = 0;
        calldatas[0] = abi.encodeWithSelector(this.updateTrustedValidators.selector, validators, trusted);
        return super.execute(targets, values, calldatas, keccak256(bytes(description)));
    }

    function executeNativeTransfer(address recipient, uint256 amount, string memory description)
        external
        payable
        returns (uint256)
    {
        _updateLimit(bytes32(0), amount, TAO_LIMIT);
        (address[] memory targets, uint256[] memory values, bytes[] memory calldatas) =
            _buildNativePayload(recipient, amount);
        return super.execute(targets, values, calldatas, keccak256(bytes(description)));
    }

    function executeERC20Transfer(address token, address recipient, uint256 amount, string memory description)
        external
        payable
        returns (uint256)
    {
        _updateLimit(bytes32(uint256(uint160(token))), amount, ERC20_LIMIT);
        (address[] memory targets, uint256[] memory values, bytes[] memory calldatas) =
            _buildERC20Payload(token, recipient, amount);
        return super.execute(targets, values, calldatas, keccak256(bytes(description)));
    }

    function executeAlphaTransfer(
        bytes32 destinationColdkey,
        bytes32 hotkey,
        uint16 originNetuid,
        uint16 destinationNetuid,
        uint256 amount,
        string memory description
    ) external payable returns (uint256) {
        _updateLimit(keccak256(abi.encode("alpha", originNetuid)), amount, ALPHA_LIMIT);
        (address[] memory targets, uint256[] memory values, bytes[] memory calldatas) =
            _buildAlphaPayload(destinationColdkey, hotkey, originNetuid, destinationNetuid, amount);
        return super.execute(targets, values, calldatas, keccak256(bytes(description)));
    }

    // ==========================================
    // CANCELLATION FUNCTIONS
    // ==========================================

    function _executeCancel(address[] memory targets, uint256[] memory values, bytes[] memory calldatas, string memory description) 
        internal returns (uint256) 
    {
        bytes32 descriptionHash = keccak256(bytes(description));
        uint256 proposalId = hashProposal(targets, values, calldatas, descriptionHash);
        ProposalState s = state(proposalId);
        require(s == ProposalState.Pending || s == ProposalState.Queued, "Can only cancel Pending or Queued proposals");
        return _cancel(targets, values, calldatas, descriptionHash);
    }

    function cancelWhitelistUpdate(address[] memory validators, bool[] memory trusted, string memory description) 
        external onlyAdminOrWhitelisted returns (uint256) 
    {
        address[] memory targets = new address[](1);
        uint256[] memory values = new uint256[](1);
        bytes[] memory calldatas = new bytes[](1);
        targets[0] = address(this);
        values[0] = 0;
        calldatas[0] = abi.encodeWithSelector(this.updateTrustedValidators.selector, validators, trusted);
        return _executeCancel(targets, values, calldatas, description);
    }

    function cancelNativeTransfer(address recipient, uint256 amount, string memory description)
        external onlyAdminOrWhitelisted returns (uint256)
    {
        (address[] memory targets, uint256[] memory values, bytes[] memory calldatas) = _buildNativePayload(recipient, amount);
        return _executeCancel(targets, values, calldatas, description);
    }

    function cancelERC20Transfer(address token, address recipient, uint256 amount, string memory description)
        external onlyAdminOrWhitelisted returns (uint256)
    {
        (address[] memory targets, uint256[] memory values, bytes[] memory calldatas) = _buildERC20Payload(token, recipient, amount);
        return _executeCancel(targets, values, calldatas, description);
    }

    function cancelAlphaTransfer(
        bytes32 destinationColdkey, bytes32 hotkey, uint16 originNetuid, uint16 destinationNetuid, uint256 amount, string memory description
    ) external onlyAdminOrWhitelisted returns (uint256) {
        (address[] memory targets, uint256[] memory values, bytes[] memory calldatas) =
            _buildAlphaPayload(destinationColdkey, hotkey, originNetuid, destinationNetuid, amount);
        return _executeCancel(targets, values, calldatas, description);
    }

    // ==========================================
    // VOTING & TALLYING
    // ==========================================

    function _requireValidVoter(uint256 proposalId, address account) internal view {
        if (proposalTypes[proposalId] == ProposalType.Whitelist) {
            if (_trustedValidators.length() == 0) {
                require(account == treasuryAdmin, "Only admin can vote on empty whitelist");
            } else {
                require(_trustedValidators.contains(account) && _hasActiveValidatorStatus(account), "Not an active, trusted validator");
            }
        } else {
            require(_trustedValidators.contains(account) && _hasActiveValidatorStatus(account), "Not an active, trusted validator");
        }
    }
        
    function _castVote(uint256 proposalId, address account, uint8 support, string memory reason) 
        internal virtual override returns (uint256) 
    {
        _requireValidVoter(proposalId, account);
        return super._castVote(proposalId, account, support, reason);
    }

    function _castVote(uint256 proposalId, address account, uint8 support, string memory reason, bytes memory params) 
        internal virtual override returns (uint256) 
    {
        _requireValidVoter(proposalId, account);
        return super._castVote(proposalId, account, support, reason, params);
    }

    function _countVote(
        uint256 proposalId,
        address account,
        uint8 support,
        uint256 weight,
        bytes memory /*params*/
    ) internal virtual override {
        require(support <= 1, "Invalid vote support");
        require(!_proposalTallies[proposalId].hasVoted[account], "Already voted");

        _proposalTallies[proposalId].hasVoted[account] = true;

        if (support == 1) {
            _proposalTallies[proposalId].forVotes += weight;
        } else {
            _proposalTallies[proposalId].againstVotes += weight;
        }
    }

    function quorum(uint256 /*timepoint*/) public view virtual override returns (uint256) {
        uint256 totalWhitelistPower = 0;
        address[] memory validators = _trustedValidators.values();
        
        for (uint256 i = 0; i < validators.length; i++) {
            // Only count them if they are actively registered on the subnet
            if (_hasActiveValidatorStatus(validators[i])) {
                bytes32[] memory hotkeys = getHotkeysForAddress(validators[i]);
                for (uint256 j = 0; j < hotkeys.length; j++) {
                    totalWhitelistPower += IBittensorVotes(BITTENSOR_VOTES_ADDRESS).getVotingPower(TARGET_NETUID, hotkeys[j]);
                }
            }
        }
        
        return (totalWhitelistPower * SUPPORT_THRESHOLD_NUMERATOR) / 10000;
    }

    function hasVoted(uint256 proposalId, address account) public view virtual override returns (bool) {
        return _proposalTallies[proposalId].hasVoted[account];
    }

    function _quorumReached(uint256 proposalId) internal view virtual override returns (bool) {
        if (proposalTypes[proposalId] == ProposalType.Whitelist && _trustedValidators.length() == 0) {
            return _proposalTallies[proposalId].forVotes >= 1;
        }

        uint256 totalVotes = _proposalTallies[proposalId].forVotes + _proposalTallies[proposalId].againstVotes;
        return totalVotes >= quorum(proposalSnapshot(proposalId));
    }

    function _voteSucceeded(uint256 proposalId) internal view virtual override returns (bool) {
        uint256 forVotes = _proposalTallies[proposalId].forVotes;
        uint256 totalVotes = forVotes + _proposalTallies[proposalId].againstVotes;
        
        if (totalVotes == 0) return false;
        return (forVotes * 10000) >= (totalVotes * SUCCESS_THRESHOLD_BPS);
    }

    function _getVotes(address account, uint256 /*timepoint*/, bytes memory /*params*/) 
        internal view virtual override returns (uint256) 
    {
        if (_trustedValidators.length() == 0 && account == treasuryAdmin) {
            return 1; 
        }

        bytes32[] memory hotkeys = getHotkeysForAddress(account);
        uint256 totalPower = 0;
        
        for (uint256 i = 0; i < hotkeys.length; i++) {
            totalPower += IBittensorVotes(BITTENSOR_VOTES_ADDRESS).getVotingPower(TARGET_NETUID, hotkeys[i]);
        }
        return totalPower;
    }

    // ==========================================
    // INTERNAL UTILITIES
    // ==========================================

    function updateTrustedValidators(address[] calldata validators, bool[] calldata trusted) external onlyGovernance {
        require(validators.length == trusted.length, "Length mismatch");
        for (uint256 i = 0; i < validators.length; i++) {
            if (trusted[i]) {
                if (_trustedValidators.add(validators[i])) {
                    emit TrustedValidatorUpdated(validators[i], true);
                }
            } else {
                if (_trustedValidators.remove(validators[i])) {
                    emit TrustedValidatorUpdated(validators[i], false);
                }
            }
        }
    }

    function _hasActiveValidatorStatus(address account) internal view returns (bool) {
        LookupItem[] memory items = IUidLookup(UID_LOOKUP_ADDRESS).uidLookup(TARGET_NETUID, account, type(uint16).max);
        for (uint256 i = 0; i < items.length; i++) {
            if (IMetagraph(METAGRAPH_ADDRESS).getValidatorStatus(TARGET_NETUID, items[i].uid)) {
                return true;
            }
        }
        return false;
    }

    function getHotkeysForAddress(address evmAddress) public view returns (bytes32[] memory) {
        LookupItem[] memory items = IUidLookup(UID_LOOKUP_ADDRESS).uidLookup(TARGET_NETUID, evmAddress, type(uint16).max);
        bytes32[] memory hotkeys = new bytes32[](items.length);
        for (uint256 i = 0; i < items.length; i++) {
            hotkeys[i] = IMetagraph(METAGRAPH_ADDRESS).getHotkey(TARGET_NETUID, items[i].uid);
        }
        return hotkeys;
    }

    function _updateLimit(bytes32 assetId, uint256 amount, uint256 limit) internal {
        uint256 currentPeriod = block.timestamp / LIMIT_RESET_PERIOD;
        require(periodSpent[currentPeriod][assetId] + amount <= limit, "Limit exceeded");
        periodSpent[currentPeriod][assetId] += amount;
    }

    function isTrustedValidator(address account) internal view returns (bool) {
        return _trustedValidators.contains(account);
    }

    // ==========================================
    // ONE-STEP PAYLOAD BUILDERS
    // ==========================================

    function _buildNativePayload(address recipient, uint256 amount)
        internal
        pure
        returns (address[] memory targets, uint256[] memory values, bytes[] memory calldatas)
    {
        targets = new address[](1);
        values = new uint256[](1);
        calldatas = new bytes[](1);
        targets[0] = recipient;
        values[0] = amount;
        calldatas[0] = "";
    }

    function _buildERC20Payload(address token, address recipient, uint256 amount)
        internal
        pure
        returns (address[] memory targets, uint256[] memory values, bytes[] memory calldatas)
    {
        targets = new address[](1);
        values = new uint256[](1);
        calldatas = new bytes[](1);
        targets[0] = token;
        values[0] = 0;
        calldatas[0] = abi.encodeWithSelector(IERC20.transfer.selector, recipient, amount);
    }

    function _buildAlphaPayload(
        bytes32 destinationColdkey,
        bytes32 hotkey,
        uint16 originNetuid,
        uint16 destinationNetuid,
        uint256 amount
    ) internal pure returns (address[] memory targets, uint256[] memory values, bytes[] memory calldatas) {
        targets = new address[](1);
        values = new uint256[](1);
        calldatas = new bytes[](1);
        targets[0] = STAKING_V2_ADDRESS;
        values[0] = 0;
        calldatas[0] = abi.encodeWithSelector(
            IStakingV2.transferStake.selector,
            destinationColdkey,
            hotkey,
            uint256(originNetuid),
            uint256(destinationNetuid),
            amount
        );
    }

    // ==========================================
    // GOVERNOR OVERRIDES
    // ==========================================

    function propose(address[] memory, uint256[] memory, bytes[] memory, string memory) 
        public pure override(Governor) returns (uint256) 
    {
        revert("Use specific propose functions");
    }

    function queue(address[] memory, uint256[] memory, bytes[] memory, bytes32) 
        public pure override(Governor) returns (uint256) 
    {
        revert("Use specific queue functions");
    }

    function execute(address[] memory, uint256[] memory, bytes[] memory, bytes32) 
        public payable override(Governor) returns (uint256) 
    {
        revert("Use specific execute functions");
    }

    function state(uint256 proposalId) public view override(Governor, GovernorTimelockControl) returns (ProposalState) {
        ProposalState currentState = super.state(proposalId);
        if (currentState == ProposalState.Succeeded) {
            if (block.number > proposalDeadline(proposalId) + proposalExpirationBlocks) {
                return ProposalState.Expired;
            }
        }
        return currentState;
    }

    function clock() public view virtual override returns (uint48) { return uint48(block.number); }
    function CLOCK_MODE() public view virtual override returns (string memory) { return "mode=blocknumber&from=default"; }
    function COUNTING_MODE() public pure virtual override returns (string memory) { return "support=bravo&quorum=for,against"; }
    function votingDelay() public view override(Governor, GovernorSettings) returns (uint256) { return super.votingDelay(); }
    function votingPeriod() public view override(Governor, GovernorSettings) returns (uint256) { return super.votingPeriod(); }
    function proposalThreshold() public view override(Governor, GovernorSettings) returns (uint256) { return super.proposalThreshold(); }
    function proposalNeedsQueuing(uint256 proposalId) public view override(Governor, GovernorTimelockControl) returns (bool) { return super.proposalNeedsQueuing(proposalId); }
    function _queueOperations(uint256 proposalId, address[] memory targets, uint256[] memory values, bytes[] memory calldatas, bytes32 descriptionHash) internal override(Governor, GovernorTimelockControl) returns (uint48) { return super._queueOperations(proposalId, targets, values, calldatas, descriptionHash); }
    function _executeOperations(uint256 proposalId, address[] memory targets, uint256[] memory values, bytes[] memory calldatas, bytes32 descriptionHash) internal override(Governor, GovernorTimelockControl) { super._executeOperations(proposalId, targets, values, calldatas, descriptionHash); }
    function _cancel(address[] memory targets, uint256[] memory values, bytes[] memory calldatas, bytes32 descriptionHash) internal override(Governor, GovernorTimelockControl) returns (uint256) { return super._cancel(targets, values, calldatas, descriptionHash); }
    function _executor() internal view override(Governor, GovernorTimelockControl) returns (address) { return super._executor(); }
    function _propose(
        address[] memory targets,
        uint256[] memory values,
        bytes[] memory calldatas,
        string memory description,
        address proposer
    ) internal virtual override(Governor, GovernorStorage) returns (uint256) {
        return super._propose(targets, values, calldatas, description, proposer);
    }
}
