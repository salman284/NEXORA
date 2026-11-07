from __future__ import annotations

from backend.app.network import preflight


def test_preflight_without_host_is_explicitly_not_requested():
    result = preflight(None, 5000)
    assert result["tcp_state"] == "not_requested"
    assert result["local_private_ipv4"] == sorted(result["local_private_ipv4"])


def test_preflight_unresolvable_host_is_not_claimed_reachable():
    result = preflight("invalid.invalid", 5000)
    assert result["resolution"] == "failed"
    assert result["tcp_state"] == "not_tested"