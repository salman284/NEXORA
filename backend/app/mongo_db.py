"""
NEXORA - MongoDB Database Layer
Replaces SQLite db.py with MongoDB (Motor async + PyMongo sync)
Collections:
  users, clusters, cluster_members, nodes, jobs, tasks,
  workloads, audit_events, telemetry_samples, pairing_codes
"""

import os
import logging
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from typing import Optional

from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.collection import Collection
from pymongo.database import Database

logger = logging.getLogger("nexora.db")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
def get_mongodb_uri() -> str:
    uri = os.getenv("MONGODB_URI")
    if not uri:
        raise RuntimeError(
            "MONGODB_URI environment variable is not set. "
            "Copy .env.example to .env and fill in your MongoDB URI."
        )
    return uri


def get_db_name() -> str:
    return os.getenv("NEXORA_DB_NAME", "nexora")


# ---------------------------------------------------------------------------
# Singleton client
# ---------------------------------------------------------------------------
_client: Optional[MongoClient] = None
_db: Optional[Database] = None


def get_client() -> MongoClient:
    global _client
    if _client is None:
        _client = MongoClient(get_mongodb_uri(), serverSelectionTimeoutMS=10_000, tz_aware=True)
        logger.info("MongoDB client created")
    return _client


def get_db() -> Database:
    global _db
    if _db is None:
        db_name = get_db_name()
        _db = get_client()[db_name]
        logger.info("Connected to database → %s", db_name)
    return _db


# ---------------------------------------------------------------------------
# Context manager (mirrors old connection_scope usage pattern)
# ---------------------------------------------------------------------------
@contextmanager
def connection_scope():
    """
    Yields the MongoDB database object.
    Usage:
        with connection_scope() as db:
            db.users.find_one({"username": "admin"})
    """
    yield get_db()


# ---------------------------------------------------------------------------
# Collection helpers
# ---------------------------------------------------------------------------
def col(name: str) -> Collection:
    return get_db()[name]


# ---------------------------------------------------------------------------
# Index + TTL setup (called once at startup)
# ---------------------------------------------------------------------------
def initialize_database() -> None:
    """Create all indexes and TTL rules. Idempotent — safe to call every boot."""
    db = get_db()

    # ── users ────────────────────────────────────────────────────────────
    db.users.create_index([("username", ASCENDING)], unique=True)
    db.users.create_index([("email", ASCENDING)], unique=True, sparse=True)

    # ── clusters ─────────────────────────────────────────────────────────
    db.clusters.create_index([("cluster_id", ASCENDING)], unique=True)
    db.clusters.create_index([("name", ASCENDING)])

    # ── cluster_members ──────────────────────────────────────────────────
    db.cluster_members.create_index(
        [("cluster_id", ASCENDING), ("node_id", ASCENDING)], unique=True
    )
    db.cluster_members.create_index([("status", ASCENDING)])

    # ── nodes ────────────────────────────────────────────────────────────
    db.nodes.create_index([("node_id", ASCENDING)], unique=True)
    db.nodes.create_index([("cluster_id", ASCENDING)])
    db.nodes.create_index([("last_heartbeat", DESCENDING)])

    # ── jobs ─────────────────────────────────────────────────────────────
    db.jobs.create_index([("job_id", ASCENDING)], unique=True)
    db.jobs.create_index([("cluster_id", ASCENDING), ("state", ASCENDING)])
    db.jobs.create_index([("submitted_by", ASCENDING)])

    # ── tasks ────────────────────────────────────────────────────────────
    db.tasks.create_index([("task_id", ASCENDING)], unique=True)
    db.tasks.create_index([("job_id", ASCENDING), ("state", ASCENDING)])
    db.tasks.create_index([("assigned_node", ASCENDING), ("state", ASCENDING)])
    db.tasks.create_index([("lease_expires_at", ASCENDING)])

    # ── workloads ────────────────────────────────────────────────────────
    db.workloads.create_index([("workload_id", ASCENDING)], unique=True)

    # ── audit_events ─────────────────────────────────────────────────────
    db.audit_events.create_index([("timestamp", DESCENDING)])
    db.audit_events.create_index([("actor", ASCENDING)])

    # ── telemetry_samples  (TTL: auto-delete after 7 days) ───────────────
    db.telemetry_samples.create_index([("node_id", ASCENDING)])
    db.telemetry_samples.create_index(
        [("recorded_at", ASCENDING)],
        expireAfterSeconds=7 * 24 * 3600,  # 7 days TTL
        name="telemetry_ttl",
    )

    # ── pairing_codes  (TTL: auto-delete at expires_at) ──────────────────
    db.pairing_codes.create_index([("code", ASCENDING)], unique=True)
    db.pairing_codes.create_index([("cluster_id", ASCENDING)])
    db.pairing_codes.create_index(
        [("expires_at", ASCENDING)],
        expireAfterSeconds=0,  # delete at expires_at
        name="pairing_codes_ttl",
    )

    logger.info("MongoDB indexes initialized successfully")


# ---------------------------------------------------------------------------
# Utility: current UTC timestamp (ISO string, matches old SQLite TEXT columns)
# ---------------------------------------------------------------------------
def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def utcnow_dt() -> datetime:
    return datetime.now(timezone.utc)


def future_dt(seconds: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)
