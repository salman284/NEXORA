from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, EmailStr, Field

from .db import connection_scope, initialize_database
from .network import preflight
from .workloads import WorkloadManifest, parse_manifest, validate_archive


SESSION_DAYS = 7
PAIRING_CODE_MINUTES = 10
MAX_TASKS_PER_JOB = 10_000
MAX_WORKLOAD_BYTES = 50 * 1024 * 1024
HEARTBEAT_STALE_SECONDS = 30


@asynccontextmanager
async def lifespan(_app: FastAPI):
    initialize_database()
    yield


app = FastAPI(title="NEXORA Master API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def timestamp(value: datetime) -> str:
    return value.isoformat()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt$16384$8$1${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, n, r, p, salt_hex, digest_hex = stored_hash.split("$")
        if algorithm != "scrypt":
            return False
        candidate = hashlib.scrypt(
            password.encode(),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
        )
        return hmac.compare_digest(candidate.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class RegisterRequest(BaseModel):
    full_name: str = Field(min_length=1, max_length=160)
    username: str = Field(min_length=3, max_length=80, pattern=r"^[A-Za-z0-9_.-]+$")
    email: EmailStr
    password: str = Field(min_length=12, max_length=256)
    organization: str | None = Field(default=None, max_length=160)
    roles: list[str] = Field(default_factory=lambda: ["master"])


class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=12, max_length=256)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    full_name: str
    username: str
    email: EmailStr
    organization: str | None
    roles: list[str]


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    user: UserResponse


class ClusterCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class ClusterResponse(BaseModel):
    id: str
    name: str
    status: str
    created_at: datetime


class PairingCodeResponse(BaseModel):
    code: str
    expires_at: datetime


class PairingRequest(BaseModel):
    code: str | None = Field(default=None, max_length=64)
    node_name: str = Field(min_length=1, max_length=120)
    hostname: str = Field(min_length=1, max_length=255)
    operating_system: str | None = Field(default=None, max_length=160)
    private_ipv4: str | None = Field(default=None, max_length=45)
    cpu_model: str | None = Field(default=None, max_length=255)
    cpu_threads: int | None = Field(default=None, ge=1, le=4096)
    ram_total_bytes: int | None = Field(default=None, ge=0)
    gpu_vendor: str | None = Field(default=None, max_length=120)
    gpu_model: str | None = Field(default=None, max_length=255)
    gpu_vram_bytes: int | None = Field(default=None, ge=0)
    compute_backend: str | None = Field(default=None, max_length=120)


class NodeResponse(BaseModel):
    id: str
    node_name: str
    hostname: str
    status: str
    membership_status: str
    operating_system: str | None
    private_ipv4: str | None
    gpu_vendor: str | None
    gpu_model: str | None
    gpu_vram_bytes: int | None
    compute_backend: str | None
    cpu_utilization: float | None
    ram_utilization: float | None
    gpu_utilization: float | None
    gpu_used_vram_bytes: int | None
    last_heartbeat_at: datetime | None


class HeartbeatRequest(BaseModel):
    status: str = Field(default="online", pattern=r"^(online|draining|offline)$")
    cpu_utilization: float | None = Field(default=None, ge=0, le=100)
    ram_utilization: float | None = Field(default=None, ge=0, le=100)
    gpu_utilization: float | None = Field(default=None, ge=0, le=100)
    gpu_used_vram_bytes: int | None = Field(default=None, ge=0)
    telemetry_source: str = Field(default="worker_agent", max_length=120)


class TelemetryResponse(BaseModel):
    node_id: str
    node_name: str
    captured_at: datetime
    source: str
    cpu_utilization: float | None
    ram_utilization: float | None
    gpu_utilization: float | None
    gpu_used_vram_bytes: int | None


class NetworkPreflightRequest(BaseModel):
    master_host: str | None = Field(default=None, max_length=255)
    master_port: int = Field(default=5000, ge=1, le=65535)


class TaskInput(BaseModel):
    task_key: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_.:-]+$")
    payload: dict[str, Any]
    pinned_node_id: str | None = None


class JobCreateRequest(BaseModel):
    cluster_id: str
    workload_id: str | None = None
    name: str = Field(min_length=1, max_length=160)
    execution_mode: str = Field(default="task_parallel", pattern=r"^task_parallel$")
    scheduling_policy: str = Field(default="automatic", pattern=r"^(automatic|manual|hybrid)$")
    scheduling_config: dict[str, Any] = Field(default_factory=dict)
    priority: int = Field(default=0, ge=-10, le=100)
    tasks: list[TaskInput] = Field(min_length=1, max_length=MAX_TASKS_PER_JOB)
    retry_limit: int = Field(default=2, ge=0, le=10)
    lease_seconds: int = Field(default=60, ge=10, le=3600)


class JobResponse(BaseModel):
    id: str
    cluster_id: str
    workload_id: str | None
    name: str
    execution_mode: str
    scheduling_policy: str = "automatic"
    scheduling_config: dict[str, Any] = Field(default_factory=dict)
    priority: int
    state: str
    retry_limit: int
    lease_seconds: int
    task_count: int
    succeeded_count: int
    failed_count: int
    queued_count: int
    created_at: datetime
    updated_at: datetime


class TaskResponse(BaseModel):
    id: str
    job_id: str
    task_key: str
    payload: dict[str, Any]
    state: str
    attempt: int
    assigned_node_id: str | None
    pinned_node_id: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_seconds: float | None = None
    lease_expires_at: datetime | None
    next_retry_at: datetime | None
    result: dict[str, Any] | None
    result_hash: str | None
    error: str | None


class TaskReportItem(BaseModel):
    task_key: str
    state: str
    attempt: int
    assigned_node_id: str | None
    node_name: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_seconds: float | None = None
    error: str | None = None


class NodePerformanceSummary(BaseModel):
    node_id: str
    node_name: str
    tasks_completed: int
    tasks_failed: int
    total_compute_seconds: float
    avg_task_duration_seconds: float


class JobReportResponse(BaseModel):
    job_id: str
    job_name: str
    cluster_id: str
    state: str
    execution_mode: str
    scheduling_policy: str
    priority: int
    task_count: int
    succeeded_count: int
    failed_count: int
    total_duration_seconds: float | None
    created_at: datetime
    updated_at: datetime
    nodes: list[NodePerformanceSummary]
    tasks: list[TaskReportItem]


class ClusterSummaryResponse(BaseModel):
    cluster_id: str
    cluster_name: str
    registered_nodes: int
    online_nodes: int
    offline_nodes: int
    detected_gpus: int
    total_vram_bytes: int
    active_jobs: int
    queued_jobs: int
    completed_jobs: int
    failed_jobs: int


class TaskLeaseResponse(BaseModel):
    lease_token: str
    task: TaskResponse


class TaskResultRequest(BaseModel):
    lease_token: str = Field(min_length=20, max_length=256)
    status: str = Field(pattern=r"^(succeeded|failed)$")
    result: dict[str, Any] | None = None
    error: str | None = Field(default=None, max_length=4000)


class FinalResultResponse(BaseModel):
    job_id: str
    aggregation: str
    task_count: int
    result_hash: str
    results: list[dict[str, Any]]


class WorkloadResponse(BaseModel):
    id: str
    name: str
    content_hash: str
    size_bytes: int
    validation_state: str
    manifest: WorkloadManifest
    created_at: datetime


class AuditEventResponse(BaseModel):
    id: str
    action: str
    scope_type: str
    scope_id: str | None
    outcome: str
    metadata: dict[str, Any]
    created_at: datetime


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "master-api"}


