from __future__ import annotations

from collections.abc import Mapping
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid
import zipfile
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

    if operation == "matmul":
        matrix_a = payload.get("matrix_a")
        matrix_b = payload.get("matrix_b")
        if not isinstance(matrix_a, list) or not isinstance(matrix_b, list):
            raise ValueError("matmul requires matrix_a and matrix_b lists")
        if not matrix_a or not matrix_b or not isinstance(matrix_a[0], list) or not isinstance(matrix_b[0], list):
            raise ValueError("matmul matrices must be non-empty 2D lists")
        rows_a = len(matrix_a)
        cols_a = len(matrix_a[0])
        rows_b = len(matrix_b)
        cols_b = len(matrix_b[0])
        if cols_a != rows_b:
            raise ValueError(f"Incompatible matrix dimensions: ({rows_a}x{cols_a}) and ({rows_b}x{cols_b})")
        result = [[0.0 for _ in range(cols_b)] for _ in range(rows_a)]
        for i in range(rows_a):
            for k in range(cols_a):
                val_a = float(matrix_a[i][k])
                for j in range(cols_b):
                    result[i][j] += val_a * float(matrix_b[k][j])
        return {"matrix": result}

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
    if getattr(sys, "frozen", False) or "NEXORA-Worker" in sys.executable:
        # In a bundled PyInstaller executable, sys.executable is the desktop app itself.
        # Spawning sys.executable would recursively launch new GUI windows!
        # Execute safely in-process using the validated adapter:
        return execute_task_with_backend(payload)

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


def execute_notebook_cells(notebook_path: Path, workspace_dir: Path) -> tuple[str, Path]:
    """Execute code cells of a Jupyter notebook sequentially and save executed notebook."""
    with open(notebook_path, "r", encoding="utf-8") as f:
        nb_data = json.load(f)

    stdout_captures = []
    run_globals = {
        "__name__": "__main__",
        "__file__": str(notebook_path),
    }

    original_cwd = os.getcwd()
    os.chdir(workspace_dir)
    try:
        for idx, cell in enumerate(nb_data.get("cells", [])):
            if cell.get("cell_type") == "code":
                source = "".join(cell.get("source", []))
                if not source.strip():
                    continue
                buf = io.StringIO()
                try:
                    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                        exec(source, run_globals)
                    out_text = buf.getvalue()
                    stdout_captures.append(out_text)
                    cell["outputs"] = [{"output_type": "stream", "name": "stdout", "text": [out_text]}]
                    cell["execution_count"] = idx + 1
                except Exception as cell_err:
                    err_text = f"Cell {idx + 1} Error: {cell_err}\n"
                    stdout_captures.append(err_text)
                    cell["outputs"] = [{"output_type": "error", "ename": type(cell_err).__name__, "evalue": str(cell_err)}]
                    raise RuntimeError(f"Error executing notebook at cell {idx + 1}: {cell_err}") from cell_err
    finally:
        os.chdir(original_cwd)

    executed_nb_path = workspace_dir / "executed_notebook.ipynb"
    with open(executed_nb_path, "w", encoding="utf-8") as f:
        json.dump(nb_data, f, indent=2)

    return "\n".join(stdout_captures), executed_nb_path


