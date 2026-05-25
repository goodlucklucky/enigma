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

import queue
from unittest.mock import Mock, patch

import numpy as np
import pytest

from qbittensor.utils.services.telemetry import TelemetryService


@pytest.fixture
def telemetry_service():
    request_manager = Mock()
    with patch.object(TelemetryService, "_start_background_worker"):
        service = TelemetryService(request_manager=request_manager)
    return service


class TestTelemetryServiceToPythonScalar:
    def test_none_and_primitives(self, telemetry_service):
        assert telemetry_service._to_python_scalar(None) is None
        assert telemetry_service._to_python_scalar(42) == 42
        assert telemetry_service._to_python_scalar(3.14) == 3.14
        assert telemetry_service._to_python_scalar("hello") == "hello"

    def test_numpy_scalars(self, telemetry_service):
        assert telemetry_service._to_python_scalar(np.int64(7)) == 7
        assert telemetry_service._to_python_scalar(np.float64(1.5)) == 1.5

    def test_object_with_item_method(self, telemetry_service):
        obj = Mock()
        obj.item.return_value = 99
        assert telemetry_service._to_python_scalar(obj) == 99

    def test_fallback_to_str(self, telemetry_service):
        assert telemetry_service._to_python_scalar({"a": 1}) == "{'a': 1}"


class TestTelemetryServiceEnqueue:
    def test_enqueue_numeric_datapoint(self, telemetry_service):
        ok = telemetry_service._enqueue_datapoint("cpu_usage", "2026-01-01T00:00:00Z", 42.0)
        assert ok is True
        item = telemetry_service.queue.get_nowait()
        assert item["type"] == "cpu_usage"
        assert item["value"] == 42.0

    def test_enqueue_string_datapoint(self, telemetry_service):
        ok = telemetry_service._enqueue_datapoint("heartbeat_version", "2026-01-01T00:00:00Z", "1.0.0")
        assert ok is True
        item = telemetry_service.queue.get_nowait()
        assert item["value"] == "1.0.0"

    def test_enqueue_drops_when_queue_full(self, telemetry_service):
        telemetry_service.max_queue_size = 1
        telemetry_service.queue = queue.Queue(maxsize=1)
        telemetry_service.queue.put_nowait({"type": "x", "timestamp": "t", "value": 1})
        ok = telemetry_service._enqueue_datapoint("overflow", "2026-01-01T00:00:00Z", 1.0)
        assert ok is False


class TestTelemetryServiceFlushBatch:
    def test_flush_batch_posts_datapoints(self, telemetry_service):
        telemetry_service.queue.task_done = Mock()
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.raise_for_status = Mock()
        telemetry_service.request_manager.post = Mock(return_value=mock_response)

        batch = [
            {
                "type": "heartbeat_version",
                "timestamp": "2026-01-01T00:00:00Z",
                "value": "1.2.3",
                "miner_uid": None,
                "miner_hotkey": None,
                "attributes": None,
            }
        ]
        telemetry_service._flush_batch(batch)

        telemetry_service.request_manager.post.assert_called_once()
        call_kwargs = telemetry_service.request_manager.post.call_args.kwargs
        assert call_kwargs["json"]["datapoints"][0]["string_value"] == "1.2.3"
        assert call_kwargs["json"]["datapoints"][0]["type"] == "heartbeat_version"
        assert call_kwargs.get("additional_headers") is not None
