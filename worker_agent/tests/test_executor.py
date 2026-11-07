from __future__ import annotations

import pytest

from worker_agent.agent.main import AgentConfig, WorkerAgent, inventory_pairing_payload
from worker_agent.agent.executor import UnsupportedOperation, execute_task, execute_task_isolated, execute_task_with_backend


def test_square_is_deterministic():
    assert execute_task({"operation": "square", "value": 7}) == {"value": 49}


def test_sum_is_deterministic():
    assert execute_task({"operation": "sum", "values": [1, 2.5, 3]}) == {"value": 6.5}


def test_isolated_executor_returns_built_in_result():
    assert execute_task_isolated({"operation": "square", "value": 6}) == {"value": 36}


def test_isolated_executor_rejects_oversized_input():
    with pytest.raises(ValueError, match="input limit"):
        execute_task_isolated({"operation": "identity", "value": "x" * 1_100_000})


def test_unknown_backend_is_rejected_without_cpu_fallback():
    with pytest.raises(UnsupportedOperation, match="backend"):
        execute_task_with_backend({"backend": "cuda", "operation": "square", "value": 4})


def test_inventory_pairing_payload_uses_only_verified_opencl():
    payload = inventory_pairing_payload("AB12CD34", {
        "hostname": "worker-one",
        "operating_system": {"version": "Windows"},
        "cpu": {"model": "CPU", "logical_processors": 8},
        "ram_total_bytes": 100,
        "network_interfaces": [{"IPAddress": "192.168.0.17"}],
        "gpus": [{"Name": "GPU", "AdapterCompatibility": "Vendor", "AdapterRAM": 8}],
        "opencl": {"state": "unavailable", "backend": "OpenCL"},
    })
    assert payload["private_ipv4"] == "192.168.0.17"
    assert payload["compute_backend"] is None


def test_arbitrary_operation_is_rejected():
    with pytest.raises(UnsupportedOperation):
        execute_task({"operation": "run_python", "source": "print('unsafe')"})


class FakeMasterClient:
    def __init__(self):
        self.submitted = None

    def heartbeat(self, node_id: str, status="online", metrics=None):
        assert node_id == "node-1"
        assert metrics is not None

    def lease_task(self, node_id: str):
        assert node_id == "node-1"
        return {"lease_token": "lease-token", "task": {"id": "task-1", "payload": {"operation": "square", "value": 5}}}

    def submit_result(self, task_id: str, lease_token: str, result=None, error=None):
        self.submitted = (task_id, lease_token, result, error)
        return {"state": "succeeded"}


def test_agent_cycle_executes_and_submits_result():
    client = FakeMasterClient()
    agent = WorkerAgent(AgentConfig("http://master", "node-1", "token"), client)
    assert agent.run_once() is True
    assert client.submitted == ("task-1", "lease-token", {"value": 25}, None)