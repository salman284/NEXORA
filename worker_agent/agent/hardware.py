from __future__ import annotations

import ctypes
import json
import os
import platform
import socket
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class OpenCLProbe:
    state: str
    detail: str
    platform: str | None = None
    device: str | None = None
    backend: str | None = None


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def private_ipv4(address: str) -> bool:
    parts = address.split(".")
    if len(parts) != 4:
        return False
    try:
        first, second, _, _ = (int(part) for part in parts)
    except ValueError:
        return False
    return first == 10 or (first == 172 and 16 <= second <= 31) or (first == 192 and second == 168)


def _powershell_json(command: str) -> list[dict[str, Any]]:
    if os.name != "nt":
        return []
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        return []
    try:
        parsed = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else [parsed]


def _memory_bytes() -> int | None:
    if os.name != "nt":
        return None

    class MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("length", ctypes.c_uint32),
            ("memory_load", ctypes.c_uint32),
            ("total_physical", ctypes.c_uint64),
            ("available_physical", ctypes.c_uint64),
            ("total_page_file", ctypes.c_uint64),
            ("available_page_file", ctypes.c_uint64),
            ("total_virtual", ctypes.c_uint64),
            ("available_virtual", ctypes.c_uint64),
            ("available_extended_virtual", ctypes.c_uint64),
        ]

    status = MemoryStatus()
    status.length = ctypes.sizeof(MemoryStatus)
    if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)) == 0:
        return None
    return status.total_physical


def qualify_opencl() -> OpenCLProbe:
    try:
        import pyopencl as cl
        import numpy as np
    except ImportError:
        return OpenCLProbe("unavailable", "pyopencl and numpy are not installed")

    try:
        platforms = cl.get_platforms()
        devices = [device for platform_item in platforms for device in platform_item.get_devices()]
        if not devices:
            return OpenCLProbe("unavailable", "No OpenCL devices were enumerated")
        device = devices[0]
        context = cl.Context([device])
        queue = cl.CommandQueue(context)
        program = cl.Program(context, """
            __kernel void known_answer(__global const int *input, __global int *output) {
                output[get_global_id(0)] = input[get_global_id(0)] * 2;
            }
        """).build()
        values = np.array([1, 2, 3, 5], dtype=np.int32)
        output = np.empty_like(values)
        input_buffer = cl.Buffer(context, cl.mem_flags.READ_ONLY | cl.mem_flags.COPY_HOST_PTR, hostbuf=values)
        output_buffer = cl.Buffer(context, cl.mem_flags.WRITE_ONLY, output.nbytes)
        program.known_answer(queue, values.shape, None, input_buffer, output_buffer)
        cl.enqueue_copy(queue, output, output_buffer).wait()
        expected = np.array([2, 4, 6, 10], dtype=np.int32)
        passed = bool(np.array_equal(output, expected))
        return OpenCLProbe(
            "passed" if passed else "failed",
            "Known-answer OpenCL kernel produced the expected output" if passed else "Known-answer kernel output was incorrect",
            platform=device.platform.name,
            device=device.name,
            backend="OpenCL",
        )
    except Exception as error:
        return OpenCLProbe("failed", f"OpenCL enumeration or kernel execution failed: {error}", backend="OpenCL")


def collect_inventory() -> dict[str, Any]:
    gpus = _powershell_json(
        "Get-CimInstance Win32_VideoController | Select-Object Name,AdapterCompatibility,DriverVersion,AdapterRAM | ConvertTo-Json -Compress"
    )
    addresses = _powershell_json(
        "Get-NetIPAddress -AddressFamily IPv4 | Select-Object IPAddress,PrefixLength,InterfaceAlias | ConvertTo-Json -Compress"
    )
    interfaces = [item for item in addresses if isinstance(item.get("IPAddress"), str) and private_ipv4(item["IPAddress"])]
    probe = qualify_opencl()
    return {
        "schema_version": "worker.inventory.v1",
        "captured_at_utc": utc_timestamp(),
        "hostname": socket.gethostname(),
        "operating_system": {"system": platform.system(), "release": platform.release(), "version": platform.version()},
        "cpu": {"model": platform.processor() or None, "logical_processors": os.cpu_count()},
        "ram_total_bytes": _memory_bytes(),
        "network_interfaces": interfaces,
        "gpus": gpus,
        "opencl": asdict(probe),
    }


def main() -> None:
    print(json.dumps(collect_inventory(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()