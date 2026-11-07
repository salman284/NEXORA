from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from backend.app.main import task_is_compatible_with_node


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    database_path = tmp_path / "test.db"
    monkeypatch.setenv("NEXORA_DATABASE", str(database_path))
    from backend.app.main import app

    with TestClient(app) as test_client:
        yield test_client


def test_health(client: TestClient):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "master-api"}


def test_task_resource_admission_rejects_unqualified_opencl_node():
    node = {"compute_backend": None, "cpu_threads": 8, "gpu_vram_bytes": 8_000_000_000, "gpu_used_vram_bytes": 0}
    assert not task_is_compatible_with_node({"backend": "opencl", "required_gpu_memory_bytes": 1}, node)
    assert task_is_compatible_with_node({"backend": "cpu", "required_cpu_threads": 4}, node)


def test_register_create_cluster_and_logout(client: TestClient):
    registration = client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "Ada Lovelace",
            "username": "ada",
            "email": "ada@example.com",
            "password": "correct horse battery staple",
            "roles": ["master"],
        },
    )
    assert registration.status_code == 201
    token = registration.json()["access_token"]
    assert registration.json()["user"]["roles"] == ["master"]

    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 200
    cluster = client.post("/api/v1/clusters", headers=headers, json={"name": "Lab Cluster"})
    assert cluster.status_code == 201
    assert client.get("/api/v1/clusters", headers=headers).json()[0]["name"] == "Lab Cluster"
    audit = client.get("/api/v1/audit-events", headers=headers)
    assert audit.status_code == 200
    assert {event["action"] for event in audit.json()} >= {"account.registered", "cluster.created"}

    changed = client.post("/api/v1/auth/change-password", headers=headers, json={"current_password": "correct horse battery staple", "new_password": "an even longer replacement password"})
    assert changed.status_code == 204
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401
    new_login = client.post("/api/v1/auth/login", json={"username": "ada", "password": "an even longer replacement password"})
    assert new_login.status_code == 200

    assert client.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {new_login.json()['access_token']}"}).status_code == 204
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401


def test_duplicate_registration_and_role_boundary(client: TestClient):
    payload = {
        "full_name": "Worker User",
        "username": "worker",
        "email": "worker@example.com",
        "password": "a sufficiently long password",
        "roles": ["worker_operator"],
    }
    assert client.post("/api/v1/auth/register", json=payload).status_code == 201
    assert client.post("/api/v1/auth/register", json=payload).status_code == 409

    login = client.post("/api/v1/auth/login", json={"username": "worker", "password": payload["password"]})
    assert login.status_code == 200
    response = client.post(
        "/api/v1/clusters",
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
        json={"name": "Should Fail"},
    )
    assert response.status_code == 403


def test_pairing_requires_approval_before_heartbeat(client: TestClient):
    master = client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "Master User",
            "username": "master",
            "email": "master@example.com",
            "password": "master password long enough",
            "roles": ["master"],
        },
    ).json()
    worker = client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "Worker User",
            "username": "worker-node",
            "email": "worker-node@example.com",
            "password": "worker password long enough",
            "roles": ["worker_operator"],
        },
    ).json()
    master_headers = {"Authorization": f"Bearer {master['access_token']}"}
    worker_headers = {"Authorization": f"Bearer {worker['access_token']}"}

    cluster = client.post("/api/v1/clusters", headers=master_headers, json={"name": "Pairing Lab"}).json()
    pairing = client.post(f"/api/v1/clusters/{cluster['id']}/pairing-codes", headers=master_headers)
    assert pairing.status_code == 200

    request = client.post(
        "/api/v1/pairing/requests",
        headers=worker_headers,
        json={
            "code": pairing.json()["code"],
            "node_name": "Worker One",
            "hostname": "worker-one",
            "operating_system": "Windows 11",
            "private_ipv4": "192.168.0.17",
            "gpu_vendor": "AMD",
            "gpu_model": "Radeon RX 580",
            "gpu_vram_bytes": 8_000_000_000,
            "compute_backend": "OpenCL",
        },
    )
    assert request.status_code == 201
    node_id = request.json()["id"]
    assert request.json()["membership_status"] == "pending"
    assert client.post(
        "/api/v1/pairing/requests",
        headers=worker_headers,
        json={"code": pairing.json()["code"], "node_name": "Duplicate", "hostname": "duplicate"},
    ).status_code == 400
    assert client.post(f"/api/v1/worker/nodes/{node_id}/heartbeat", headers=worker_headers, json={}).status_code == 403

    approved = client.post(f"/api/v1/clusters/{cluster['id']}/members/{node_id}/approve", headers=master_headers)
    assert approved.status_code == 200
    assert approved.json()["membership_status"] == "approved"
    heartbeat = client.post(
        f"/api/v1/worker/nodes/{node_id}/heartbeat",
        headers=worker_headers,
        json={"gpu_utilization": 42.5, "status": "online"},
    )
    assert heartbeat.status_code == 200
    assert heartbeat.json()["status"] == "online"
    assert heartbeat.json()["gpu_utilization"] == 42.5
    telemetry = client.get(f"/api/v1/clusters/{cluster['id']}/telemetry", headers=master_headers)
    assert telemetry.status_code == 200
    assert telemetry.json()[0]["cpu_utilization"] is None
    assert telemetry.json()[0]["gpu_utilization"] == 42.5
    assert telemetry.json()[0]["source"] == "worker_agent"
    assert len(client.get(f"/api/v1/clusters/{cluster['id']}/nodes", headers=master_headers).json()) == 1
    revoked = client.post(f"/api/v1/clusters/{cluster['id']}/members/{node_id}/revoke", headers=master_headers)
    assert revoked.status_code == 200
    assert revoked.json()["membership_status"] == "revoked"
    assert client.post(f"/api/v1/worker/nodes/{node_id}/heartbeat", headers=worker_headers, json={}).status_code == 403
    assert client.post(f"/api/v1/clusters/{cluster['id']}/pairing-codes", headers=master_headers).json()["code"]


