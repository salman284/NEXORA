"""
NEXORA - Desktop Worker GUI Application v2
Enhanced features:
  - Persistent settings (save/load config)
  - Live CPU / RAM / GPU resource gauges
  - Session task stats (completed, failed, compute time)
  - Current task display with live elapsed timer
  - Auto-reconnect on connection drop (up to 5 retries)
  - System tray minimize (requires pystray + Pillow)
  - Desktop notifications (requires plyer)
  - Log filtering (All / Errors / Tasks) + Clear + Export
  - Collapsible connection config card
"""

from __future__ import annotations

import json
import logging
import os
import platform
import socket
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import psutil
import requests
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog

# ── Optional: System Tray ──────────────────────────────────────────────────
try:
    import pystray
    from PIL import Image, ImageDraw
    HAS_TRAY = True
except ImportError:
    HAS_TRAY = False

# ── Optional: Desktop Notifications ───────────────────────────────────────
try:
    from plyer import notification as plyer_notify
    HAS_NOTIFY = True
except ImportError:
    HAS_NOTIFY = False

# ── Project path ───────────────────────────────────────────────────────────
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

try:
    from worker_agent.agent.executor import execute_task_and_collect as run_task_engine
    from worker_agent.agent.executor import execute_task_with_backend as run_task
except ImportError:
    run_task_engine = None

    def run_task(payload: dict[str, Any]) -> dict[str, Any]:
        op = payload.get("operation")
        if op == "identity":
            return {"value": payload.get("value")}
        elif op == "square":
            v = payload.get("value", 0)
            return {"value": v * v}
        elif op == "sum":
            return {"value": sum(payload.get("values", []))}
        elif op == "matmul":
            a = payload.get("matrix_a") or payload.get("a", [])
            b = payload.get("matrix_b") or payload.get("b", [])
            rows_a, cols_a = len(a), len(a[0]) if a else 0
            cols_b = len(b[0]) if b else 0
            if cols_a != len(b):
                raise ValueError("Dimension mismatch")
            result = [
                [sum(a[i][k] * b[k][j] for k in range(cols_a)) for j in range(cols_b)]
                for i in range(rows_a)
            ]
            return {"matrix": result}
        else:
            raise ValueError(f"Unsupported operation: {op}")


logger = logging.getLogger("nexora.worker")

# ── Colour palette ─────────────────────────────────────────────────────────
C_BG     = "#0f172a"
C_CARD   = "#1e293b"
C_BORDER = "#334155"
C_TEXT   = "#f8fafc"
C_SUB    = "#94a3b8"
C_BLUE   = "#38bdf8"
C_GREEN  = "#22c55e"
C_LIME   = "#4ade80"
C_YELLOW = "#f59e0b"
C_RED    = "#ef4444"
C_PURPLE = "#a855f7"