def workload_storage_path() -> Path:
    return Path(os.getenv("NEXORA_WORKLOAD_STORAGE", "backend/data/workloads"))


def workload_response(row) -> WorkloadResponse:
    return WorkloadResponse(
        id=row["id"],
        name=row["name"],
        content_hash=row["content_hash"],
        size_bytes=row["size_bytes"],
        validation_state=row["validation_state"],
        manifest=WorkloadManifest.model_validate(json.loads(row["manifest_json"])),
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def record_audit(connection, actor_id: str | None, action: str, scope_type: str, scope_id: str | None, outcome: str, metadata: dict[str, Any] | None = None) -> None:
    connection.execute(
        "INSERT INTO audit_events(id, actor_id, action, scope_type, scope_id, outcome, metadata_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), actor_id, action, scope_type, scope_id, outcome, canonical_json(metadata or {}), timestamp(utc_now())),
    )


def audit_response(row) -> AuditEventResponse:
    return AuditEventResponse(
        id=row["id"],
        action=row["action"],
        scope_type=row["scope_type"],
        scope_id=row["scope_id"],
        outcome=row["outcome"],
        metadata=json.loads(row["metadata_json"]),
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def user_response(connection, user_row) -> UserResponse:
    roles = [row["role"] for row in connection.execute("SELECT role FROM roles WHERE user_id = ?", (user_row["id"],))]
    return UserResponse(
        id=user_row["id"],
        full_name=user_row["full_name"],
        username=user_row["username"],
        email=user_row["email"],
        organization=user_row["organization"],
        roles=roles,
    )


def create_session(connection, user_id: str) -> tuple[str, datetime]:
    raw_token = secrets.token_urlsafe(32)
    now = utc_now()
    expires_at = now + timedelta(days=SESSION_DAYS)
    connection.execute(
        "INSERT INTO sessions(token_hash, user_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
        (hash_token(raw_token), user_id, timestamp(expires_at), timestamp(now)),
    )
    return raw_token, expires_at


def current_user(authorization: Annotated[str | None, Header()] = None) -> UserResponse:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bearer token required")
    token = authorization[7:].strip()
    with connection_scope() as connection:
        row = connection.execute(
            """
            SELECT users.* FROM sessions
            JOIN users ON users.id = sessions.user_id
            WHERE sessions.token_hash = ? AND sessions.revoked_at IS NULL
              AND users.is_active = 1 AND sessions.expires_at > ?
            """,
            (hash_token(token), timestamp(utc_now())),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired session")
        return user_response(connection, row)


CurrentUser = Annotated[UserResponse, Depends(current_user)]


@app.post("/api/v1/network/preflight")
def network_preflight(payload: NetworkPreflightRequest, user: CurrentUser) -> dict[str, Any]:
    if "master" not in user.roles and "worker_operator" not in user.roles:
        raise HTTPException(status_code=403, detail="A cluster role is required")
    return preflight(payload.master_host, payload.master_port)


@app.get("/api/v1/audit-events", response_model=list[AuditEventResponse])
def list_audit_events(user: CurrentUser) -> list[AuditEventResponse]:
    with connection_scope() as connection:
        rows = connection.execute(
            "SELECT * FROM audit_events WHERE actor_id = ? ORDER BY created_at DESC LIMIT 200",
            (user.id,),
        ).fetchall()
    return [audit_response(row) for row in rows]


@app.post("/api/v1/auth/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest) -> AuthResponse:
    requested_roles = set(payload.roles)
    if not requested_roles or not requested_roles.issubset({"master", "worker_operator"}):
        raise HTTPException(status_code=400, detail="roles must be master and/or worker_operator")
    user_id = str(uuid.uuid4())
    try:
        with connection_scope() as connection:
            connection.execute(
                """
                INSERT INTO users(id, full_name, username, email, organization, password_hash, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, payload.full_name.strip(), payload.username, payload.email, payload.organization, hash_password(payload.password), timestamp(utc_now())),
            )
            connection.executemany("INSERT INTO roles(user_id, role) VALUES (?, ?)", [(user_id, role) for role in requested_roles])
            record_audit(connection, user_id, "account.registered", "user", user_id, "success", {"roles": sorted(requested_roles)})
            token, expires_at = create_session(connection, user_id)
            user = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            return AuthResponse(access_token=token, expires_at=expires_at, user=user_response(connection, user))
    except Exception as error:
        if "UNIQUE constraint failed" in str(error):
            raise HTTPException(status_code=409, detail="Username or email is already registered") from error
        raise


@app.post("/api/v1/auth/login", response_model=AuthResponse)
def login(payload: LoginRequest) -> AuthResponse:
    with connection_scope() as connection:
        user = connection.execute("SELECT * FROM users WHERE username = ?", (payload.username,)).fetchone()
        if user is None or not verify_password(payload.password, user["password_hash"]):
            raise HTTPException(status_code=401, detail="Invalid credentials")
        token, expires_at = create_session(connection, user["id"])
        record_audit(connection, user["id"], "auth.login", "user", user["id"], "success")
        return AuthResponse(access_token=token, expires_at=expires_at, user=user_response(connection, user))


@app.post("/api/v1/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(authorization: Annotated[str | None, Header()] = None) -> None:
    if authorization and authorization.lower().startswith("bearer "):
        with connection_scope() as connection:
            token_hash = hash_token(authorization[7:].strip())
            session = connection.execute("SELECT user_id FROM sessions WHERE token_hash = ?", (token_hash,)).fetchone()
            connection.execute("UPDATE sessions SET revoked_at = ? WHERE token_hash = ?", (timestamp(utc_now()), token_hash))
            if session:
                record_audit(connection, session["user_id"], "auth.logout", "user", session["user_id"], "success")


@app.get("/api/v1/auth/me", response_model=UserResponse)
def me(user: CurrentUser) -> UserResponse:
    return user


@app.post("/api/v1/auth/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(payload: ChangePasswordRequest, user: CurrentUser) -> None:
    if payload.current_password == payload.new_password:
        raise HTTPException(status_code=400, detail="New password must differ from current password")
    with connection_scope() as connection:
        row = connection.execute("SELECT password_hash FROM users WHERE id = ?", (user.id,)).fetchone()
        if row is None or not verify_password(payload.current_password, row["password_hash"]):
            raise HTTPException(status_code=400, detail="Current password is incorrect")
        now = timestamp(utc_now())
        connection.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(payload.new_password), user.id))
        connection.execute("UPDATE sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL", (now, user.id))
        record_audit(connection, user.id, "auth.password_changed", "user", user.id, "success")


@app.post("/api/v1/clusters", response_model=ClusterResponse, status_code=status.HTTP_201_CREATED)
def create_cluster(payload: ClusterCreateRequest, user: CurrentUser) -> ClusterResponse:
    if "master" not in user.roles:
        raise HTTPException(status_code=403, detail="Master role required")
    cluster_id = str(uuid.uuid4())
    created_at = utc_now()
    with connection_scope() as connection:
        connection.execute(
            "INSERT INTO clusters(id, owner_id, name, created_at) VALUES (?, ?, ?, ?)",
            (cluster_id, user.id, payload.name.strip(), timestamp(created_at)),
        )
        record_audit(connection, user.id, "cluster.created", "cluster", cluster_id, "success", {"name": payload.name.strip()})
    return ClusterResponse(id=cluster_id, name=payload.name.strip(), status="active", created_at=created_at)


@app.get("/api/v1/clusters", response_model=list[ClusterResponse])
def list_clusters(user: CurrentUser) -> list[ClusterResponse]:
    with connection_scope() as connection:
        rows = connection.execute("SELECT * FROM clusters WHERE owner_id = ? ORDER BY created_at", (user.id,)).fetchall()
    return [ClusterResponse(id=row["id"], name=row["name"], status=row["status"], created_at=datetime.fromisoformat(row["created_at"])) for row in rows]


def require_master_cluster(connection, cluster_id: str, user: UserResponse):
    if "master" not in user.roles:
        raise HTTPException(status_code=403, detail="Master role required")
    cluster = connection.execute("SELECT * FROM clusters WHERE id = ? AND owner_id = ?", (cluster_id, user.id)).fetchone()
    if cluster is None:
        raise HTTPException(status_code=404, detail="Cluster not found")
    return cluster


def require_worker_node(connection, node_id: str, user: UserResponse):
    if "worker_operator" not in user.roles:
        raise HTTPException(status_code=403, detail="Worker Operator role required")
    node = connection.execute("SELECT * FROM nodes WHERE id = ? AND owner_id = ?", (node_id, user.id)).fetchone()
    if node is None:
        raise HTTPException(status_code=404, detail="Node not found")
    return node


def node_has_fresh_heartbeat(node, now: datetime) -> bool:
    if node["status"] != "online" or not node["last_heartbeat_at"]:
        return False
    return datetime.fromisoformat(node["last_heartbeat_at"]) >= now - timedelta(seconds=HEARTBEAT_STALE_SECONDS)


def node_response(row) -> NodeResponse:
    heartbeat = row["last_heartbeat_at"]
    return NodeResponse(
        id=row["id"],
        node_name=row["node_name"],
        hostname=row["hostname"],
        status=row["status"],
        membership_status=row["membership_status"],
        operating_system=row["operating_system"],
        private_ipv4=row["private_ipv4"],
        gpu_vendor=row["gpu_vendor"],
        gpu_model=row["gpu_model"],
        gpu_vram_bytes=row["gpu_vram_bytes"],
        compute_backend=row["compute_backend"],
        cpu_utilization=row["cpu_utilization"],
        ram_utilization=row["ram_utilization"],
        gpu_utilization=row["gpu_utilization"],
        gpu_used_vram_bytes=row["gpu_used_vram_bytes"],
        last_heartbeat_at=datetime.fromisoformat(heartbeat) if heartbeat else None,
    )


@app.post("/api/v1/clusters/{cluster_id}/pairing-codes", response_model=PairingCodeResponse)
def create_pairing_code(cluster_id: str, user: CurrentUser) -> PairingCodeResponse:
    now = utc_now()
    expires_at = now + timedelta(minutes=PAIRING_CODE_MINUTES)
    raw_code = secrets.token_hex(4).upper()
    with connection_scope() as connection:
        require_master_cluster(connection, cluster_id, user)
        connection.execute(
            "INSERT INTO pairing_codes(id, cluster_id, code_hash, expires_at, created_at) VALUES (?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), cluster_id, hash_token(raw_code), timestamp(expires_at), timestamp(now)),
        )
        record_audit(connection, user.id, "pairing.code_created", "cluster", cluster_id, "success", {"expires_at": timestamp(expires_at)})
    return PairingCodeResponse(code=raw_code, expires_at=expires_at)


@app.post("/api/v1/pairing/requests", response_model=NodeResponse, status_code=status.HTTP_201_CREATED)
def request_pairing(payload: PairingRequest, user: CurrentUser) -> NodeResponse:
    now = utc_now()
    with connection_scope() as connection:
        cluster_id = None
        pairing_id = None
        if payload.code and payload.code.strip():
            pairing = connection.execute(
                """
                SELECT * FROM pairing_codes
                WHERE code_hash = ? AND used_at IS NULL AND expires_at > ?
                """,
                (hash_token(payload.code.strip().upper()), timestamp(now)),
            ).fetchone()
            if pairing is None:
                raise HTTPException(status_code=400, detail="Pairing code is invalid, expired, or already used")
            cluster_id = pairing["cluster_id"]
            pairing_id = pairing["id"]
        else:
            cluster = connection.execute(
                "SELECT id FROM clusters WHERE status = 'active' ORDER BY created_at ASC LIMIT 1"
            ).fetchone()
            if cluster is None:
                raise HTTPException(status_code=400, detail="No active cluster found to accept join request")
            cluster_id = cluster["id"]

        node_id = str(uuid.uuid4())
        connection.execute(
            """
            INSERT INTO nodes(
                id, owner_id, node_name, hostname, operating_system, private_ipv4,
                cpu_model, cpu_threads, ram_total_bytes, gpu_vendor, gpu_model,
                gpu_vram_bytes, compute_backend, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (node_id, user.id, payload.node_name, payload.hostname, payload.operating_system, payload.private_ipv4, payload.cpu_model, payload.cpu_threads, payload.ram_total_bytes, payload.gpu_vendor, payload.gpu_model, payload.gpu_vram_bytes, payload.compute_backend, timestamp(now), timestamp(now)),
        )
        connection.execute("INSERT INTO cluster_members(cluster_id, node_id, status) VALUES (?, ?, 'pending')", (cluster_id, node_id))
        if pairing_id:
            connection.execute("UPDATE pairing_codes SET used_at = ? WHERE id = ?", (timestamp(now), pairing_id))
        record_audit(connection, user.id, "pairing.requested", "node", node_id, "success", {"cluster_id": cluster_id})
        row = connection.execute(
            """
            SELECT nodes.*, cluster_members.status AS membership_status
            FROM nodes JOIN cluster_members ON cluster_members.node_id = nodes.id
            WHERE nodes.id = ?
            """,
            (node_id,),
        ).fetchone()
    return node_response(row)


@app.post("/api/v1/clusters/{cluster_id}/members/{node_id}/approve", response_model=NodeResponse)
def approve_node(cluster_id: str, node_id: str, user: CurrentUser) -> NodeResponse:
    now = utc_now()
    with connection_scope() as connection:
        require_master_cluster(connection, cluster_id, user)
        updated = connection.execute(
            "UPDATE cluster_members SET status = 'approved', approved_at = ?, revoked_at = NULL WHERE cluster_id = ? AND node_id = ? AND status = 'pending'",
            (timestamp(now), cluster_id, node_id),
        )
        if updated.rowcount != 1:
            raise HTTPException(status_code=404, detail="Pending node membership not found")
        record_audit(connection, user.id, "node.approved", "node", node_id, "success", {"cluster_id": cluster_id})
        row = connection.execute(
            """
            SELECT nodes.*, cluster_members.status AS membership_status
            FROM nodes JOIN cluster_members ON cluster_members.node_id = nodes.id
            WHERE nodes.id = ? AND cluster_members.cluster_id = ?
            """,
            (node_id, cluster_id),
        ).fetchone()
    return node_response(row)


@app.post("/api/v1/clusters/{cluster_id}/members/{node_id}/revoke", response_model=NodeResponse)
def revoke_node(cluster_id: str, node_id: str, user: CurrentUser) -> NodeResponse:
    now = utc_now()
    with connection_scope() as connection:
        require_master_cluster(connection, cluster_id, user)
        updated = connection.execute(
            "UPDATE cluster_members SET status = 'revoked', revoked_at = ? WHERE cluster_id = ? AND node_id = ? AND status = 'approved'",
            (timestamp(now), cluster_id, node_id),
        )
        if updated.rowcount != 1:
            raise HTTPException(status_code=404, detail="Approved node membership not found")
        connection.execute("UPDATE nodes SET status = 'offline', updated_at = ? WHERE id = ?", (timestamp(now), node_id))
        record_audit(connection, user.id, "node.revoked", "node", node_id, "success", {"cluster_id": cluster_id})
        row = connection.execute(
            """
            SELECT nodes.*, cluster_members.status AS membership_status
            FROM nodes JOIN cluster_members ON cluster_members.node_id = nodes.id
            WHERE nodes.id = ? AND cluster_members.cluster_id = ?
            """,
            (node_id, cluster_id),
        ).fetchone()
    return node_response(row)


@app.get("/api/v1/clusters/{cluster_id}/nodes", response_model=list[NodeResponse])
def list_nodes(cluster_id: str, user: CurrentUser) -> list[NodeResponse]:
    with connection_scope() as connection:
        require_master_cluster(connection, cluster_id, user)
        connection.execute(
            """
            UPDATE nodes SET status = 'offline', updated_at = ?
            WHERE status = 'online' AND last_heartbeat_at < ?
              AND id IN (SELECT node_id FROM cluster_members WHERE cluster_id = ?)
            """,
            (timestamp(utc_now()), timestamp(utc_now() - timedelta(seconds=HEARTBEAT_STALE_SECONDS)), cluster_id),
        )
        rows = connection.execute(
            """
            SELECT nodes.*, cluster_members.status AS membership_status
            FROM nodes JOIN cluster_members ON cluster_members.node_id = nodes.id
            WHERE cluster_members.cluster_id = ? ORDER BY nodes.created_at
            """,
            (cluster_id,),
        ).fetchall()
    return [node_response(row) for row in rows]


@app.post("/api/v1/worker/nodes/{node_id}/heartbeat", response_model=NodeResponse)
def heartbeat(node_id: str, payload: HeartbeatRequest, user: CurrentUser) -> NodeResponse:
    now = utc_now()
    with connection_scope() as connection:
        node = require_worker_node(connection, node_id, user)
        membership = connection.execute(
            "SELECT status FROM cluster_members WHERE node_id = ?", (node_id,)
        ).fetchone()
        if membership is None or membership["status"] != "approved":
            raise HTTPException(status_code=403, detail="Node is not approved for a cluster")
        connection.execute(
            """
            UPDATE nodes SET status = ?, cpu_utilization = ?, ram_utilization = ?,
                gpu_utilization = ?, gpu_used_vram_bytes = ?, last_heartbeat_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (payload.status, payload.cpu_utilization, payload.ram_utilization, payload.gpu_utilization, payload.gpu_used_vram_bytes, timestamp(now), timestamp(now), node_id),
        )
        connection.execute(
            "INSERT INTO telemetry_samples(id, node_id, captured_at, source, cpu_utilization, ram_utilization, gpu_utilization, gpu_used_vram_bytes) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), node_id, timestamp(now), payload.telemetry_source, payload.cpu_utilization, payload.ram_utilization, payload.gpu_utilization, payload.gpu_used_vram_bytes),
        )
        row = connection.execute(
            """
            SELECT nodes.*, cluster_members.status AS membership_status
            FROM nodes JOIN cluster_members ON cluster_members.node_id = nodes.id
            WHERE nodes.id = ?
            """,
            (node_id,),
        ).fetchone()
    return node_response(row)


@app.get("/api/v1/clusters/{cluster_id}/telemetry", response_model=list[TelemetryResponse])
def list_telemetry(cluster_id: str, user: CurrentUser) -> list[TelemetryResponse]:
    with connection_scope() as connection:
        require_master_cluster(connection, cluster_id, user)
        rows = connection.execute(
            """
            SELECT telemetry_samples.*, nodes.node_name
            FROM telemetry_samples JOIN nodes ON nodes.id = telemetry_samples.node_id
            JOIN cluster_members ON cluster_members.node_id = nodes.id
            WHERE cluster_members.cluster_id = ?
            ORDER BY telemetry_samples.captured_at DESC LIMIT 1000
            """,
            (cluster_id,),
        ).fetchall()
    return [TelemetryResponse(node_id=row["node_id"], node_name=row["node_name"], captured_at=datetime.fromisoformat(row["captured_at"]), source=row["source"], cpu_utilization=row["cpu_utilization"], ram_utilization=row["ram_utilization"], gpu_utilization=row["gpu_utilization"], gpu_used_vram_bytes=row["gpu_used_vram_bytes"]) for row in rows]


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def json_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def task_response(row) -> TaskResponse:
    row_keys = row.keys() if hasattr(row, "keys") else []
    return TaskResponse(
        id=row["id"],
        job_id=row["job_id"],
        task_key=row["task_key"],
        payload=json.loads(row["payload_json"]),
        state=row["state"],
        attempt=row["attempt"],
        assigned_node_id=row["assigned_node_id"],
        pinned_node_id=row["pinned_node_id"] if "pinned_node_id" in row_keys else None,
        started_at=datetime.fromisoformat(row["started_at"]) if ("started_at" in row_keys and row["started_at"]) else None,
        completed_at=datetime.fromisoformat(row["completed_at"]) if ("completed_at" in row_keys and row["completed_at"]) else None,
        duration_seconds=row["duration_seconds"] if "duration_seconds" in row_keys else None,
        lease_expires_at=datetime.fromisoformat(row["lease_expires_at"]) if row["lease_expires_at"] else None,
        next_retry_at=datetime.fromisoformat(row["next_retry_at"]) if row["next_retry_at"] else None,
        result=json.loads(row["result_json"]) if row["result_json"] else None,
        result_hash=row["result_hash"],
        error=row["error"],
    )


def job_response(connection, row) -> JobResponse:
    counts = connection.execute(
        "SELECT state, COUNT(*) AS count FROM tasks WHERE job_id = ? GROUP BY state",
        (row["id"],),
    ).fetchall()
    state_counts = {item["state"]: item["count"] for item in counts}
    row_keys = row.keys() if hasattr(row, "keys") else []
    scheduling_policy = row["scheduling_policy"] if "scheduling_policy" in row_keys else "automatic"
    scheduling_config = (
        json.loads(row["scheduling_config_json"])
        if ("scheduling_config_json" in row_keys and row["scheduling_config_json"])
        else {}
    )
    return JobResponse(
        id=row["id"],
        cluster_id=row["cluster_id"],
        workload_id=row["workload_id"],
        name=row["name"],
        execution_mode=row["execution_mode"],
        scheduling_policy=scheduling_policy,
        scheduling_config=scheduling_config,
        priority=row["priority"],
        state=row["state"],
        retry_limit=row["retry_limit"],
        lease_seconds=row["lease_seconds"],
        task_count=sum(state_counts.values()),
        succeeded_count=state_counts.get("succeeded", 0),
        failed_count=state_counts.get("failed", 0),
        queued_count=state_counts.get("queued", 0),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def require_master_job(connection, job_id: str, user: UserResponse):
    if "master" not in user.roles:
        raise HTTPException(status_code=403, detail="Master role required")
    row = connection.execute("SELECT * FROM jobs WHERE id = ? AND owner_id = ?", (job_id, user.id)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return row


def require_master_workload(connection, workload_id: str, user: UserResponse):
    row = connection.execute(
        "SELECT * FROM workload_packages WHERE id = ? AND owner_id = ? AND validation_state = 'validated'",
        (workload_id, user.id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Validated workload not found")
    return row


def reconcile_job(connection, job_id: str, now: datetime) -> None:
    counts = connection.execute("SELECT state, COUNT(*) AS count FROM tasks WHERE job_id = ? GROUP BY state", (job_id,)).fetchall()
    state_counts = {item["state"]: item["count"] for item in counts}
    total = sum(state_counts.values())
    if state_counts.get("succeeded", 0) == total and total > 0:
        state = "completed"
    elif state_counts.get("failed", 0) > 0 and not any(state_counts.get(active, 0) for active in ("queued", "leased", "running", "retrying")):
        state = "failed"
    elif state_counts.get("leased", 0) or state_counts.get("running", 0) or state_counts.get("queued", 0):
        state = "running"
    else:
        return
    connection.execute("UPDATE jobs SET state = ?, updated_at = ? WHERE id = ?", (state, timestamp(now), job_id))


@app.post("/api/v1/jobs", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
def create_job(payload: JobCreateRequest, user: CurrentUser) -> JobResponse:
    now = utc_now()
    task_keys = [task.task_key for task in payload.tasks]
    if len(task_keys) != len(set(task_keys)):
        raise HTTPException(status_code=400, detail="task_key values must be unique within a job")
    with connection_scope() as connection:
        require_master_cluster(connection, payload.cluster_id, user)
        workload = require_master_workload(connection, payload.workload_id, user) if payload.workload_id else None
        if workload:
            manifest = WorkloadManifest.model_validate(json.loads(workload["manifest_json"]))
            mismatched_tasks = [
                task.task_key
                for task in payload.tasks
                if task.payload.get("operation") != manifest.operation
                or task.payload.get("backend", "cpu") != manifest.backend
            ]
            if mismatched_tasks:
                raise HTTPException(status_code=400, detail=f"Tasks do not match workload operation: {mismatched_tasks}")
        
        # Pinned node resolution for Manual / Hybrid scheduling
        pinned_map: dict[str, str | None] = {}
        for task in payload.tasks:
            if task.pinned_node_id:
                pinned_map[task.task_key] = task.pinned_node_id

        allocations = payload.scheduling_config.get("allocations") if isinstance(payload.scheduling_config, dict) else None
        if payload.scheduling_policy in ("manual", "hybrid") and isinstance(allocations, dict) and allocations:
            total_tasks = len(payload.tasks)
            alloc_sum = sum(v for v in allocations.values() if isinstance(v, (int, float)))
            is_percentage = alloc_sum > 0 and 95 <= alloc_sum <= 105
            
            task_targets: list[str] = []
            for node_id, weight in allocations.items():
                if not isinstance(weight, (int, float)) or weight <= 0:
                    continue
                count = int(round((weight / 100.0) * total_tasks)) if is_percentage else int(weight)
                task_targets.extend([node_id] * count)
            
            target_idx = 0
            for task in payload.tasks:
                if task.task_key not in pinned_map and target_idx < len(task_targets):
                    pinned_map[task.task_key] = task_targets[target_idx]
                    target_idx += 1

        job_id = str(uuid.uuid4())
        connection.execute(
            """
            INSERT INTO jobs(
                id, cluster_id, owner_id, workload_id, name, execution_mode,
                scheduling_policy, scheduling_config_json, priority, retry_limit,
                lease_seconds, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                payload.cluster_id,
                user.id,
                payload.workload_id,
                payload.name.strip(),
                payload.execution_mode,
                payload.scheduling_policy,
                canonical_json(payload.scheduling_config),
                payload.priority,
                payload.retry_limit,
                payload.lease_seconds,
                timestamp(now),
                timestamp(now),
            ),
        )
        connection.executemany(
            """
            INSERT INTO tasks(id, job_id, task_key, payload_json, payload_hash, pinned_node_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    str(uuid.uuid4()),
                    job_id,
                    task.task_key,
                    canonical_json(task.payload),
                    json_hash(task.payload),
                    pinned_map.get(task.task_key),
                    timestamp(now),
                    timestamp(now),
                )
                for task in payload.tasks
            ],
        )
        record_audit(
            connection,
            user.id,
            "job.created",
            "job",
            job_id,
            "success",
            {
                "task_count": len(payload.tasks),
                "workload_id": payload.workload_id,
                "scheduling_policy": payload.scheduling_policy,
            },
        )
        row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return job_response(connection, row)


@app.get("/api/v1/jobs/{job_id}", response_model=JobResponse)
def get_job(job_id: str, user: CurrentUser) -> JobResponse:
    with connection_scope() as connection:
        row = require_master_job(connection, job_id, user)
        return job_response(connection, row)


@app.get("/api/v1/jobs", response_model=list[JobResponse])
def list_jobs(user: CurrentUser) -> list[JobResponse]:
    with connection_scope() as connection:
        rows = connection.execute("SELECT * FROM jobs WHERE owner_id = ? ORDER BY created_at DESC", (user.id,)).fetchall()
        return [job_response(connection, row) for row in rows]


@app.get("/api/v1/jobs/{job_id}/tasks", response_model=list[TaskResponse])
def list_tasks(job_id: str, user: CurrentUser) -> list[TaskResponse]:
    with connection_scope() as connection:
        require_master_job(connection, job_id, user)
        rows = connection.execute("SELECT * FROM tasks WHERE job_id = ? ORDER BY task_key", (job_id,)).fetchall()
    return [task_response(row) for row in rows]


@app.get("/api/v1/jobs/{job_id}/result", response_model=FinalResultResponse)
def get_final_result(job_id: str, user: CurrentUser) -> FinalResultResponse:
    with connection_scope() as connection:
        job = require_master_job(connection, job_id, user)
        if job["state"] != "completed":
            raise HTTPException(status_code=409, detail=f"Job is {job['state']}; required tasks are not complete")
        rows = connection.execute(
            "SELECT task_key, result_json FROM tasks WHERE job_id = ? AND state = 'succeeded' ORDER BY task_key",
            (job_id,),
        ).fetchall()
    results = [{"task_key": row["task_key"], "result": json.loads(row["result_json"])} for row in rows]
    return FinalResultResponse(job_id=job_id, aggregation="collect", task_count=len(results), result_hash=json_hash(results), results=results)


@app.get("/api/v1/jobs/{job_id}/report")
def get_job_report(job_id: str, user: CurrentUser, format: str | None = None) -> Any:
    with connection_scope() as connection:
        job = require_master_job(connection, job_id, user)
        tasks_rows = connection.execute(
            """
            SELECT tasks.*, nodes.node_name FROM tasks
            LEFT JOIN nodes ON nodes.id = tasks.assigned_node_id
            WHERE tasks.job_id = ? ORDER BY tasks.task_key
            """,
            (job_id,),
        ).fetchall()

        node_stats: dict[str, dict[str, Any]] = {}
        report_tasks: list[TaskReportItem] = []
        for row in tasks_rows:
            node_id = row["assigned_node_id"] or "unassigned"
            node_name = row["node_name"] or "Unassigned"
            if node_id not in node_stats and node_id != "unassigned":
                node_stats[node_id] = {
                    "node_id": node_id,
                    "node_name": node_name,
                    "tasks_completed": 0,
                    "tasks_failed": 0,
                    "total_compute_seconds": 0.0,
                }
            if row["state"] == "succeeded" and node_id in node_stats:
                node_stats[node_id]["tasks_completed"] += 1
                if row["duration_seconds"]:
                    node_stats[node_id]["total_compute_seconds"] += float(row["duration_seconds"])
            elif row["state"] == "failed" and node_id in node_stats:
                node_stats[node_id]["tasks_failed"] += 1

            report_tasks.append(
                TaskReportItem(
                    task_key=row["task_key"],
                    state=row["state"],
                    attempt=row["attempt"],
                    assigned_node_id=row["assigned_node_id"],
                    node_name=row["node_name"],
                    started_at=datetime.fromisoformat(row["started_at"]) if row["started_at"] else None,
                    completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
                    duration_seconds=row["duration_seconds"],
                    error=row["error"],
                )
            )

        nodes_summary = []
        for n_id, data in node_stats.items():
            completed = data["tasks_completed"]
            tot_sec = round(data["total_compute_seconds"], 3)
            avg_sec = round(tot_sec / completed, 3) if completed > 0 else 0.0
            nodes_summary.append(
                NodePerformanceSummary(
                    node_id=n_id,
                    node_name=data["node_name"],
                    tasks_completed=completed,
                    tasks_failed=data["tasks_failed"],
                    total_compute_seconds=tot_sec,
                    avg_task_duration_seconds=avg_sec,
                )
            )

        task_count = len(report_tasks)
        succeeded_count = sum(1 for t in report_tasks if t.state == "succeeded")
        failed_count = sum(1 for t in report_tasks if t.state == "failed")

        created = datetime.fromisoformat(job["created_at"])
        updated = datetime.fromisoformat(job["updated_at"])
        total_duration = (updated - created).total_seconds() if job["state"] in ("completed", "failed", "canceled") else None

        row_keys = job.keys() if hasattr(job, "keys") else []
        scheduling_policy = job["scheduling_policy"] if "scheduling_policy" in row_keys else "automatic"

        report = JobReportResponse(
            job_id=job["id"],
            job_name=job["name"],
            cluster_id=job["cluster_id"],
            state=job["state"],
            execution_mode=job["execution_mode"],
            scheduling_policy=scheduling_policy,
            priority=job["priority"],
            task_count=task_count,
            succeeded_count=succeeded_count,
            failed_count=failed_count,
            total_duration_seconds=round(total_duration, 2) if total_duration else None,
            created_at=created,
            updated_at=updated,
            nodes=nodes_summary,
            tasks=report_tasks,
        )

        if format == "markdown":
            from fastapi.responses import PlainTextResponse
            md_lines = [
                f"# NEXORA Job Execution Report: {report.job_name}",
                f"- **Job ID**: `{report.job_id}`",
                f"- **Cluster ID**: `{report.cluster_id}`",
                f"- **State**: **{report.state.upper()}**",
                f"- **Scheduling Policy**: {report.scheduling_policy}",
                f"- **Tasks Summary**: {report.succeeded_count}/{report.task_count} succeeded, {report.failed_count} failed",
                f"- **Total Wall Duration**: {report.total_duration_seconds or 'In-progress'} seconds",
                f"- **Created At**: {report.created_at.isoformat()}",
                f"- **Updated At**: {report.updated_at.isoformat()}",
                "",
                "## Node Contribution Summary",
                "| Node Name | Node ID | Succeeded | Failed | Total Compute (s) | Avg Duration (s) |",
                "| :--- | :--- | :--- | :--- | :--- | :--- |",
            ]
            for n in report.nodes:
                md_lines.append(f"| {n.node_name} | `{n.node_id}` | {n.tasks_completed} | {n.tasks_failed} | {n.total_compute_seconds} | {n.avg_task_duration_seconds} |")
            if not report.nodes:
                md_lines.append("| None | — | 0 | 0 | 0.0 | 0.0 |")
            md_lines.extend([
                "",
                "## Task Execution Details",
                "| Task Key | State | Attempt | Assigned Node | Duration (s) | Error |",
                "| :--- | :--- | :--- | :--- | :--- | :--- |",
            ])
            for t in report.tasks:
                md_lines.append(f"| {t.task_key} | {t.state} | {t.attempt} | {t.node_name or 'Unassigned'} | {t.duration_seconds or '—'} | {t.error or 'None'} |")
            return PlainTextResponse("\n".join(md_lines), media_type="text/markdown")

        return report


@app.get("/api/v1/clusters/{cluster_id}/summary", response_model=ClusterSummaryResponse)
def get_cluster_summary(cluster_id: str, user: CurrentUser) -> ClusterSummaryResponse:
    with connection_scope() as connection:
        cluster = require_master_cluster(connection, cluster_id, user)
        connection.execute(
            """
            UPDATE nodes SET status = 'offline', updated_at = ?
            WHERE status = 'online' AND last_heartbeat_at < ?
              AND id IN (SELECT node_id FROM cluster_members WHERE cluster_id = ?)
            """,
            (timestamp(utc_now()), timestamp(utc_now() - timedelta(seconds=HEARTBEAT_STALE_SECONDS)), cluster_id),
        )
        nodes = connection.execute(
            """
            SELECT nodes.* FROM nodes
            JOIN cluster_members ON cluster_members.node_id = nodes.id
            WHERE cluster_members.cluster_id = ?
            """,
            (cluster_id,),
        ).fetchall()
        jobs = connection.execute(
            "SELECT state FROM jobs WHERE cluster_id = ?",
            (cluster_id,),
        ).fetchall()

        reg_count = len(nodes)
        online_count = sum(1 for n in nodes if n["status"] == "online")
        offline_count = sum(1 for n in nodes if n["status"] == "offline")
        detected_gpus = sum(1 for n in nodes if n["gpu_model"])
        total_vram = sum((n["gpu_vram_bytes"] or 0) for n in nodes)

        active_jobs = sum(1 for j in jobs if j["state"] in ("running", "paused"))
        queued_jobs = sum(1 for j in jobs if j["state"] == "queued")
        completed_jobs = sum(1 for j in jobs if j["state"] == "completed")
        failed_jobs = sum(1 for j in jobs if j["state"] == "failed")

        return ClusterSummaryResponse(
            cluster_id=cluster_id,
            cluster_name=cluster["name"],
            registered_nodes=reg_count,
            online_nodes=online_count,
            offline_nodes=offline_count,
            detected_gpus=detected_gpus,
            total_vram_bytes=total_vram,
            active_jobs=active_jobs,
            queued_jobs=queued_jobs,
            completed_jobs=completed_jobs,
            failed_jobs=failed_jobs,
        )


@app.get("/api/v1/worker/my-nodes", response_model=list[NodeResponse])
def list_my_worker_nodes(user: CurrentUser) -> list[NodeResponse]:
    with connection_scope() as connection:
        rows = connection.execute(
            """
            SELECT nodes.*, COALESCE(cluster_members.status, 'unpaired') AS membership_status
            FROM nodes
            LEFT JOIN cluster_members ON cluster_members.node_id = nodes.id
            WHERE nodes.owner_id = ?
            ORDER BY nodes.created_at DESC
            """,
            (user.id,),
        ).fetchall()
    return [node_response(row) for row in rows]


@app.post("/api/v1/worker/nodes/{node_id}/drain", response_model=NodeResponse)
def drain_worker_node(node_id: str, user: CurrentUser) -> NodeResponse:
    now = utc_now()
    with connection_scope() as connection:
        require_worker_node(connection, node_id, user)
        connection.execute(
            "UPDATE nodes SET status = 'draining', updated_at = ? WHERE id = ?",
            (timestamp(now), node_id),
        )
        row = connection.execute(
            """
            SELECT nodes.*, COALESCE(cluster_members.status, 'unpaired') AS membership_status
            FROM nodes
            LEFT JOIN cluster_members ON cluster_members.node_id = nodes.id
            WHERE nodes.id = ?
            """,
            (node_id,),
        ).fetchone()
        record_audit(connection, user.id, "node.drain", "node", node_id, "success")
    return node_response(row)


def update_job_state(job_id: str, user: UserResponse, expected_states: tuple[str, ...], next_state: str) -> JobResponse:
    now = utc_now()
    with connection_scope() as connection:
        job = require_master_job(connection, job_id, user)
        if job["state"] not in expected_states:
            raise HTTPException(status_code=409, detail=f"Job is {job['state']} and cannot become {next_state}")
        connection.execute("UPDATE jobs SET state = ?, updated_at = ? WHERE id = ?", (next_state, timestamp(now), job_id))
        if next_state == "canceled":
            connection.execute(
                "UPDATE tasks SET state = 'canceled', lease_token_hash = NULL, lease_expires_at = NULL, updated_at = ? WHERE job_id = ? AND state NOT IN ('succeeded', 'failed', 'canceled')",
                (timestamp(now), job_id),
            )
        record_audit(connection, user.id, f"job.{next_state}", "job", job_id, "success")
        updated = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return job_response(connection, updated)


@app.post("/api/v1/jobs/{job_id}/pause", response_model=JobResponse)
def pause_job(job_id: str, user: CurrentUser) -> JobResponse:
    return update_job_state(job_id, user, ("queued", "running"), "paused")


@app.post("/api/v1/jobs/{job_id}/resume", response_model=JobResponse)
def resume_job(job_id: str, user: CurrentUser) -> JobResponse:
    return update_job_state(job_id, user, ("paused",), "running")


@app.post("/api/v1/jobs/{job_id}/cancel", response_model=JobResponse)
def cancel_job(job_id: str, user: CurrentUser) -> JobResponse:
    return update_job_state(job_id, user, ("queued", "running", "paused"), "canceled")


def recover_expired_leases(connection, now: datetime) -> None:
    expired = connection.execute(
        """
        SELECT tasks.*, jobs.retry_limit FROM tasks JOIN jobs ON jobs.id = tasks.job_id
        WHERE tasks.state IN ('leased', 'running') AND tasks.lease_expires_at <= ?
        """,
        (timestamp(now),),
    ).fetchall()
    for row in expired:
        next_state = "queued" if row["attempt"] < row["retry_limit"] + 1 else "failed"
        retry_at = now + timedelta(seconds=retry_delay_seconds(row["attempt"])) if next_state == "queued" else None
        connection.execute(
            """
            UPDATE tasks SET state = ?, assigned_node_id = NULL, lease_token_hash = NULL,
                lease_expires_at = NULL, next_retry_at = ?, error = ?, updated_at = ? WHERE id = ?
            """,
            (next_state, timestamp(retry_at) if retry_at else None, "Lease expired" if next_state == "failed" else None, timestamp(now), row["id"]),
        )
        connection.execute(
            "UPDATE task_attempts SET state = 'timed_out', completed_at = ?, error = ? WHERE task_id = ? AND attempt = ? AND completed_at IS NULL",
            (timestamp(now), "Lease expired", row["id"], row["attempt"]),
        )
        reconcile_job(connection, row["job_id"], now)


def retry_delay_seconds(attempt: int) -> int:
    return min(60, 2 ** max(0, attempt - 1))


def task_is_compatible_with_node(task_payload: dict[str, Any], node) -> bool:
    backend = task_payload.get("backend", "cpu")
    if backend == "opencl" and (node["compute_backend"] or "").lower() != "opencl":
        return False
    required_threads = task_payload.get("required_cpu_threads", 0)
    if isinstance(required_threads, (int, float)) and node["cpu_threads"] is not None and required_threads > node["cpu_threads"]:
        return False
    required_vram = task_payload.get("required_gpu_memory_bytes", 0)
    if backend == "opencl":
        available_vram = (node["gpu_vram_bytes"] or 0) - (node["gpu_used_vram_bytes"] or 0)
        if not isinstance(required_vram, (int, float)) or required_vram > available_vram:
            return False
    return True


@app.post("/api/v1/worker/nodes/{node_id}/tasks/lease", response_model=TaskLeaseResponse | None)
def lease_task(node_id: str, user: CurrentUser) -> TaskLeaseResponse | None:
    now = utc_now()
    with connection_scope() as connection:
        node = require_worker_node(connection, node_id, user)
        membership = connection.execute(
            "SELECT cluster_id, status FROM cluster_members WHERE node_id = ?", (node_id,)
        ).fetchone()
        if membership is None or membership["status"] != "approved":
            raise HTTPException(status_code=403, detail="Node is not approved for a cluster")
        if not node_has_fresh_heartbeat(node, now):
            raise HTTPException(status_code=409, detail="Node heartbeat is stale or offline")
        recover_expired_leases(connection, now)
        candidates = connection.execute(
            """
            SELECT tasks.*, jobs.lease_seconds FROM tasks
            JOIN jobs ON jobs.id = tasks.job_id
            WHERE jobs.cluster_id = ? AND jobs.state IN ('queued', 'running')
              AND tasks.state = 'queued' AND (tasks.next_retry_at IS NULL OR tasks.next_retry_at <= ?)
              AND (tasks.pinned_node_id IS NULL OR tasks.pinned_node_id = ?)
            ORDER BY jobs.priority DESC, jobs.created_at, tasks.created_at LIMIT 100
            """,
            (membership["cluster_id"], timestamp(now), node_id),
        ).fetchall()
        row = next((candidate for candidate in candidates if task_is_compatible_with_node(json.loads(candidate["payload_json"]), node)), None)
        if row is None:
            return None
        lease_token = secrets.token_urlsafe(32)
        lease_expires_at = now + timedelta(seconds=row["lease_seconds"])
        attempt = row["attempt"] + 1
        started_at = row["started_at"] or timestamp(now)
        connection.execute(
            """
            UPDATE tasks SET state = 'leased', attempt = ?, assigned_node_id = ?,
                lease_token_hash = ?, lease_expires_at = ?, next_retry_at = NULL, started_at = ?, updated_at = ? WHERE id = ?
            """,
            (attempt, node_id, hash_token(lease_token), timestamp(lease_expires_at), started_at, timestamp(now), row["id"]),
        )
        connection.execute(
            "INSERT INTO task_attempts(id, task_id, attempt, node_id, state, leased_at) VALUES (?, ?, ?, ?, 'leased', ?)",
            (str(uuid.uuid4()), row["id"], attempt, node_id, timestamp(now)),
        )
        connection.execute("UPDATE jobs SET state = 'running', updated_at = ? WHERE id = ?", (timestamp(now), row["job_id"]))
        leased = connection.execute("SELECT * FROM tasks WHERE id = ?", (row["id"],)).fetchone()
    return TaskLeaseResponse(lease_token=lease_token, task=task_response(leased))


@app.post("/api/v1/worker/tasks/{task_id}/result", response_model=TaskResponse)
def submit_task_result(task_id: str, payload: TaskResultRequest, user: CurrentUser) -> TaskResponse:
    now = utc_now()
    if payload.status == "failed" and not payload.error:
        raise HTTPException(status_code=400, detail="error is required for a failed task")
    with connection_scope() as connection:
        task = connection.execute(
            """
            SELECT tasks.*, jobs.retry_limit FROM tasks JOIN jobs ON jobs.id = tasks.job_id
            WHERE tasks.id = ?
            """,
            (task_id,),
        ).fetchone()
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")
        node = require_worker_node(connection, task["assigned_node_id"], user)
        if task["state"] == "succeeded":
            return task_response(task)
        if task["state"] not in ("leased", "running") or task["lease_expires_at"] <= timestamp(now):
            raise HTTPException(status_code=409, detail="Task lease is no longer active")
        if not hmac.compare_digest(task["lease_token_hash"], hash_token(payload.lease_token)):
            raise HTTPException(status_code=409, detail="Invalid task lease token")
        result_json = canonical_json(payload.result) if payload.result is not None else None
        result_hash = json_hash(payload.result) if payload.result is not None else None
        if payload.status == "succeeded":
            next_state = "succeeded"
            next_error = None
        else:
            next_state = "queued" if task["attempt"] < task["retry_limit"] + 1 else "failed"
            next_error = payload.error
        retry_at = now + timedelta(seconds=retry_delay_seconds(task["attempt"])) if next_state == "queued" else None
        
        started_at = task["started_at"]
        duration_seconds = None
        if started_at:
            try:
                duration_seconds = round(max(0.0, (now - datetime.fromisoformat(started_at)).total_seconds()), 3)
            except Exception:
                pass
        completed_at = timestamp(now) if payload.status == "succeeded" else None

        connection.execute(
            """
            UPDATE tasks SET state = ?, assigned_node_id = ?, lease_token_hash = NULL,
                lease_expires_at = NULL, next_retry_at = ?, result_json = ?, result_hash = ?, error = ?,
                completed_at = ?, duration_seconds = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                next_state,
                task["assigned_node_id"] if next_state == "succeeded" else None,
                timestamp(retry_at) if retry_at else None,
                result_json,
                result_hash,
                next_error,
                completed_at,
                duration_seconds,
                timestamp(now),
                task_id,
            ),
        )
        connection.execute(
            "UPDATE task_attempts SET state = ?, completed_at = ?, error = ? WHERE task_id = ? AND attempt = ?",
            (payload.status, timestamp(now), payload.error, task_id, task["attempt"]),
        )
        reconcile_job(connection, task["job_id"], now)
        updated = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return task_response(updated)


@app.post("/api/v1/workloads", response_model=WorkloadResponse, status_code=status.HTTP_201_CREATED)
async def upload_workload(
    manifest: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
    user: CurrentUser,
) -> WorkloadResponse:
    if not file.filename:
        raise HTTPException(status_code=400, detail="Workload filename is required")
    try:
        parsed_manifest = parse_manifest(manifest)
        data = await file.read(MAX_WORKLOAD_BYTES + 1)
        if len(data) > MAX_WORKLOAD_BYTES:
            raise ValueError(f"Workload exceeds the {MAX_WORKLOAD_BYTES} byte limit")
        validate_archive(data, file.filename)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    now = utc_now()
    workload_id = str(uuid.uuid4())
    content_hash = hashlib.sha256(data).hexdigest()
    storage_directory = workload_storage_path()
    storage_directory.mkdir(parents=True, exist_ok=True)
    storage_path = storage_directory / f"{workload_id}.zip"
    storage_path.write_bytes(data)
    with connection_scope() as connection:
        connection.execute(
            """
            INSERT INTO workload_packages(
                id, owner_id, name, manifest_json, content_hash, size_bytes,
                validation_state, storage_path, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'validated', ?, ?)
            """,
            (workload_id, user.id, parsed_manifest.name, parsed_manifest.model_dump_json(), content_hash, len(data), str(storage_path), timestamp(now)),
        )
        record_audit(connection, user.id, "workload.validated", "workload", workload_id, "success", {"content_hash": content_hash, "size_bytes": len(data)})
        row = connection.execute("SELECT * FROM workload_packages WHERE id = ?", (workload_id,)).fetchone()
    return workload_response(row)


@app.get("/api/v1/workloads", response_model=list[WorkloadResponse])
def list_workloads(user: CurrentUser) -> list[WorkloadResponse]:
    with connection_scope() as connection:
        rows = connection.execute(
            "SELECT * FROM workload_packages WHERE owner_id = ? ORDER BY created_at DESC", (user.id,)
        ).fetchall()
    return [workload_response(row) for row in rows]