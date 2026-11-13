from __future__ import annotations

import argparse
import os
import time
from dataclasses import dataclass

from .client import MasterApiError, MasterClient
from .executor import execute_task_isolated
from .hardware import collect_inventory
from .monitoring import collect_metrics


@dataclass(frozen=True)
class AgentConfig:
    master_url: str
    node_id: str
    access_token: str
    poll_interval_seconds: float = 2.0
    execution_timeout_seconds: float = 30.0


class WorkerAgent:
    def __init__(self, config: AgentConfig, client: MasterClient | None = None):
        self.config = config
        self.client = client or MasterClient(config.master_url, config.access_token)

    def run_once(self) -> bool:
        self.client.heartbeat(self.config.node_id, metrics=collect_metrics())
        lease = self.client.lease_task(self.config.node_id)
        if lease is None:
            return False

        task = lease["task"]
        try:
            result = execute_task_isolated(task["payload"], self.config.execution_timeout_seconds)
        except Exception as error:
            self.client.submit_result(task["id"], lease["lease_token"], error=str(error)[:4000])
        else:
            self.client.submit_result(task["id"], lease["lease_token"], result=result)
        return True

    def run_forever(self) -> None:
        while True:
            try:
                self.run_once()
            except MasterApiError:
                time.sleep(min(self.config.poll_interval_seconds * 4, 30.0))
            time.sleep(self.config.poll_interval_seconds)


def inventory_pairing_payload(code: str, inventory: dict) -> dict:
    gpu = inventory.get("gpus", [{}])[0] if inventory.get("gpus") else {}
    addresses = inventory.get("network_interfaces", [])
    address = addresses[0].get("IPAddress") if addresses else None
    cpu = inventory.get("cpu", {})
    return {
        "code": code,
        "node_name": inventory.get("hostname", "worker-node"),
        "hostname": inventory.get("hostname", "worker-node"),
        "operating_system": inventory.get("operating_system", {}).get("version"),
        "private_ipv4": address,
        "cpu_model": cpu.get("model"),
        "cpu_threads": cpu.get("logical_processors"),
        "ram_total_bytes": inventory.get("ram_total_bytes"),
        "gpu_vendor": gpu.get("AdapterCompatibility"),
        "gpu_model": gpu.get("Name"),
        "gpu_vram_bytes": gpu.get("AdapterRAM"),
        "compute_backend": inventory.get("opencl", {}).get("backend") if inventory.get("opencl", {}).get("state") == "passed" else None,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="NEXORA Worker Agent")
    parser.add_argument("--master-url", default=os.getenv("NEXORA_MASTER_URL", "http://127.0.0.1:5000"))
    parser.add_argument("--node-id", default=os.getenv("NEXORA_NODE_ID"))
    parser.add_argument("--pairing-code", default=os.getenv("NEXORA_PAIRING_CODE"))
    parser.add_argument("--token", default=os.getenv("NEXORA_ACCESS_TOKEN"))
    parser.add_argument("--node-name", default=os.getenv("NEXORA_NODE_NAME"), help="Custom display name for this Worker node")
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--execution-timeout", type=float, default=30.0)
    parser.add_argument("--once", action="store_true", help="Run one heartbeat/lease cycle and exit")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.token:
        raise SystemExit("--token (or NEXORA_ACCESS_TOKEN) is required")
    client = MasterClient(args.master_url, args.token)
    node_id = args.node_id
    if not node_id:
        inventory = collect_inventory()
        payload = inventory_pairing_payload(args.pairing_code or "", inventory)
        if args.node_name:
            payload["node_name"] = args.node_name
            payload["hostname"] = args.node_name
        paired = client.request_pairing(payload)
        node_id = paired["id"]
        print("")
        print("=============================================================")
        print("  NEXORA Distributed Worker Agent — Join Request Sent")
        print("=============================================================")
        print(f"  Master URL : {args.master_url}")
        print(f"  Node ID    : {node_id}")
        print(f"  Node Name  : {payload['node_name']}")
        print(f"  Status     : PENDING (Waiting for Master approval)")
        print("=============================================================")
        print("⏳ Waiting for Master to accept/approve in the Dashboard...", flush=True)

        # Poll until Master approves this node in Dashboard
        while True:
            time.sleep(2.5)
            try:
                client.heartbeat(node_id, metrics=collect_metrics())
                print("\n🎉 Master accepted the request! Node is now APPROVED.")
                print("🚀 Connected to GPU cluster. Listening for computational tasks...\n", flush=True)
                break
            except MasterApiError as err:
                if "not approved" in str(err).lower() or err.status_code == 403:
                    print(".", end="", flush=True)
                else:
                    print(f"\n[Status check: {err}]", flush=True)
            except Exception as ex:
                print(f"\n[Connection: {ex}]", flush=True)

    agent = WorkerAgent(AgentConfig(args.master_url, node_id, args.token, args.interval, args.execution_timeout), client)
    if args.once:
        agent.run_once()
    else:
        agent.run_forever()


if __name__ == "__main__":
    main()