# ── Config persistence ─────────────────────────────────────────────────────
CONFIG_PATH = Path.home() / ".nexora_worker" / "config.json"


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_config(data: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ── Hardware detection ─────────────────────────────────────────────────────
def detect_hardware() -> dict[str, Any]:
    info: dict[str, Any] = {
        "hostname":     socket.gethostname(),
        "os":           f"{platform.system()} {platform.release()}",
        "cpu_model":    platform.processor() or "Unknown CPU",
        "cpu_threads":  psutil.cpu_count(logical=True) or 1,
        "ram_total_gb": round(psutil.virtual_memory().total / 1024 ** 3, 1),
        "gpu_model":    "None (CPU Compute)",
        "gpu_vendor":   None,
        "gpu_vram_gb":  0.0,
    }
    if platform.system() == "Windows":
        try:
            import subprocess
            cmd = (
                'powershell -NoProfile -Command '
                '"Get-CimInstance Win32_VideoController | '
                'Select-Object -Property Name,AdapterCompatibility,AdapterRAM | '
                'ConvertTo-Json"'
            )
            res = subprocess.run(cmd, capture_output=True, text=True, shell=True, timeout=5)
            if res.returncode == 0 and res.stdout.strip():
                data = json.loads(res.stdout)
                card = data[0] if isinstance(data, list) else data
                name = card.get("Name")
                if name:
                    info["gpu_model"]   = name
                    info["gpu_vendor"]  = card.get("AdapterCompatibility")
                    vram = card.get("AdapterRAM") or 0
                    info["gpu_vram_gb"] = round(vram / 1024 ** 3, 1) if vram else 0.0
        except Exception:
            pass
    return info


# ══════════════════════════════════════════════════════════════════════════════
#  Main Application
# ══════════════════════════════════════════════════════════════════════════════
class NexoraWorkerApp(tk.Tk):
    # ── Init ──────────────────────────────────────────────────────────────────
    def __init__(self):
        super().__init__()
        self.title("NEXORA — Worker Agent")
        self.geometry("860x820")
        self.minsize(740, 660)
        self.configure(bg=C_BG)

        self.hardware    = detect_hardware()
        self.config_data = load_config()

        # Worker state
        self.is_running: bool                   = False
        self.worker_thread: Optional[threading.Thread] = None
        self._tray_icon: Optional[Any]          = None
        self._conn_card_visible: bool           = True

        # Task tracking
        self._tasks_completed:   int   = 0
        self._tasks_failed:      int   = 0
        self._total_compute_sec: float = 0.0
        self._current_task:      Optional[str]  = None
        self._task_start:        float          = 0.0
        self._session_start:     float          = time.time()

        # Log
        self._log_entries: list[tuple[str, str, str]] = []  # (hh:mm:ss, level, text)
        self._log_filter   = tk.StringVar(value="all")

        self._apply_styles()
        self._build_ui()
        self._start_periodic_update()

        if HAS_TRAY:
            self._setup_tray()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── Styles ────────────────────────────────────────────────────────────────
    def _apply_styles(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".", background=C_BG, foreground=C_TEXT, font=("Segoe UI", 10))

        s.configure("H.TLabel",    font=("Segoe UI", 16, "bold"), foreground=C_BLUE,  background=C_BG)
        s.configure("Sub.TLabel",  font=("Segoe UI", 9),          foreground=C_SUB,   background=C_BG)
        s.configure("Sec.TLabel",  font=("Segoe UI", 8, "bold"),  foreground=C_SUB,   background=C_CARD)
        s.configure("Card.TFrame", background=C_CARD, relief="flat")
        s.configure("KV.TLabel",   background=C_CARD, foreground=C_SUB,  font=("Segoe UI", 9))
        s.configure("VV.TLabel",   background=C_CARD, foreground=C_BLUE, font=("Segoe UI", 9, "bold"))

        s.configure("Primary.TButton",   background="#0284c7", foreground="#fff", font=("Segoe UI", 10, "bold"), borderwidth=0, padding=8)
        s.configure("Danger.TButton",    background="#dc2626", foreground="#fff", font=("Segoe UI", 10, "bold"), borderwidth=0, padding=8)
        s.configure("Secondary.TButton", background=C_BORDER,  foreground=C_TEXT, font=("Segoe UI", 9),          borderwidth=0, padding=6)

        s.map("Primary.TButton",   background=[("active", "#0369a1"), ("disabled", "#475569")])
        s.map("Danger.TButton",    background=[("active", "#b91c1c"), ("disabled", "#475569")])
        s.map("Secondary.TButton", background=[("active", "#475569"), ("disabled", C_CARD)])

        s.configure("CPU.Horizontal.TProgressbar", troughcolor=C_BORDER, background=C_BLUE,   borderwidth=0, thickness=10)
        s.configure("RAM.Horizontal.TProgressbar", troughcolor=C_BORDER, background=C_GREEN,  borderwidth=0, thickness=10)
        s.configure("GPU.Horizontal.TProgressbar", troughcolor=C_BORDER, background=C_PURPLE, borderwidth=0, thickness=10)

    # ── UI build ──────────────────────────────────────────────────────────────
    def _build_ui(self):
        # Outer scrollable canvas
        outer = tk.Frame(self, bg=C_BG)
        outer.pack(fill=tk.BOTH, expand=True)

        canvas = tk.Canvas(outer, bg=C_BG, highlightthickness=0)
        sb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        self._scroll_frame = tk.Frame(canvas, bg=C_BG)

        self._scroll_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        win_id = canvas.create_window((0, 0), window=self._scroll_frame, anchor="nw")

        # KEY FIX: make scroll_frame always fill the canvas width and expand height in fullscreen
        def _on_canvas_configure(e):
            try:
                req_h = self._scroll_frame.winfo_reqheight()
                canvas.itemconfig(win_id, width=e.width, height=max(e.height, req_h))
            except Exception:
                canvas.itemconfig(win_id, width=e.width)

        canvas.bind("<Configure>", _on_canvas_configure)

        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(-1 * (e.delta // 120), "units"))

        mf = self._scroll_frame

        # ── 1. Header ─────────────────────────────────────────────────────
        hdr = tk.Frame(mf, bg=C_BG)
        hdr.pack(fill=tk.X, padx=16, pady=(14, 10))
        tk.Label(hdr, text="⚡ NEXORA Distributed Worker",
                 font=("Segoe UI", 16, "bold"), fg=C_BLUE, bg=C_BG).pack(anchor=tk.W)
        tk.Label(hdr, text="Connect your CPU/GPU hardware to the distributed cluster with 1 click.",
                 font=("Segoe UI", 9), fg=C_SUB, bg=C_BG).pack(anchor=tk.W)

        # ── 2. Hardware + Live Gauges (side by side) ───────────────────────
        row2 = tk.Frame(mf, bg=C_BG)
        row2.pack(fill=tk.X, padx=16, pady=(0, 8))
        row2.columnconfigure(0, weight=1)
        row2.columnconfigure(1, weight=1)
        self._build_hardware_card(row2)
        self._build_gauge_card(row2)

        # ── 3. Session Stats ───────────────────────────────────────────────
        self._build_stats_card(mf)

        # ── 4. Current Task ────────────────────────────────────────────────
        self._build_task_card(mf)

        # ── 5. Connection Config ───────────────────────────────────────────
        self._build_conn_card(mf)

        # ── 6. Buttons ────────────────────────────────────────────────────
        self._build_buttons(mf)

        # ── 7. Log section ─────────────────────────────────────────────────
        self._build_log_section(mf)

    # ── Hardware card ─────────────────────────────────────────────────────────
    def _build_hardware_card(self, parent):
        card = tk.Frame(parent, bg=C_CARD)
        card.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        self._add_padding(card)

        tk.Label(card, text="💻  DETECTED HARDWARE", font=("Segoe UI", 8, "bold"),
                 fg=C_SUB, bg=C_CARD).pack(anchor=tk.W, pady=(0, 8))

        has_gpu = self.hardware["gpu_vram_gb"] > 0
        rows = [
            ("CPU", f"{self.hardware['cpu_model']} ({self.hardware['cpu_threads']}T)", C_BLUE),
            ("RAM", f"{self.hardware['ram_total_gb']} GB total",                       C_BLUE),
            ("GPU", (
                f"{self.hardware['gpu_model']} ({self.hardware['gpu_vram_gb']} GB VRAM)"
                if has_gpu else self.hardware["gpu_model"]
            ), C_LIME if has_gpu else C_SUB),
            ("OS",  self.hardware["os"],                                                C_SUB),
        ]
        for lbl, val, color in rows:
            r = tk.Frame(card, bg=C_CARD)
            r.pack(fill=tk.X, pady=2)
            tk.Label(r, text=f"{lbl}:", width=4, anchor=tk.W,
                     font=("Segoe UI", 9), fg=C_SUB, bg=C_CARD).pack(side=tk.LEFT)
            tk.Label(r, text=val, font=("Segoe UI", 9, "bold"),
                     fg=color, bg=C_CARD, wraplength=600, anchor=tk.W).pack(side=tk.LEFT, padx=(4, 0))

    # ── Gauge card ────────────────────────────────────────────────────────────
    def _build_gauge_card(self, parent):
        card = tk.Frame(parent, bg=C_CARD)
        card.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        self._add_padding(card)

        tk.Label(card, text="📊  LIVE RESOURCE USAGE", font=("Segoe UI", 8, "bold"),
                 fg=C_SUB, bg=C_CARD).pack(anchor=tk.W, pady=(0, 8))

        self.cpu_bar  = self._gauge_row(card, "CPU ", "CPU.Horizontal.TProgressbar")
        self.ram_bar  = self._gauge_row(card, "RAM ", "RAM.Horizontal.TProgressbar")

        has_gpu = self.hardware["gpu_vram_gb"] > 0
        if has_gpu:
            self.gpu_bar = self._gauge_row(card, "GPU ", "GPU.Horizontal.TProgressbar")
        else:
            self.gpu_bar = None
            tk.Label(card, text="GPU: Not detected (CPU compute)",
                     font=("Segoe UI", 8), fg=C_SUB, bg=C_CARD).pack(anchor=tk.W, pady=4)

        self.uptime_lbl = tk.Label(card, text="Session: 00:00:00",
                                   font=("Segoe UI", 8), fg=C_SUB, bg=C_CARD)
        self.uptime_lbl.pack(anchor=tk.W, pady=(10, 0))

    def _gauge_row(self, parent, label: str, style: str):
        """Create a labelled progress-bar row; bar fills available width."""
        row = tk.Frame(parent, bg=C_CARD)
        row.pack(fill=tk.X, pady=4)
        row.columnconfigure(1, weight=1)   # bar column stretches

        tk.Label(row, text=label, width=4, font=("Segoe UI", 9),
                 fg=C_SUB, bg=C_CARD).grid(row=0, column=0, sticky=tk.W)
        bar = ttk.Progressbar(row, style=style, mode="determinate")
        bar.grid(row=0, column=1, sticky=tk.EW, padx=(4, 8))
        pct = tk.Label(row, text="0%", width=18, font=("Segoe UI", 9, "bold"),
                       fg=C_TEXT, bg=C_CARD, anchor=tk.W)
        pct.grid(row=0, column=2, sticky=tk.W)
        bar._pct_label = pct
        return bar

    # ── Stats card ────────────────────────────────────────────────────────────
    def _build_stats_card(self, parent):
        wrapper = tk.Frame(parent, bg=C_BG)
        wrapper.pack(fill=tk.X, padx=16, pady=(0, 8))

        card = tk.Frame(wrapper, bg=C_CARD)
        card.pack(fill=tk.X)
        self._add_padding(card)

        tk.Label(card, text="📈  SESSION STATS", font=("Segoe UI", 8, "bold"),
                 fg=C_SUB, bg=C_CARD).pack(anchor=tk.W, pady=(0, 8))

        row = tk.Frame(card, bg=C_CARD)
        row.pack(fill=tk.X)

        self.stat_ok_lbl   = self._stat_col(row, "0",      "Completed",   C_GREEN)
        self._sep(row)
        self.stat_fail_lbl = self._stat_col(row, "0",      "Failed",      C_RED)
        self._sep(row)
        self.stat_time_lbl = self._stat_col(row, "0.0s",   "Compute",     C_BLUE)
        self._sep(row)
        self.stat_sess_lbl = self._stat_col(row, "00:00:00","Session",     C_SUB)

    def _stat_col(self, parent, num: str, label: str, color: str) -> tk.Label:
        col = tk.Frame(parent, bg=C_CARD)
        col.pack(side=tk.LEFT, expand=True, padx=8)
        num_lbl = tk.Label(col, text=num, font=("Segoe UI", 22, "bold"), fg=color, bg=C_CARD)
        num_lbl.pack()
        tk.Label(col, text=label, font=("Segoe UI", 8), fg=C_SUB, bg=C_CARD).pack()
        return num_lbl

    def _sep(self, parent):
        tk.Frame(parent, bg=C_BORDER, width=1).pack(side=tk.LEFT, fill=tk.Y, padx=4, pady=4)

    # ── Current task card ─────────────────────────────────────────────────────
    def _build_task_card(self, parent):
        wrapper = tk.Frame(parent, bg=C_BG)
        wrapper.pack(fill=tk.X, padx=16, pady=(0, 8))

        card = tk.Frame(wrapper, bg=C_CARD)
        card.pack(fill=tk.X)
        self._add_padding(card, py=8)

        hdr = tk.Frame(card, bg=C_CARD)
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text="⚡  CURRENT TASK", font=("Segoe UI", 8, "bold"),
                 fg=C_SUB, bg=C_CARD).pack(side=tk.LEFT)
        self.task_timer_lbl = tk.Label(hdr, text="", font=("Segoe UI", 9, "bold"),
                                       fg=C_PURPLE, bg=C_CARD)
        self.task_timer_lbl.pack(side=tk.RIGHT)

        self.task_name_lbl = tk.Label(card, text="No task running",
                                      font=("Segoe UI", 10, "italic"), fg=C_SUB, bg=C_CARD)
        self.task_name_lbl.pack(anchor=tk.W, pady=(4, 0))

    # ── Connection card ───────────────────────────────────────────────────────
    def _build_conn_card(self, parent):
        wrapper = tk.Frame(parent, bg=C_BG)
        wrapper.pack(fill=tk.X, padx=16, pady=(0, 4))

        # Header row (always visible)
        hdr = tk.Frame(wrapper, bg=C_BG)
        hdr.pack(fill=tk.X, pady=(0, 2))
        tk.Label(hdr, text="🌐  CONNECTION CONFIGURATION",
                 font=("Segoe UI", 8, "bold"), fg=C_SUB, bg=C_BG).pack(side=tk.LEFT)
        self._toggle_btn = tk.Button(
            hdr, text="▲ Collapse", font=("Segoe UI", 8), fg=C_SUB, bg=C_BG,
            bd=0, cursor="hand2", activebackground=C_BG,
            command=self._toggle_conn_card,
        )
        self._toggle_btn.pack(side=tk.RIGHT)

        # Collapsible body
        self._conn_body = tk.Frame(wrapper, bg=C_CARD)
        self._conn_body.pack(fill=tk.X)
        self._add_padding(self._conn_body)

        entry_kw = dict(
            bg=C_BG, fg=C_TEXT, insertbackground=C_BLUE,
            relief="solid", bd=1, highlightbackground=C_BORDER,
            highlightcolor=C_BLUE, font=("Segoe UI", 10),
        )

        cfg = self.config_data
        fields = [
            ("Master Host / URL:",      "entry_url",  cfg.get("master_url",    os.getenv("NEXORA_MASTER_URL", "http://127.0.0.1:5000")), False),
            ("Worker Display Name:",    "entry_name", cfg.get("node_name",     f"{self.hardware['hostname']}-Worker"),                   False),
            ("Pairing Code (Optional):","entry_code", cfg.get("pairing_code",  ""),                                                      False),
            ("Worker Username:",        "entry_user", cfg.get("username",      "worker1"),                                               False),
            ("Worker Password:",        "entry_pass", cfg.get("password",      "workerPassword123!"),                                    True),
        ]

        self._conn_body.columnconfigure(1, weight=1)
        for i, (label, attr, default, masked) in enumerate(fields):
            tk.Label(self._conn_body, text=label, font=("Segoe UI", 9),
                     fg=C_SUB, bg=C_CARD).grid(row=i, column=0, sticky=tk.W, pady=4, padx=(0, 10))
            e = tk.Entry(self._conn_body, width=46, show="*" if masked else "", **entry_kw)
            e.insert(0, default)
            e.grid(row=i, column=1, sticky=tk.EW, pady=4, ipady=3)
            setattr(self, attr, e)

    def _toggle_conn_card(self):
        if self._conn_card_visible:
            self._conn_body.pack_forget()
            self._toggle_btn.config(text="▼ Expand")
        else:
            self._conn_body.pack(fill=tk.X)
            self._toggle_btn.config(text="▲ Collapse")
        self._conn_card_visible = not self._conn_card_visible

    # ── Buttons ───────────────────────────────────────────────────────────────
    def _build_buttons(self, parent):
        wrapper = tk.Frame(parent, bg=C_BG)
        wrapper.pack(fill=tk.X, padx=16, pady=(8, 8))

        self.btn_connect = ttk.Button(
            wrapper, text="🚀  Connect GPU / CPU to Cluster",
            style="Primary.TButton", command=self.start_worker,
        )
        self.btn_connect.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))

        self.btn_disconnect = ttk.Button(
            wrapper, text="🛑  Disconnect",
            style="Danger.TButton", command=self.stop_worker, state=tk.DISABLED,
        )
        self.btn_disconnect.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 4))

        ttk.Button(
            wrapper, text="💾  Save Settings",
            style="Secondary.TButton", command=self._save_settings,
        ).pack(side=tk.LEFT, padx=(4, 0))

    # ── Log section ───────────────────────────────────────────────────────────
    def _build_log_section(self, parent):
        wrapper = tk.Frame(parent, bg=C_BG)
        wrapper.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 12))

        # Header row
        hdr = tk.Frame(wrapper, bg=C_BG)
        hdr.pack(fill=tk.X, pady=(0, 2))
        tk.Label(hdr, text="📋  LIVE ACTIVITY", font=("Segoe UI", 8, "bold"),
                 fg=C_SUB, bg=C_BG).pack(side=tk.LEFT)
        self.status_pill = tk.Label(hdr, text="● OFFLINE",
                                    font=("Segoe UI", 9, "bold"), fg=C_RED, bg=C_BG)
        self.status_pill.pack(side=tk.RIGHT)

        # Filter + action bar
        bar = tk.Frame(wrapper, bg=C_CARD)
        bar.pack(fill=tk.X, pady=(0, 2))
        pad = tk.Frame(bar, bg=C_CARD)
        pad.pack(fill=tk.X, padx=8, pady=4)

        tk.Label(pad, text="Filter:", font=("Segoe UI", 9),
                 fg=C_SUB, bg=C_CARD).pack(side=tk.LEFT, padx=(0, 6))
        for text, val in [("All", "all"), ("Errors", "errors"), ("Tasks", "tasks")]:
            tk.Radiobutton(
                pad, text=text, variable=self._log_filter, value=val,
                command=self._render_log_full,
                bg=C_CARD, fg=C_SUB, selectcolor=C_BG,
                activebackground=C_CARD, font=("Segoe UI", 9), bd=0, cursor="hand2",
            ).pack(side=tk.LEFT, padx=4)

        ttk.Button(pad, text="Export .txt", style="Secondary.TButton",
                   command=self._export_log).pack(side=tk.RIGHT, padx=(4, 0))
        ttk.Button(pad, text="Clear", style="Secondary.TButton",
                   command=self._clear_log).pack(side=tk.RIGHT, padx=(4, 0))

        # Log area
        self.log_area = scrolledtext.ScrolledText(
            wrapper, wrap=tk.WORD,
            bg="#020617", fg="#e2e8f0", insertbackground=C_BLUE,
            font=("Consolas", 9), borderwidth=0, relief="flat", height=14,
        )
        self.log_area.pack(fill=tk.BOTH, expand=True)

        # Colour tags
        self.log_area.tag_config("time",    foreground="#475569")
        self.log_area.tag_config("info",    foreground="#e2e8f0")
        self.log_area.tag_config("success", foreground=C_GREEN)
        self.log_area.tag_config("task",    foreground=C_LIME)
        self.log_area.tag_config("warning", foreground=C_YELLOW)
        self.log_area.tag_config("error",   foreground=C_RED)

        self.log("Ready. Enter cluster master address and click 'Connect'.")

    # ── Periodic update (gauges + timers) ─────────────────────────────────────
    def _start_periodic_update(self):
        self._periodic_tick()

    def _periodic_tick(self):
        # CPU gauge
        cpu = psutil.cpu_percent()
        self.cpu_bar["value"] = cpu
        self.cpu_bar._pct_label.config(text=f"{cpu:.0f}%")

        # RAM gauge
        mem = psutil.virtual_memory()
        self.ram_bar["value"] = mem.percent
        used_gb = mem.used / 1024 ** 3
        self.ram_bar._pct_label.config(
            text=f"{mem.percent:.0f}%  ({used_gb:.1f}/{self.hardware['ram_total_gb']}GB)"
        )

        # GPU gauge (GPUtil optional)
        if self.gpu_bar:
            try:
                import GPUtil
                gpus = GPUtil.getGPUs()
                if gpus:
                    g = gpus[0]
                    self.gpu_bar["value"] = g.load * 100
                    self.gpu_bar._pct_label.config(
                        text=f"{g.load * 100:.0f}%  ({g.memoryUsed:.0f}/{g.memoryTotal:.0f}MB)"
                    )
            except Exception:
                pass

        # Session uptime
        elapsed = int(time.time() - self._session_start)
        h, m, s = elapsed // 3600, (elapsed % 3600) // 60, elapsed % 60
        uptime_str = f"{h:02d}:{m:02d}:{s:02d}"
        self.uptime_lbl.config(text=f"Session: {uptime_str}")
        self.stat_sess_lbl.config(text=uptime_str)

        # Current task elapsed timer
        if self._current_task:
            task_elapsed = time.time() - self._task_start
            self.task_timer_lbl.config(text=f"⏱ {task_elapsed:.1f}s")

        self.after(2000, self._periodic_tick)

    # ── Log helpers ───────────────────────────────────────────────────────────
    def log(self, text: str, level: str = "info"):
        """Thread-safe log. Schedules insertion on the main thread."""
        now = datetime.now().strftime("%H:%M:%S")
        self._log_entries.append((now, level, text))
        self.after(0, lambda ts=now, lv=level, tx=text: self._append_log_line(ts, lv, tx))

    def _append_log_line(self, ts: str, level: str, text: str):
        """Append one line if it passes the current filter (main-thread only)."""
        f = self._log_filter.get()
        if f == "errors" and level != "error":
            return
        if f == "tasks" and level not in ("task", "success", "error"):
            return
        self.log_area.config(state=tk.NORMAL)
        self.log_area.insert(tk.END, f"[{ts}] ", "time")
        self.log_area.insert(tk.END, text + "\n", level)
        self.log_area.see(tk.END)
        self.log_area.config(state=tk.DISABLED)

    def _render_log_full(self):
        """Re-render entire log based on the selected filter."""
        self.log_area.config(state=tk.NORMAL)
        self.log_area.delete(1.0, tk.END)
        f = self._log_filter.get()
        for ts, level, text in self._log_entries:
            if f == "errors" and level != "error":
                continue
            if f == "tasks" and level not in ("task", "success", "error"):
                continue
            self.log_area.insert(tk.END, f"[{ts}] ", "time")
            self.log_area.insert(tk.END, text + "\n", level)
        self.log_area.config(state=tk.DISABLED)
        self.log_area.see(tk.END)

    def _clear_log(self):
        self._log_entries.clear()
        self.log_area.config(state=tk.NORMAL)
        self.log_area.delete(1.0, tk.END)
        self.log_area.config(state=tk.DISABLED)
        self.log("Log cleared.")

    def _export_log(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All", "*.*")],
            initialfile=f"nexora_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
        )
        if path:
            lines = [f"[{ts}] {text}\n" for ts, _, text in self._log_entries]
            Path(path).write_text("".join(lines), encoding="utf-8")
            self.log(f"Log exported → {path}", "success")

    # ── Status pill ───────────────────────────────────────────────────────────
    def set_status(self, text: str, color: str):
        self.after(0, lambda: self.status_pill.config(
            text=f"● {text.upper()}", fg=color
        ))
        if self._tray_icon and HAS_TRAY:
            try:
                self._tray_icon.icon = self._make_tray_img(color)
            except Exception:
                pass

    # ── Settings ──────────────────────────────────────────────────────────────
    def _save_settings(self):
        data = {
            "master_url":   self.entry_url.get().strip(),
            "node_name":    self.entry_name.get().strip(),
            "pairing_code": self.entry_code.get().strip(),
            "username":     self.entry_user.get().strip(),
            "password":     self.entry_pass.get().strip(),
        }
        save_config(data)
        self.config_data = data
        self.log("✅ Settings saved to ~/.nexora_worker/config.json", "success")

    # ── Current task UI helpers ───────────────────────────────────────────────
    def _set_task(self, name: str):
        self._current_task = name
        self._task_start   = time.time()
        self.after(0, lambda: [
            self.task_name_lbl.config(
                text=f"⚡ {name}", fg=C_PURPLE, font=("Segoe UI", 10, "bold")
            ),
        ])

    def _clear_task(self):
        self._current_task = None
        self.after(0, lambda: [
            self.task_name_lbl.config(
                text="No task running", fg=C_SUB, font=("Segoe UI", 10, "italic")
            ),
            self.task_timer_lbl.config(text=""),
        ])

    # ── Stats update ──────────────────────────────────────────────────────────
    def _refresh_stats(self):
        self.after(0, lambda: [
            self.stat_ok_lbl.config(text=str(self._tasks_completed)),
            self.stat_fail_lbl.config(text=str(self._tasks_failed)),
            self.stat_time_lbl.config(text=f"{self._total_compute_sec:.1f}s"),
        ])

    # ── Notifications ─────────────────────────────────────────────────────────
    def _notify(self, title: str, message: str):
        if HAS_NOTIFY:
            try:
                plyer_notify.notify(
                    title=title, message=message,
                    app_name="NEXORA Worker", timeout=5,
                )
            except Exception:
                pass

    # ── System tray ───────────────────────────────────────────────────────────
    def _make_tray_img(self, hex_color: str = C_RED) -> "Image.Image":
        img  = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        r, g, b = int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16)
        draw.ellipse([8, 8, 56, 56], fill=(r, g, b, 255))
        # Draw "N" letter
        draw.text((22, 18), "N", fill=(255, 255, 255, 220))
        return img

    def _setup_tray(self):
        menu = pystray.Menu(
            pystray.MenuItem("Open NEXORA Worker", self._tray_open, default=True),
            pystray.MenuItem("Disconnect",         lambda: self.after(0, self.stop_worker)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit",               self._tray_quit),
        )
        self._tray_icon = pystray.Icon(
            "NEXORA Worker", self._make_tray_img(C_RED), "NEXORA Worker", menu
        )
        threading.Thread(target=self._tray_icon.run_detached, daemon=True).start()

    def _tray_open(self):
        self.after(0, lambda: [self.deiconify(), self.lift()])

    def _tray_quit(self):
        self.after(0, self._quit_app)

    def _on_close(self):
        if HAS_TRAY and self._tray_icon:
            self.withdraw()
            self._notify("NEXORA Worker", "Running in background. Right-click tray icon to quit.")
        else:
            self._quit_app()

    def _quit_app(self):
        self.is_running = False
        if self._tray_icon:
            try:
                self._tray_icon.stop()
            except Exception:
                pass
        self.destroy()

    # ── Worker start / stop ───────────────────────────────────────────────────
    def start_worker(self):
        url      = self.entry_url.get().strip()
        username = self.entry_user.get().strip()
        password = self.entry_pass.get().strip()
        name     = self.entry_name.get().strip() or self.hardware["hostname"]
        code     = self.entry_code.get().strip() or None

        if not url or not username or not password:
            messagebox.showerror("Validation Error", "Master URL, Username, and Password are required.")
            return

        # Reset stats
        self._tasks_completed = self._tasks_failed = 0
        self._total_compute_sec = 0.0
        self._refresh_stats()

        self.is_running = True
        self.btn_connect.configure(state=tk.DISABLED)
        self.btn_disconnect.configure(state=tk.NORMAL)
        self.set_status("CONNECTING...", C_YELLOW)

        self.worker_thread = threading.Thread(
            target=self._worker_loop,
            args=(url, username, password, name, code),
            daemon=True,
        )
        self.worker_thread.start()

    def stop_worker(self):
        self.log("Stopping worker...")
        self.is_running = False
        self._clear_task()
        self.after(0, lambda: [
            self.btn_connect.configure(state=tk.NORMAL),
            self.btn_disconnect.configure(state=tk.DISABLED),
            self.set_status("OFFLINE", C_RED),
        ])

    # ── Worker loop ───────────────────────────────────────────────────────────
    def _worker_loop(
        self,
        master_url:   str,
        username:     str,
        password:     str,
        node_name:    str,
        pairing_code: Optional[str],
    ):
        MAX_RECONNECT   = 5
        RECONNECT_DELAY = 5

        session = requests.Session()
        session.headers.update({"Content-Type": "application/json"})

        # ── 1. Authenticate ────────────────────────────────────────────────
        self.log(f"Authenticating with master at {master_url}...")
        token: Optional[str] = None
        try:
            r = session.post(
                f"{master_url}/api/v1/auth/login",
                json={"username": username, "password": password},
                timeout=8,
            )
            if r.status_code == 200:
                token = r.json().get("access_token")
                self.log(f"Login successful for user '{username}'.")
            elif r.status_code == 401:
                self.log("Account not found — attempting auto-registration...")
                reg = session.post(
                    f"{master_url}/api/v1/auth/register",
                    json={
                        "full_name": node_name, "username": username,
                        "email": f"{username}@example.com", "password": password,
                        "roles": ["worker_operator"],
                    },
                    timeout=8,
                )
                if reg.status_code in (200, 201):
                    token = reg.json().get("access_token")
                    self.log("Auto-registration successful.")
                elif reg.status_code == 409:
                    self.log(f"Wrong password for '{username}'. Please check credentials.", "error")
                    self.after(0, self.stop_worker)
                    return
                else:
                    self.log(f"Registration failed: {reg.text}", "error")
                    self.after(0, self.stop_worker)
                    return
            else:
                self.log(f"Master error: HTTP {r.status_code}", "error")
                self.after(0, self.stop_worker)
                return
        except Exception as ex:
            self.log(f"Network error during auth: {ex}", "error")
            self.after(0, self.stop_worker)
            return

        session.headers.update({"Authorization": f"Bearer {token}"})

        # ── 2. Pairing request ─────────────────────────────────────────────
        self.log(f"Submitting join request for node '{node_name}'...")
        local_ip: Optional[str] = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.connect(("8.8.8.8", 80))
            local_ip = sock.getsockname()[0]
            sock.close()
        except Exception:
            try:
                local_ip = socket.gethostbyname(socket.gethostname())
            except Exception:
                pass

        gpu_bytes = int(self.hardware["gpu_vram_gb"] * 1024 ** 3) if self.hardware["gpu_vram_gb"] > 0 else None
        pair_payload = {
            "code":            pairing_code,
            "node_name":       node_name,
            "hostname":        self.hardware["hostname"],
            "operating_system":self.hardware["os"],
            "private_ipv4":    local_ip,
            "cpu_model":       self.hardware["cpu_model"],
            "cpu_threads":     self.hardware["cpu_threads"],
            "ram_total_bytes": int(self.hardware["ram_total_gb"] * 1024 ** 3),
            "gpu_model":       self.hardware["gpu_model"] if self.hardware["gpu_vram_gb"] > 0 else None,
            "gpu_vendor":      self.hardware["gpu_vendor"],
            "gpu_vram_bytes":  gpu_bytes,
            "compute_backend": "opencl" if self.hardware["gpu_vram_gb"] > 0 else "cpu",
        }
        try:
            r = session.post(f"{master_url}/api/v1/pairing/requests", json=pair_payload, timeout=8)
            if r.status_code in (200, 201):
                node_id = r.json()["id"]
                self.log(f"Join request created!  Node ID: {node_id}")
            else:
                self.log(f"Pairing failed: {r.text}", "error")
                self.after(0, self.stop_worker)
                return
        except Exception as ex:
            self.log(f"Pairing error: {ex}", "error")
            self.after(0, self.stop_worker)
            return

        # ── 3. Wait for admin approval ─────────────────────────────────────
        self.set_status("AWAITING APPROVAL", C_YELLOW)
        self.log("⏳ Waiting for Master Admin to approve in Dashboard...")

        approved = False
        while self.is_running and not approved:
            try:
                hb = session.post(
                    f"{master_url}/api/v1/worker/nodes/{node_id}/heartbeat",
                    json={
                        "status": "online",
                        "cpu_utilization": psutil.cpu_percent(),
                        "ram_utilization": psutil.virtual_memory().percent,
                    },
                    timeout=5,
                )
                if hb.status_code == 200:
                    approved = True
                    self.log("🎉 Node APPROVED by Admin! Connected to Cluster.", "success")
                    self.set_status("ONLINE & READY", C_GREEN)
                    self._notify("NEXORA Worker", "🎉 Node approved — connected to cluster!")
                elif hb.status_code == 403:
                    time.sleep(2.5)
                else:
                    time.sleep(3.0)
            except Exception as ex:
                self.log(f"Waiting for approval… ({ex})", "warning")
                time.sleep(3.0)

        # ── 4. Active loop — heartbeat + task lease ────────────────────────
        HB_INTERVAL      = 2.0
        last_hb          = time.time()
        reconnect_count  = 0

        while self.is_running:
            now = time.time()

            # Heartbeat
            if now - last_hb >= HB_INTERVAL:
                try:
                    session.post(
                        f"{master_url}/api/v1/worker/nodes/{node_id}/heartbeat",
                        json={
                            "status": "online",
                            "cpu_utilization": psutil.cpu_percent(),
                            "ram_utilization": psutil.virtual_memory().percent,
                        },
                        timeout=5,
                    )
                    last_hb = now
                    reconnect_count = 0        # Reset on success

                except requests.exceptions.ConnectionError:
                    reconnect_count += 1
                    if reconnect_count <= MAX_RECONNECT:
                        rc = reconnect_count
                        self.log(
                            f"⚠️ Connection lost — retrying in {RECONNECT_DELAY}s "
                            f"({rc}/{MAX_RECONNECT})...",
                            "warning",
                        )
                        self.set_status(f"RECONNECTING {rc}/{MAX_RECONNECT}", C_YELLOW)
                        time.sleep(RECONNECT_DELAY)
                        continue
                    else:
                        self.log("❌ Max reconnect attempts reached. Disconnecting.", "error")
                        self._notify("NEXORA Worker", "⚠️ Lost connection — disconnected.")
                        self.after(0, self.stop_worker)
                        return

                except Exception:
                    pass

            # Lease task
            try:
                lr = session.post(
                    f"{master_url}/api/v1/worker/nodes/{node_id}/tasks/lease",
                    timeout=6,
                )
                if lr.status_code == 200 and lr.json():
                    ld          = lr.json()
                    lease_token = ld["lease_token"]
                    task        = ld["task"]
                    task_id     = task["id"]
                    task_key    = task["task_key"]

                    self.log(f"⚡ Leased task '{task_key}' — executing...", "task")
                    self.set_status("COMPUTING", C_PURPLE)
                    self._set_task(task_key)

                    t0 = time.time()
                    try:
                        # Download workload if needed
                        workload_zip: Optional[Path] = None
                        wid = task.get("workload_id")
                        if wid:
                            cache = Path(tempfile.gettempdir()) / "nexora_worker_workloads"
                            cache.mkdir(parents=True, exist_ok=True)
                            cached = cache / f"{wid}.zip"
                            if not cached.is_file():
                                self.log("📥 Downloading workload package...")
                                dl = session.get(
                                    f"{master_url}/api/v1/worker/workloads/{wid}/download",
                                    timeout=30,
                                )
                                if dl.status_code == 200:
                                    cached.write_bytes(dl.content)
                            if cached.is_file():
                                workload_zip = cached

                        # Execute
                        if run_task_engine:
                            res, artifact_zip = run_task_engine(
                                task["payload"], workload_archive=workload_zip
                            )
                        else:
                            res, artifact_zip = run_task(task["payload"]), None

                        dur = round(time.time() - t0, 3)

                        # Upload artifacts if any
                        if artifact_zip and os.path.isfile(artifact_zip):
                            self.log("📦 Uploading output artifacts...")
                            with open(artifact_zip, "rb") as fh:
                                session.post(
                                    f"{master_url}/api/v1/worker/tasks/{task_id}/artifacts",
                                    files={"file": (f"{task_key}.zip", fh, "application/zip")},
                                    timeout=60,
                                )

                        # Submit result
                        sub = session.post(
                            f"{master_url}/api/v1/worker/tasks/{task_id}/result",
                            json={"lease_token": lease_token, "status": "succeeded", "result": res},
                            timeout=6,
                        )
                        if sub.status_code == 200:
                            self._tasks_completed += 1
                            self._total_compute_sec += dur
                            self._refresh_stats()
                            self.log(f"✅ '{task_key}' done in {dur}s", "success")
                            self._notify("NEXORA Worker", f"✅ Task '{task_key}' done in {dur}s")
                        else:
                            self.log(f"Result submission error: {sub.text}", "error")

                    except Exception as exc:
                        self._tasks_failed += 1
                        self._refresh_stats()
                        err = str(exc)
                        self.log(f"❌ '{task_key}' failed: {err}", "error")
                        self._notify("NEXORA Worker", f"❌ Task '{task_key}' failed")
                        try:
                            session.post(
                                f"{master_url}/api/v1/worker/tasks/{task_id}/result",
                                json={"lease_token": lease_token, "status": "failed",
                                      "error": err[:4000]},
                                timeout=6,
                            )
                        except Exception:
                            pass

                    self.set_status("ONLINE & READY", C_GREEN)
                    self._clear_task()

            except Exception:
                pass

            time.sleep(1.0)

    # ── Helpers ───────────────────────────────────────────────────────────────
    @staticmethod
    def _add_padding(frame: tk.Frame, px: int = 12, py: int = 12):
        frame.config(padx=px, pady=py)


# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    app = NexoraWorkerApp()
    app.mainloop()


if __name__ == "__main__":
    main()
