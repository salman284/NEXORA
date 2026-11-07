from __future__ import annotations

from collections.abc import Mapping
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any

from .gpu import execute_opencl


class UnsupportedOperation(ValueError):
    """The task payload does not name a supported safe operation."""


MAX_TASK_JSON_BYTES = 1_048_576
MAX_RESULT_JSON_BYTES = 1_048_576


def execute_task(payload: Mapping[str, Any]) -> dict[str, Any]:
    operation = payload.get("operation")
    if not isinstance(operation, str):
        raise UnsupportedOperation("Task payload must declare an operation")

    if operation == "identity":
        return {"value": payload.get("value")}

    if operation == "square":
        value = payload.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("square requires a numeric value")
        return {"value": value * value}

    if operation == "sum":
        values = payload.get("values")
        if not isinstance(values, list) or any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values):
            raise ValueError("sum requires a list of numeric values")
        return {"value": sum(values)}

    raise UnsupportedOperation(f"Unsupported task operation: {operation}")


def execute_task_with_backend(payload: Mapping[str, Any]) -> dict[str, Any]:
    backend = payload.get("backend", "cpu")
    if backend == "cpu":
        return execute_task(payload)
    if backend == "opencl":
        return execute_opencl(payload)
    raise UnsupportedOperation(f"Unsupported execution backend: {backend}")


def execute_task_isolated(payload: Mapping[str, Any], timeout_seconds: float = 30.0) -> dict[str, Any]:
    """Run only the built-in adapter in a bounded child process."""
    serialized_payload = json.dumps(payload).encode()
    if len(serialized_payload) > MAX_TASK_JSON_BYTES:
        raise ValueError("Task payload exceeds the execution input limit")
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LOCALAPPDATA"}
    }
    environment["PYTHONNOUSERSITE"] = "1"
    creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    module_root = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix="nexora-task-") as working_directory:
        environment["PYTHONPATH"] = str(module_root)
        process = subprocess.Popen(
            [sys.executable, "-m", "worker_agent.agent.safe_runner"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=working_directory,
            env=environment,
            creationflags=creation_flags,
            start_new_session=os.name != "nt",
        )
        try:
            stdout, stderr = process.communicate(input=serialized_payload, timeout=timeout_seconds)
        except subprocess.TimeoutExpired as error:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=False)
            else:
                process.kill()
            process.communicate()
            raise TimeoutError(f"Task exceeded the {timeout_seconds:g} second execution limit") from error
    if process.returncode != 0:
        detail = stderr.decode(errors="replace").strip()[:1000]
        raise RuntimeError(detail or "Isolated task process failed")
    if len(stdout) > MAX_RESULT_JSON_BYTES:
        raise RuntimeError("Isolated task result exceeds the output limit")
    try:
        result = json.loads(stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("Isolated task returned invalid JSON") from error
    if not isinstance(result, dict):
        raise RuntimeError("Isolated task result must be a JSON object")
    return result