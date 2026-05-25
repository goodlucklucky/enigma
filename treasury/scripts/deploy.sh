#!/bin/bash

# The MIT License (MIT)
# Copyright © 2026 qBitTensor Labs

# Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
# documentation files (the “Software”), to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all copies or substantial portions of
# the Software.

# THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO
# THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
# THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.

set -euo pipefail

# Load .env first so it can override defaults below
if [ -f .env ]; then
    echo "Loading variables from .env..."
    # Auto-export variables defined in .env while preserving shell comment parsing.
    set -a
    # shellcheck disable=SC1091
    . ./.env
    set +a
fi

# ── Network ──
export RPC_URL="${RPC_URL:-https://lite.chain.opentensor.ai}"

# ── Governance Parameters (12s/block) ──
export NETUID="${NETUID:-63}"
export GOV_NAME="${GOV_NAME:-SN63Treasury}"
export MIN_DELAY="${MIN_DELAY:-86400}"                        # 24 hour timelock (seconds)

export VOTING_DELAY="${VOTING_DELAY:-900}"                    # ~3 hours before voting starts
export VOTING_PERIOD="${VOTING_PERIOD:-21600}"                # ~3 days for voting
export PROPOSAL_THRESHOLD="${PROPOSAL_THRESHOLD:-0}"          # Any validator can propose
export QUORUM_BPS="${QUORUM_BPS:-5000}"                       # 50% of total stake must vote FOR
export SUCCESS_THRESHOLD_BPS="${SUCCESS_THRESHOLD_BPS:-6000}" # 60% of vote must be FOR
export PROPOSAL_EXPIRATION="${PROPOSAL_EXPIRATION:-14400}"    # ~48 hours to queue after vote success

# ── Rate Limits (IMMUTABLE after deployment) ──
export TAO_LIMIT="${TAO_LIMIT:-1000000000000000000000}"           # 1000 TAO per period (in wei)
export ALPHA_LIMIT="${ALPHA_LIMIT:-25000000000000}"               # 25000 Alpha per period (in RAO / 9 decimals)
export ERC20_LIMIT="${ERC20_LIMIT:-10000000000000000000000}"      # 10000 ERC20 per period (in wei)
export LIMIT_RESET_PERIOD_MIN="${LIMIT_RESET_PERIOD_MIN:-2880}"   # 2 days in minutes

# ── Validation ──
if [ -z "${PRIVATE_KEY:-}" ]; then
    echo "ERROR: PRIVATE_KEY environment variable is not set."
    echo "Set it in .env or export it before running this script."
    exit 1
fi

export TREASURY_ADMIN="${TREASURY_ADMIN:-$INITIAL_VALIDATOR}" # Fallback to validator if unset
export INITIAL_VALIDATOR="${INITIAL_VALIDATOR:-}"

# Forces Foundry to ask for 1 thing at a time instead of 100
export ETH_RPC_MAX_BATCH_SIZE=1
# Forces Foundry to wait between requests
export ETH_RPC_MAX_REQUESTS_PER_SECOND=2

echo "========================================================="
echo "  Treasury Deployment — Subnet $NETUID"
echo "========================================================="
echo "  RPC:               $RPC_URL"
echo "  Governor Name:     $GOV_NAME"
echo "  NetUID:            $NETUID"
echo "  Quorum:            $QUORUM_BPS BPS ($(( QUORUM_BPS / 100 ))%)"
echo "  Success Threshold: $SUCCESS_THRESHOLD_BPS BPS ($(( SUCCESS_THRESHOLD_BPS / 100 ))%)"
echo "  Voting Delay:      $VOTING_DELAY blocks (~$(( VOTING_DELAY * 12 / 3600 ))h)"
echo "  Voting Period:     $VOTING_PERIOD blocks (~$(( VOTING_PERIOD * 12 / 3600 ))h)"
echo "  Proposal Exp:      $PROPOSAL_EXPIRATION blocks (~$(( PROPOSAL_EXPIRATION * 12 / 3600 ))h)"
echo "  Timelock Delay:    $MIN_DELAY seconds ($(( MIN_DELAY / 3600 ))h)"
echo "  Limit Reset:       $LIMIT_RESET_PERIOD_MIN minutes (~$(( LIMIT_RESET_PERIOD_MIN / 1440 )) days)"
PERIOD_DAYS=$(echo "scale=2; $LIMIT_RESET_PERIOD_MIN / 1440" | bc)
echo "  TAO Limit/${PERIOD_DAYS}d:   $(echo "scale=0; $TAO_LIMIT / 1000000000000000000" | bc) TAO"
echo "  Alpha Limit/${PERIOD_DAYS}d: $(echo "scale=0; $ALPHA_LIMIT / 1000000000" | bc) Alpha"
echo "========================================================="
echo ""

# Skip confirmation if SKIP_CONFIRMATION is set to 1 or true
if [[ "${SKIP_CONFIRMATION:-0}" == "1" || "${SKIP_CONFIRMATION:-}" == "true" ]]; then
    echo "Skipping confirmation due to SKIP_CONFIRMATION=${SKIP_CONFIRMATION}"
else
    read -p "Proceed with deployment? (y/N) " confirm
    if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
        echo "Deployment cancelled."
        exit 0
    fi
fi

forge script scripts/Deploy.s.sol:DeployGovernance \
    --rpc-url "$RPC_URL" \
    --broadcast \
    --legacy \
    --skip-simulation \
    --slow \
    -vvvv

echo "========================================================="
echo "  Deployment complete."
echo "  SAVE THE VAULT AND GOVERNOR ADDRESSES FROM ABOVE."
echo "========================================================="