def execute_script_or_notebook(
    payload: Mapping[str, Any],
    workload_archive: Path | str | None = None,
    timeout_seconds: float = 300.0,
) -> tuple[dict[str, Any], Path | None]:
    """Execute custom script or notebook in an isolated workspace and bundle created artifacts."""
    workspace_dir = Path(tempfile.mkdtemp(prefix="nexora-exec-"))
    initial_files: set[str] = set()

    try:
        # 1. Unpack workload archive if present
        if workload_archive and Path(workload_archive).is_file():
            with zipfile.ZipFile(workload_archive, "r") as zf:
                zf.extractall(workspace_dir)

        # 2. Record initial files to detect newly created outputs
        for p in workspace_dir.rglob("*"):
            if p.is_file():
                initial_files.add(str(p.resolve()))

        # 3. Write input parameters
        input_json_path = workspace_dir / "task_input.json"
        with open(input_json_path, "w", encoding="utf-8") as f:
            json.dump(dict(payload), f, indent=2)

        # 4. Determine script or notebook entrypoint
        entrypoint = payload.get("entrypoint") or payload.get("script") or payload.get("notebook")
        code = payload.get("code")

        stdout_text = ""
        stderr_text = ""

        if code:
            script_path = workspace_dir / "entrypoint.py"
            with open(script_path, "w", encoding="utf-8") as f:
                f.write(code)
            cmd = [sys.executable, str(script_path)]
            proc = subprocess.run(cmd, cwd=str(workspace_dir), capture_output=True, text=True, timeout=timeout_seconds)
            stdout_text = proc.stdout
            stderr_text = proc.stderr
            if proc.returncode != 0:
                raise RuntimeError(f"Script execution failed with exit code {proc.returncode}:\n{stderr_text[:1500]}")

        elif entrypoint and str(entrypoint).endswith(".ipynb"):
            nb_path = workspace_dir / str(entrypoint)
            if not nb_path.is_file():
                matches = list(workspace_dir.glob("*.ipynb"))
                if matches:
                    nb_path = matches[0]
                else:
                    raise FileNotFoundError(f"Notebook file '{entrypoint}' not found in workload")
            stdout_text, _ = execute_notebook_cells(nb_path, workspace_dir)

        elif entrypoint and str(entrypoint).endswith(".py"):
            py_path = workspace_dir / str(entrypoint)
            if not py_path.is_file():
                matches = list(workspace_dir.glob("*.py"))
                if matches:
                    py_path = matches[0]
                else:
                    raise FileNotFoundError(f"Script file '{entrypoint}' not found in workload")
            cmd = [sys.executable, str(py_path)]
            proc = subprocess.run(cmd, cwd=str(workspace_dir), capture_output=True, text=True, timeout=timeout_seconds)
            stdout_text = proc.stdout
            stderr_text = proc.stderr
            if proc.returncode != 0:
                raise RuntimeError(f"Script execution failed with exit code {proc.returncode}:\n{stderr_text[:1500]}")

        else:
            # Auto-detect if there is a .py or .ipynb in workspace
            ipynb_files = list(workspace_dir.glob("*.ipynb"))
            py_files = [f for f in workspace_dir.glob("*.py") if f.name != "entrypoint.py"]
            if ipynb_files:
                stdout_text, _ = execute_notebook_cells(ipynb_files[0], workspace_dir)
            elif py_files:
                cmd = [sys.executable, str(py_files[0])]
                proc = subprocess.run(cmd, cwd=str(workspace_dir), capture_output=True, text=True, timeout=timeout_seconds)
                stdout_text = proc.stdout
                stderr_text = proc.stderr
                if proc.returncode != 0:
                    raise RuntimeError(f"Script execution failed with exit code {proc.returncode}:\n{stderr_text[:1500]}")
            else:
                raise UnsupportedOperation("No executable Python script or Jupyter notebook found in workload")

        # 5. Collect created output artifacts
        ignored_names = {"task_input.json", "entrypoint.py"}
        ignored_exts = {".pyc"}
        new_files: list[Path] = []
        for p in workspace_dir.rglob("*"):
            if not p.is_file():
                continue
            if p.name in ignored_names or p.suffix in ignored_exts or "__pycache__" in p.parts:
                continue
            # If it's a generated file
            if str(p.resolve()) not in initial_files or p.name.startswith("output") or p.name == "executed_notebook.ipynb":
                new_files.append(p)

        artifact_zip_path = None
        artifact_names: list[str] = []
        if new_files:
            artifact_zip_path = Path(tempfile.gettempdir()) / f"nexora_art_{uuid.uuid4().hex[:8]}.zip"
            with zipfile.ZipFile(artifact_zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for file_path in new_files:
                    rel_name = file_path.relative_to(workspace_dir).as_posix()
                    zf.write(file_path, arcname=rel_name)
                    artifact_names.append(rel_name)

        result_dict = {
            "status": "succeeded",
            "artifact_count": len(artifact_names),
            "artifacts": artifact_names,
            "stdout": stdout_text.strip()[:1000] if stdout_text else "Execution completed successfully",
        }
        return result_dict, artifact_zip_path

    finally:
        shutil.rmtree(workspace_dir, ignore_errors=True)


def execute_task_and_collect(
    payload: Mapping[str, Any],
    workload_archive: Path | str | None = None,
) -> tuple[dict[str, Any], Path | None]:
    """Universal dispatcher: handles both built-in math and custom scripts/notebooks."""
    op = payload.get("operation")
    if op in {"identity", "square", "sum", "matmul"}:
        return execute_task_with_backend(payload), None
    if op in {"execute_script", "run_script", "script", "execute_notebook", "run_notebook", "notebook"} or workload_archive or payload.get("code") or payload.get("script") or payload.get("entrypoint"):
        return execute_script_or_notebook(payload, workload_archive)
    raise UnsupportedOperation(f"Unsupported task operation: {op}")
