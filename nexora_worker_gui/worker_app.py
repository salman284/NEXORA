"""
NEXORA - Desktop Worker GUI Application
Standalone 1-Click GUI for worker nodes to join the NEXORA Distributed GPU Cluster.
No terminal required!
"""

from __future__ import annotations

import json
import logging
import os
import platform
import socket
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import psutil
import requests
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext

# Add project root to sys.path so we can reuse execution modules if present
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

try:
    from worker_agent.agent.executor import execute_task_with_backend as run_task
except ImportError:
    # Standalone fallback if packaged outside repo
    def run_task(payload: dict[str, Any]) -> dict[str, Any]:
        op = payload.get("operation")
        if op == "identity":
            return {"value": payload.get("value")}
        elif op == "square":
            v = payload.get("value", 0)
            return {"value": v * v}
        elif op == "sum":
            vals = payload.get("values", [])
            return {"value": sum(vals)}
        elif op == "matmul":
            a = payload.get("matrix_a") or payload.get("a", [])
            b = payload.get("matrix_b") or payload.get("b", [])
            rows_a = len(a)
            cols_a = len(a[0]) if rows_a > 0 else 0
            rows_b = len(b)
            cols_b = len(b[0]) if rows_b > 0 else 0
            if cols_a != rows_b:
                raise ValueError("Dimension mismatch")
            result = [[sum(a[i][k] * b[k][j] for k in range(cols_a)) for j in range(cols_b)] for i in range(rows_a)]
            return {"matrix": result}
        else:
            raise ValueError(f"Unsupported operation: {op}")


# ---------------------------------------------------------------------------
# Hardware Discovery
# ---------------------------------------------------------------------------
def detect_hardware() -> dict[str, Any]:
    info: dict[str, Any] = {
        "hostname": socket.gethostname(),
        "os": f"{platform.system()} {platform.release()}",
        "cpu_model": platform.processor() or "Unknown CPU",
        "cpu_threads": psutil.cpu_count(logical=True) or 1,
        "ram_total_gb": round(psutil.virtual_memory().total / (1024**3), 1),
        "gpu_model": "None (CPU Compute)",
        "gpu_vendor": None,
        "gpu_vram_gb": 0.0,
    }

    # Windows WMIC / PowerShell GPU discovery
    if platform.system() == "Windows":
        try:
            import subprocess
            cmd = "powershell -NoProfile -Command \"Get-CimInstance Win32_VideoController | Select-Object -Property Name, AdapterCompatibility, AdapterRAM | ConvertTo-Json\""
            res = subprocess.run(cmd, capture_output=True, text=True, shell=True, timeout=5)
            if res.returncode == 0 and res.stdout.strip():
                data = json.loads(res.stdout)
                card = data[0] if isinstance(data, list) else data
                name = card.get("Name")
                vendor = card.get("AdapterCompatibility")
                vram_bytes = card.get("AdapterRAM") or 0
                if name:
                    info["gpu_model"] = name
                    info["gpu_vendor"] = vendor
                    info["gpu_vram_gb"] = round(vram_bytes / (1024**3), 1) if vram_bytes else 0.0
        except Exception:
            pass

    return info


