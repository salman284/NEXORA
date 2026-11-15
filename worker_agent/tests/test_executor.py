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


def test_execute_script_and_collect_artifacts():
    from worker_agent.agent.executor import execute_task_and_collect
    import zipfile

    script_code = """
import pickle, json
data = {"model_name": "ResNet18", "val_acc": 0.98}
with open("trained_model.pkl", "wb") as f:
    pickle.dump(data, f)
with open("metrics.json", "w") as f:
    json.dump(data, f)
print("Training complete successfully")
"""
    payload = {
        "operation": "execute_script",
        "code": script_code,
    }
    result, artifact_zip = execute_task_and_collect(payload)
    assert result["status"] == "succeeded"
    assert "trained_model.pkl" in result["artifacts"]
    assert "metrics.json" in result["artifacts"]
    assert artifact_zip is not None and artifact_zip.is_file()

    with zipfile.ZipFile(artifact_zip) as zf:
        assert "trained_model.pkl" in zf.namelist()
        assert "metrics.json" in zf.namelist()
    artifact_zip.unlink(missing_ok=True)


def test_execute_notebook_and_collect_artifacts(tmp_path):
    from worker_agent.agent.executor import execute_task_and_collect
    import json, zipfile

    nb = {
        "cells": [
            {"cell_type": "code", "source": ["x = 10\n", "y = 20\n", "print(f'Sum: {x + y}')\n"]},
            {"cell_type": "code", "source": ["with open('output_plot.png', 'wb') as f: f.write(b'PNG_DATA')\n"]}
        ]
    }
    workload_zip = tmp_path / "notebook_pkg.zip"
    with zipfile.ZipFile(workload_zip, "w") as zf:
        zf.writestr("experiment.ipynb", json.dumps(nb))

    payload = {
        "operation": "execute_notebook",
        "entrypoint": "experiment.ipynb"
    }
    result, artifact_zip = execute_task_and_collect(payload, workload_archive=workload_zip)
    assert result["status"] == "succeeded"
    assert "output_plot.png" in result["artifacts"]
    assert "executed_notebook.ipynb" in result["artifacts"]
    assert artifact_zip is not None and artifact_zip.is_file()
    artifact_zip.unlink(missing_ok=True)
