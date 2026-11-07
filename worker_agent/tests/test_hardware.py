from __future__ import annotations

from worker_agent.agent.hardware import private_ipv4


def test_private_ipv4_classification():
    assert private_ipv4("10.0.0.2")
    assert private_ipv4("172.16.4.20")
    assert private_ipv4("192.168.1.10")
    assert not private_ipv4("172.32.0.1")
    assert not private_ipv4("8.8.8.8")