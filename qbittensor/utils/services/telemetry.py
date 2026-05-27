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

import time
import bittensor as bt
import numpy as np
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional
import queue
import threading
import psutil
import platform

from qbittensor.utils.request.request_manager import RequestManager
from qbittensor.utils.timer import Timer
import qbittensor
from qbittensor.utils.time import timestamp_iso

try:
    from pynvml import *
    NVML_AVAILABLE = True
except ImportError:
    NVML_AVAILABLE = False
    bt.logging.warning("pynvml not available, GPU metrics will be skipped")


class TelemetryService:
    def __init__(
        self,
        request_manager: Optional[RequestManager] = None,
        device: str = "cpu",
        export_interval_millis=5000,
        max_queue_size=1000,
        batch_size=10,
        retry_attempts=3,
        retry_delay=1,
        service_name: Optional[str] = None,
        network: Optional[str] = None,
        *,
        keypair: Optional[bt.Keypair] = None,
        base_url: Optional[str] = None,
        tensorauth_url: Optional[str] = None,
        netuid: Optional[int] = None,
    ):
        """
        Telemetry / metrics service for validators and miners.

        Recommended: pass keypair + base_url (telemetry) + tensorauth_url + netuid
        and the service will create its own RequestManager (one RM per client).

        Alternatively, pass a pre-configured request_manager (advanced / testing).
        """
        if request_manager is not None:
            self.request_manager = request_manager
        elif keypair is not None and base_url is not None:
            self.request_manager = RequestManager(
                keypair,
                base_url=base_url,
                tensorauth_url=tensorauth_url,
                netuid=netuid,
            )
        else:
            raise ValueError(
                "TelemetryService requires either request_manager or (keypair + base_url)"
            )
        self.max_queue_size = max_queue_size
        self.batch_size = batch_size
        self.retry_attempts = retry_attempts
        self.retry_delay = retry_delay
        self.flush_interval = export_interval_millis / 1000.0
        self.device = device
        self.gpu_indices = []
        self.queue = queue.Queue(maxsize=max_queue_size)
        self._stop_event = threading.Event()
        self._worker_thread = None
        self.heartbeat_timer = Timer(timeout=timedelta(minutes=5), run=self.record_heartbeat, run_on_start=True)
        self.system_metrics_timer = Timer(timeout=timedelta(minutes=5), run=self.record_system_metrics, run_on_start=True)

        self._service_name = service_name
        self._network = network

        bt.logging.info("TelemetryService initialized (using RequestManager for all API calls)")
        self._start_background_worker()

    def _to_python_scalar(self, x: Any) -> Any:
        """Convert NumPy or Torch scalars to JSON-serializable Python types."""
        if x is None:
            return None
        if isinstance(x, (int, float, str)):
            return x
        if hasattr(x, 'item'):  # Handles torch.Tensor scalars and NumPy arrays
            return x.item()
        if isinstance(x, (np.integer, np.floating, np.number)):
            return x.item()
        return str(x)  # Fallback for other types

    def _start_background_worker(self):
        """Start the background thread for flushing the queue."""
        def worker():
            while not self._stop_event.is_set():
                try:
                    # Flush every interval or when batch_size reached
                    start_time = time.time()
                    batch = []
                    while len(batch) < self.batch_size and not self._stop_event.is_set():
                        try:
                            item = self.queue.get(timeout=0.1)
                            batch.append(item)
                        except queue.Empty:
                            break
                    if batch:
                        self._flush_batch(batch)
                    sleep_time = max(0, self.flush_interval - (time.time() - start_time))
                    if sleep_time > 0:
                        self._stop_event.wait(sleep_time)
                except Exception as e:
                    bt.logging.error(f"Background worker error: {e}")
                    time.sleep(self.retry_delay)

        self._worker_thread = threading.Thread(target=worker, daemon=True)
        self._worker_thread.start()

    def _flush_batch(self, batch: list[Dict[str, Any]]) -> None:
        """Flush a batch of datapoints via the RequestManager (single POST to /v1/datapoints)."""
        # Build the payload once
        datapoints = []
        for item in batch:
            payload_item = {
                "type": item["type"],
                "timestamp": item["timestamp"],
            }
            if item.get("miner_uid") is not None:
                payload_item["miner_uid"] = item["miner_uid"]
            if item.get("miner_hotkey"):
                payload_item["miner_hotkey"] = item["miner_hotkey"]
            if isinstance(item["value"], (int, float)):
                payload_item["numeric_value"] = item["value"]
            else:
                payload_item["string_value"] = item["value"]
            if item.get("attributes"):
                payload_item["attributes"] = item["attributes"]
            datapoints.append(payload_item)

        additional_headers = []
        if self._service_name:
            additional_headers.append(("X-Service-Name", self._service_name))
        if self._network:
            additional_headers.append(("X-Network", self._network))

        # Retry loop around the RequestManager call
        for attempt in range(self.retry_attempts):
            try:
                response = self.request_manager.post(
                    "v1/datapoints",
                    json={"datapoints": datapoints},
                    additional_headers=additional_headers,
                )
                if 200 <= response.status_code <= 299:
                    for _ in batch:
                        self.queue.task_done()
                    return
                else:
                    raise RuntimeError(f"Telemetry POST returned {response.status_code}")
            except Exception as e:
                bt.logging.warning(f"Batch send attempt {attempt + 1} failed (size {len(batch)}): {e}")
                if attempt < self.retry_attempts - 1:
                    time.sleep(self.retry_delay * (2 ** attempt))
                else:
                    bt.logging.error(
                        f"Failed to send batch of {len(batch)} after {self.retry_attempts} attempts; dropping."
                    )
                    for _ in batch:
                        self.queue.task_done()

    def _enqueue_datapoint(self, type: str, timestamp: str, value: float | str, miner_uid: Optional[int] = None, miner_hotkey: Optional[str] = None, attributes: Optional[Dict[str, Any]] = None) -> bool:
        """Enqueue a datapoint; return True if enqueued, False if queue full (dropped)."""
        # CHANGE: timestamp now str (ISO)
        try:
            if self.queue.full():
                bt.logging.warning(f"Queue full (size {self.max_queue_size}); dropping datapoint {type}")
                return False
            # Convert value to ensure it's a Python scalar (handles NumPy/Torch)
            safe_value = self._to_python_scalar(value)
            if isinstance(safe_value, (int, float)):
                safe_value = float(safe_value)  # Ensure float for numericValue

            # onvert miner_uid
            safe_miner_uid = self._to_python_scalar(miner_uid) if miner_uid is not None else None
            if safe_miner_uid is not None:
                safe_miner_uid = int(safe_miner_uid)

            # Convert attributes values
            safe_attributes = None
            if attributes:
                safe_attributes = {
                    k: self._to_python_scalar(v)
                    for k, v in attributes.items()
                }

            item = {
                'type': type,
                'timestamp': timestamp,
                'value': safe_value,
                'miner_uid': safe_miner_uid,
                'miner_hotkey': miner_hotkey,
                'attributes': safe_attributes,
            }
            self.queue.put_nowait(item)

            bt.logging.debug(f"Recorded datapoint: {item}")

            return True
        except queue.Full:
            bt.logging.warning(f"Queue full; dropping datapoint {type}")
            return False

    def record_heartbeat(self):
        version = qbittensor.__version__
        bt.logging.info(f"🫀 Recording heartbeat version: {version}")
        try:
            timestamp: str = timestamp_iso()
            # Record version as string
            self._enqueue_datapoint("heartbeat_version", timestamp, version)
        except Exception as e:
            bt.logging.info(f"Failed to enqueue heartbeat: {e}")

    def record_startup_metrics(self):
        """Record startup system metrics (CPU family, count, GPU count/models)."""
        try:
            timestamp = datetime.now(timezone.utc).isoformat()
            cpu_family = platform.processor()
            cpu_count = psutil.cpu_count()
            self._enqueue_datapoint("system_cpu_family", timestamp, cpu_family)
            self._enqueue_datapoint("system_cpu_count", timestamp, cpu_count)

            # GPU info
            if NVML_AVAILABLE:
                try:
                    nvmlInit()
                    device_count = nvmlDeviceGetCount()
                    if self.device == "cpu":
                        gpu_count = 0
                        gpu_models = "none"
                    elif self.device.startswith("cuda:"):
                        gpu_index = int(self.device.split(":")[1])
                        if gpu_index < device_count:
                            handle = nvmlDeviceGetHandleByIndex(gpu_index)
                            gpu_count = 1
                            gpu_models = nvmlDeviceGetName(handle).decode()
                        else:
                            gpu_count = 0
                            gpu_models = "invalid device"
                    else:
                        gpu_count = device_count
                        gpu_models_list = []
                        for i in range(device_count):
                            handle = nvmlDeviceGetHandleByIndex(i)
                            gpu_models_list.append(nvmlDeviceGetName(handle).decode())
                        gpu_models = ", ".join(gpu_models_list)
                except Exception as e:
                    bt.logging.warning(f"Failed to get GPU info: {e}")
                    gpu_count = 0
                    gpu_models = "error"
            else:
                gpu_count = 0
                gpu_models = "pynvml not available"

            self._enqueue_datapoint("system_gpu_count", timestamp, gpu_count)
            self._enqueue_datapoint("system_gpu_models", timestamp, gpu_models)

            # Store GPU indices for periodic metrics
            self.gpu_indices = []
            if gpu_count > 0:
                if self.device == "cpu":
                    pass
                elif self.device.startswith("cuda:"):
                    self.gpu_indices = [gpu_index]
                else:
                    self.gpu_indices = list(range(device_count))
        except Exception as e:
            bt.logging.warning(f"Startup metrics recording failed: {e}")

    def record_system_metrics(self):
        """Record periodic system metrics (CPU/RAM usage, GPU util/memory)."""
        try:
            timestamp = datetime.now(timezone.utc).isoformat()
            cpu_usage = psutil.cpu_percent(interval=1)
            ram_usage = psutil.virtual_memory().percent
            self._enqueue_datapoint("system_cpu_usage", timestamp, cpu_usage)
            self._enqueue_datapoint("system_ram_usage", timestamp, ram_usage)

            # GPU metrics
            if self.gpu_indices:
                for i in self.gpu_indices:
                    handle = nvmlDeviceGetHandleByIndex(i)
                    util = nvmlDeviceGetUtilizationRates(handle).gpu
                    mem_info = nvmlDeviceGetMemoryInfo(handle)
                    mem_usage = (mem_info.used / mem_info.total) * 100
                    self._enqueue_datapoint("system_gpu_utilization", timestamp, util, attributes={"gpu_index": i})
                    self._enqueue_datapoint("system_gpu_memory_usage", timestamp, mem_usage, attributes={"gpu_index": i})

            bt.logging.info("System metrics sent")
        except Exception as e:
            bt.logging.warning(f"System metrics recording failed: {e}")

    def shutdown(self):
        """
        Stop the background worker, flush any remaining datapoints, and shut down.
        Note: We do not close the RequestManager's session (it may be shared).
        """
        try:
            bt.logging.info("Shutting down metrics service...")
            self._stop_event.set()
            if self._worker_thread:
                self._worker_thread.join(timeout=5.0)

            # Force flush remaining items
            batch = []
            while not self.queue.empty():
                try:
                    batch.append(self.queue.get_nowait())
                except queue.Empty:
                    break
            if batch:
                self._flush_batch(batch)

            bt.logging.info("Metrics service shutdown complete. ✅")
        except Exception as e:
            bt.logging.warning(f"Error during shutdown: {e}")