def test_task_parallel_job_lease_and_result_lifecycle(client: TestClient):
    master = client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "Job Master",
            "username": "job-master",
            "email": "job-master@example.com",
            "password": "job master password long",
            "roles": ["master"],
        },
    ).json()
    worker = client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "Job Worker",
            "username": "job-worker",
            "email": "job-worker@example.com",
            "password": "job worker password long",
            "roles": ["worker_operator"],
        },
    ).json()
    master_headers = {"Authorization": f"Bearer {master['access_token']}"}
    worker_headers = {"Authorization": f"Bearer {worker['access_token']}"}
    cluster = client.post("/api/v1/clusters", headers=master_headers, json={"name": "Execution Lab"}).json()
    code = client.post(f"/api/v1/clusters/{cluster['id']}/pairing-codes", headers=master_headers).json()["code"]
    node = client.post(
        "/api/v1/pairing/requests",
        headers=worker_headers,
        json={"code": code, "node_name": "Executor", "hostname": "executor"},
    ).json()
    client.post(f"/api/v1/clusters/{cluster['id']}/members/{node['id']}/approve", headers=master_headers)
    client.post(f"/api/v1/worker/nodes/{node['id']}/heartbeat", headers=worker_headers, json={"status": "online"})

    job = client.post(
        "/api/v1/jobs",
        headers=master_headers,
        json={
            "cluster_id": cluster["id"],
            "name": "Known Answer Batch",
            "tasks": [
                {"task_key": "item-1", "payload": {"value": 2}},
                {"task_key": "item-2", "payload": {"value": 3}},
            ],
        },
    )
    assert job.status_code == 201
    assert job.json()["task_count"] == 2
    lease = client.post(f"/api/v1/worker/nodes/{node['id']}/tasks/lease", headers=worker_headers)
    assert lease.status_code == 200
    lease_data = lease.json()
    task_id = lease_data["task"]["id"]
    assert lease_data["task"]["state"] == "leased"
    assert client.post(
        f"/api/v1/worker/tasks/{task_id}/result",
        headers=worker_headers,
        json={"lease_token": "invalid-token-that-is-long-enough", "status": "succeeded", "result": {"value": 4}},
    ).status_code == 409
    result = client.post(
        f"/api/v1/worker/tasks/{task_id}/result",
        headers=worker_headers,
        json={"lease_token": lease_data["lease_token"], "status": "succeeded", "result": {"value": 4}},
    )
    assert result.status_code == 200
    assert result.json()["state"] == "succeeded"
    duplicate_result = client.post(
        f"/api/v1/worker/tasks/{task_id}/result",
        headers=worker_headers,
        json={"lease_token": lease_data["lease_token"], "status": "succeeded", "result": {"value": 4}},
    )
    assert duplicate_result.status_code == 200
    assert duplicate_result.json()["result_hash"] == result.json()["result_hash"]

    second_lease = client.post(f"/api/v1/worker/nodes/{node['id']}/tasks/lease", headers=worker_headers)
    assert second_lease.status_code == 200
    second_task = second_lease.json()["task"]
    client.post(
        f"/api/v1/worker/tasks/{second_task['id']}/result",
        headers=worker_headers,
        json={"lease_token": second_lease.json()["lease_token"], "status": "succeeded", "result": {"value": 9}},
    )
    final_job = client.get(f"/api/v1/jobs/{job.json()['id']}", headers=master_headers)
    assert final_job.status_code == 200
    assert final_job.json()["state"] == "completed"
    assert final_job.json()["succeeded_count"] == 2
    final_result = client.get(f"/api/v1/jobs/{job.json()['id']}/result", headers=master_headers)
    assert final_result.status_code == 200
    assert final_result.json()["task_count"] == 2
    assert final_result.json()["results"][0]["result"]["value"] == 4
    assert len(final_result.json()["result_hash"]) == 64
    jobs = client.get("/api/v1/jobs", headers=master_headers)
    assert jobs.status_code == 200
    assert jobs.json()[0]["id"] == job.json()["id"]

    controlled = client.post(
        "/api/v1/jobs",
        headers=master_headers,
        json={
            "cluster_id": cluster["id"],
            "name": "Controlled Job",
            "tasks": [{"task_key": "controlled-1", "payload": {"operation": "square", "value": 5}}],
        },
    ).json()
    assert client.post(f"/api/v1/jobs/{controlled['id']}/pause", headers=master_headers).json()["state"] == "paused"
    assert client.post(f"/api/v1/worker/nodes/{node['id']}/tasks/lease", headers=worker_headers).json() is None
    assert client.post(f"/api/v1/jobs/{controlled['id']}/resume", headers=master_headers).json()["state"] == "running"
    controlled_lease = client.post(f"/api/v1/worker/nodes/{node['id']}/tasks/lease", headers=worker_headers).json()
    assert controlled_lease["task"]["id"]
    assert client.post(f"/api/v1/jobs/{controlled['id']}/cancel", headers=master_headers).json()["state"] == "canceled"
    assert client.post(
        f"/api/v1/worker/tasks/{controlled_lease['task']['id']}/result",
        headers=worker_headers,
        json={"lease_token": controlled_lease["lease_token"], "status": "succeeded", "result": {"value": 25}},
    ).status_code == 409


