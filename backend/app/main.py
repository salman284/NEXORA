from __future__ import annotations

from __future__ import annotations
import hashlib
import hmac
import io
import json
import os
import secrets
import uuid
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any

from dotenv import load_dotenv
load_dotenv()

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, EmailStr, Field

from .mongo_db import connection_scope, initialize_database, utcnow_dt, future_dt
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


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def json_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


# ---------------------------------------------------------------------------
# Pydantic models (unchanged)
# ---------------------------------------------------------------------------

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
    cpu_threads: int | None = None
    gpu_vendor: str | None
    gpu_model: str | None
    gpu_vram_bytes: int | None
    compute_backend: str | None
    cpu_utilization: float | None
    ram_utilization: float | None
    gpu_utilization: float | None
    gpu_used_vram_bytes: int | None
    last_heartbeat_at: datetime | None
    tasks_completed: int = 0
    tasks_failed: int = 0


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
    workload_id: str | None = None
    task_key: str
    payload: dict[str, Any]
    state: str
    attempt: int
    assigned_node_id: str | None
    assigned_node_name: str | None = None
    pinned_node_id: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_seconds: float | None = None
    lease_expires_at: datetime | None
    next_retry_at: datetime | None
    result: dict[str, Any] | None
    result_hash: str | None
    error: str | None
    has_artifacts: bool = False
    artifact_size_bytes: int | None = None


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


class TaskRenewRequest(BaseModel):
    lease_token: str = Field(min_length=20, max_length=256)


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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "master-api"}


def workload_storage_path() -> Path:
    return Path(os.getenv("NEXORA_WORKLOAD_STORAGE", "backend/data/workloads"))


def artifact_storage_path() -> Path:
    return Path(os.getenv("NEXORA_ARTIFACT_STORAGE", "backend/data/artifacts"))


