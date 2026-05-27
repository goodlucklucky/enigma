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
from sqlalchemy import Column, String, DateTime, func
from ..base import Base

logger = logging.getLogger(__name__)


class ChallengeSolution(Base):
    __tablename__ = "challenge_solutions"

    id = Column(String(50), primary_key=True, default=lambda: str(uuid.uuid4()))
    challenge_validation_solution_id = Column(String(50))
    container_id = Column(String(100), nullable=False)
    container_name = Column(String(100), nullable=False)
    image_id = Column(String(100), nullable=False)
    challenge_id = Column(String(100), nullable=True)
    challenge_milestone_id = Column(String(100), nullable=False)
    absolute_path_to_solution = Column(String(100), nullable=False)
    submission_id = Column(String(100), nullable=False)
    solution_status = Column(String(100), nullable=False)
    tx_hash = Column(String(100), nullable=False, unique=True)
    miner_hotkey = Column(String(100), nullable=False)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<ChallengeSolution(challenge_validation_solution_id='{self.challenge_validation_solution_id}', container_id='{self.container_id}', container_name='{self.container_name}', challenge_milestone_id='{self.challenge_milestone_id}', absolute_path_to_solution='{self.absolute_path_to_solution}', submission_id='{self.submission_id}', created_at='{self.created_at}')>"

    def __str__(self):
        return self.__repr__()


class MinerMaintenanceIncentive(Base):
    __tablename__ = "miner_maintenance_incentives"

    id = Column(String(50), primary_key=True, default=lambda: str(uuid.uuid4()))
    miner_hotkey = Column(String(100), nullable=False)
    challenge_milestone_id = Column(String(100), nullable=False)
    tx_hash = Column(String(100), nullable=False, unique=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<MinerMaintenanceIncentive(miner_hotkey='{self.miner_hotkey}', challenge_milestone_id='{self.challenge_milestone_id}', tx_hash='{self.tx_hash}', created_at='{self.created_at}')>"

    def __str__(self):
        return self.__repr__()
