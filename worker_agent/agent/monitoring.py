from __future__ import annotations

from typing import Any


def collect_metrics() -> dict[str, Any]:
    try:
        import psutil
    except ImportError:
        return {
            "cpu_utilization": None,
            "ram_utilization": None,
            "gpu_utilization": None,
            "gpu_used_vram_bytes": None,
            "telemetry_status": "unavailable",
            "telemetry_detail": "psutil is not installed; GPU backend telemetry is unavailable",
        }

    memory = psutil.virtual_memory()
    return {
        "cpu_utilization": psutil.cpu_percent(interval=0.1),
        "ram_utilization": memory.percent,
        "gpu_utilization": None,
        "gpu_used_vram_bytes": None,
        "telemetry_status": "measured_cpu_ram",
        "telemetry_detail": "CPU and RAM measured by psutil; GPU metrics require a verified backend adapter",
    }