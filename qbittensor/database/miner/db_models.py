# The MIT License (MIT)
# Copyright © 2026 qBitTensor Labs
#
# Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
# documentation files (the “Software”), to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all copies or substantial portions of
# the Software.
#
# THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO
# THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
# THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.

import logging
import uuid
from sqlalchemy import Column, String, DateTime, ForeignKey, func
from ..base import Base


logger = logging.getLogger(__name__)

class MinerSubmission(Base):
    __tablename__ = "miner_submissions"

    id = Column(String(50), primary_key=True, default=lambda: str(uuid.uuid4()))
    challenge_milestone_id = Column(String(100), nullable=False)
    upload_id = Column(String(100), nullable=False)
    miner_hotkey = Column(String(100), nullable=False)
    tx_hash = Column(String(100), nullable=False, unique=True)
    transfer_block_hash = Column(String(128), nullable=True)
    transfer_from_ss58 = Column(String(100), nullable=True)
    transfer_to_ss58 = Column(String(100), nullable=True)
    transfer_amount_rao = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    submitted_at = Column(DateTime, nullable=True)

    def __repr__(self):
        return (
            "MinerSubmission("
            f"upload_id='{self.upload_id}', "
            f"challenge_milestone_id='{self.challenge_milestone_id}', "
            f"miner_hotkey='{self.miner_hotkey}')"
        )
    def __str__(self):
        return self.__repr__()


class MinerSubmissionStatus(Base):
    __tablename__ = "miner_submission_statuses"

    id = Column(String(50), primary_key=True, default=lambda: str(uuid.uuid4()))
    challenge_milestone_id = Column(String(100), nullable=False)
    solution_status = Column(String(100), nullable=False)
    validator_hotkey = Column(String(100), nullable=False)
    tx_hash = Column(
        String(100),
        ForeignKey("miner_submissions.tx_hash"),
        nullable=False,
    )
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    def __repr__(self):
        return (
            "MinerSubmissionStatus("
            f"tx_hash='{self.tx_hash}', "
            f"challenge_milestone_id='{self.challenge_milestone_id}', "
            f"solution_status='{self.solution_status}', "
            f"validator_hotkey='{self.validator_hotkey}')"
        )
    def __str__(self):
        return self.__repr__()
