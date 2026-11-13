from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


def database_path() -> Path:
    return Path(os.getenv("NEXORA_DATABASE", "backend/data/nexora.db"))


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    full_name TEXT NOT NULL,
    username TEXT NOT NULL COLLATE NOCASE UNIQUE,
    email TEXT NOT NULL COLLATE NOCASE UNIQUE,
    organization TEXT,
    password_hash TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS roles (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('master', 'worker_operator')),
    PRIMARY KEY (user_id, role)
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL,
    revoked_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS clusters (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pairing_codes (
    id TEXT PRIMARY KEY,
    cluster_id TEXT NOT NULL REFERENCES clusters(id) ON DELETE CASCADE,
    code_hash TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    used_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS nodes (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    node_name TEXT NOT NULL,
    hostname TEXT NOT NULL,
    operating_system TEXT,
    private_ipv4 TEXT,
    cpu_model TEXT,
    cpu_threads INTEGER,
    ram_total_bytes INTEGER,
    gpu_vendor TEXT,
    gpu_model TEXT,
    gpu_vram_bytes INTEGER,
    compute_backend TEXT,
    status TEXT NOT NULL DEFAULT 'offline',
    cpu_utilization REAL,
    ram_utilization REAL,
    gpu_utilization REAL,
    gpu_used_vram_bytes INTEGER,
    last_heartbeat_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cluster_members (
    cluster_id TEXT NOT NULL REFERENCES clusters(id) ON DELETE CASCADE,
    node_id TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'revoked')),
    approved_at TEXT,
    revoked_at TEXT,
    PRIMARY KEY (cluster_id, node_id)
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    cluster_id TEXT NOT NULL REFERENCES clusters(id) ON DELETE CASCADE,
    owner_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    workload_id TEXT REFERENCES workload_packages(id) ON DELETE SET NULL,
    name TEXT NOT NULL,
    execution_mode TEXT NOT NULL CHECK (execution_mode = 'task_parallel'),
    scheduling_policy TEXT NOT NULL DEFAULT 'automatic' CHECK (scheduling_policy IN ('automatic', 'manual', 'hybrid')),
    scheduling_config_json TEXT NOT NULL DEFAULT '{}',
    priority INTEGER NOT NULL DEFAULT 0,
    state TEXT NOT NULL DEFAULT 'queued' CHECK (state IN ('queued', 'running', 'paused', 'completed', 'failed', 'canceled')),
    retry_limit INTEGER NOT NULL DEFAULT 2,
    lease_seconds INTEGER NOT NULL DEFAULT 60,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    task_key TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'queued' CHECK (state IN ('queued', 'leased', 'running', 'succeeded', 'failed', 'retrying', 'canceled', 'timed_out')),
    attempt INTEGER NOT NULL DEFAULT 0,
    assigned_node_id TEXT REFERENCES nodes(id) ON DELETE SET NULL,
    pinned_node_id TEXT REFERENCES nodes(id) ON DELETE SET NULL,
    lease_token_hash TEXT,
    lease_expires_at TEXT,
    next_retry_at TEXT,
    started_at TEXT,
    completed_at TEXT,
    duration_seconds REAL,
    result_json TEXT,
    result_hash TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (job_id, task_key)
);

CREATE TABLE IF NOT EXISTS task_attempts (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    attempt INTEGER NOT NULL,
    node_id TEXT REFERENCES nodes(id) ON DELETE SET NULL,
    state TEXT NOT NULL,
    leased_at TEXT NOT NULL,
    completed_at TEXT,
    error TEXT,
    UNIQUE (task_id, attempt)
);

CREATE TABLE IF NOT EXISTS workload_packages (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    manifest_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    validation_state TEXT NOT NULL CHECK (validation_state IN ('validated', 'rejected')),
    storage_path TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_events (
    id TEXT PRIMARY KEY,
    actor_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    action TEXT NOT NULL,
    scope_type TEXT NOT NULL,
    scope_id TEXT,
    outcome TEXT NOT NULL CHECK (outcome IN ('success', 'failure')),
    metadata_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS telemetry_samples (
    id TEXT PRIMARY KEY,
    node_id TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    captured_at TEXT NOT NULL,
    source TEXT NOT NULL,
    cpu_utilization REAL,
    ram_utilization REAL,
    gpu_utilization REAL,
    gpu_used_vram_bytes INTEGER
);

CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_clusters_owner ON clusters(owner_id);
CREATE INDEX IF NOT EXISTS idx_pairing_codes_cluster ON pairing_codes(cluster_id);
CREATE INDEX IF NOT EXISTS idx_nodes_owner ON nodes(owner_id);
CREATE INDEX IF NOT EXISTS idx_cluster_members_node ON cluster_members(node_id);
CREATE INDEX IF NOT EXISTS idx_jobs_cluster ON jobs(cluster_id);
CREATE INDEX IF NOT EXISTS idx_tasks_job_state ON tasks(job_id, state);
CREATE INDEX IF NOT EXISTS idx_tasks_lease ON tasks(state, lease_expires_at);
CREATE INDEX IF NOT EXISTS idx_workloads_owner ON workload_packages(owner_id);
CREATE INDEX IF NOT EXISTS idx_audit_actor_time ON audit_events(actor_id, created_at);
CREATE INDEX IF NOT EXISTS idx_telemetry_node_time ON telemetry_samples(node_id, captured_at);
"""


def connect() -> sqlite3.Connection:
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


@contextmanager
def connection_scope() -> Iterator[sqlite3.Connection]:
    connection = connect()
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _ensure_column(connection: sqlite3.Connection, table: str, column_def: str) -> None:
    column_name = column_def.split()[0]
    cursor = connection.execute(f"PRAGMA table_info({table})")
    existing = [row["name"] for row in cursor.fetchall()]
    if column_name not in existing:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column_def}")


def initialize_database() -> None:
    with connection_scope() as connection:
        connection.executescript(SCHEMA)
        _ensure_column(connection, "jobs", "priority INTEGER NOT NULL DEFAULT 0")
        _ensure_column(connection, "jobs", "retry_limit INTEGER NOT NULL DEFAULT 2")
        _ensure_column(connection, "jobs", "lease_seconds INTEGER NOT NULL DEFAULT 60")
        _ensure_column(connection, "jobs", "scheduling_policy TEXT NOT NULL DEFAULT 'automatic'")
        _ensure_column(connection, "jobs", "scheduling_config_json TEXT NOT NULL DEFAULT '{}'")
        _ensure_column(connection, "tasks", "next_retry_at TEXT")
        _ensure_column(connection, "tasks", "pinned_node_id TEXT REFERENCES nodes(id) ON DELETE SET NULL")
        _ensure_column(connection, "tasks", "started_at TEXT")
        _ensure_column(connection, "tasks", "completed_at TEXT")
        _ensure_column(connection, "tasks", "duration_seconds REAL")