# NEXORA — Distributed GPU Computing Platform

NEXORA is a high-performance, distributed GPU computing platform designed for Windows LAN networks and cloud deployment. It enables a Master node to orchestrate and distribute parallel computational workloads across multiple GPU/CPU worker machines with zero friction.

---

## ⚡ Architecture Highlights

1. **MongoDB Atlas Cloud Database**:
   - Replaced SQLite with MongoDB Atlas persistence.
   - Built-in TTL indexes for automated 7-day telemetry log cleanup (preventing storage bloat).
   - Dynamic per-test database isolation.

2. **Unified Frontend & Backend Server**:
   - Single FastAPI process serving both the REST API and the React SPA dashboard on port `5000`.
   - Eliminates the need to run separate Node.js development servers in production.

3. **1-Click Standalone Desktop Worker GUI (`NEXORA-Worker.exe`)**:
   - Built-in Windows executable (`dist/NEXORA-Worker.exe`) for friends/workers.
   - Requires **no Python, Git, or terminal commands** on worker machines.
   - Auto-detects local hardware (CPU cores, RAM, and GPU model/VRAM).
   - Real-time telemetry reporting (CPU/RAM/GPU utilization).

4. **Dynamic Scheduling Policies**:
   - **Automatic**: Dynamic resource-aware allocation to the most qualified available nodes.
   - **Manual**: Explicit node pinning or percentage/count quotas across nodes.
   - **Hybrid**: Mix of manual pinning and automatic dynamic scheduling.

5. **Cloud Deployment Ready**:
   - Multi-stage `Dockerfile` (Node 20 builder + Python 3.12 runner).
   - `render.yaml` for 1-click Render / Railway deployment.

---

## 🚀 Quick Start (Localhost)

### 1. Start Master Server (Dashboard + Backend API)

Double-click `start_backend.bat` or run:

```powershell
python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 5000 --reload
```

Open your browser at: **[http://localhost:5000](http://localhost:5000)**

### 2. Connect a Worker Node

#### Option A: 1-Click Desktop GUI (Recommended)
1. Run `dist\NEXORA-Worker.exe` (or `scripts\start-worker-gui.bat`).
2. Verify detected hardware specs.
3. Enter Master URL (`http://localhost:5000` or your LAN IP `http://192.168.x.x:5000`).
4. Click **`[ 🚀 Connect GPU / CPU to Cluster ]`**.
5. Approve the worker request from the Master Dashboard under the **Overview / Nodes** section.

#### Option B: Terminal CLI Worker
```powershell
.\scripts\start-worker.bat
```

---

## 🧪 Running Tests

To run the complete test suite:

```powershell
.\run_tests.bat
# Or manually:
python -m pytest backend/tests worker_agent/tests -v
```

All 22 core integration tests verify:
- Authentication & Sessions
- Cluster Creation & Pairing
- Automated & Manual Scheduling
- Worker Leasing & Heartbeat Health
- Deterministic Task Parallel Workload Execution

---

## 📁 Repository Structure

```
NEXORA/
├── backend/                  # FastAPI Master Application
│   ├── app/
│   │   ├── main.py           # Core REST API + SPA static server
│   │   ├── mongo_db.py       # MongoDB Atlas connection & TTL indexes
│   │   ├── network.py        # Preflight network diagnostics
│   │   └── workloads.py      # Manifest & execution validators
│   ├── requirements.txt      # Python dependencies
│   └── tests/                # Master API test suite
├── frontend/                 # React + TypeScript + Vite Dashboard
│   ├── src/                  # Dashboard UI components & views
│   └── dist/                 # Built production frontend assets
├── nexora_worker_gui/        # Standalone Worker Desktop GUI (Tkinter)
│   └── worker_app.py         # 1-Click Worker application source
├── worker_agent/             # Headless Worker Engine & OpenCL Executor
│   └── agent/                # Hardware discovery, metrics & safe execution
├── dist/                     # Compiled executables (NEXORA-Worker.exe)
├── scripts/                  # Automation scripts (.bat & .ps1)
├── Dockerfile                # Multi-stage production container
├── render.yaml               # Cloud deployment blueprint
├── start_backend.bat         # 1-Click Master launcher
└── run_tests.bat             # 1-Click test runner
```
