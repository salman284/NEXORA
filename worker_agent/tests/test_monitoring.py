from __future__ import annotations

from worker_agent.agent.monitoring import collect_metrics


def test_monitoring_contract_contains_provenance_fields():
    metrics = collect_metrics()
    assert "cpu_utilization" in metrics
    assert "ram_utilization" in metrics
    assert "gpu_utilization" in metrics
    assert "telemetry_status" in metrics
    if metrics["telemetry_status"] == "measured_cpu_ram":
        assert 0 <= metrics["cpu_utilization"] <= 100
        assert 0 <= metrics["ram_utilization"] <= 100