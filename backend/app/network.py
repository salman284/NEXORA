from __future__ import annotations

import ipaddress
import socket
import time
from typing import Any


def private_addresses() -> list[str]:
    addresses: set[str] = set()
    hostname = socket.gethostname()
    for candidate in (hostname, socket.getfqdn(hostname)):
        try:
            for record in socket.getaddrinfo(candidate, None, socket.AF_INET):
                address = record[4][0]
                if ipaddress.ip_address(address).is_private:
                    addresses.add(address)
        except socket.gaierror:
            continue
    return sorted(addresses)


def preflight(master_host: str | None, master_port: int) -> dict[str, Any]:
    result: dict[str, Any] = {
        "local_private_ipv4": private_addresses(),
        "master_host": master_host,
        "master_port": master_port,
        "resolution": "not_requested",
        "resolved_addresses": [],
        "tcp_state": "not_requested",
        "latency_ms": None,
        "detail": None,
    }
    if not master_host:
        result["detail"] = "Provide a Master hostname or private IPv4 address to test reachability."
        return result
    try:
        resolved = socket.getaddrinfo(master_host, master_port, socket.AF_INET, socket.SOCK_STREAM)
        result["resolution"] = "succeeded"
        result["resolved_addresses"] = sorted({record[4][0] for record in resolved})
    except socket.gaierror as error:
        result["resolution"] = "failed"
        result["tcp_state"] = "not_tested"
        result["detail"] = f"Master host could not be resolved: {error}"
        return result
    started = time.perf_counter()
    try:
        with socket.create_connection((master_host, master_port), timeout=3):
            result["tcp_state"] = "succeeded"
            result["detail"] = "TCP connection succeeded."
    except TimeoutError:
        result["tcp_state"] = "timeout"
        result["detail"] = "TCP connection timed out after 3 seconds."
    except OSError as error:
        result["tcp_state"] = "failed"
        result["detail"] = f"TCP connection failed: {error}"
    result["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
    return result