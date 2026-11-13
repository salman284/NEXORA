from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class MasterApiError(RuntimeError):
    """The Master API rejected or could not receive a Worker request."""


@dataclass(frozen=True)
class MasterClient:
    base_url: str
    access_token: str
    timeout_seconds: float = 10.0

    def _request(self, method: str, path: str, body: dict | None = None):
        url = f"{self.base_url.rstrip('/')}{path}"
        data = json.dumps(body).encode() if body is not None else None
        request = Request(
            url,
            data=data,
            method=method,
            headers={
                "Accept": "application/json",
                "Authorization": "Bearer " + self.access_token,
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read()
                return json.loads(raw) if raw else None
        except HTTPError as error:
            detail = error.read().decode(errors="replace")[:1000]
            raise MasterApiError(f"Master returned HTTP {error.code}: {detail}") from error
        except URLError as error:
            raise MasterApiError(f"Master connection failed: {error.reason}") from error

    def heartbeat(self, node_id: str, status: str = "online", metrics: dict | None = None) -> dict:
        body = {"status": status}
        if metrics:
            body.update({key: metrics[key] for key in ("cpu_utilization", "ram_utilization", "gpu_utilization", "gpu_used_vram_bytes") if key in metrics})
        return self._request("POST", f"/api/v1/worker/nodes/{node_id}/heartbeat", body)

    def request_pairing(self, payload: dict) -> dict:
        return self._request("POST", "/api/v1/pairing/requests", payload)

    def lease_task(self, node_id: str) -> dict | None:
        return self._request("POST", f"/api/v1/worker/nodes/{node_id}/tasks/lease")

    def submit_result(self, task_id: str, lease_token: str, result: dict | None = None, error: str | None = None) -> dict:
        status = "failed" if error else "succeeded"
        return self._request(
            "POST",
            f"/api/v1/worker/tasks/{task_id}/result",
            {"lease_token": lease_token, "status": status, "result": result, "error": error},
        )