# ---------------------------------------------------------------------------
# GUI Application
# ---------------------------------------------------------------------------
class NexoraWorkerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("NEXORA — Worker Agent")
        self.geometry("740x680")
        self.minsize(680, 600)
        self.configure(bg="#0f172a")

        self.hardware = detect_hardware()
        self.is_running = False
        self.worker_thread: Optional[threading.Thread] = None

        self._apply_styles()
        self._build_ui()

    def _apply_styles(self):
        style = ttk.Style(self)
        style.theme_use("clam")

        style.configure(".", background="#0f172a", foreground="#f8fafc", font=("Segoe UI", 10))
        style.configure("Header.TLabel", font=("Segoe UI", 16, "bold"), foreground="#38bdf8", background="#0f172a")
        style.configure("Subheader.TLabel", font=("Segoe UI", 9), foreground="#94a3b8", background="#0f172a")
        style.configure("Card.TFrame", background="#1e293b", relief="flat")
        style.configure("CardLabel.TLabel", background="#1e293b", foreground="#cbd5e1", font=("Segoe UI", 9))
        style.configure("CardValue.TLabel", background="#1e293b", foreground="#38bdf8", font=("Segoe UI", 10, "bold"))

        style.configure("Primary.TButton", background="#0284c7", foreground="#ffffff", font=("Segoe UI", 10, "bold"), borderwidth=0, padding=8)
        style.map("Primary.TButton", background=[("active", "#0369a1"), ("disabled", "#475569")])

        style.configure("Danger.TButton", background="#dc2626", foreground="#ffffff", font=("Segoe UI", 10, "bold"), borderwidth=0, padding=8)
        style.map("Danger.TButton", background=[("active", "#b91c1c"), ("disabled", "#475569")])

    def _build_ui(self):
        main_frame = ttk.Frame(self, padding=16)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # ── Header ──────────────────────────────────────────────────────────
        header_frame = ttk.Frame(main_frame)
        header_frame.pack(fill=tk.X, pady=(0, 12))
        ttk.Label(header_frame, text="⚡ NEXORA Distributed Worker", style="Header.TLabel").pack(anchor=tk.W)
        ttk.Label(header_frame, text="Connect your CPU/GPU hardware to the distributed cluster with 1 click.", style="Subheader.TLabel").pack(anchor=tk.W)

        # ── Hardware Spec Card ───────────────────────────────────────────────
        hw_card = ttk.Frame(main_frame, style="Card.TFrame", padding=12)
        hw_card.pack(fill=tk.X, pady=(0, 12))

        ttk.Label(hw_card, text="💻 DETECTED HARDWARE", style="CardValue.TLabel", foreground="#38bdf8").grid(row=0, column=0, columnspan=4, sticky=tk.W, pady=(0, 8))

        ttk.Label(hw_card, text="CPU:", style="CardLabel.TLabel").grid(row=1, column=0, sticky=tk.W, padx=(0, 6))
        ttk.Label(hw_card, text=f"{self.hardware['cpu_model']} ({self.hardware['cpu_threads']}T)", style="CardValue.TLabel").grid(row=1, column=1, sticky=tk.W, padx=(0, 24))

        ttk.Label(hw_card, text="RAM:", style="CardLabel.TLabel").grid(row=1, column=2, sticky=tk.W, padx=(0, 6))
        ttk.Label(hw_card, text=f"{self.hardware['ram_total_gb']} GB", style="CardValue.TLabel").grid(row=1, column=3, sticky=tk.W)

        ttk.Label(hw_card, text="GPU:", style="CardLabel.TLabel").grid(row=2, column=0, sticky=tk.W, pady=(6, 0), padx=(0, 6))
        gpu_txt = f"{self.hardware['gpu_model']} ({self.hardware['gpu_vram_gb']} GB VRAM)" if self.hardware['gpu_vram_gb'] > 0 else self.hardware['gpu_model']
        ttk.Label(hw_card, text=gpu_txt, style="CardValue.TLabel", foreground="#4ade80" if self.hardware['gpu_vram_gb'] > 0 else "#94a3b8").grid(row=2, column=1, columnspan=3, sticky=tk.W, pady=(6, 0))

        # ── Connection Settings Card ─────────────────────────────────────────
        conn_card = ttk.Frame(main_frame, style="Card.TFrame", padding=12)
        conn_card.pack(fill=tk.X, pady=(0, 12))

        ttk.Label(conn_card, text="🌐 CONNECTION CONFIGURATION", style="CardValue.TLabel", foreground="#38bdf8").grid(row=0, column=0, columnspan=2, sticky=tk.W, pady=(0, 8))

        entry_opts = {
            "bg": "#0f172a",
            "fg": "#f8fafc",
            "insertbackground": "#38bdf8",
            "relief": "solid",
            "bd": 1,
            "highlightbackground": "#334155",
            "highlightcolor": "#38bdf8",
            "font": ("Segoe UI", 10),
        }

        # Master URL
        ttk.Label(conn_card, text="Master Host / URL:", style="CardLabel.TLabel").grid(row=1, column=0, sticky=tk.W, pady=4)
        self.entry_url = tk.Entry(conn_card, width=42, **entry_opts)
        default_url = os.getenv("NEXORA_MASTER_URL", "http://127.0.0.1:5000")
        self.entry_url.insert(0, default_url)
        self.entry_url.grid(row=1, column=1, sticky=tk.EW, pady=4, padx=(8, 0), ipady=3)

        # Worker Node Name
        ttk.Label(conn_card, text="Worker Display Name:", style="CardLabel.TLabel").grid(row=2, column=0, sticky=tk.W, pady=4)
        self.entry_name = tk.Entry(conn_card, width=42, **entry_opts)
        self.entry_name.insert(0, f"{self.hardware['hostname']}-Worker")
        self.entry_name.grid(row=2, column=1, sticky=tk.EW, pady=4, padx=(8, 0), ipady=3)

        # Pairing Code (Optional)
        ttk.Label(conn_card, text="Pairing Code (Optional):", style="CardLabel.TLabel").grid(row=3, column=0, sticky=tk.W, pady=4)
        self.entry_code = tk.Entry(conn_card, width=42, **entry_opts)
        self.entry_code.grid(row=3, column=1, sticky=tk.EW, pady=4, padx=(8, 0), ipady=3)

        # Username
        ttk.Label(conn_card, text="Worker Username:", style="CardLabel.TLabel").grid(row=4, column=0, sticky=tk.W, pady=4)
        self.entry_user = tk.Entry(conn_card, width=42, **entry_opts)
        self.entry_user.insert(0, "worker1")
        self.entry_user.grid(row=4, column=1, sticky=tk.EW, pady=4, padx=(8, 0), ipady=3)

        # Password
        ttk.Label(conn_card, text="Worker Password:", style="CardLabel.TLabel").grid(row=5, column=0, sticky=tk.W, pady=4)
        self.entry_pass = tk.Entry(conn_card, width=42, show="*", **entry_opts)
        self.entry_pass.insert(0, "workerPassword123!")
        self.entry_pass.grid(row=5, column=1, sticky=tk.EW, pady=4, padx=(8, 0), ipady=3)

        conn_card.columnconfigure(1, weight=1)

        # ── Buttons Frame ───────────────────────────────────────────────────
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, pady=(0, 12))

        self.btn_connect = ttk.Button(btn_frame, text="🚀 Connect GPU / CPU to Cluster", style="Primary.TButton", command=self.start_worker)
        self.btn_connect.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        self.btn_disconnect = ttk.Button(btn_frame, text="🛑 Disconnect", style="Danger.TButton", command=self.stop_worker, state=tk.DISABLED)
        self.btn_disconnect.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(6, 0))

        # ── Activity Log ─────────────────────────────────────────────────────
        log_label_frame = ttk.Frame(main_frame)
        log_label_frame.pack(fill=tk.X, pady=(4, 4))
        ttk.Label(log_label_frame, text="📋 LIVE ACTIVITY & TELEMETRY", style="CardValue.TLabel", foreground="#94a3b8").pack(side=tk.LEFT)
        self.status_pill = ttk.Label(log_label_frame, text="● OFFLINE", foreground="#ef4444", font=("Segoe UI", 9, "bold"))
        self.status_pill.pack(side=tk.RIGHT)

        self.log_area = scrolledtext.ScrolledText(
            main_frame,
            wrap=tk.WORD,
            bg="#020617",
            fg="#e2e8f0",
            insertbackground="#38bdf8",
            font=("Consolas", 9),
            borderwidth=1,
            relief="flat",
        )
        self.log_area.pack(fill=tk.BOTH, expand=True)

        self.log("Ready. Enter cluster master address and click 'Connect'.")

    def log(self, text: str):
        now = datetime.now().strftime("%H:%M:%S")
        self.log_area.insert(tk.END, f"[{now}] {text}\n")
        self.log_area.see(tk.END)

    def set_status(self, text: str, color: str):
        self.status_pill.configure(text=f"● {text.upper()}", foreground=color)

    def start_worker(self):
        url = self.entry_url.get().strip()
        username = self.entry_user.get().strip()
        password = self.entry_pass.get().strip()
        node_name = self.entry_name.get().strip() or self.hardware["hostname"]
        code = self.entry_code.get().strip() or None

        if not url or not username or not password:
            messagebox.showerror("Validation Error", "Master URL, Username, and Password are required.")
            return

        self.is_running = True
        self.btn_connect.configure(state=tk.DISABLED)
        self.btn_disconnect.configure(state=tk.NORMAL)
        self.set_status("CONNECTING...", "#f59e0b")

        self.worker_thread = threading.Thread(
            target=self._worker_loop,
            args=(url, username, password, node_name, code),
            daemon=True,
        )
        self.worker_thread.start()

    def stop_worker(self):
        self.log("Stopping worker...")
        self.is_running = False
        self.btn_connect.configure(state=tk.NORMAL)
        self.btn_disconnect.configure(state=tk.DISABLED)
        self.set_status("DISCONNECTED", "#ef4444")

    def _worker_loop(self, master_url: str, username: str, password: str, node_name: str, pairing_code: str | None):
        session = requests.Session()
        session.headers.update({"Content-Type": "application/json"})

        # 1. Login or Auto-Register
        self.log(f"Authenticating with master at {master_url}...")
        token: Optional[str] = None
        try:
            r = session.post(f"{master_url}/api/v1/auth/login", json={"username": username, "password": password}, timeout=8)
            if r.status_code == 200:
                token = r.json().get("access_token")
                self.log(f"Login successful for user '{username}'.")
            elif r.status_code == 401:
                # Attempt auto-register for worker
                self.log(f"Account '{username}' not found. Attempting auto-registration as worker_operator...")
                reg_payload = {
                    "full_name": node_name,
                    "username": username,
                    "email": f"{username}@example.com",
                    "password": password,
                    "roles": ["worker_operator"],
                }
                reg = session.post(f"{master_url}/api/v1/auth/register", json=reg_payload, timeout=8)
                if reg.status_code in (200, 201):
                    token = reg.json().get("access_token")
                    self.log(f"Auto-registration successful for user '{username}'.")
                elif reg.status_code == 409:
                    self.log(f"User '{username}' already exists with a different password. Please enter the correct password.")
                    self.stop_worker()
                    return
                else:
                    self.log(f"Registration failed: {reg.text}")
                    self.stop_worker()
                    return
            else:
                self.log(f"Master connection error: HTTP {r.status_code} - {r.text}")
                self.stop_worker()
                return
        except Exception as ex:
            self.log(f"Network error connecting to master: {ex}")
            self.stop_worker()
            return

        session.headers.update({"Authorization": f"Bearer {token}"})

        # 2. Pairing Request
        self.log(f"Submitting join request for node '{node_name}'...")
        local_ip = None
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
        except Exception:
            try:
                local_ip = socket.gethostbyname(socket.gethostname())
            except Exception:
                pass

        gpu_bytes = int(self.hardware["gpu_vram_gb"] * 1024**3) if self.hardware["gpu_vram_gb"] > 0 else None
        pair_payload = {
            "code": pairing_code,
            "node_name": node_name,
            "hostname": self.hardware["hostname"],
            "operating_system": self.hardware["os"],
            "private_ipv4": local_ip,
            "cpu_model": self.hardware["cpu_model"],
            "cpu_threads": self.hardware["cpu_threads"],
            "ram_total_bytes": int(self.hardware["ram_total_gb"] * 1024**3),
            "gpu_model": self.hardware["gpu_model"] if self.hardware["gpu_vram_gb"] > 0 else None,
            "gpu_vendor": self.hardware["gpu_vendor"],
            "gpu_vram_bytes": gpu_bytes,
            "compute_backend": "opencl" if self.hardware["gpu_vram_gb"] > 0 else "cpu",
        }

        try:
            r = session.post(f"{master_url}/api/v1/pairing/requests", json=pair_payload, timeout=8)
            if r.status_code in (200, 201):
                node_data = r.json()
                node_id = node_data["id"]
                self.log(f"Join request created! Node ID: {node_id}")
            else:
                self.log(f"Pairing request failed: {r.text}")
                self.stop_worker()
                return
        except Exception as ex:
            self.log(f"Error during pairing: {ex}")
            self.stop_worker()
            return

        # 3. Wait for approval loop
        self.set_status("AWAITING APPROVAL", "#eab308")
        self.log("⏳ Waiting for Master Admin to approve join request in Dashboard...")

        is_approved = False
        while self.is_running and not is_approved:
            try:
                # Probe heartbeat
                cpu_util = psutil.cpu_percent()
                ram_util = psutil.virtual_memory().percent
                hb_payload = {
                    "status": "online",
                    "cpu_utilization": cpu_util,
                    "ram_utilization": ram_util,
                }
                hb = session.post(f"{master_url}/api/v1/worker/nodes/{node_id}/heartbeat", json=hb_payload, timeout=5)
                if hb.status_code == 200:
                    is_approved = True
                    self.log("🎉 Node APPROVED by Master Admin! Connected to Cluster.")
                    self.set_status("ONLINE & READY", "#22c55e")
                    break
                elif hb.status_code == 403:
                    # Still pending
                    time.sleep(2.5)
                else:
                    self.log(f"Heartbeat status check: {hb.text}")
                    time.sleep(3.0)
            except Exception as ex:
                self.log(f"Waiting for approval: {ex}")
                time.sleep(3.0)

        # 4. Active Work Loop (Lease + Execute + Heartbeat)
        heartbeat_interval = 2.0
        last_hb = time.time()

        while self.is_running:
            now = time.time()
            if now - last_hb >= heartbeat_interval:
                try:
                    cpu_u = psutil.cpu_percent()
                    ram_u = psutil.virtual_memory().percent
                    session.post(
                        f"{master_url}/api/v1/worker/nodes/{node_id}/heartbeat",
                        json={"status": "online", "cpu_utilization": cpu_u, "ram_utilization": ram_u},
                        timeout=5,
                    )
                    last_hb = now
                except Exception:
                    pass

            # Try to lease a task
            try:
                lease_resp = session.post(f"{master_url}/api/v1/worker/nodes/{node_id}/tasks/lease", timeout=6)
                if lease_resp.status_code == 200 and lease_resp.json():
                    lease_data = lease_resp.json()
                    lease_token = lease_data["lease_token"]
                    task = lease_data["task"]
                    task_id = task["id"]
                    task_key = task["task_key"]
                    self.log(f"⚡ Leased task '{task_key}' (ID: {task_id[:8]}...). Executing...")
                    self.set_status("COMPUTING", "#a855f7")

                    # Execute task
                    t0 = time.time()
                    try:
                        res = run_task(task["payload"])
                        dur = round(time.time() - t0, 3)
                        # Submit success
                        sub = session.post(
                            f"{master_url}/api/v1/worker/tasks/{task_id}/result",
                            json={"lease_token": lease_token, "status": "succeeded", "result": res},
                            timeout=6,
                        )
                        if sub.status_code == 200:
                            self.log(f"✅ Task '{task_key}' succeeded in {dur}s! Result sent to master.")
                        else:
                            self.log(f"Result submission error: {sub.text}")
                    except Exception as exec_err:
                        self.log(f"❌ Task '{task_key}' failed: {exec_err}")
                        session.post(
                            f"{master_url}/api/v1/worker/tasks/{task_id}/result",
                            json={"lease_token": lease_token, "status": "failed", "error": str(exec_err)[:4000]},
                            timeout=6,
                        )
                    self.set_status("ONLINE & READY", "#22c55e")
            except Exception as loop_ex:
                pass

            time.sleep(1.0)


def main():
    app = NexoraWorkerApp()
    app.mainloop()


if __name__ == "__main__":
    main()