def test_workload_upload_validates_manifest_and_archive(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NEXORA_WORKLOAD_STORAGE", str(tmp_path / "workloads"))
    registration = client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "Package Owner",
            "username": "package-owner",
            "email": "package-owner@example.com",
            "password": "package owner password",
            "roles": ["master"],
        },
    ).json()
    headers = {"Authorization": f"Bearer {registration['access_token']}"}
    manifest = {
        "schema_version": "1",
        "name": "Square Demo",
        "runtime": "builtin",
        "execution_mode": "task_parallel",
        "operation": "square",
        "aggregation": "collect",
    }
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("README.txt", "Validated but never executed by the API")
    response = client.post(
        "/api/v1/workloads",
        headers=headers,
        data={"manifest": json.dumps(manifest)},
        files={"file": ("square-demo.zip", archive.getvalue(), "application/zip")},
    )
    assert response.status_code == 201
    assert response.json()["validation_state"] == "validated"
    assert len(client.get("/api/v1/workloads", headers=headers).json()) == 1
    cluster = client.post("/api/v1/clusters", headers=headers, json={"name": "Package Cluster"}).json()
    linked_job = client.post(
        "/api/v1/jobs",
        headers=headers,
        json={
            "cluster_id": cluster["id"],
            "workload_id": response.json()["id"],
            "name": "Manifest Linked Job",
            "tasks": [{"task_key": "square-1", "payload": {"operation": "square", "value": 4}}],
        },
    )
    assert linked_job.status_code == 201
    assert linked_job.json()["workload_id"] == response.json()["id"]
    mismatch = client.post(
        "/api/v1/jobs",
        headers=headers,
        json={
            "cluster_id": cluster["id"],
            "workload_id": response.json()["id"],
            "name": "Invalid Manifest Job",
            "tasks": [{"task_key": "sum-1", "payload": {"operation": "sum", "values": [1, 2]}}],
        },
    )
    assert mismatch.status_code == 400


def test_workload_upload_rejects_unsafe_archive(client: TestClient):
    registration = client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "Unsafe Package Owner",
            "username": "unsafe-owner",
            "email": "unsafe-owner@example.com",
            "password": "unsafe owner password",
            "roles": ["master"],
        },
    ).json()
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("../escape.txt", "must be rejected")
    response = client.post(
        "/api/v1/workloads",
        headers={"Authorization": f"Bearer {registration['access_token']}"},
        data={"manifest": json.dumps({
            "schema_version": "1", "name": "Unsafe", "runtime": "builtin",
            "execution_mode": "task_parallel", "operation": "square", "aggregation": "collect",
        })},
        files={"file": ("unsafe.zip", archive.getvalue(), "application/zip")},
    )
    assert response.status_code == 400