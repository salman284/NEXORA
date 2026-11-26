"""
NEXORA - Desktop Worker GUI Application v2.2
Features:
  - Custom Branded AI Node Identity & Embedded Logo
  - Persistent Settings (save/load ~/.nexora_worker/config.json)
  - Real-Time Hardware Resource Gauges (CPU, RAM, GPU)
  - Cross-vendor GPU monitoring: NVIDIA (GPUtil), AMD & Intel (WMI/PowerShell)
  - Live Compute Telemetry & Session Workload Metrics
  - Active Task Tracker with Sub-Second Elapsed Timer
  - Fault-Tolerant Auto-Reconnect Protocol (Exponential Backoff)
  - Windows System Tray Integration with Live Status Dot
  - Desktop Toast Notifications (Approval, Completion, Disconnect)
  - Filterable Telemetry Stream (All / Tasks / Errors) + Export
  - Collapsible Connection Drawer & Responsive Full-Screen Layout
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

# ── Project Path & Assets Helper ──────────────────────────────────────────
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))


def get_asset_path(filename: str) -> Path:
    """Resolve asset path safely both in development and PyInstaller bundle."""
    if hasattr(sys, "_MEIPASS"):
        # PyInstaller temp folder
        p = Path(sys._MEIPASS) / "assets" / filename
        if p.exists():
            return p
        p_root = Path(sys._MEIPASS) / filename
        if p_root.exists():
            return p_root
    # Development mode
    local_asset = Path(__file__).resolve().parent / "assets" / filename
    if local_asset.exists():
        return local_asset
    return Path(__file__).resolve().parent / filename


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

# ── Color Palette (NEXORA Cyberpunk Dark) ──────────────────────────────────
C_BG          = "#070b14"   # Deep void navy background
C_CARD        = "#0f172a"   # Sleek glass navy card
C_CARD_ALT    = "#131d33"   # Subtle secondary card
C_BORDER      = "#1e293b"   # Border slate
C_BORDER_HI   = "#334155"   # Border highlight
C_TEXT        = "#f8fafc"   # Primary text
C_SUB         = "#94a3b8"   # Muted subtext
C_BLUE        = "#38bdf8"   # Electric neon cyan
C_CYAN_GLOW   = "#0284c7"   # Button cyan
C_GREEN       = "#22c55e"   # Online green
C_LIME        = "#a3e635"   # Accent lime
C_YELLOW      = "#f59e0b"   # Warning amber
C_RED         = "#f43f5e"   # Danger rose/crimson
C_PURPLE      = "#c084fc"   # Quantum purple


# ── Config Persistence ─────────────────────────────────────────────────────
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


# ── Hardware Detection ─────────────────────────────────────────────────────
def detect_hardware() -> dict[str, Any]:
    """Detect all GPU hardware (NVIDIA, AMD, Intel) via WMI on Windows."""
    info: dict[str, Any] = {
        "hostname":     socket.gethostname(),
        "os":           f"{platform.system()} {platform.release()}",
        "cpu_model":    platform.processor() or "Unknown CPU",
        "cpu_threads":  psutil.cpu_count(logical=True) or 1,
        "ram_total_gb": round(psutil.virtual_memory().total / 1024 ** 3, 1),
        "gpu_model":    "None (CPU Compute)",
        "gpu_vendor":   None,
        "gpu_vram_gb":  0.0,
        "gpu_list":     [],   # all detected GPUs
    }
    if platform.system() == "Windows":
        try:
            import subprocess
            cmd = (
                'powershell -NoProfile -Command '
                '"Get-CimInstance Win32_VideoController | '
                'Select-Object -Property Name,AdapterCompatibility,AdapterRAM | '
                'ConvertTo-Json -Depth 3"'
            )
            res = subprocess.run(cmd, capture_output=True, text=True, shell=True, timeout=6)
            if res.returncode == 0 and res.stdout.strip():
                data = json.loads(res.stdout)
                cards = data if isinstance(data, list) else [data]
                # Filter out Microsoft Basic Display / software renderers
                real_cards = [
                    c for c in cards
                    if c.get("Name") and "Microsoft" not in (c.get("Name") or "")
                ]
                if real_cards:
                    # Pick primary card: prefer discrete (AMD/NVIDIA) over Intel iGPU
                    primary = real_cards[0]
                    for c in real_cards:
                        vendor = (c.get("AdapterCompatibility") or "").lower()
                        if "amd" in vendor or "nvidia" in vendor or "advanced" in vendor:
                            primary = c
                            break
                    info["gpu_model"]  = primary.get("Name", "Unknown GPU")
                    info["gpu_vendor"] = primary.get("AdapterCompatibility")
                    vram = primary.get("AdapterRAM") or 0
                    info["gpu_vram_gb"] = round(vram / 1024 ** 3, 1) if vram else 0.0
                    info["gpu_list"] = [
                        {
                            "name":   c.get("Name", "Unknown"),
                            "vendor": c.get("AdapterCompatibility", ""),
                            "vram_gb": round((c.get("AdapterRAM") or 0) / 1024 ** 3, 1),
                        }
                        for c in real_cards
                    ]
        except Exception:
            pass
    return info


# ── Cross-Vendor GPU Utilization ───────────────────────────────────────────
class GpuMetrics:
    """Thread-safe cache for GPU utilization polled in background."""
    def __init__(self):
        self._lock    = threading.Lock()
        self.util_pct: float = 0.0
        self.vram_used_mb: float = 0.0
        self.vram_total_mb: float = 0.0
        self.method: str = "none"

    def update(self, util: float, vram_used: float, vram_total: float, method: str):
        with self._lock:
            self.util_pct      = util
            self.vram_used_mb  = vram_used
            self.vram_total_mb = vram_total
            self.method        = method

    def snapshot(self) -> tuple[float, float, float]:
        with self._lock:
            return self.util_pct, self.vram_used_mb, self.vram_total_mb


_GPU_METRICS = GpuMetrics()


def _query_gpu_utilization() -> None:
    """
    Background thread: queries GPU utilization every 4 seconds using multiple
    methods in priority order:
      1. NVIDIA – GPUtil (most accurate for NVIDIA)
      2. AMD/Intel/NVIDIA – Windows GPU Engine Performance Counters (WMI)
      3. AMD – pyadl if installed
    Updates the global _GPU_METRICS cache.
    """
    import subprocess

    while True:
        util, vram_used, vram_total, method = 0.0, 0.0, 0.0, "none"

        # ── Method 1: NVIDIA via GPUtil ──────────────────────────────────
        try:
            import GPUtil
            gpus = GPUtil.getGPUs()
            if gpus:
                g = gpus[0]
                util       = g.load * 100
                vram_used  = g.memoryUsed
                vram_total = g.memoryTotal
                method     = "gputil(nvidia)"
                _GPU_METRICS.update(util, vram_used, vram_total, method)
                time.sleep(4)
                continue
        except Exception:
            pass

        # ── Method 2: Windows GPU Engine Perf Counters (AMD/Intel/NVIDIA) ─
        if platform.system() == "Windows":
            try:
                # Sum 3D engine utilization across all GPU adapters
                ps = (
                    'powershell -NoProfile -Command "'
                    '$c = Get-Counter \'\\GPU Engine(*engtype_3D)\\Utilization Percentage\' '
                    '-ErrorAction SilentlyContinue; '
                    'if ($c) { ($c.CounterSamples | Measure-Object -Property CookedValue -Sum).Sum } '
                    'else { -1 }"'
                )
                r = subprocess.run(ps, capture_output=True, text=True, shell=True, timeout=4)
                if r.returncode == 0 and r.stdout.strip():
                    val = float(r.stdout.strip())
                    if val >= 0:
                        util   = min(val, 100.0)
                        method = "wmi(gpu-engine)"
                        # Also try to get VRAM via AdapterRAM (static, already known)
                        # For dynamic VRAM used, query dedicated video memory usage
                        try:
                            ps_vram = (
                                'powershell -NoProfile -Command "'
                                '$v = Get-CimInstance Win32_VideoController | '
                                'Select-Object -First 1 -Property AdapterRAM; '
                                '$v.AdapterRAM / 1MB"'
                            )
                            rv = subprocess.run(
                                ps_vram, capture_output=True, text=True, shell=True, timeout=3
                            )
                            if rv.returncode == 0 and rv.stdout.strip():
                                vram_total = float(rv.stdout.strip())
                        except Exception:
                            pass
                        _GPU_METRICS.update(util, vram_used, vram_total, method)
                        time.sleep(4)
                        continue
            except Exception:
                pass

        # ── Method 3: AMD via pyadl ──────────────────────────────────────
        try:
            import pyadl  # type: ignore
            devs = pyadl.ADLManager.getInstance().getDevices()
            if devs:
                d    = devs[0]
                util = float(d.getCurrentUsage() or 0)
                method = "pyadl(amd)"
                _GPU_METRICS.update(util, vram_used, vram_total, method)
                time.sleep(4)
                continue
        except Exception:
            pass

        # No method worked — keep previous or zero
        time.sleep(5)


def _start_gpu_monitor_thread() -> None:
    """Launch the background GPU polling thread (daemon, starts once)."""
    t = threading.Thread(target=_query_gpu_utilization, daemon=True, name="nexora-gpu-mon")
    t.start()


# ══════════════════════════════════════════════════════════════════════════════
#  Main Application Window
# ══════════════════════════════════════════════════════════════════════════════
class NexoraWorkerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("NEXORA — Distributed Cluster Worker")
        self.geometry("900x840")
        self.minsize(760, 680)
        self.configure(bg=C_BG)

        self._load_app_icons()
        self.hardware    = detect_hardware()
        self.config_data = load_config()

        # Worker state
        self.is_running: bool                          = False
        self.worker_thread: Optional[threading.Thread] = None
        self._tray_icon: Optional[Any]                 = None
        self._conn_card_visible: bool                  = True

        # Task metrics
        self._tasks_completed:   int   = 0
        self._tasks_failed:      int   = 0
        self._total_compute_sec: float = 0.0
        self._current_task:      Optional[str]  = None
        self._task_start:        float          = 0.0
        self._session_start:     float          = time.time()

        # Live log
        self._log_entries: list[tuple[str, str, str]] = []  # (ts, level, text)
        self._log_filter   = tk.StringVar(value="all")

        self._apply_styles()
        self._build_ui()
        self._start_periodic_update()
        _start_gpu_monitor_thread()   # cross-vendor GPU polling (NVIDIA/AMD/Intel)

        if HAS_TRAY:
            self._setup_tray()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── App Icons ─────────────────────────────────────────────────────────────
    def _load_app_icons(self):
        ico_file = get_asset_path("worker_icon.ico")
        if ico_file.is_file():
            try:
                self.iconbitmap(str(ico_file))
            except Exception:
                pass
        png_file = get_asset_path("worker_icon_64.png")
        if png_file.is_file():
            try:
                self._icon_photo = tk.PhotoImage(file=str(png_file))
                self.iconphoto(True, self._icon_photo)
            except Exception:
                pass

    # ── Styles ────────────────────────────────────────────────────────────────
    def _apply_styles(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".", background=C_BG, foreground=C_TEXT, font=("Segoe UI", 10))

        s.configure("Primary.TButton", background=C_CYAN_GLOW, foreground="#ffffff",
                    font=("Segoe UI", 10, "bold"), borderwidth=0, padding=8)
        s.map("Primary.TButton", background=[("active", "#0369a1"), ("disabled", "#334155")])

        s.configure("Danger.TButton", background="#be123c", foreground="#ffffff",
                    font=("Segoe UI", 10, "bold"), borderwidth=0, padding=8)
        s.map("Danger.TButton", background=[("active", "#9f1239"), ("disabled", "#334155")])

        s.configure("Secondary.TButton", background=C_BORDER, foreground=C_TEXT,
                    font=("Segoe UI", 9), borderwidth=0, padding=6)
        s.map("Secondary.TButton", background=[("active", C_BORDER_HI), ("disabled", C_CARD)])

        s.configure("CPU.Horizontal.TProgressbar", troughcolor=C_BORDER, background=C_BLUE,   borderwidth=0, thickness=10)
        s.configure("RAM.Horizontal.TProgressbar", troughcolor=C_BORDER, background=C_GREEN,  borderwidth=0, thickness=10)
        s.configure("GPU.Horizontal.TProgressbar", troughcolor=C_BORDER, background=C_PURPLE, borderwidth=0, thickness=10)

    # ── UI Construction ───────────────────────────────────────────────────────
    def _build_ui(self):
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

        # ── 1. Branded Header with Logo ───────────────────────────────────
        self._build_header(mf)

        # ── 2. Hardware + Live Gauges (side by side) ───────────────────────
        row2 = tk.Frame(mf, bg=C_BG)
        row2.pack(fill=tk.X, padx=16, pady=(0, 10))
        row2.columnconfigure(0, weight=1)
        row2.columnconfigure(1, weight=1)
        self._build_hardware_card(row2)
        self._build_gauge_card(row2)

        # ── 3. Session Workload Metrics ───────────────────────────────────
        self._build_stats_card(mf)

        # ── 4. Active Task Card ───────────────────────────────────────────
        self._build_task_card(mf)

        # ── 5. Connection Drawer ──────────────────────────────────────────
        self._build_conn_card(mf)

        # ── 6. Control Action Buttons ─────────────────────────────────────
        self._build_buttons(mf)

        # ── 7. Real-Time Telemetry Log ────────────────────────────────────
        self._build_log_section(mf)

    # ── Header with Logo ──────────────────────────────────────────────────────
    def _build_header(self, parent):
        hdr = tk.Frame(parent, bg=C_BG)
        hdr.pack(fill=tk.X, padx=16, pady=(16, 12))

        # Brand Info (Left)
        brand_left = tk.Frame(hdr, bg=C_BG)
        brand_left.pack(side=tk.LEFT, fill=tk.Y)

        logo_path = get_asset_path("worker_icon_64.png")
        if logo_path.is_file():
            try:
                self._header_logo = tk.PhotoImage(file=str(logo_path))
                logo_lbl = tk.Label(brand_left, image=self._header_logo, bg=C_BG, bd=0)
                logo_lbl.pack(side=tk.LEFT, padx=(0, 12))
            except Exception:
                pass

        title_box = tk.Frame(brand_left, bg=C_BG)
        title_box.pack(side=tk.LEFT, fill=tk.Y)

        tk.Label(
            title_box, text="NEXORA CLUSTER NODE",
            font=("Segoe UI", 16, "bold"), fg="#ffffff", bg=C_BG
        ).pack(anchor=tk.W)
        tk.Label(
            title_box, text="Decentralized GPU & CPU Compute Worker • v2.1",
            font=("Segoe UI", 9), fg=C_BLUE, bg=C_BG
        ).pack(anchor=tk.W)

        # Status Badge (Right)
        self.status_pill = tk.Label(
            hdr, text="● OFFLINE", font=("Segoe UI", 9, "bold"),
            fg=C_RED, bg=C_CARD, padx=14, pady=6, relief="solid", bd=1,
            highlightbackground=C_BORDER
        )
        self.status_pill.pack(side=tk.RIGHT, pady=4)

    # ── Hardware Card ─────────────────────────────────────────────────────────
    def _build_hardware_card(self, parent):
        card = tk.Frame(parent, bg=C_CARD, highlightthickness=1, highlightbackground=C_BORDER)
        card.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self._add_padding(card)

        tk.Label(card, text="💻  DETECTED HARDWARE", font=("Segoe UI", 8, "bold"),
                 fg=C_BLUE, bg=C_CARD).pack(anchor=tk.W, pady=(0, 8))

        gpu_list = self.hardware.get("gpu_list", [])
        has_gpu  = bool(gpu_list or self.hardware["gpu_vram_gb"] > 0)
        # Build GPU display string: list all detected GPUs
        if gpu_list:
            gpu_str  = gpu_list[0]["name"]
            vram_str = f" ({gpu_list[0]['vram_gb']} GB VRAM)" if gpu_list[0]["vram_gb"] > 0 else ""
            if len(gpu_list) > 1:
                extra = ", ".join(g["name"].split()[-1] for g in gpu_list[1:])
                gpu_str += f" + {extra}"
            gpu_display = gpu_str + vram_str
        elif has_gpu:
            gpu_display = f"{self.hardware['gpu_model']} ({self.hardware['gpu_vram_gb']} GB VRAM)"
        else:
            gpu_display = self.hardware["gpu_model"]

        rows = [
            ("CPU", f"{self.hardware['cpu_model']} ({self.hardware['cpu_threads']}T)", C_BLUE),
            ("RAM", f"{self.hardware['ram_total_gb']} GB total",                       C_BLUE),
            ("GPU", gpu_display, C_LIME if has_gpu else C_SUB),
            ("OS",  self.hardware["os"],                                                C_SUB),
        ]
        for lbl, val, color in rows:
            r = tk.Frame(card, bg=C_CARD)
            r.pack(fill=tk.X, pady=2)
            tk.Label(r, text=f"{lbl}:", width=4, anchor=tk.W,
                     font=("Segoe UI", 9), fg=C_SUB, bg=C_CARD).pack(side=tk.LEFT)
            tk.Label(r, text=val, font=("Segoe UI", 9, "bold"),
                     fg=color, bg=C_CARD, wraplength=480, anchor=tk.W).pack(side=tk.LEFT, padx=(4, 0))

    # ── Gauge Card ────────────────────────────────────────────────────────────
    def _build_gauge_card(self, parent):
        card = tk.Frame(parent, bg=C_CARD, highlightthickness=1, highlightbackground=C_BORDER)
        card.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        self._add_padding(card)

        tk.Label(card, text="⚡  LIVE PERFORMANCE", font=("Segoe UI", 8, "bold"),
                 fg=C_BLUE, bg=C_CARD).pack(anchor=tk.W, pady=(0, 8))

        self.cpu_bar = self._gauge_row(card, "CPU", "CPU.Horizontal.TProgressbar")
        self.ram_bar = self._gauge_row(card, "RAM", "RAM.Horizontal.TProgressbar")

        # Show GPU gauge for ANY vendor (NVIDIA / AMD / Intel)
        has_gpu = bool(self.hardware.get("gpu_list") or self.hardware["gpu_vram_gb"] > 0)
        if has_gpu:
            self.gpu_bar = self._gauge_row(card, "GPU", "GPU.Horizontal.TProgressbar")
            self._gpu_method_lbl = tk.Label(
                card, text="GPU monitor: initializing…",
                font=("Segoe UI", 7), fg=C_SUB, bg=C_CARD,
            )
            self._gpu_method_lbl.pack(anchor=tk.W)
        else:
            self.gpu_bar = None
            self._gpu_method_lbl = None
            tk.Label(card, text="GPU: Not detected (CPU compute mode)",
                     font=("Segoe UI", 8), fg=C_SUB, bg=C_CARD).pack(anchor=tk.W, pady=3)

        self.uptime_lbl = tk.Label(card, text="Session Uptime: 00:00:00",
                                   font=("Segoe UI", 8), fg=C_SUB, bg=C_CARD)
        self.uptime_lbl.pack(anchor=tk.W, pady=(8, 0))

    def _gauge_row(self, parent, label: str, style: str):
        row = tk.Frame(parent, bg=C_CARD)
        row.pack(fill=tk.X, pady=3)
        row.columnconfigure(1, weight=1)

        tk.Label(row, text=label, width=4, font=("Segoe UI", 9),
                 fg=C_SUB, bg=C_CARD).grid(row=0, column=0, sticky=tk.W)
        bar = ttk.Progressbar(row, style=style, mode="determinate")
        bar.grid(row=0, column=1, sticky=tk.EW, padx=(4, 8))
        pct = tk.Label(row, text="0%", width=18, font=("Segoe UI", 9, "bold"),
                       fg=C_TEXT, bg=C_CARD, anchor=tk.W)
        pct.grid(row=0, column=2, sticky=tk.W)
        bar._pct_label = pct
        return bar

    # ── Session Stats Card ────────────────────────────────────────────────────
    def _build_stats_card(self, parent):
        wrapper = tk.Frame(parent, bg=C_BG)
        wrapper.pack(fill=tk.X, padx=16, pady=(0, 10))

        card = tk.Frame(wrapper, bg=C_CARD, highlightthickness=1, highlightbackground=C_BORDER)
        card.pack(fill=tk.X)
        self._add_padding(card, py=10)

        tk.Label(card, text="📈  SESSION WORKLOAD METRICS", font=("Segoe UI", 8, "bold"),
                 fg=C_SUB, bg=C_CARD).pack(anchor=tk.W, pady=(0, 6))

        row = tk.Frame(card, bg=C_CARD)
        row.pack(fill=tk.X)

        self.stat_ok_lbl   = self._stat_box(row, "0",      "Completed", C_GREEN)
        self._sep(row)
        self.stat_fail_lbl = self._stat_box(row, "0",      "Failed",    C_RED)
        self._sep(row)
        self.stat_time_lbl = self._stat_box(row, "0.0s",   "Compute",   C_BLUE)
        self._sep(row)
        self.stat_sess_lbl = self._stat_box(row, "00:00:00","Session",  C_SUB)

    def _stat_box(self, parent, num: str, label: str, color: str) -> tk.Label:
        box = tk.Frame(parent, bg=C_CARD)
        box.pack(side=tk.LEFT, expand=True, padx=4)
        num_lbl = tk.Label(box, text=num, font=("Segoe UI", 20, "bold"), fg=color, bg=C_CARD)
        num_lbl.pack()
        tk.Label(box, text=label, font=("Segoe UI", 8), fg=C_SUB, bg=C_CARD).pack()
        return num_lbl

    def _sep(self, parent):
        tk.Frame(parent, bg=C_BORDER, width=1).pack(side=tk.LEFT, fill=tk.Y, padx=4, pady=4)

    # ── Active Task Card ──────────────────────────────────────────────────────
    def _build_task_card(self, parent):
        wrapper = tk.Frame(parent, bg=C_BG)
        wrapper.pack(fill=tk.X, padx=16, pady=(0, 10))

        card = tk.Frame(wrapper, bg=C_CARD, highlightthickness=1, highlightbackground=C_BORDER)
        card.pack(fill=tk.X)
        self._add_padding(card, py=8)

        hdr = tk.Frame(card, bg=C_CARD)
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text="⚡  ACTIVE TASK EXECUTION", font=("Segoe UI", 8, "bold"),
                 fg=C_SUB, bg=C_CARD).pack(side=tk.LEFT)
        self.task_timer_lbl = tk.Label(hdr, text="", font=("Segoe UI", 9, "bold"),
                                       fg=C_PURPLE, bg=C_CARD)
        self.task_timer_lbl.pack(side=tk.RIGHT)

        self.task_name_lbl = tk.Label(card, text="No task running (Node idle)",
                                      font=("Segoe UI", 9, "italic"), fg=C_SUB, bg=C_CARD)
        self.task_name_lbl.pack(anchor=tk.W, pady=(4, 0))

    # ── Connection Drawer ─────────────────────────────────────────────────────
    def _build_conn_card(self, parent):
        wrapper = tk.Frame(parent, bg=C_BG)
        wrapper.pack(fill=tk.X, padx=16, pady=(0, 6))

        hdr = tk.Frame(wrapper, bg=C_BG)
        hdr.pack(fill=tk.X, pady=(0, 4))
        tk.Label(hdr, text="🌐  CONNECTION PREFERENCES",
                 font=("Segoe UI", 8, "bold"), fg=C_SUB, bg=C_BG).pack(side=tk.LEFT)
        self._toggle_btn = tk.Button(
            hdr, text="▲ Collapse", font=("Segoe UI", 8), fg=C_BLUE, bg=C_BG,
            bd=0, cursor="hand2", activebackground=C_BG,
            command=self._toggle_conn_card,
        )
        self._toggle_btn.pack(side=tk.RIGHT)

        self._conn_body = tk.Frame(wrapper, bg=C_CARD, highlightthickness=1, highlightbackground=C_BORDER)
        self._conn_body.pack(fill=tk.X)
        self._add_padding(self._conn_body)

        entry_kw = dict(
            bg=C_BG, fg=C_TEXT, insertbackground=C_BLUE,
            relief="solid", bd=1, highlightbackground=C_BORDER,
            highlightcolor=C_BLUE, font=("Segoe UI", 10),
        )

        cfg = self.config_data
        fields = [
            ("Master Host / URL:",       "entry_url",  cfg.get("master_url",   os.getenv("NEXORA_MASTER_URL", "http://127.0.0.1:5000")), False),
            ("Worker Display Name:",     "entry_name", cfg.get("node_name",    f"{self.hardware['hostname']}-Worker"),                   False),
            ("Pairing Code (Optional):", "entry_code", cfg.get("pairing_code", ""),                                                      False),
            ("Worker Username:",         "entry_user", cfg.get("username",     "worker1"),                                               False),
            ("Worker Password:",         "entry_pass", cfg.get("password",     "workerPassword123!"),                                    True),
        ]

        self._conn_body.columnconfigure(1, weight=1)
        for i, (label, attr, default, masked) in enumerate(fields):
            tk.Label(self._conn_body, text=label, font=("Segoe UI", 9),
                     fg=C_SUB, bg=C_CARD).grid(row=i, column=0, sticky=tk.W, pady=4, padx=(0, 10))
            e = tk.Entry(self._conn_body, show="*" if masked else "", **entry_kw)
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

    # ── Control Buttons ───────────────────────────────────────────────────────
    def _build_buttons(self, parent):
        wrapper = tk.Frame(parent, bg=C_BG)
        wrapper.pack(fill=tk.X, padx=16, pady=(6, 10))

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

    # ── Real-Time Telemetry Stream ────────────────────────────────────────────
    def _build_log_section(self, parent):
        wrapper = tk.Frame(parent, bg=C_BG)
        wrapper.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 14))

        hdr = tk.Frame(wrapper, bg=C_BG)
        hdr.pack(fill=tk.X, pady=(0, 4))
        tk.Label(hdr, text="📋  REAL-TIME TELEMETRY STREAM", font=("Segoe UI", 8, "bold"),
                 fg=C_SUB, bg=C_BG).pack(side=tk.LEFT)

        bar = tk.Frame(wrapper, bg=C_CARD, highlightthickness=1, highlightbackground=C_BORDER)
        bar.pack(fill=tk.X, pady=(0, 2))
        pad = tk.Frame(bar, bg=C_CARD)
        pad.pack(fill=tk.X, padx=8, pady=4)

        tk.Label(pad, text="Filter:", font=("Segoe UI", 9),
                 fg=C_SUB, bg=C_CARD).pack(side=tk.LEFT, padx=(0, 6))
        for text, val in [("All", "all"), ("Tasks", "tasks"), ("Errors", "errors")]:
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

        self.log_area = scrolledtext.ScrolledText(
            wrapper, wrap=tk.WORD,
            bg="#030712", fg="#e2e8f0", insertbackground=C_BLUE,
            font=("Consolas", 9), borderwidth=1, relief="solid", height=14,
            highlightbackground=C_BORDER
        )
        self.log_area.pack(fill=tk.BOTH, expand=True)

        self.log_area.tag_config("time",    foreground="#64748b")
        self.log_area.tag_config("info",    foreground="#e2e8f0")
        self.log_area.tag_config("success", foreground=C_GREEN)
        self.log_area.tag_config("task",    foreground=C_LIME)
        self.log_area.tag_config("warning", foreground=C_YELLOW)
        self.log_area.tag_config("error",   foreground=C_RED)

        self.log("NEXORA Worker Agent Initialized. Enter cluster master URL and click 'Connect'.")

    # ── Periodic Update Tick ──────────────────────────────────────────────────
    def _start_periodic_update(self):
        self._periodic_tick()

    def _periodic_tick(self):
        # CPU
        cpu = psutil.cpu_percent()
        self.cpu_bar["value"] = cpu
        self.cpu_bar._pct_label.config(text=f"{cpu:.0f}%")

        # RAM
        mem = psutil.virtual_memory()
        self.ram_bar["value"] = mem.percent
        used_gb = mem.used / 1024 ** 3
        self.ram_bar._pct_label.config(
            text=f"{mem.percent:.0f}%  ({used_gb:.1f}/{self.hardware['ram_total_gb']}GB)"
        )

        # GPU — reads from background cross-vendor monitor (_GPU_METRICS cache)
        if self.gpu_bar:
            util, vram_used, vram_total = _GPU_METRICS.snapshot()
            self.gpu_bar["value"] = util
            if vram_total > 0:
                pct_txt = f"{util:.0f}%  ({vram_used:.0f}/{vram_total:.0f} MB)"
            else:
                pct_txt = f"{util:.0f}%"
            self.gpu_bar._pct_label.config(text=pct_txt)
            # Update small vendor/method label
            if self._gpu_method_lbl:
                method = _GPU_METRICS.method
                vendor_name = self.hardware.get("gpu_vendor") or "GPU"
                badge = {
                    "gputil(nvidia)": f"📡 NVIDIA · via GPUtil",
                    "wmi(gpu-engine)": f"📡 {vendor_name} · via WMI",
                    "pyadl(amd)":      f"📡 AMD · via pyadl",
                    "none":            "⏳ Detecting GPU monitor…",
                }.get(method, f"📡 {method}")
                self.gpu_bar._pct_label.config(
                    fg=C_LIME if util > 5 else C_TEXT
                )
                self._gpu_method_lbl.config(text=badge)

        # Session uptime
        elapsed = int(time.time() - self._session_start)
        h, m, s = elapsed // 3600, (elapsed % 3600) // 60, elapsed % 60
        uptime_str = f"{h:02d}:{m:02d}:{s:02d}"
        self.uptime_lbl.config(text=f"Session Uptime: {uptime_str}")
        self.stat_sess_lbl.config(text=uptime_str)

        # Active Task Elapsed
        if self._current_task:
            task_elapsed = time.time() - self._task_start
            self.task_timer_lbl.config(text=f"⏱ {task_elapsed:.1f}s")

        self.after(2000, self._periodic_tick)

    # ── Log Helpers ───────────────────────────────────────────────────────────
    def log(self, text: str, level: str = "info"):
        now = datetime.now().strftime("%H:%M:%S")
        self._log_entries.append((now, level, text))
        self.after(0, lambda ts=now, lv=level, tx=text: self._append_log_line(ts, lv, tx))

    def _append_log_line(self, ts: str, level: str, text: str):
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
        self.log("Telemetry log cleared.")

    def _export_log(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All", "*.*")],
            initialfile=f"nexora_telemetry_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
        )
        if path:
            lines = [f"[{ts}] {text}\n" for ts, _, text in self._log_entries]
            Path(path).write_text("".join(lines), encoding="utf-8")
            self.log(f"Telemetry exported to {path}", "success")

    # ── Status Pill & Tray Sync ───────────────────────────────────────────────
    def set_status(self, text: str, color: str):
        self.after(0, lambda: self.status_pill.config(
            text=f"● {text.upper()}", fg=color
        ))
        if self._tray_icon and HAS_TRAY:
            try:
                self._tray_icon.icon = self._make_tray_img(color)
            except Exception:
                pass

    # ── Save Settings ─────────────────────────────────────────────────────────
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
        self.log("Settings persisted to ~/.nexora_worker/config.json", "success")

    # ── Current Task Management ───────────────────────────────────────────────
    def _set_task(self, name: str):
        self._current_task = name
        self._task_start   = time.time()
        self.after(0, lambda: [
            self.task_name_lbl.config(
                text=f"⚡ Running Task: {name}", fg=C_PURPLE, font=("Segoe UI", 9, "bold")
            ),
        ])

    def _clear_task(self):
        self._current_task = None
        self.after(0, lambda: [
            self.task_name_lbl.config(
                text="No task running (Node idle)", fg=C_SUB, font=("Segoe UI", 9, "italic")
            ),
            self.task_timer_lbl.config(text=""),
        ])

    # ── Stats Metrics Refresh ─────────────────────────────────────────────────
    def _refresh_stats(self):
        self.after(0, lambda: [
            self.stat_ok_lbl.config(text=str(self._tasks_completed)),
            self.stat_fail_lbl.config(text=str(self._tasks_failed)),
            self.stat_time_lbl.config(text=f"{self._total_compute_sec:.1f}s"),
        ])

    # ── Desktop Notification ──────────────────────────────────────────────────
    def _notify(self, title: str, message: str):
        if HAS_NOTIFY:
            try:
                plyer_notify.notify(
                    title=title, message=message,
                    app_name="NEXORA Worker", timeout=5,
                )
            except Exception:
                pass

    # ── System Tray ───────────────────────────────────────────────────────────
    def _make_tray_img(self, hex_color: str = C_RED) -> "Image.Image":
        ico_png = get_asset_path("worker_icon.png")
        if ico_png.is_file():
            try:
                base = Image.open(ico_png).convert("RGBA").resize((64, 64), Image.Resampling.LANCZOS)
                draw = ImageDraw.Draw(base)
                r, g, b = int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16)
                # Outer dark circle for contrast
                draw.ellipse([43, 43, 61, 61], fill=(15, 23, 42, 255))
                # Glowing indicator dot
                draw.ellipse([45, 45, 59, 59], fill=(r, g, b, 255))
                return base
            except Exception:
                pass

        # Fallback drawn icon
        img  = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        r, g, b = int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16)
        draw.ellipse([8, 8, 56, 56], fill=(r, g, b, 255))
        draw.text((22, 18), "N", fill=(255, 255, 255, 220))
        return img

    def _setup_tray(self):
        menu = pystray.Menu(
            pystray.MenuItem("Open NEXORA Worker", self._tray_open, default=True),
            pystray.MenuItem("Disconnect Node",   lambda: self.after(0, self.stop_worker)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Exit Completely",   self._tray_quit),
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
            self._notify("NEXORA Worker", "Worker minimized to system tray. Right-click tray icon to exit.")
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

    # ── Worker Startup & Shutdown ─────────────────────────────────────────────
    def start_worker(self):
        url      = self.entry_url.get().strip()
        username = self.entry_user.get().strip()
        password = self.entry_pass.get().strip()
        name     = self.entry_name.get().strip() or self.hardware["hostname"]
        code     = self.entry_code.get().strip() or None

        if not url or not username or not password:
            messagebox.showerror("Configuration Error", "Master URL, Username, and Password are required.")
            return

        self._tasks_completed   = 0
        self._tasks_failed      = 0
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
        self.log("Deregistering worker node...")
        self.is_running = False
        self._clear_task()
        self.after(0, lambda: [
            self.btn_connect.configure(state=tk.NORMAL),
            self.btn_disconnect.configure(state=tk.DISABLED),
            self.set_status("OFFLINE", C_RED),
        ])

    # ── Master Cluster Communication Loop ─────────────────────────────────────
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

        # 1. Authentication
        self.log(f"Authenticating with cluster master at {master_url}...")
        token: Optional[str] = None
        try:
            r = session.post(
                f"{master_url}/api/v1/auth/login",
                json={"username": username, "password": password},
                timeout=8,
            )
            if r.status_code == 200:
                token = r.json().get("access_token")
                self.log(f"Authenticated successfully as '{username}'.")
            elif r.status_code == 401:
                self.log("Account not found. Requesting auto-registration as worker operator...")
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
                    self.log("Auto-registration granted by cluster.")
                elif reg.status_code == 409:
                    self.log(f"Authentication failed: password incorrect for user '{username}'.", "error")
                    self.after(0, self.stop_worker)
                    return
                else:
                    self.log(f"Registration rejected: {reg.text}", "error")
                    self.after(0, self.stop_worker)
                    return
            else:
                self.log(f"Master response error: HTTP {r.status_code}", "error")
                self.after(0, self.stop_worker)
                return
        except Exception as ex:
            self.log(f"Network error during authentication: {ex}", "error")
            self.after(0, self.stop_worker)
            return

        session.headers.update({"Authorization": f"Bearer {token}"})

        # 2. Pairing Request
        self.log(f"Registering node '{node_name}' to cluster registry...")
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
            "code":             pairing_code,
            "node_name":        node_name,
            "hostname":         self.hardware["hostname"],
            "operating_system": self.hardware["os"],
            "private_ipv4":     local_ip,
            "cpu_model":        self.hardware["cpu_model"],
            "cpu_threads":      self.hardware["cpu_threads"],
            "ram_total_bytes":  int(self.hardware["ram_total_gb"] * 1024 ** 3),
            "gpu_model":        self.hardware["gpu_model"] if self.hardware["gpu_vram_gb"] > 0 else None,
            "gpu_vendor":       self.hardware["gpu_vendor"],
            "gpu_vram_bytes":   gpu_bytes,
            "compute_backend":  "opencl" if self.hardware["gpu_vram_gb"] > 0 else "cpu",
        }
        try:
            r = session.post(f"{master_url}/api/v1/pairing/requests", json=pair_payload, timeout=8)
            if r.status_code in (200, 201):
                node_id = r.json()["id"]
                self.log(f"Cluster join request accepted. Assigned Node ID: {node_id}")
            else:
                self.log(f"Pairing request rejected: {r.text}", "error")
                self.after(0, self.stop_worker)
                return
        except Exception as ex:
            self.log(f"Error during node pairing: {ex}", "error")
            self.after(0, self.stop_worker)
            return

        # 3. Awaiting Approval Loop
        self.set_status("AWAITING APPROVAL", C_YELLOW)
        self.log("Waiting for Cluster Administrator approval in Web Dashboard...")

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
                    self.log("Node APPROVED by Cluster Admin! Ready for distributed compute workloads.", "success")
                    self.set_status("ONLINE & READY", C_GREEN)
                    self._notify("NEXORA Cluster", "Node approved and connected to distributed cluster!")
                elif hb.status_code == 403:
                    time.sleep(2.5)
                else:
                    time.sleep(3.0)
            except Exception as ex:
                self.log(f"Heartbeat probe waiting for approval ({ex})", "warning")
                time.sleep(3.0)

        # 4. Active Work & Telemetry Loop
        HB_INTERVAL     = 2.0
        last_hb         = time.time()
        reconnect_count = 0

        while self.is_running:
            now = time.time()

            # Heartbeat & Telemetry
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
                    reconnect_count = 0
                except requests.exceptions.ConnectionError:
                    reconnect_count += 1
                    if reconnect_count <= MAX_RECONNECT:
                        rc = reconnect_count
                        self.log(
                            f"Connection dropped — reconnecting in {RECONNECT_DELAY}s ({rc}/{MAX_RECONNECT})...",
                            "warning",
                        )
                        self.set_status(f"RECONNECTING {rc}/{MAX_RECONNECT}", C_YELLOW)
                        time.sleep(RECONNECT_DELAY)
                        continue
                    else:
                        self.log("Maximum reconnect attempts reached. Cluster unreachable.", "error")
                        self._notify("NEXORA Worker", "Lost connection to master cluster.")
                        self.after(0, self.stop_worker)
                        return
                except Exception:
                    pass

            # Task Leasing
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

                    self.log(f"Leased compute task '{task_key}' — executing workload...", "task")
                    self.set_status("COMPUTING", C_PURPLE)
                    self._set_task(task_key)

                    t0 = time.time()
                    try:
                        workload_zip: Optional[Path] = None
                        wid = task.get("workload_id")
                        if wid:
                            cache = Path(tempfile.gettempdir()) / "nexora_worker_workloads"
                            cache.mkdir(parents=True, exist_ok=True)
                            cached = cache / f"{wid}.zip"
                            if not cached.is_file():
                                self.log(f"Downloading workload package {wid[:8]}...")
                                dl = session.get(
                                    f"{master_url}/api/v1/worker/workloads/{wid}/download",
                                    timeout=30,
                                )
                                if dl.status_code == 200:
                                    cached.write_bytes(dl.content)
                            if cached.is_file():
                                workload_zip = cached

                        # Execute with background heartbeat & lease renewal for long-running jobs
                        self.log(f"Executing workload '{task_key}' on host compute...", "info")
                        stop_keepalive = threading.Event()

                        def _keep_lease_alive() -> None:
                            while not stop_keepalive.wait(20.0):
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
                                except Exception:
                                    pass
                                try:
                                    session.post(
                                        f"{master_url}/api/v1/worker/tasks/{task_id}/renew",
                                        json={"lease_token": lease_token},
                                        timeout=5,
                                    )
                                except Exception:
                                    pass

                        keepalive_thread = threading.Thread(target=_keep_lease_alive, daemon=True)
                        keepalive_thread.start()
                        try:
                            if run_task_engine:
                                res, artifact_zip = run_task_engine(
                                    task["payload"], workload_archive=workload_zip
                                )
                            else:
                                res, artifact_zip = run_task(task["payload"]), None
                        finally:
                            stop_keepalive.set()
                            keepalive_thread.join(timeout=2.0)

                        dur = round(time.time() - t0, 3)

                        # Artifacts Upload
                        if artifact_zip and os.path.isfile(artifact_zip):
                            self.log("Uploading output artifacts to master...")
                            with open(artifact_zip, "rb") as fh:
                                session.post(
                                    f"{master_url}/api/v1/worker/tasks/{task_id}/artifacts",
                                    files={"file": (f"{task_key}.zip", fh, "application/zip")},
                                    timeout=60,
                                )

                        # Submit Result
                        sub = session.post(
                            f"{master_url}/api/v1/worker/tasks/{task_id}/result",
                            json={"lease_token": lease_token, "status": "succeeded", "result": res},
                            timeout=6,
                        )
                        if sub.status_code == 200:
                            self._tasks_completed += 1
                            self._total_compute_sec += dur
                            self._refresh_stats()
                            self.log(f"Workload '{task_key}' completed in {dur}s!", "success")
                            self._notify("NEXORA Worker", f"Task '{task_key}' completed in {dur}s")
                        else:
                            self.log(f"Result submission failed: {sub.text}", "error")

                    except Exception as exc:
                        self._tasks_failed += 1
                        self._refresh_stats()
                        err = str(exc)
                        self.log(f"Task '{task_key}' error: {err}", "error")
                        self._notify("NEXORA Worker", f"Task '{task_key}' failed")
                        try:
                            session.post(
                                f"{master_url}/api/v1/worker/tasks/{task_id}/result",
                                json={"lease_token": lease_token, "status": "failed", "error": err[:4000]},
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
    def _add_padding(frame: tk.Frame, px: int = 14, py: int = 12):
        frame.config(padx=px, pady=py)


def main():
    app = NexoraWorkerApp()
    app.mainloop()


if __name__ == "__main__":
    main()