def _parse_dt(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    dt = datetime.fromisoformat(str(value))
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def node_response(node_doc: dict, membership_status: str, tasks_completed: int = 0, tasks_failed: int = 0) -> NodeResponse:
    hb = node_doc.get("last_heartbeat_at")
    return NodeResponse(
        id=node_doc["node_id"],
        node_name=node_doc["node_name"],
        hostname=node_doc["hostname"],
        status=node_doc.get("status", "offline"),
        membership_status=membership_status,
        operating_system=node_doc.get("operating_system"),
        private_ipv4=node_doc.get("private_ipv4"),
        cpu_threads=node_doc.get("cpu_threads"),
        gpu_vendor=node_doc.get("gpu_vendor"),
        gpu_model=node_doc.get("gpu_model"),
        gpu_vram_bytes=node_doc.get("gpu_vram_bytes"),
        compute_backend=node_doc.get("compute_backend"),
        cpu_utilization=node_doc.get("cpu_utilization"),
        ram_utilization=node_doc.get("ram_utilization"),
        gpu_utilization=node_doc.get("gpu_utilization"),
        gpu_used_vram_bytes=node_doc.get("gpu_used_vram_bytes"),
        last_heartbeat_at=_parse_dt(hb),
        tasks_completed=tasks_completed,
        tasks_failed=tasks_failed,
    )


def task_response(doc: dict, workload_id: str | None = None, node_name: str | None = None) -> TaskResponse:
    return TaskResponse(
        id=doc["task_id"],
        job_id=doc["job_id"],
        workload_id=workload_id or doc.get("workload_id"),
        task_key=doc["task_key"],
        payload=json.loads(doc["payload_json"]),
        state=doc["state"],
        attempt=doc.get("attempt", 0),
        assigned_node_id=doc.get("assigned_node_id"),
        assigned_node_name=node_name or doc.get("assigned_node_name"),
        pinned_node_id=doc.get("pinned_node_id"),
        started_at=_parse_dt(doc.get("started_at")),
        completed_at=_parse_dt(doc.get("completed_at")),
        duration_seconds=doc.get("duration_seconds"),
        lease_expires_at=_parse_dt(doc.get("lease_expires_at")),
        next_retry_at=_parse_dt(doc.get("next_retry_at")),
        result=json.loads(doc["result_json"]) if doc.get("result_json") else None,
        result_hash=doc.get("result_hash"),
        error=doc.get("error"),
        has_artifacts=bool(doc.get("has_artifacts")),
        artifact_size_bytes=doc.get("artifact_size_bytes"),
    )


def job_response(db, doc: dict) -> JobResponse:
    job_id = doc["job_id"]
    pipeline = [
        {"$match": {"job_id": job_id}},
        {"$group": {"_id": "$state", "count": {"$sum": 1}}},
    ]
    state_counts: dict[str, int] = {}
    for item in db.tasks.aggregate(pipeline):
        state_counts[item["_id"]] = item["count"]
    return JobResponse(
        id=doc["job_id"],
        cluster_id=doc["cluster_id"],
        workload_id=doc.get("workload_id"),
        name=doc["name"],
        execution_mode=doc["execution_mode"],
        scheduling_policy=doc.get("scheduling_policy", "automatic"),
        scheduling_config=json.loads(doc.get("scheduling_config_json", "{}")),
        priority=doc.get("priority", 0),
        state=doc["state"],
        retry_limit=doc.get("retry_limit", 2),
        lease_seconds=doc.get("lease_seconds", 60),
        task_count=sum(state_counts.values()),
        succeeded_count=state_counts.get("succeeded", 0),
        failed_count=state_counts.get("failed", 0),
        queued_count=state_counts.get("queued", 0),
        created_at=_parse_dt(doc["created_at"]),
        updated_at=_parse_dt(doc["updated_at"]),
    )


def workload_response(doc: dict) -> WorkloadResponse:
    return WorkloadResponse(
        id=doc["workload_id"],
        name=doc["name"],
        content_hash=doc["content_hash"],
        size_bytes=doc["size_bytes"],
        validation_state=doc["validation_state"],
        manifest=WorkloadManifest.model_validate(json.loads(doc["manifest_json"])),
        created_at=_parse_dt(doc["created_at"]),
    )


def audit_response(doc: dict) -> AuditEventResponse:
    return AuditEventResponse(
        id=doc["event_id"],
        action=doc["action"],
        scope_type=doc["scope_type"],
        scope_id=doc.get("scope_id"),
        outcome=doc["outcome"],
        metadata=json.loads(doc.get("metadata_json", "{}")),
        created_at=_parse_dt(doc["created_at"]),
    )


def record_audit(db, actor_id: str | None, action: str, scope_type: str, scope_id: str | None, outcome: str, metadata: dict[str, Any] | None = None) -> None:
    db.audit_events.insert_one({
        "event_id": str(uuid.uuid4()),
        "actor_id": actor_id,
        "action": action,
        "scope_type": scope_type,
        "scope_id": scope_id,
        "outcome": outcome,
        "metadata_json": canonical_json(metadata or {}),
        "created_at": utc_now(),
    })


def user_response_from_doc(doc: dict) -> UserResponse:
    return UserResponse(
        id=doc["user_id"],
        full_name=doc["full_name"],
        username=doc["username"],
        email=doc["email"],
        organization=doc.get("organization"),
        roles=doc.get("roles", []),
    )


def create_session(db, user_id: str) -> tuple[str, datetime]:
    raw_token = secrets.token_urlsafe(32)
    now = utc_now()
    expires_at = now + timedelta(days=SESSION_DAYS)
    db.sessions.insert_one({
        "token_hash": hash_token(raw_token),
        "user_id": user_id,
        "expires_at": expires_at,
        "revoked_at": None,
        "created_at": now,
    })
    return raw_token, expires_at


def node_has_fresh_heartbeat(node_doc: dict, now: datetime) -> bool:
    if node_doc.get("status") != "online":
        return False
    hb = node_doc.get("last_heartbeat_at")
    if not hb:
        return False
    hb_dt = _parse_dt(hb)
    if hb_dt is None:
        return False
    if hb_dt.tzinfo is None:
        hb_dt = hb_dt.replace(tzinfo=timezone.utc)
    return hb_dt >= now - timedelta(seconds=HEARTBEAT_STALE_SECONDS)


# ---------------------------------------------------------------------------
# Auth Guards
# ---------------------------------------------------------------------------

def current_user(authorization: Annotated[str | None, Header()] = None) -> UserResponse:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bearer token required")
    token = authorization[7:].strip()
    with connection_scope() as db:
        token_hash = hash_token(token)
        now = utc_now()
        session = db.sessions.find_one({
            "token_hash": token_hash,
            "revoked_at": None,
            "expires_at": {"$gt": now},
        })
        if session is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired session")
        user_doc = db.users.find_one({"user_id": session["user_id"], "is_active": True})
        if user_doc is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
        return user_response_from_doc(user_doc)


CurrentUser = Annotated[UserResponse, Depends(current_user)]


def require_master_cluster(db, cluster_id: str, user: UserResponse) -> dict:
    if "master" not in user.roles:
        raise HTTPException(status_code=403, detail="Master role required")
    cluster = db.clusters.find_one({"cluster_id": cluster_id, "owner_id": user.id})
    if cluster is None:
        raise HTTPException(status_code=404, detail="Cluster not found")
    return cluster


def require_worker_node(db, node_id: str, user: UserResponse) -> dict:
    if "worker_operator" not in user.roles:
        raise HTTPException(status_code=403, detail="Worker Operator role required")
    node = db.nodes.find_one({"node_id": node_id, "owner_id": user.id})
    if node is None:
        raise HTTPException(status_code=404, detail="Node not found")
    return node


def require_master_job(db, job_id: str, user: UserResponse) -> dict:
    if "master" not in user.roles:
        raise HTTPException(status_code=403, detail="Master role required")
    doc = db.jobs.find_one({"job_id": job_id, "owner_id": user.id})
    if doc is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return doc


def require_master_workload(db, workload_id: str, user: UserResponse) -> dict:
    doc = db.workloads.find_one({"workload_id": workload_id, "owner_id": user.id, "validation_state": "validated"})
    if doc is None:
        raise HTTPException(status_code=404, detail="Validated workload not found")
    return doc


# ---------------------------------------------------------------------------
# Network / Audit
# ---------------------------------------------------------------------------

@app.post("/api/v1/network/preflight")
def network_preflight(payload: NetworkPreflightRequest, user: CurrentUser) -> dict[str, Any]:
    if "master" not in user.roles and "worker_operator" not in user.roles:
        raise HTTPException(status_code=403, detail="A cluster role is required")
    return preflight(payload.master_host, payload.master_port)


@app.get("/api/v1/audit-events", response_model=list[AuditEventResponse])
def list_audit_events(user: CurrentUser) -> list[AuditEventResponse]:
    with connection_scope() as db:
        docs = list(db.audit_events.find({"actor_id": user.id}).sort("created_at", -1).limit(200))
    return [audit_response(doc) for doc in docs]


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

@app.post("/api/v1/auth/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest) -> AuthResponse:
    requested_roles = set(payload.roles)
    if not requested_roles or not requested_roles.issubset({"master", "worker_operator"}):
        raise HTTPException(status_code=400, detail="roles must be master and/or worker_operator")
    user_id = str(uuid.uuid4())
    with connection_scope() as db:
        # Check uniqueness
        if db.users.find_one({"username": {"$regex": f"^{payload.username}$", "$options": "i"}}):
            raise HTTPException(status_code=409, detail="Username or email is already registered")
        if db.users.find_one({"email": {"$regex": f"^{payload.email}$", "$options": "i"}}):
            raise HTTPException(status_code=409, detail="Username or email is already registered")
        try:
            db.users.insert_one({
                "user_id": user_id,
                "full_name": payload.full_name.strip(),
                "username": payload.username,
                "email": payload.email,
                "organization": payload.organization,
                "password_hash": hash_password(payload.password),
                "roles": sorted(requested_roles),
                "is_active": True,
                "created_at": utc_now(),
            })
        except Exception as error:
            if "duplicate key" in str(error).lower() or "E11000" in str(error):
                raise HTTPException(status_code=409, detail="Username or email is already registered") from error
            raise
        record_audit(db, user_id, "account.registered", "user", user_id, "success", {"roles": sorted(requested_roles)})
        token, expires_at = create_session(db, user_id)
        user_doc = db.users.find_one({"user_id": user_id})
        return AuthResponse(access_token=token, expires_at=expires_at, user=user_response_from_doc(user_doc))


@app.post("/api/v1/auth/login", response_model=AuthResponse)
def login(payload: LoginRequest) -> AuthResponse:
    with connection_scope() as db:
        user_doc = db.users.find_one({"username": {"$regex": f"^{payload.username}$", "$options": "i"}, "is_active": True})
        if user_doc is None or not verify_password(payload.password, user_doc["password_hash"]):
            raise HTTPException(status_code=401, detail="Invalid credentials")
        token, expires_at = create_session(db, user_doc["user_id"])
        record_audit(db, user_doc["user_id"], "auth.login", "user", user_doc["user_id"], "success")
        return AuthResponse(access_token=token, expires_at=expires_at, user=user_response_from_doc(user_doc))


@app.post("/api/v1/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(authorization: Annotated[str | None, Header()] = None) -> None:
    if authorization and authorization.lower().startswith("bearer "):
        token_hash = hash_token(authorization[7:].strip())
        with connection_scope() as db:
            session = db.sessions.find_one({"token_hash": token_hash})
            db.sessions.update_one({"token_hash": token_hash}, {"$set": {"revoked_at": utc_now()}})
            if session:
                record_audit(db, session["user_id"], "auth.logout", "user", session["user_id"], "success")


@app.get("/api/v1/auth/me", response_model=UserResponse)
def me(user: CurrentUser) -> UserResponse:
    return user


@app.post("/api/v1/auth/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(payload: ChangePasswordRequest, user: CurrentUser) -> None:
    if payload.current_password == payload.new_password:
        raise HTTPException(status_code=400, detail="New password must differ from current password")
    with connection_scope() as db:
        user_doc = db.users.find_one({"user_id": user.id})
        if user_doc is None or not verify_password(payload.current_password, user_doc["password_hash"]):
            raise HTTPException(status_code=400, detail="Current password is incorrect")
        db.users.update_one({"user_id": user.id}, {"$set": {"password_hash": hash_password(payload.new_password)}})
        db.sessions.update_many({"user_id": user.id, "revoked_at": None}, {"$set": {"revoked_at": utc_now()}})
        record_audit(db, user.id, "auth.password_changed", "user", user.id, "success")


# ---------------------------------------------------------------------------
# Clusters
# ---------------------------------------------------------------------------

@app.post("/api/v1/clusters", response_model=ClusterResponse, status_code=status.HTTP_201_CREATED)
def create_cluster(payload: ClusterCreateRequest, user: CurrentUser) -> ClusterResponse:
    if "master" not in user.roles:
        raise HTTPException(status_code=403, detail="Master role required")
    cluster_id = str(uuid.uuid4())
    now = utc_now()
    with connection_scope() as db:
        db.clusters.insert_one({
            "cluster_id": cluster_id,
            "owner_id": user.id,
            "name": payload.name.strip(),
            "status": "active",
            "created_at": now,
        })
        record_audit(db, user.id, "cluster.created", "cluster", cluster_id, "success", {"name": payload.name.strip()})
    return ClusterResponse(id=cluster_id, name=payload.name.strip(), status="active", created_at=now)


@app.get("/api/v1/clusters", response_model=list[ClusterResponse])
def list_clusters(user: CurrentUser) -> list[ClusterResponse]:
    with connection_scope() as db:
        docs = list(db.clusters.find({"owner_id": user.id}).sort("created_at", 1))
    return [ClusterResponse(id=d["cluster_id"], name=d["name"], status=d["status"], created_at=_parse_dt(d["created_at"])) for d in docs]


# ---------------------------------------------------------------------------
# Pairing
# ---------------------------------------------------------------------------

@app.post("/api/v1/clusters/{cluster_id}/pairing-codes", response_model=PairingCodeResponse)
def create_pairing_code(cluster_id: str, user: CurrentUser) -> PairingCodeResponse:
    now = utc_now()
    expires_at = now + timedelta(minutes=PAIRING_CODE_MINUTES)
    raw_code = secrets.token_hex(4).upper()
    with connection_scope() as db:
        require_master_cluster(db, cluster_id, user)
        db.pairing_codes.insert_one({
            "pairing_id": str(uuid.uuid4()),
            "cluster_id": cluster_id,
            "code": raw_code,
            "code_hash": hash_token(raw_code),
            "expires_at": expires_at,
            "used_at": None,
            "created_at": now,
        })
        record_audit(db, user.id, "pairing.code_created", "cluster", cluster_id, "success", {"expires_at": timestamp(expires_at)})
    return PairingCodeResponse(code=raw_code, expires_at=expires_at)


@app.post("/api/v1/pairing/requests", response_model=NodeResponse, status_code=status.HTTP_201_CREATED)
def request_pairing(payload: PairingRequest, user: CurrentUser) -> NodeResponse:
    now = utc_now()
    with connection_scope() as db:
        cluster_id = None
        pairing_doc = None

        if payload.code and payload.code.strip():
            pairing_doc = db.pairing_codes.find_one({
                "code_hash": hash_token(payload.code.strip().upper()),
                "used_at": None,
                "expires_at": {"$gt": now},
            })
            if pairing_doc is None:
                raise HTTPException(status_code=400, detail="Pairing code is invalid, expired, or already used")
            cluster_id = pairing_doc["cluster_id"]
        else:
            cluster_doc = db.clusters.find_one({"status": "active"}, sort=[("created_at", -1)])
            if cluster_doc is None:
                raise HTTPException(status_code=400, detail="No active cluster found to accept join request")
            cluster_id = cluster_doc["cluster_id"]

        node_id = str(uuid.uuid4())
        db.nodes.insert_one({
            "node_id": node_id,
            "owner_id": user.id,
            "node_name": payload.node_name,
            "hostname": payload.hostname,
            "operating_system": payload.operating_system,
            "private_ipv4": payload.private_ipv4,
            "cpu_model": payload.cpu_model,
            "cpu_threads": payload.cpu_threads,
            "ram_total_bytes": payload.ram_total_bytes,
            "gpu_vendor": payload.gpu_vendor,
            "gpu_model": payload.gpu_model,
            "gpu_vram_bytes": payload.gpu_vram_bytes,
            "compute_backend": payload.compute_backend,
            "status": "offline",
            "cpu_utilization": None,
            "ram_utilization": None,
            "gpu_utilization": None,
            "gpu_used_vram_bytes": None,
            "last_heartbeat_at": None,
            "created_at": now,
            "updated_at": now,
        })
        db.cluster_members.insert_one({
            "cluster_id": cluster_id,
            "node_id": node_id,
            "status": "pending",
            "approved_at": None,
            "revoked_at": None,
        })
        if pairing_doc:
            db.pairing_codes.update_one({"pairing_id": pairing_doc["pairing_id"]}, {"$set": {"used_at": now}})
        record_audit(db, user.id, "pairing.requested", "node", node_id, "success", {"cluster_id": cluster_id})
        node_doc = db.nodes.find_one({"node_id": node_id})
    return node_response(node_doc, "pending")


@app.post("/api/v1/clusters/{cluster_id}/members/{node_id}/approve", response_model=NodeResponse)
def approve_node(cluster_id: str, node_id: str, user: CurrentUser) -> NodeResponse:
    now = utc_now()
    with connection_scope() as db:
        require_master_cluster(db, cluster_id, user)
        existing = db.cluster_members.find_one({"cluster_id": cluster_id, "node_id": node_id})
        if existing is None:
            raise HTTPException(status_code=404, detail="Pending node membership not found")
        if existing.get("status") != "approved":
            result = db.cluster_members.update_one(
                {"cluster_id": cluster_id, "node_id": node_id, "status": "pending"},
                {"$set": {"status": "approved", "approved_at": now, "revoked_at": None}},
            )
            if result.modified_count != 1:
                raise HTTPException(status_code=404, detail="Pending node membership not found")
            record_audit(db, user.id, "node.approved", "node", node_id, "success", {"cluster_id": cluster_id})
        node_doc = db.nodes.find_one({"node_id": node_id})
    return node_response(node_doc, "approved")


@app.post("/api/v1/clusters/{cluster_id}/members/{node_id}/revoke", response_model=NodeResponse)
def revoke_node(cluster_id: str, node_id: str, user: CurrentUser) -> NodeResponse:
    now = utc_now()
    with connection_scope() as db:
        require_master_cluster(db, cluster_id, user)
        result = db.cluster_members.update_one(
            {"cluster_id": cluster_id, "node_id": node_id, "status": "approved"},
            {"$set": {"status": "revoked", "revoked_at": now}},
        )
        if result.modified_count != 1:
            raise HTTPException(status_code=404, detail="Approved node membership not found")
        db.nodes.update_one({"node_id": node_id}, {"$set": {"status": "offline", "updated_at": now}})
        record_audit(db, user.id, "node.revoked", "node", node_id, "success", {"cluster_id": cluster_id})
        node_doc = db.nodes.find_one({"node_id": node_id})
    return node_response(node_doc, "revoked")


@app.delete("/api/v1/clusters/{cluster_id}/members/{node_id}", status_code=status.HTTP_200_OK)
def hard_delete_node(cluster_id: str, node_id: str, user: CurrentUser) -> dict[str, str]:
    """Master permanently removes an offline/revoked node from the cluster and DB."""
    with connection_scope() as db:
        require_master_cluster(db, cluster_id, user)
        member = db.cluster_members.find_one({"cluster_id": cluster_id, "node_id": node_id})
        if member is None:
            raise HTTPException(status_code=404, detail="Node is not a member of this cluster")
        node_doc = db.nodes.find_one({"node_id": node_id})
        if node_doc and node_doc.get("status") == "online":
            raise HTTPException(status_code=409, detail="Cannot delete an online node. Drain or disconnect it first.")
        active_task = db.tasks.find_one({"assigned_node_id": node_id, "state": {"$in": ["leased", "running"]}})
        if active_task:
            raise HTTPException(status_code=409, detail="Node has active tasks; wait for them to finish or cancel the job.")
        db.cluster_members.delete_many({"node_id": node_id})
        db.telemetry_samples.delete_many({"node_id": node_id})
        db.nodes.delete_one({"node_id": node_id})
        record_audit(db, user.id, "node.hard_deleted", "node", node_id, "success", {"cluster_id": cluster_id})
    return {"message": "Node permanently removed from cluster", "node_id": node_id}


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

def _mark_stale_nodes_offline(db, cluster_id: str, now: datetime) -> None:
    stale_cutoff = now - timedelta(seconds=HEARTBEAT_STALE_SECONDS)
    # find node_ids in this cluster
    member_ids = [m["node_id"] for m in db.cluster_members.find({"cluster_id": cluster_id})]
    db.nodes.update_many(
        {"node_id": {"$in": member_ids}, "status": "online", "last_heartbeat_at": {"$lt": stale_cutoff}},
        {"$set": {"status": "offline", "updated_at": now}},
    )


@app.get("/api/v1/clusters/{cluster_id}/nodes", response_model=list[NodeResponse])
def list_nodes(cluster_id: str, user: CurrentUser) -> list[NodeResponse]:
    with connection_scope() as db:
        require_master_cluster(db, cluster_id, user)
        now = utc_now()
        _mark_stale_nodes_offline(db, cluster_id, now)
        members = list(db.cluster_members.find({"cluster_id": cluster_id}))
        result = []
        for m in members:
            node_doc = db.nodes.find_one({"node_id": m["node_id"]})
            if node_doc:
                result.append(node_response(node_doc, m["status"]))
    return result


@app.post("/api/v1/worker/nodes/{node_id}/heartbeat", response_model=NodeResponse)
def heartbeat(node_id: str, payload: HeartbeatRequest, user: CurrentUser) -> NodeResponse:
    now = utc_now()
    with connection_scope() as db:
        require_worker_node(db, node_id, user)
        membership = db.cluster_members.find_one({"node_id": node_id})
        if membership is None or membership["status"] != "approved":
            raise HTTPException(status_code=403, detail="Node is not approved for a cluster")
        db.nodes.update_one(
            {"node_id": node_id},
            {"$set": {
                "status": payload.status,
                "cpu_utilization": payload.cpu_utilization,
                "ram_utilization": payload.ram_utilization,
                "gpu_utilization": payload.gpu_utilization,
                "gpu_used_vram_bytes": payload.gpu_used_vram_bytes,
                "last_heartbeat_at": now,
                "updated_at": now,
            }},
        )
        db.telemetry_samples.insert_one({
            "sample_id": str(uuid.uuid4()),
            "node_id": node_id,
            "recorded_at": now,
            "source": payload.telemetry_source,
            "cpu_utilization": payload.cpu_utilization,
            "ram_utilization": payload.ram_utilization,
            "gpu_utilization": payload.gpu_utilization,
            "gpu_used_vram_bytes": payload.gpu_used_vram_bytes,
        })
        node_doc = db.nodes.find_one({"node_id": node_id})
    return node_response(node_doc, "approved")


@app.get("/api/v1/clusters/{cluster_id}/telemetry", response_model=list[TelemetryResponse])
def list_telemetry(cluster_id: str, user: CurrentUser) -> list[TelemetryResponse]:
    with connection_scope() as db:
        require_master_cluster(db, cluster_id, user)
        member_ids = [m["node_id"] for m in db.cluster_members.find({"cluster_id": cluster_id})]
        samples = list(
            db.telemetry_samples.find({"node_id": {"$in": member_ids}})
            .sort("recorded_at", -1)
            .limit(1000)
        )
        node_names: dict[str, str] = {}
        for nid in member_ids:
            n = db.nodes.find_one({"node_id": nid}, {"node_name": 1})
            if n:
                node_names[nid] = n["node_name"]
    return [
        TelemetryResponse(
            node_id=s["node_id"],
            node_name=node_names.get(s["node_id"], "Unknown"),
            captured_at=_parse_dt(s["recorded_at"]),
            source=s.get("source", "worker_agent"),
            cpu_utilization=s.get("cpu_utilization"),
            ram_utilization=s.get("ram_utilization"),
            gpu_utilization=s.get("gpu_utilization"),
            gpu_used_vram_bytes=s.get("gpu_used_vram_bytes"),
        )
        for s in samples
    ]


# ---------------------------------------------------------------------------
# Worker node management
# ---------------------------------------------------------------------------

@app.get("/api/v1/worker/my-nodes", response_model=list[NodeResponse])
def list_my_worker_nodes(user: CurrentUser) -> list[NodeResponse]:
    with connection_scope() as db:
        nodes = list(db.nodes.find({"owner_id": user.id}).sort("created_at", -1))
        result = []
        for node_doc in nodes:
            m = db.cluster_members.find_one({"node_id": node_doc["node_id"]})
            ms = m["status"] if m else "unpaired"
            nid = node_doc["node_id"]
            tasks_done = db.tasks.count_documents({"assigned_node_id": nid, "state": "succeeded"})
            tasks_fail = db.tasks.count_documents({"assigned_node_id": nid, "state": "failed"})
            result.append(node_response(node_doc, ms, tasks_completed=tasks_done, tasks_failed=tasks_fail))
    return result


@app.post("/api/v1/worker/nodes/{node_id}/drain", response_model=NodeResponse)
def drain_worker_node(node_id: str, user: CurrentUser) -> NodeResponse:
    now = utc_now()
    with connection_scope() as db:
        require_worker_node(db, node_id, user)
        db.nodes.update_one({"node_id": node_id}, {"$set": {"status": "draining", "updated_at": now}})
        record_audit(db, user.id, "node.drain", "node", node_id, "success")
        node_doc = db.nodes.find_one({"node_id": node_id})
        m = db.cluster_members.find_one({"node_id": node_id})
        ms = m["status"] if m else "unpaired"
    return node_response(node_doc, ms)


@app.delete("/api/v1/worker/nodes/{node_id}", status_code=status.HTTP_200_OK)
def deregister_worker_node(node_id: str, user: CurrentUser) -> dict[str, str]:
    with connection_scope() as db:
        node_doc = require_worker_node(db, node_id, user)
        if node_doc.get("status") == "online":
            raise HTTPException(status_code=409, detail="Cannot deregister an online node. Disconnect the worker app first.")
        active_task = db.tasks.find_one({"assigned_node_id": node_id, "state": {"$in": ["leased", "running"]}})
        if active_task:
            raise HTTPException(status_code=409, detail="Node has active tasks in progress")
        db.cluster_members.delete_many({"node_id": node_id})
        db.telemetry_samples.delete_many({"node_id": node_id})
        db.nodes.delete_one({"node_id": node_id})
        record_audit(db, user.id, "node.deregistered", "node", node_id, "success")
    return {"message": "Node deregistered successfully", "node_id": node_id}


@app.get("/api/v1/worker/download-agent")
def download_worker_agent() -> FileResponse:
    dist_dir = Path(__file__).resolve().parent.parent.parent / "dist"
    exe_path = dist_dir / "NEXORA-Worker.exe"
    if exe_path.is_file():
        return FileResponse(
            exe_path,
            media_type="application/vnd.microsoft.portable-executable",
            filename="NEXORA-Worker.exe",
        )
    raise HTTPException(status_code=404, detail="NEXORA-Worker.exe executable build not found on server")


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

def reconcile_job(db, job_id: str, now: datetime) -> None:
    pipeline = [
        {"$match": {"job_id": job_id}},
        {"$group": {"_id": "$state", "count": {"$sum": 1}}},
    ]
    state_counts: dict[str, int] = {}
    for item in db.tasks.aggregate(pipeline):
        state_counts[item["_id"]] = item["count"]
    total = sum(state_counts.values())
    if state_counts.get("succeeded", 0) == total and total > 0:
        new_state = "completed"
    elif state_counts.get("failed", 0) > 0 and not any(
        state_counts.get(s, 0) for s in ("queued", "leased", "running", "retrying")
    ):
        new_state = "failed"
    elif state_counts.get("leased", 0) or state_counts.get("running", 0) or state_counts.get("queued", 0):
        new_state = "running"
    else:
        return
    db.jobs.update_one({"job_id": job_id}, {"$set": {"state": new_state, "updated_at": now}})


@app.post("/api/v1/jobs", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
def create_job(payload: JobCreateRequest, user: CurrentUser) -> JobResponse:
    now = utc_now()
    task_keys = [task.task_key for task in payload.tasks]
    if len(task_keys) != len(set(task_keys)):
        raise HTTPException(status_code=400, detail="task_key values must be unique within a job")
    with connection_scope() as db:
        require_master_cluster(db, payload.cluster_id, user)
        workload = require_master_workload(db, payload.workload_id, user) if payload.workload_id else None
        if workload:
            manifest = WorkloadManifest.model_validate(json.loads(workload["manifest_json"]))
            mismatched_tasks = [
                task.task_key
                for task in payload.tasks
                if manifest.runtime == "builtin" and (
                    task.payload.get("operation") != manifest.operation
                    or task.payload.get("backend", "cpu") != manifest.backend
                )
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
            for nid, weight in allocations.items():
                if not isinstance(weight, (int, float)) or weight <= 0:
                    continue
                count = int(round((weight / 100.0) * total_tasks)) if is_percentage else int(weight)
                task_targets.extend([nid] * count)
            target_idx = 0
            for task in payload.tasks:
                if task.task_key not in pinned_map and target_idx < len(task_targets):
                    pinned_map[task.task_key] = task_targets[target_idx]
                    target_idx += 1

        job_id = str(uuid.uuid4())
        db.jobs.insert_one({
            "job_id": job_id,
            "cluster_id": payload.cluster_id,
            "owner_id": user.id,
            "workload_id": payload.workload_id,
            "name": payload.name.strip(),
            "execution_mode": payload.execution_mode,
            "scheduling_policy": payload.scheduling_policy,
            "scheduling_config_json": canonical_json(payload.scheduling_config),
            "priority": payload.priority,
            "retry_limit": payload.retry_limit,
            "lease_seconds": payload.lease_seconds,
            "state": "queued",
            "created_at": now,
            "updated_at": now,
        })
        db.tasks.insert_many([
            {
                "task_id": str(uuid.uuid4()),
                "job_id": job_id,
                "workload_id": payload.workload_id,
                "task_key": task.task_key,
                "payload_json": canonical_json(task.payload),
                "payload_hash": json_hash(task.payload),
                "pinned_node_id": pinned_map.get(task.task_key),
                "state": "queued",
                "attempt": 0,
                "assigned_node_id": None,
                "lease_token_hash": None,
                "lease_expires_at": None,
                "next_retry_at": None,
                "started_at": None,
                "completed_at": None,
                "duration_seconds": None,
                "result_json": None,
                "result_hash": None,
                "error": None,
                "created_at": now,
                "updated_at": now,
            }
            for task in payload.tasks
        ])
        record_audit(db, user.id, "job.created", "job", job_id, "success", {
            "task_count": len(payload.tasks),
            "workload_id": payload.workload_id,
            "scheduling_policy": payload.scheduling_policy,
        })
        job_doc = db.jobs.find_one({"job_id": job_id})
        return job_response(db, job_doc)


@app.get("/api/v1/jobs/{job_id}", response_model=JobResponse)
def get_job(job_id: str, user: CurrentUser) -> JobResponse:
    with connection_scope() as db:
        doc = require_master_job(db, job_id, user)
        return job_response(db, doc)


@app.get("/api/v1/jobs", response_model=list[JobResponse])
def list_jobs(user: CurrentUser) -> list[JobResponse]:
    with connection_scope() as db:
        docs = list(db.jobs.find({"owner_id": user.id}).sort("created_at", -1))
        return [job_response(db, d) for d in docs]


@app.get("/api/v1/jobs/{job_id}/tasks", response_model=list[TaskResponse])
def list_tasks(job_id: str, user: CurrentUser) -> list[TaskResponse]:
    with connection_scope() as db:
        require_master_job(db, job_id, user)
        docs = list(db.tasks.find({"job_id": job_id}).sort("task_key", 1))
        node_ids = {d.get("assigned_node_id") for d in docs if d.get("assigned_node_id")}
        node_map: dict[str, str] = {}
        if node_ids:
            for n in db.nodes.find({"node_id": {"$in": list(node_ids)}}):
                if n.get("node_id") and n.get("node_name"):
                    node_map[n["node_id"]] = n["node_name"]
        return [task_response(d, node_name=node_map.get(d.get("assigned_node_id"))) for d in docs]


@app.get("/api/v1/jobs/{job_id}/result", response_model=FinalResultResponse)
def get_final_result(job_id: str, user: CurrentUser) -> FinalResultResponse:
    with connection_scope() as db:
        job_doc = require_master_job(db, job_id, user)
        if job_doc["state"] != "completed":
            raise HTTPException(status_code=409, detail=f"Job is {job_doc['state']}; required tasks are not complete")
        docs = list(db.tasks.find({"job_id": job_id, "state": "succeeded"}).sort("task_key", 1))
    results = [{"task_key": d["task_key"], "result": json.loads(d["result_json"])} for d in docs]
    return FinalResultResponse(job_id=job_id, aggregation="collect", task_count=len(results), result_hash=json_hash(results), results=results)


@app.get("/api/v1/jobs/{job_id}/report")
def get_job_report(job_id: str, user: CurrentUser, format: str | None = None) -> Any:
    with connection_scope() as db:
        job_doc = require_master_job(db, job_id, user)
        tasks_docs = list(db.tasks.find({"job_id": job_id}).sort("task_key", 1))

        # Build node name lookup
        node_name_map: dict[str, str] = {}
        for td in tasks_docs:
            nid = td.get("assigned_node_id")
            if nid and nid not in node_name_map:
                nd = db.nodes.find_one({"node_id": nid}, {"node_name": 1})
                node_name_map[nid] = nd["node_name"] if nd else "Unknown"

        node_stats: dict[str, dict[str, Any]] = {}
        report_tasks: list[TaskReportItem] = []
        for td in tasks_docs:
            nid = td.get("assigned_node_id") or "unassigned"
            nn = node_name_map.get(nid, "Unassigned")
            if nid != "unassigned" and nid not in node_stats:
                node_stats[nid] = {"node_id": nid, "node_name": nn, "tasks_completed": 0, "tasks_failed": 0, "total_compute_seconds": 0.0}
            if td["state"] == "succeeded" and nid in node_stats:
                node_stats[nid]["tasks_completed"] += 1
                if td.get("duration_seconds"):
                    node_stats[nid]["total_compute_seconds"] += float(td["duration_seconds"])
            elif td["state"] == "failed" and nid in node_stats:
                node_stats[nid]["tasks_failed"] += 1
            report_tasks.append(TaskReportItem(
                task_key=td["task_key"],
                state=td["state"],
                attempt=td.get("attempt", 0),
                assigned_node_id=td.get("assigned_node_id"),
                node_name=node_name_map.get(td.get("assigned_node_id", ""), None),
                started_at=_parse_dt(td.get("started_at")),
                completed_at=_parse_dt(td.get("completed_at")),
                duration_seconds=td.get("duration_seconds"),
                error=td.get("error"),
            ))

        nodes_summary = []
        for nid, data in node_stats.items():
            completed = data["tasks_completed"]
            tot_sec = round(data["total_compute_seconds"], 3)
            avg_sec = round(tot_sec / completed, 3) if completed > 0 else 0.0
            nodes_summary.append(NodePerformanceSummary(
                node_id=nid, node_name=data["node_name"],
                tasks_completed=completed, tasks_failed=data["tasks_failed"],
                total_compute_seconds=tot_sec, avg_task_duration_seconds=avg_sec,
            ))

        task_count = len(report_tasks)
        succeeded_count = sum(1 for t in report_tasks if t.state == "succeeded")
        failed_count = sum(1 for t in report_tasks if t.state == "failed")
        created = _parse_dt(job_doc["created_at"])
        updated = _parse_dt(job_doc["updated_at"])
        total_duration = (updated - created).total_seconds() if job_doc["state"] in ("completed", "failed", "canceled") else None

        report = JobReportResponse(
            job_id=job_doc["job_id"], job_name=job_doc["name"], cluster_id=job_doc["cluster_id"],
            state=job_doc["state"], execution_mode=job_doc["execution_mode"],
            scheduling_policy=job_doc.get("scheduling_policy", "automatic"),
            priority=job_doc.get("priority", 0),
            task_count=task_count, succeeded_count=succeeded_count, failed_count=failed_count,
            total_duration_seconds=round(total_duration, 2) if total_duration else None,
            created_at=created, updated_at=updated,
            nodes=nodes_summary, tasks=report_tasks,
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
    with connection_scope() as db:
        cluster_doc = require_master_cluster(db, cluster_id, user)
        now = utc_now()
        _mark_stale_nodes_offline(db, cluster_id, now)
        member_ids = [m["node_id"] for m in db.cluster_members.find({"cluster_id": cluster_id})]
        nodes = list(db.nodes.find({"node_id": {"$in": member_ids}}))
        jobs = list(db.jobs.find({"cluster_id": cluster_id}, {"state": 1}))

        return ClusterSummaryResponse(
            cluster_id=cluster_id,
            cluster_name=cluster_doc["name"],
            registered_nodes=len(nodes),
            online_nodes=sum(1 for n in nodes if n["status"] == "online"),
            offline_nodes=sum(1 for n in nodes if n["status"] == "offline"),
            detected_gpus=sum(1 for n in nodes if n.get("gpu_model")),
            total_vram_bytes=sum((n.get("gpu_vram_bytes") or 0) for n in nodes),
            active_jobs=sum(1 for j in jobs if j["state"] in ("running", "paused")),
            queued_jobs=sum(1 for j in jobs if j["state"] == "queued"),
            completed_jobs=sum(1 for j in jobs if j["state"] == "completed"),
            failed_jobs=sum(1 for j in jobs if j["state"] == "failed"),
        )


# ---------------------------------------------------------------------------
# Job state transitions
# ---------------------------------------------------------------------------

def update_job_state(job_id: str, user: UserResponse, expected_states: tuple[str, ...], next_state: str) -> JobResponse:
    now = utc_now()
    with connection_scope() as db:
        job_doc = require_master_job(db, job_id, user)
        if job_doc["state"] not in expected_states:
            raise HTTPException(status_code=409, detail=f"Job is {job_doc['state']} and cannot become {next_state}")
        db.jobs.update_one({"job_id": job_id}, {"$set": {"state": next_state, "updated_at": now}})
        if next_state == "canceled":
            db.tasks.update_many(
                {"job_id": job_id, "state": {"$nin": ["succeeded", "failed", "canceled"]}},
                {"$set": {"state": "canceled", "lease_token_hash": None, "lease_expires_at": None, "updated_at": now}},
            )
        record_audit(db, user.id, f"job.{next_state}", "job", job_id, "success")
        updated = db.jobs.find_one({"job_id": job_id})
        return job_response(db, updated)


@app.post("/api/v1/jobs/{job_id}/pause", response_model=JobResponse)
def pause_job(job_id: str, user: CurrentUser) -> JobResponse:
    return update_job_state(job_id, user, ("queued", "running"), "paused")


@app.post("/api/v1/jobs/{job_id}/resume", response_model=JobResponse)
def resume_job(job_id: str, user: CurrentUser) -> JobResponse:
    return update_job_state(job_id, user, ("paused",), "running")


@app.post("/api/v1/jobs/{job_id}/cancel", response_model=JobResponse)
def cancel_job(job_id: str, user: CurrentUser) -> JobResponse:
    return update_job_state(job_id, user, ("queued", "running", "paused"), "canceled")


@app.delete("/api/v1/jobs/{job_id}", status_code=status.HTTP_200_OK)
def delete_job(job_id: str, user: CurrentUser) -> dict[str, str]:
    with connection_scope() as db:
        require_master_job(db, job_id, user)
        db.tasks.delete_many({"job_id": job_id})
        db.jobs.delete_one({"job_id": job_id})
        record_audit(db, user.id, "job.deleted", "job", job_id, "success")
    return {"message": "Job and associated tasks deleted successfully", "job_id": job_id}


# ---------------------------------------------------------------------------
# Task Lease & Result
# ---------------------------------------------------------------------------

def retry_delay_seconds(attempt: int) -> int:
    return min(60, 2 ** max(0, attempt - 1))


def task_is_compatible_with_node(task_payload: dict[str, Any], node_doc: dict) -> bool:
    backend = task_payload.get("backend", "cpu")
    if backend == "opencl" and (node_doc.get("compute_backend") or "").lower() != "opencl":
        return False
    required_threads = task_payload.get("required_cpu_threads", 0)
    if isinstance(required_threads, (int, float)) and node_doc.get("cpu_threads") is not None and required_threads > node_doc["cpu_threads"]:
        return False
    required_vram = task_payload.get("required_gpu_memory_bytes", 0)
    if backend == "opencl":
        available_vram = (node_doc.get("gpu_vram_bytes") or 0) - (node_doc.get("gpu_used_vram_bytes") or 0)
        if not isinstance(required_vram, (int, float)) or required_vram > available_vram:
            return False
    return True


def recover_expired_leases(db, now: datetime) -> None:
    expired = list(db.tasks.aggregate([
        {"$match": {"state": {"$in": ["leased", "running"]}, "lease_expires_at": {"$lte": now}}},
        {"$lookup": {"from": "jobs", "localField": "job_id", "foreignField": "job_id", "as": "job"}},
        {"$unwind": "$job"},
        {"$addFields": {"retry_limit": "$job.retry_limit"}},
    ]))
    for row in expired:
        next_state = "queued" if row.get("attempt", 0) < row["retry_limit"] + 1 else "failed"
        retry_at = now + timedelta(seconds=retry_delay_seconds(row.get("attempt", 0))) if next_state == "queued" else None
        db.tasks.update_one(
            {"task_id": row["task_id"]},
            {"$set": {
                "state": next_state,
                "assigned_node_id": None,
                "lease_token_hash": None,
                "lease_expires_at": None,
                "next_retry_at": retry_at,
                "error": "Lease expired" if next_state == "failed" else None,
                "updated_at": now,
            }},
        )
        reconcile_job(db, row["job_id"], now)


@app.post("/api/v1/worker/nodes/{node_id}/tasks/lease", response_model=TaskLeaseResponse | None)
def lease_task(node_id: str, user: CurrentUser) -> TaskLeaseResponse | None:
    now = utc_now()
    with connection_scope() as db:
        node_doc = require_worker_node(db, node_id, user)
        membership = db.cluster_members.find_one({"node_id": node_id})
        if membership is None or membership["status"] != "approved":
            raise HTTPException(status_code=403, detail="Node is not approved for a cluster")
        if not node_has_fresh_heartbeat(node_doc, now):
            raise HTTPException(status_code=409, detail="Node heartbeat is stale or offline")
        recover_expired_leases(db, now)
        cluster_id = membership["cluster_id"]

        # Find candidate tasks
        candidates = list(db.tasks.aggregate([
            {"$match": {
                "state": "queued",
                "$or": [{"next_retry_at": None}, {"next_retry_at": {"$lte": now}}],
                "$or": [{"pinned_node_id": None}, {"pinned_node_id": node_id}],
            }},
            {"$lookup": {"from": "jobs", "localField": "job_id", "foreignField": "job_id", "as": "job"}},
            {"$unwind": "$job"},
            {"$match": {"job.cluster_id": cluster_id, "job.state": {"$in": ["queued", "running"]}}},
            {"$sort": {"job.priority": -1, "job.created_at": 1, "created_at": 1}},
            {"$limit": 100},
        ]))

        selected = None
        for candidate in candidates:
            payload_data = json.loads(candidate["payload_json"])
            if task_is_compatible_with_node(payload_data, node_doc):
                selected = candidate
                break

        if selected is None:
            return None

        lease_token = secrets.token_urlsafe(32)
        lease_seconds = selected["job"].get("lease_seconds", 60)
        lease_expires_at = now + timedelta(seconds=lease_seconds)
        attempt = selected.get("attempt", 0) + 1
        started_at = selected.get("started_at") or now

        db.tasks.update_one(
            {"task_id": selected["task_id"]},
            {"$set": {
                "state": "leased",
                "attempt": attempt,
                "assigned_node_id": node_id,
                "assigned_node_name": node_doc.get("node_name"),
                "lease_token_hash": hash_token(lease_token),
                "lease_expires_at": lease_expires_at,
                "next_retry_at": None,
                "started_at": started_at,
                "updated_at": now,
            }},
        )
        db.jobs.update_one({"job_id": selected["job_id"]}, {"$set": {"state": "running", "updated_at": now}})
        leased_doc = db.tasks.find_one({"task_id": selected["task_id"]})
    return TaskLeaseResponse(lease_token=lease_token, task=task_response(leased_doc, workload_id=selected["job"].get("workload_id")))


@app.post("/api/v1/worker/tasks/{task_id}/renew", response_model=TaskResponse)
def renew_task_lease(task_id: str, payload: TaskRenewRequest, user: CurrentUser) -> TaskResponse:
    """Extend an active task lease while a long-running job is still computing."""
    now = utc_now()
    with connection_scope() as db:
        task_docs = list(db.tasks.aggregate([
            {"$match": {"task_id": task_id}},
            {"$lookup": {"from": "jobs", "localField": "job_id", "foreignField": "job_id", "as": "job"}},
            {"$unwind": "$job"},
        ]))
        if not task_docs:
            raise HTTPException(status_code=404, detail="Task not found")
        task_doc = task_docs[0]
        require_worker_node(db, task_doc.get("assigned_node_id", ""), user)
        lease_exp = task_doc.get("lease_expires_at")
        if task_doc["state"] not in ("leased", "running") or (lease_exp and _parse_dt(lease_exp) <= now):
            raise HTTPException(status_code=409, detail="Task lease is no longer active")
        stored_hash = task_doc.get("lease_token_hash", "")
        if not hmac.compare_digest(stored_hash, hash_token(payload.lease_token)):
            raise HTTPException(status_code=409, detail="Invalid task lease token")
        lease_seconds = max(30, int(task_doc["job"].get("lease_seconds", 60)))
        new_expires = now + timedelta(seconds=lease_seconds)
        db.tasks.update_one(
            {"task_id": task_id},
            {"$set": {"state": "running", "lease_expires_at": new_expires, "updated_at": now}},
        )
        updated = db.tasks.find_one({"task_id": task_id})
    return task_response(updated, workload_id=task_doc["job"].get("workload_id"))


@app.post("/api/v1/worker/tasks/{task_id}/result", response_model=TaskResponse)
def submit_task_result(task_id: str, payload: TaskResultRequest, user: CurrentUser) -> TaskResponse:
    now = utc_now()
    if payload.status == "failed" and not payload.error:
        raise HTTPException(status_code=400, detail="error is required for a failed task")
    with connection_scope() as db:
        task_docs = list(db.tasks.aggregate([
            {"$match": {"task_id": task_id}},
            {"$lookup": {"from": "jobs", "localField": "job_id", "foreignField": "job_id", "as": "job"}},
            {"$unwind": "$job"},
            {"$addFields": {"retry_limit": "$job.retry_limit"}},
        ]))
        if not task_docs:
            raise HTTPException(status_code=404, detail="Task not found")
        task_doc = task_docs[0]
        require_worker_node(db, task_doc.get("assigned_node_id", ""), user)
        if task_doc["state"] == "succeeded":
            return task_response(task_doc)
        lease_exp = task_doc.get("lease_expires_at")
        if task_doc["state"] not in ("leased", "running") or (lease_exp and _parse_dt(lease_exp) <= now):
            raise HTTPException(status_code=409, detail="Task lease is no longer active")
        stored_hash = task_doc.get("lease_token_hash", "")
        if not hmac.compare_digest(stored_hash, hash_token(payload.lease_token)):
            raise HTTPException(status_code=409, detail="Invalid task lease token")

        result_json = canonical_json(payload.result) if payload.result is not None else None
        result_hash = json_hash(payload.result) if payload.result is not None else None

        if payload.status == "succeeded":
            next_state = "succeeded"
            next_error = None
        else:
            next_state = "queued" if task_doc.get("attempt", 0) < task_doc["retry_limit"] + 1 else "failed"
            next_error = payload.error

        retry_at = now + timedelta(seconds=retry_delay_seconds(task_doc.get("attempt", 0))) if next_state == "queued" else None
        started_at = task_doc.get("started_at")
        duration_seconds = None
        if started_at:
            try:
                duration_seconds = round(max(0.0, (now - _parse_dt(started_at)).total_seconds()), 3)
            except Exception:
                pass
        completed_at = now if payload.status == "succeeded" else None

        db.tasks.update_one(
            {"task_id": task_id},
            {"$set": {
                "state": next_state,
                "assigned_node_id": task_doc.get("assigned_node_id") if next_state == "succeeded" else None,
                "lease_token_hash": None,
                "lease_expires_at": None,
                "next_retry_at": retry_at,
                "result_json": result_json,
                "result_hash": result_hash,
                "error": next_error,
                "completed_at": completed_at,
                "duration_seconds": duration_seconds,
                "updated_at": now,
            }},
        )
        reconcile_job(db, task_doc["job_id"], now)
        updated = db.tasks.find_one({"task_id": task_id})
    return task_response(updated)


# ---------------------------------------------------------------------------
# Workloads
# ---------------------------------------------------------------------------

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
        
        filename = file.filename
        if filename.lower().endswith((".py", ".ipynb")):
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr(filename, data)
            data = buf.getvalue()
            filename = f"{Path(filename).stem}.zip"
        
        validate_archive(data, filename)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    now = utc_now()
    workload_id = str(uuid.uuid4())
    content_hash = hashlib.sha256(data).hexdigest()
    storage_directory = workload_storage_path()
    storage_directory.mkdir(parents=True, exist_ok=True)
    storage_path = storage_directory / f"{workload_id}.zip"
    storage_path.write_bytes(data)

    with connection_scope() as db:
        db.workloads.insert_one({
            "workload_id": workload_id,
            "owner_id": user.id,
            "name": parsed_manifest.name,
            "manifest_json": parsed_manifest.model_dump_json(),
            "content_hash": content_hash,
            "size_bytes": len(data),
            "validation_state": "validated",
            "storage_path": str(storage_path),
            "created_at": now,
        })
        record_audit(db, user.id, "workload.validated", "workload", workload_id, "success", {"content_hash": content_hash, "size_bytes": len(data)})
        doc = db.workloads.find_one({"workload_id": workload_id})
    return workload_response(doc)


@app.get("/api/v1/workloads", response_model=list[WorkloadResponse])
def list_workloads(user: CurrentUser) -> list[WorkloadResponse]:
    with connection_scope() as db:
        docs = list(db.workloads.find({"owner_id": user.id}).sort("created_at", -1))
    return [workload_response(d) for d in docs]


@app.get("/api/v1/worker/workloads/{workload_id}/download")
def download_workload_package(workload_id: str, user: CurrentUser) -> FileResponse:
    with connection_scope() as db:
        doc = db.workloads.find_one({"workload_id": workload_id})
        if not doc:
            raise HTTPException(status_code=404, detail="Workload not found")
        path = Path(doc["storage_path"])
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Workload file not found on disk")
        return FileResponse(path, media_type="application/zip", filename=f"{workload_id}.zip")


@app.post("/api/v1/worker/tasks/{task_id}/artifacts")
async def upload_task_artifacts(
    task_id: str,
    file: Annotated[UploadFile, File()],
    user: CurrentUser,
) -> dict[str, Any]:
    with connection_scope() as db:
        task = db.tasks.find_one({"task_id": task_id})
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        job_id = task["job_id"]
        task_key = task["task_key"]

        data = await file.read(500 * 1024 * 1024 + 1)
        if len(data) > 500 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="Artifact archive exceeds 500 MB limit")

        artifact_dir = artifact_storage_path() / job_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_file = artifact_dir / f"{task_key}.zip"
        artifact_file.write_bytes(data)

        db.tasks.update_one(
            {"task_id": task_id},
            {"$set": {
                "has_artifacts": True,
                "artifact_path": str(artifact_file),
                "artifact_size_bytes": len(data),
                "updated_at": utc_now(),
            }},
        )
        record_audit(db, user.id, "task.artifacts_uploaded", "task", task_id, "success", {"size_bytes": len(data)})
    return {"message": "Artifacts stored successfully", "task_id": task_id, "size_bytes": len(data)}


@app.get("/api/v1/jobs/{job_id}/bundle")
def download_job_bundle(job_id: str, user: CurrentUser) -> FileResponse:
    with connection_scope() as db:
        job_doc = require_master_job(db, job_id, user)
        tasks_docs = list(db.tasks.find({"job_id": job_id}).sort("task_key", 1))

        bundle_dir = artifact_storage_path() / job_id
        bundle_dir.mkdir(parents=True, exist_ok=True)
        bundle_zip_path = bundle_dir / "final_output_bundle.zip"

        node_name_map = {}
        for td in tasks_docs:
            nid = td.get("assigned_node_id")
            if nid and nid not in node_name_map:
                n = db.nodes.find_one({"node_id": nid}, {"node_name": 1})
                node_name_map[nid] = n["node_name"] if n else "Unknown"

        with zipfile.ZipFile(bundle_zip_path, "w", zipfile.ZIP_DEFLATED) as master_zf:
            summary_data = {
                "job_id": job_id,
                "job_name": job_doc["name"],
                "state": job_doc["state"],
                "total_tasks": len(tasks_docs),
                "succeeded_tasks": sum(1 for t in tasks_docs if t["state"] == "succeeded"),
                "created_at": _parse_dt(job_doc["created_at"]).isoformat() if _parse_dt(job_doc["created_at"]) else None,
                "updated_at": _parse_dt(job_doc["updated_at"]).isoformat() if _parse_dt(job_doc["updated_at"]) else None,
                "tasks": [
                    {
                        "task_key": t["task_key"],
                        "assigned_node": node_name_map.get(t.get("assigned_node_id"), "unassigned"),
                        "state": t["state"],
                        "duration_seconds": t.get("duration_seconds"),
                        "has_artifacts": bool(t.get("has_artifacts")),
                        "result": json.loads(t["result_json"]) if t.get("result_json") else None,
                        "error": t.get("error"),
                    }
                    for t in tasks_docs
                ],
            }
            master_zf.writestr("consolidated_summary.json", json.dumps(summary_data, indent=2))

            for td in tasks_docs:
                art_path_str = td.get("artifact_path")
                t_key = td["task_key"]
                raw_node = node_name_map.get(td.get("assigned_node_id"), "worker")
                safe_node = "".join(c if c.isalnum() else "_" for c in raw_node).strip("_")
                subfolder = f"{t_key}_{safe_node}"

                if art_path_str and Path(art_path_str).is_file():
                    try:
                        with zipfile.ZipFile(art_path_str, "r") as task_zf:
                            for member_name in task_zf.namelist():
                                if not member_name.endswith("/"):
                                    member_bytes = task_zf.read(member_name)
                                    master_zf.writestr(f"{subfolder}/{member_name}", member_bytes)
                    except Exception:
                        pass
                elif td.get("result_json"):
                    master_zf.writestr(f"{subfolder}/result.json", td["result_json"])

        slug = "".join(c if c.isalnum() else "_" for c in job_doc["name"]).strip("_") or "job"
        return FileResponse(
            bundle_zip_path,
            media_type="application/zip",
            filename=f"nexora-{slug}-{job_id[:8]}-bundle.zip",
        )


@app.get("/api/v1/tasks/{task_id}/artifacts")
def download_task_artifacts(task_id: str, user: CurrentUser) -> FileResponse:
    with connection_scope() as db:
        task = db.tasks.find_one({"task_id": task_id})
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        art_path = task.get("artifact_path")
        if not art_path or not Path(art_path).is_file():
            raise HTTPException(status_code=404, detail="No artifacts found for this task")
        return FileResponse(
            Path(art_path),
            media_type="application/zip",
            filename=f"nexora-task-{task['task_key']}-artifacts.zip",
        )


# ---------------------------------------------------------------------------
# Frontend SPA Static Serving (Unified Server)
# ---------------------------------------------------------------------------
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

frontend_dist_path = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
assets_path = frontend_dist_path / "assets"

if assets_path.is_dir():
    app.mount("/assets", StaticFiles(directory=str(assets_path)), name="frontend_assets")


@app.get("/{full_path:path}", include_in_schema=False)
async def serve_frontend_spa(full_path: str):
    if full_path and not full_path.startswith("api/"):
        candidate = frontend_dist_path / full_path
        if candidate.is_file():
            return FileResponse(candidate)
    index_file = frontend_dist_path / "index.html"
    if index_file.is_file():
        return FileResponse(index_file)
    return {"message": "NEXORA Master API running. Frontend build not found."}
