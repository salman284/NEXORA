# Initial Data Model and API Contract

## Persistence

SQLite is the development default. PostgreSQL is the production migration target. IDs are UUIDs; timestamps are UTC; state transitions are append-only in audit/event tables where practical.

### Core tables

- `users`: account identity, password hash, status, and timestamps.
- `roles`: user-to-role assignments (`master`, `worker_operator`).
- `clusters`: owner, name, policy, and status.
- `cluster_members`: approved node identity, role, credential fingerprint, approval/revocation state.
- `nodes`: hostname, OS, network metadata, capability snapshot, telemetry freshness, and online state.
- `workload_packages`: owner, manifest version, content hash, storage path, validation state, and retention.
- `jobs`: package, cluster, execution mode, scheduling policy, state, and immutable configuration snapshot.
- `tasks`: job, deterministic task ID, payload hash, state, lease, retry count, and assigned node.
- `task_attempts`: task attempt, node, timestamps, resource samples, logs, and error category.
- `artifacts`: owner scope, job/task relation, content hash, storage path, media type, and retention.
- `telemetry_samples`: node metrics with provenance, backend, and timestamp.
- `audit_events`: actor, action, scope, outcome, and structured metadata.

## API groups

### Authentication

- `POST /api/v1/auth/register`
- `POST /api/v1/auth/login`
- `POST /api/v1/auth/refresh`
- `POST /api/v1/auth/logout`
- `POST /api/v1/auth/change-password`
- `POST /api/v1/auth/password-reset/request`
- `POST /api/v1/auth/password-reset/confirm`
- `GET /api/v1/audit-events`

### Clusters and nodes

- `POST /api/v1/clusters`
- `GET /api/v1/clusters/{cluster_id}`
- `POST /api/v1/clusters/{cluster_id}/pairing-codes`
- `POST /api/v1/pairing/requests`
- `POST /api/v1/clusters/{cluster_id}/members/{node_id}/approve`
- `POST /api/v1/clusters/{cluster_id}/members/{node_id}/revoke`
- `GET /api/v1/clusters/{cluster_id}/nodes`
- `GET /api/v1/clusters/{cluster_id}/telemetry`
- `POST /api/v1/network/preflight`

### Workloads and jobs

- `POST /api/v1/workloads` multipart upload with a JSON manifest, ZIP size/path checks, and SHA-256 integrity metadata.
- `GET /api/v1/workloads` owner-scoped validated package listing.
- `POST /api/v1/workloads/{workload_id}/validate`
- `POST /api/v1/jobs/preview`
- `POST /api/v1/jobs`
- `POST /api/v1/jobs/{job_id}/pause`
- `POST /api/v1/jobs/{job_id}/resume`
- `POST /api/v1/jobs/{job_id}/cancel`
- `GET /api/v1/jobs/{job_id}`
- `GET /api/v1/jobs/{job_id}/tasks`
- `GET /api/v1/jobs/{job_id}/result`
- `GET /api/v1/jobs/{job_id}/artifacts`
- `GET /api/v1/artifacts/{artifact_id}/download`

### Worker session

- `POST /api/v1/worker/register`
- `WS /api/v1/worker/session`
- `POST /api/v1/worker/heartbeat`
- `POST /api/v1/worker/nodes/{node_id}/tasks/lease`
- `POST /api/v1/worker/tasks/{task_id}/result`

The current task-parallel MVP accepts explicit task payloads through `POST /api/v1/jobs`. A Worker lease contains a short-lived token and expiry; results require the matching token, node ownership, and an active lease. Expired leases are requeued within the job retry budget, and accepted results are deduplicated by task state. Worker endpoints currently use the authenticated Worker Operator session; a bound agent credential will replace that transport identity in the Worker Agent phase.

Workload upload currently accepts only a ZIP archive plus a manifest for the built-in deterministic adapters. The API checks size, ZIP integrity, and path traversal without extracting or executing package contents. Dependency installation, arbitrary entry points, and sandboxed uploaded-code execution are intentionally not enabled yet.

Jobs may reference a validated workload package. When they do, every task payload must declare the operation named by the manifest. The Worker Agent executes the built-in operation in a timed child process; this is a limited process boundary and does not provide the full filesystem, network, privilege, and resource isolation required for arbitrary uploaded code.

The final-result endpoint returns `409` until the job is completed. Completed task results are ordered by task key, labeled with each task key, aggregated using the declared `collect` contract, and accompanied by a deterministic SHA-256 result hash.

The current child process policy uses a temporary working directory, a scrubbed environment, bounded JSON input/output, a timeout, and process-group cleanup. Windows Job Object CPU/RAM quotas, network denial, and arbitrary-code execution are intentionally deferred until a platform-specific sandbox adapter is implemented.

Worker heartbeats carry CPU and RAM values measured by `psutil`. GPU utilization and VRAM are nullable and must remain unavailable until a backend-specific adapter supplies a verified measurement.

Each accepted heartbeat appends a `telemetry_samples` row with UTC capture time and source. The cluster telemetry endpoint returns the latest 1,000 samples for approved, pending, or revoked members visible to the cluster owner; unsupported metrics remain `null`.

The built-in execution adapter supports explicit `cpu` and optional `opencl` backends for `square` and `sum`. A workload manifest and each task payload must agree on the backend. OpenCL requests fail when enumeration or kernel execution fails; there is no silent CPU fallback.

Worker leasing performs admission checks before assignment: declared backend compatibility, required CPU threads, and required GPU memory against the node's reported VRAM and current usage. Incompatible tasks remain queued for another eligible node.

Transient task failures and expired leases use exponential retry backoff starting at 1 second and capped at 60 seconds. Once the configured retry limit is exhausted, the task becomes permanently failed and the job can fail.

Jobs accept priority values from `-10` to `100`; higher-priority jobs are leased first, with creation order retained within the same priority.

Worker endpoints require the paired node identity. Dashboard WebSockets carry events only after normal API authorization; they are not a replacement for authorization checks.

Audit events persist successful account, cluster, pairing, node, workload, and job actions with actor, scope, outcome, metadata, and UTC timestamp. The current history endpoint returns the authenticated actor's latest 200 events; broader cluster-owner audit views will be added with the multi-user administration surface.

Password changes verify the current password, store a new adaptive hash, revoke all active sessions, and record an audit event. Password reset by email is still a deployment-specific feature because no mail provider or recovery identity has been configured.

Network preflight reports current private IPv4 candidates, DNS resolution, TCP reachability, latency, and an actionable detail string. It never treats subnet resemblance as trust and never modifies Windows Firewall rules.