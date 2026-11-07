# Phase 1: Hardware and Network Audit

## Purpose

Phase 1 produces machine-local evidence before the platform trusts a node. Historical addresses such as `172.10.22.15` and `172.10.22.17` are examples only; the audit records current values and does not assume that similar addresses share a subnet.

## Required evidence per computer

- OS version and hostname.
- Python version and executable path.
- CPU model, logical processor count, and memory totals.
- All network adapters, operational state, IPv4 address, prefix, gateway, and DNS where available.
- Private IPv4 candidates and the selected interface for the configured Master endpoint.
- Route and TCP connectivity result for the endpoint.
- GPU candidates, vendor/model, driver metadata, and the source of each value.
- OpenCL platform/device enumeration and a known-answer kernel result.

The current script collects the first group and records GPU/OpenCL limitations honestly. The Worker Agent will add the actual OpenCL test because driver/runtime availability cannot be proven from a GPU model or installed package alone.

## LAN validation sequence

1. Enumerate adapters and private IPv4 addresses.
2. Resolve the configured Master host and test TCP connectivity to its port.
3. Compare subnet calculations only as a diagnostic; do not use them as trust.
4. Measure latency after a successful TCP connection.
5. Record blocked or unreachable states with the likely local firewall action.
6. Pair using a short-lived token and verify the Master identity before authorization.

The product must not modify Windows Firewall automatically. A failed check should suggest an explicit inbound Master-port rule or outbound Worker connection check, depending on the failure, and should include the exact endpoint tested.

## Acceptance criteria

- The report is JSON and timestamped.
- No historical address is inserted when no address is supplied.
- Unsupported GPU metrics are `null` with an explanation, never zero.
- TCP success, failure, timeout, DNS failure, and missing endpoint are distinct states.
- The same script can run on every computer without editing source code.

## Next Phase 1 checks

- Run this script on every intended node and preserve reports outside source control.
- Install the chosen OpenCL runtime only after identifying the GPU driver/runtime already present.
- Add and run a small known-answer OpenCL kernel through the Worker Agent adapter.
- Use the reports to choose the configured Master endpoint and confirm the port policy.

The Worker Agent inventory command is:

```powershell
python -m worker_agent.agent.hardware
```

It is safe to run before installing an OpenCL package. A missing runtime produces an `unavailable` result; only successful device enumeration and known-answer kernel execution produce `passed`.