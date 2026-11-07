# NEXORA

NEXORA is a Windows-first, LAN-based distributed GPU computing platform.

## Current Scope

The first release is an explicit task-parallel MVP:

- One Master console with dynamically paired Worker Agents.
- Outbound Worker connections over a configured LAN endpoint.
- SQLite persistence for local development.
- Versioned workload manifests with an explicit task generator and result contract.
- Automatic, manual, and hybrid scheduling with leases, bounded retries, and result deduplication.
- Actual per-machine hardware and OpenCL capability qualification.
- CPU fallback reported separately from GPU execution.

The platform does not merge GPU VRAM, automatically partition arbitrary code, or claim data/model parallelism before a workload adapter exists. Historical addresses in the PRD are treated as unverified and are never hard-coded.

## Phase 1

Run the local audit from PowerShell:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\phase1-audit.ps1 -MasterHost 127.0.0.1 -MasterPort 5000
```

The script writes a timestamped JSON report to `artifacts/phase1/` and reports network interfaces, private IPv4 addresses, routes, CPU/RAM, GPU candidates, Python availability, and TCP reachability. GPU metrics are marked unavailable unless Windows exposes them through a supported query source; an OpenCL known-answer test will be added to the Worker Agent phase.

## Backend Development

Install and run the current Master API from the repository root:

```powershell
python -m pip install -r backend\requirements.txt
python -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 5000
```

Run the backend tests:

```powershell
python -m pytest backend\tests -q
```

The current API implements health checks, registration, login, logout, session validation, Master-owned clusters, single-use Worker pairing, explicit approval, node inventory, authenticated heartbeat telemetry, explicit task-parallel jobs, Worker leases, retry recovery, idempotent result acceptance, validated ZIP workload package storage, and manifest-linked jobs. The Worker Agent runs only built-in adapters in a timed child process. Dependency installation and full sandboxed uploaded-code execution are next.

## Worker Agent

The Worker Agent currently uses the paired Worker session token and supports the deterministic task contract. Run one cycle for a smoke test:

```powershell
python -m pip install -r worker_agent\requirements.txt
$env:NEXORA_MASTER_URL = "http://127.0.0.1:5000"
$env:NEXORA_NODE_ID = "<approved-node-id>"
$env:NEXORA_ACCESS_TOKEN = "<worker-session-token>"
python -m worker_agent.agent.main --pairing-code "<pairing-code>"
```

The command submits the local hardware inventory as a pending node request and exits. Approve the node from the Master API/dashboard, then run continuously with the printed node ID. The execution timeout defaults to 30 seconds:

```powershell
python -m worker_agent.agent.main --node-id "<approved-node-id>" --interval 2 --execution-timeout 30
```

The agent does not execute uploaded source code. It rejects operations outside the explicit executor contract. Each built-in task runs in a temporary directory with a scrubbed environment, bounded JSON input/output, a timeout, and process-group cleanup. Windows Job Object CPU/RAM quotas and arbitrary-code sandboxing are not enabled yet.

Each heartbeat reports CPU and RAM utilization measured by `psutil`. GPU utilization and VRAM remain `null` until a verified GPU backend adapter provides those measurements.

Collect hardware evidence on each intended Master or Worker:

```powershell
python -m worker_agent.agent.hardware
```

The command reports current private IPv4 addresses, CPU/RAM, GPU driver metadata, and OpenCL status. OpenCL is marked `passed` only after a known-answer kernel executes successfully; missing runtimes are reported as `unavailable`.

Workload manifests may declare `backend: "cpu"` or `backend: "opencl"`. Task payloads must match the manifest. Requesting OpenCL on a node without a verified OpenCL runtime fails explicitly; the agent never silently falls back to CPU.

## Design Documents

## Dashboard

Start the Master API and dashboard in separate PowerShell terminals:

```powershell
python -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 5000
Push-Location frontend
npm install
npm run dev -- --host 127.0.0.1
Pop-Location
```

Open `http://127.0.0.1:5173`. The dashboard uses the real API for login, cluster creation, node listing, logout, and five-second telemetry refresh.

Equivalent scripts are available under `scripts/`:

```powershell
.\scripts\start-master.ps1
.\scripts\start-dashboard.ps1
$env:NEXORA_ACCESS_TOKEN = "<worker-session-token>"
.\scripts\start-worker.ps1 -PairingCode "<pairing-code>"
```

- [Architecture](docs/architecture.md)
- [Phase 1 audit](docs/phase-1-audit.md)
- [Data model and API](docs/data-model-and-api.md)
