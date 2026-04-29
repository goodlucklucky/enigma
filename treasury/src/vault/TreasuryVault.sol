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

Contract based upon the Church of Rao Treasury Contract (https://github.com/bittensor-church/treasury-contract).
*/

pragma solidity ^0.8.24;

import { TimelockController } from "@openzeppelin/contracts/governance/TimelockController.sol";

address constant NEURON_PRECOMPILE = 0x0000000000000000000000000000000000000804;

interface INeuron {
    function registerLimit(uint16 netuid, bytes32 hotkey, uint64 limitPrice) external payable;
}

contract TreasuryVault is TimelockController {
    error NeuronRegistrationFailed();
    error RefundError();
    error LimitPriceOverflow();
    error InsufficientValueForBurn(uint256 burned, uint256 provided);

    event NeuronRegistration(uint16 indexed netuid, bytes32 hotkey, address indexed caller);

    constructor(uint256 minDelay, address[] memory proposers, address[] memory executors, address admin)
        TimelockController(minDelay, proposers, executors, admin)
    { }

    function registerNeuron(uint16 netuid, bytes32 hotkey) external payable returns (bool) {
        uint256 limitRao = msg.value / 1e9;
        if (limitRao > type(uint64).max) {
            revert LimitPriceOverflow();
        }
        uint64 limitPrice = uint64(limitRao);

        uint256 balanceBefore = address(this).balance;

        try INeuron(NEURON_PRECOMPILE).registerLimit(netuid, hotkey, limitPrice) { }
        catch {
            revert NeuronRegistrationFailed();
        }

        uint256 consumed = balanceBefore - address(this).balance;
        if (consumed > msg.value) {
            revert InsufficientValueForBurn(consumed, msg.value);
        }
        uint256 refundAmount = msg.value - consumed;

        if (refundAmount > 0) {
            _processRefund(msg.sender, refundAmount);
        }

        emit NeuronRegistration(netuid, hotkey, msg.sender);
        return true;
    }

    function _processRefund(address recipient, uint256 amount) private {
        (bool success,) = payable(recipient).call{ value: amount }("");
        if (!success) {
            revert RefundError();
        }
    }
}