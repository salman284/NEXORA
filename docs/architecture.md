# NEXORA Architecture

## Product boundary

NEXORA coordinates compatible, explicitly partitionable workloads across trusted computers on a private LAN. It is not a shared-memory GPU runtime: every GPU owns an independent VRAM pool, and aggregate VRAM is shown only as capacity across devices.

The MVP supports task parallelism. Data parallelism and model parallelism are future workload-specific adapters that must declare synchronization, tensor transfer, compatibility, and failure semantics before they can be enabled.

## Runtime topology

```text
Master Console/API
  |-- Auth, cluster policy, pairing, job API
  |-- Scheduler and lease manager
  |-- SQLite metadata store
  |-- Artifact store
  |-- WebSocket telemetry stream
  |
  +--> Worker Agent  (outbound TLS/WebSocket session)
  +--> Worker Agent
  +--> Worker Agent
```

The Master can run the same Worker Agent locally. Workers do not expose an inbound public service. Pairing is short-lived and single-use; reconnect uses a bound worker identity and revocable credential.

## Components

### Master

- FastAPI management and artifact API.
- Scheduler that selects eligible nodes and creates task leases.
- Persistent state machine for jobs, tasks, attempts, nodes, and results.
- WebSocket event fan-out for dashboard updates.
- Artifact validation, retention, and download authorization.

### Worker Agent

- Windows service or foreground process with explicit operator controls.
- Hardware inventory and periodic telemetry.
- Authenticated reconnect with exponential backoff.
- Task executor with a per-job working directory and resource policy.
- Backend adapters: CPU first, then verified OpenCL/CUDA/DirectML adapters.

### Workload contract

Uploaded packages are untrusted. A manifest must declare the runtime, entry point, resource needs, task generator, task input schema, output schema, and aggregation method. A package without a valid partitioner is single-node only and cannot be distributed by the MVP scheduler.

## Task lifecycle

`queued -> leased -> running -> uploading_result -> succeeded`

Failure paths are `failed`, `timed_out`, `canceled`, `rejected`, and `retrying`. A lease has an expiry and heartbeat requirement. Late results are accepted only once for a task/attempt after schema, hash, ownership, and job-state validation.

The system provides at-least-once execution with idempotent result acceptance. It does not claim exactly-once execution.

## Trust and isolation

- Passwords use an adaptive password hash; sessions use short-lived access credentials and revocable refresh state.
- Pairing establishes an authenticated, encrypted channel and records approval/revocation events.
- Every API query is scoped to the authenticated user and cluster ownership.
- Uploaded archives are size/type/hash checked and never imported into the application process.
- Execution uses a restricted process boundary, per-job directories, deny-by-default network access, configured CPU/RAM/disk/time limits, and local Worker policy ceilings.
- TLS, artifact signing or hashing, audit events, quotas, and retention are first-class records rather than UI promises.

## Delivery order

1. Hardware and network audit with evidence reports.
2. FastAPI/SQLite foundation, authentication, cluster and node records.
3. Worker pairing, heartbeat, inventory, and reconnect.
4. Deterministic task-parallel demo with CPU execution and OpenCL qualification.
5. Scheduler policies, leases, retries, artifacts, and live monitoring.
6. Sandboxed workload packages, aggregation, downloads, and Windows packaging.

Model/data parallelism remains outside the MVP until a concrete workload adapter is specified and tested.