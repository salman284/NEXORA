import { useEffect, useMemo, useState } from "react";
import { Activity, Check, Copy, Cpu, Database, Download, FileText, FileUp, LogOut, Network, Plus, RefreshCw, Settings, ShieldCheck, Trash2, Wifi, Zap } from "lucide-react";

type User = { full_name: string; username: string; email: string; roles: string[] };
type Cluster = { id: string; name: string; status: string; created_at: string };
type PairingCode = { code: string; expires_at: string };
type Workload = { id: string; name: string; content_hash: string; size_bytes: number; validation_state: string; manifest: { operation: string; backend: string; execution_mode: string }; created_at: string };
type Job = { id: string; cluster_id: string; workload_id: string | null; name: string; execution_mode: string; scheduling_policy?: string; scheduling_config?: Record<string, unknown>; state: string; task_count: number; succeeded_count: number; failed_count: number; queued_count: number; created_at: string };
type Task = { id: string; job_id?: string; workload_id?: string | null; task_key: string; state: string; attempt: number; assigned_node_id: string | null; assigned_node_name?: string | null; pinned_node_id?: string | null; started_at?: string | null; completed_at?: string | null; duration_seconds?: number | null; result: Record<string, unknown> | null; error: string | null; has_artifacts?: boolean; artifact_size_bytes?: number | null; };
type FinalResult = { job_id: string; aggregation: string; task_count: number; result_hash: string; results: Array<{ task_key: string; result: Record<string, unknown> }> };
type Node = { id: string; node_name: string; hostname: string; status: string; membership_status: string; private_ipv4: string | null; cpu_threads?: number | null; gpu_vendor?: string | null; gpu_model: string | null; gpu_vram_bytes: number | null; compute_backend: string | null; cpu_utilization: number | null; ram_utilization: number | null; gpu_utilization: number | null; gpu_used_vram_bytes?: number | null; last_heartbeat_at: string | null; tasks_completed?: number; tasks_failed?: number };
type Telemetry = { node_id: string; node_name: string; captured_at: string; source: string; cpu_utilization: number | null; ram_utilization: number | null; gpu_utilization: number | null; gpu_used_vram_bytes: number | null };
type NetworkReport = { local_private_ipv4: string[]; master_host: string | null; master_port: number; resolution: string; resolved_addresses: string[]; tcp_state: string; latency_ms: number | null; detail: string | null };
type AuditEvent = { id: string; action: string; scope_type: string; scope_id: string | null; outcome: string; metadata: Record<string, unknown>; created_at: string };
type ClusterSummary = { cluster_id: string; cluster_name: string; registered_nodes: number; online_nodes: number; offline_nodes: number; detected_gpus: number; total_vram_bytes: number; active_jobs: number; queued_jobs: number; completed_jobs: number; failed_jobs: number };

const api = async <T,>(path: string, options: RequestInit = {}): Promise<T> => {
  const headers = options.body instanceof FormData ? { ...options.headers } : { "Content-Type": "application/json", ...options.headers };
  const response = await fetch(path, { ...options, headers });
  if (response.headers.get("content-type")?.includes("text/markdown")) {
    return response.text() as unknown as T;
  }
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new Error(body?.detail ?? `Request failed (${response.status})`);
  return body as T;
};

function Login({ onLogin }: { onLogin: (token: string, user: User) => void }) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [fullName, setFullName] = useState("");
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState("master");
  const [error, setError] = useState("");
  async function submit(event: React.FormEvent) {
    event.preventDefault(); setError("");
    try {
      const body = mode === "login" ? { username, password } : { full_name: fullName, username, email, password, roles: [role] };
      const result = await api<{ access_token: string; user: User }>(mode === "login" ? "/api/v1/auth/login" : "/api/v1/auth/register", { method: "POST", body: JSON.stringify(body) });
      onLogin(result.access_token, result.user);
    }
    catch (requestError) { setError(requestError instanceof Error ? requestError.message : "Unable to sign in"); }
  }
  return <main className="login-shell"><div className="login-panel"><div className="brand-mark-wrapper"><img src="/logo-64.png" alt="NEXORA Logo" className="brand-logo-img" /></div><p className="eyebrow">Distributed compute console</p><h1>{mode === "login" ? "Welcome back." : "Create your account."}</h1><p className="muted">{mode === "login" ? "Sign in to inspect your trusted LAN cluster." : "Choose a role now. You can operate as both Master and Worker later."}</p><form onSubmit={submit}>{mode === "register" && <label>Full name<input value={fullName} onChange={(event) => setFullName(event.target.value)} required /></label>}<label>Username<input value={username} onChange={(event) => setUsername(event.target.value)} required /></label>{mode === "register" && <label>Email<input type="email" value={email} onChange={(event) => setEmail(event.target.value)} required /></label>}<label>Password<input type="password" minLength={12} value={password} onChange={(event) => setPassword(event.target.value)} required /></label>{mode === "register" && <label>Primary role<select value={role} onChange={(event) => setRole(event.target.value)}><option value="master">Master</option><option value="worker_operator">Worker Operator</option></select></label>}{error && <p className="error-text">{error}</p>}<button className="primary-button" type="submit">{mode === "login" ? "Open console" : "Create account"}</button></form><button className="mode-button" onClick={() => { setMode(mode === "login" ? "register" : "login"); setError(""); }}>{mode === "login" ? "Need an account? Register" : "Already registered? Sign in"}</button></div><div className="login-aside"><span>01 / control plane</span><strong>Every node.<br />Visible.</strong><p>Capacity, trust, and execution state in one measured surface.</p></div></main>;
}

function Metric({ label, value, tone = "green" }: { label: string; value: string; tone?: string }) { return <div className="metric"><span>{label}</span><strong className={tone}>{value}</strong></div>; }

function ClusterTopology({ nodes, clusterName }: { nodes: Node[]; clusterName: string }) {
  return (
    <section className="topology-panel">
      <div className="card-title">
        <Network size={18} />
        <div>
          <span className="eyebrow">Cluster Topology Diagram</span>
          <h2>LAN Compute Mesh</h2>
        </div>
      </div>
      <div className="topology-container">
        <div className="topology-node master-box">
          <div className="node-icon"><Cpu size={18} /></div>
          <div>
            <strong>Master Hub ({clusterName || "Cluster"})</strong>
            <span>Control Plane · Port 5000 · Outbound Worker Discovery</span>
          </div>
          <span className="status online">Active Master</span>
        </div>
        <div className="topology-tree">
          {nodes.length === 0 ? (
            <div className="topology-empty">
              No Worker Nodes paired yet. Click "Pair Worker" to generate a pairing code for LAN machines.
            </div>
          ) : (
            nodes.map((node) => (
              <div key={node.id} className="topology-branch">
                <div className="topology-line" />
                <div className={`topology-node worker-box ${node.status}`}>
                  <div className="worker-info">
                    <strong>{node.node_name}</strong>
                    <span>{node.hostname} · {node.private_ipv4 ?? "IPv4 unverified"} · {node.compute_backend ?? "CPU"}</span>
                    <small>{node.gpu_model ?? "No GPU"} {node.gpu_vram_bytes ? `(${(node.gpu_vram_bytes / 1e9).toFixed(1)} GB VRAM)` : ""}</small>
                  </div>
                  <div className="worker-status-badge">
                    <span className={`status ${node.status}`}>{node.status}</span>
                    <span className="task-pill">{node.status === "online" ? "Ready / Polling" : "Offline"}</span>
                  </div>
                </div>
              </div>
            ))
          )}
        </div>
      </div>
    </section>
  );
}

function NodeOverview({ nodes, summary, telemetry, jobs, clusters, selectedId, clusterName, setSelectedId, setClusterName, onCreateCluster, onRefresh, onPair, onApprove, onRevoke, onDelete }: { nodes: Node[]; summary: ClusterSummary | null; telemetry: Telemetry[]; jobs: Job[]; clusters: Cluster[]; selectedId: string; clusterName: string; setSelectedId: (value: string) => void; setClusterName: (value: string) => void; onCreateCluster: (event: React.FormEvent) => void; onRefresh: () => void; onPair: () => void; onApprove: (nodeId: string) => void; onRevoke: (nodeId: string) => void; onDelete: (nodeId: string) => void }) {
  const onlineCount = summary?.online_nodes ?? nodes.filter((node) => node.status === "online").length;
  const offlineCount = summary?.offline_nodes ?? nodes.filter((node) => node.status === "offline").length;
  const activeJobs = summary?.active_jobs ?? jobs.filter((job) => ["queued", "running", "paused"].includes(job.state)).length;
  const queuedJobs = summary?.queued_jobs ?? jobs.filter((job) => job.state === "queued").length;
  const completedJobs = summary?.completed_jobs ?? jobs.filter((job) => job.state === "completed").length;
  const failedJobs = summary?.failed_jobs ?? jobs.filter((job) => job.state === "failed").length;
  const detectedGpus = summary?.detected_gpus ?? nodes.filter((node) => node.gpu_model).length;
  const totalVramBytes = summary?.total_vram_bytes ?? nodes.reduce((acc, n) => acc + (n.gpu_vram_bytes ?? 0), 0);
  const recent = telemetry.slice(0, 12).reverse();

  return (
    <>
      <section className="hero-strip">
        <div>
          <span className="eyebrow">Operational snapshot</span>
          <h2>{nodes.length ? `${onlineCount} node${onlineCount === 1 ? "" : "s"} reporting` : "Cluster is ready"}</h2>
          <p>{nodes.length ? "Telemetry refreshes every five seconds." : "Pair a Worker to start seeing live capacity."}</p>
        </div>
        <div className="hero-orbit">
          <div className="orbit-core"><ShieldCheck size={25} /><span>trusted LAN</span></div>
          <div className="orbit-line line-a" />
          <div className="orbit-line line-b" />
        </div>
      </section>

      <section className="metrics-grid">
        <div className="stat-card">
          <span>Registered nodes</span>
          <strong>{nodes.length}</strong>
          <small>{onlineCount} online · {offlineCount} offline</small>
        </div>
        <div className="stat-card">
          <span>Detected GPUs</span>
          <strong className="green-text">{detectedGpus}</strong>
          <small>per-node acceleration</small>
        </div>
        <div className="stat-card">
          <span>Aggregate VRAM</span>
          <strong>{(totalVramBytes / 1e9).toFixed(1)} GB</strong>
          <span className="vram-capacity-note">Aggregate capacity across nodes (not unified pool)</span>
        </div>
        <div className="stat-card">
          <span>Job Pipeline</span>
          <strong>{activeJobs} Active</strong>
          <small>{queuedJobs} queued · {completedJobs} done · {failedJobs} failed</small>
        </div>
      </section>

      <ClusterTopology nodes={nodes} clusterName={clusterName} />

      <section className="telemetry-panel">
        <div><span className="eyebrow">Measured telemetry</span><h2>Recent resource trend</h2></div>
        {recent.length === 0 ? <span className="telemetry-empty">No heartbeat samples yet</span> : (
          <div className="telemetry-bars">
            {recent.map((sample) => (
              <div className="telemetry-column" key={`${sample.node_id}-${sample.captured_at}`} title={`${sample.node_name} · ${new Date(sample.captured_at).toLocaleTimeString()}`}>
                <i className="cpu-bar" style={{ height: `${sample.cpu_utilization ?? 0}%` }} />
                <i className="ram-bar" style={{ height: `${sample.ram_utilization ?? 0}%` }} />
              </div>
            ))}
          </div>
        )}
        <div className="telemetry-legend">
          <span><i className="cpu-dot" /> CPU measured</span>
          <span><i className="ram-dot" /> RAM measured</span>
          <span>GPU metrics require verified backend adapter</span>
        </div>
      </section>

      <div className="section-heading">
        <div><span className="eyebrow">Cluster capacity</span><h2>Nodes</h2></div>
        <div className="cluster-tools">
          <select value={selectedId} onChange={(event) => setSelectedId(event.target.value)}>
            <option value="">Select cluster</option>
            {clusters.map((cluster) => <option key={cluster.id} value={cluster.id}>{cluster.name}</option>)}
          </select>
          <form onSubmit={onCreateCluster} className="create-cluster">
            <input placeholder="New cluster name" value={clusterName} onChange={(event) => setClusterName(event.target.value)} />
            <button className="small-button" title="Create cluster" type="submit"><Plus size={16} /></button>
          </form>
          <button className="small-button pair-button" onClick={onPair}><Wifi size={15} /> Pair Worker</button>
          <button className="icon-button" title="Refresh nodes" onClick={onRefresh}><RefreshCw size={17} /></button>
        </div>
      </div>

      {nodes.filter((n) => n.membership_status === "pending").length > 0 && (
        <section style={{
          background: "linear-gradient(135deg, rgba(211, 243, 107, 0.15), rgba(120, 200, 50, 0.05))",
          border: "1px solid #d3f36b",
          borderRadius: "12px",
          padding: "16px 20px",
          marginBottom: "20px",
          display: "flex",
          flexDirection: "column",
          gap: "12px"
        }}>
          <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
            <span style={{ fontSize: "22px" }}>🔔</span>
            <div>
              <strong style={{ color: "#d3f36b", fontSize: "16px" }}>
                {nodes.filter((n) => n.membership_status === "pending").length} Worker Join Request(s) Waiting for Approval
              </strong>
              <p style={{ margin: 0, fontSize: "13px", color: "#bbb" }}>
                Machines on your LAN want to contribute their GPUs to this cluster. Click Accept to authorize.
              </p>
            </div>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
            {nodes.filter((n) => n.membership_status === "pending").map((pNode) => (
              <div key={pNode.id} style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                background: "rgba(0,0,0,0.5)",
                padding: "10px 14px",
                borderRadius: "8px",
                border: "1px solid rgba(255,255,255,0.08)"
              }}>
                <div>
                  <strong style={{ fontSize: "15px", color: "#fff" }}>{pNode.node_name}</strong>
                  <span style={{ color: "#aaa", fontSize: "13px", marginLeft: "10px" }}>
                    {pNode.hostname} · {pNode.private_ipv4 ?? "LAN"} · GPU: {pNode.gpu_model ?? "CPU Mode"} ({pNode.compute_backend ?? "cpu"})
                  </span>
                </div>
                <div style={{ display: "flex", gap: "8px" }}>
                  <button
                    onClick={() => onApprove(pNode.id)}
                    style={{
                      background: "#d3f36b",
                      color: "#111",
                      border: "none",
                      borderRadius: "6px",
                      padding: "7px 14px",
                      fontWeight: 700,
                      cursor: "pointer"
                    }}
                  >
                    ✅ Accept Worker
                  </button>
                  <button
                    onClick={() => onRevoke(pNode.id)}
                    style={{
                      background: "transparent",
                      color: "#ff6b6b",
                      border: "1px solid rgba(255,107,107,0.3)",
                      borderRadius: "6px",
                      padding: "7px 12px",
                      cursor: "pointer"
                    }}
                  >
                    Reject
                  </button>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      <section className="node-grid">
        {nodes.length === 0 ? (
          <div className="empty-state">
            <Wifi size={24} />
            <strong>No nodes connected</strong>
            <p>Generate a pairing code, approve a Worker, and its heartbeat will appear here.</p>
          </div>
        ) : (
          nodes.map((node) => {
            const freeVram = node.gpu_vram_bytes ? Math.max(0, (node.gpu_vram_bytes - (node.gpu_used_vram_bytes ?? 0))) : null;
            return (
              <article className="node-card" key={node.id}>
                <div className="node-header">
                  <div className="node-icon"><Cpu size={18} /></div>
                  <div>
                    <h3>{node.node_name}</h3>
                    <span>{node.hostname} · {node.private_ipv4 ?? "address unavailable"}</span>
                  </div>
                  <span className={`status ${node.status}`}>{node.status}</span>
                </div>
                <div className="node-gpu">
                  <span>GPU & Compute Backend</span>
                  <strong>{node.gpu_model ?? "Not detected"}</strong>
                  <small>
                    {node.compute_backend ?? "Backend unavailable"} · {node.gpu_vram_bytes ? `${(node.gpu_vram_bytes / 1e9).toFixed(1)} GB Total (${freeVram ? (freeVram / 1e9).toFixed(1) : 0} GB Free)` : "VRAM unavailable"}
                  </small>
                </div>
                <div className="node-metrics">
                  <Metric label="CPU" value={node.cpu_utilization == null ? "Unavailable" : `${node.cpu_utilization.toFixed(1)}%`} />
                  <Metric label="RAM" value={node.ram_utilization == null ? "Unavailable" : `${node.ram_utilization.toFixed(1)}%`} />
                  <Metric label="GPU" value={node.gpu_utilization == null ? "Unavailable" : `${node.gpu_utilization.toFixed(1)}%`} tone={node.gpu_utilization == null ? "muted" : "green"} />
                </div>
                <footer>
                  <span>Membership: {node.membership_status}</span>
                  <div style={{ display: "flex", gap: "6px", alignItems: "center" }}>
                    {node.membership_status === "pending" ? (
                      <button className="approve-button" onClick={() => onApprove(node.id)}>Approve</button>
                    ) : node.membership_status === "approved" ? (
                      <button className="revoke-button" onClick={() => onRevoke(node.id)}>Revoke</button>
                    ) : (
                      <span style={{ color: "#64748b", fontSize: "12px" }}>Access revoked</span>
                    )}
                    {node.status !== "online" && (
                      <button
                        title="Permanently remove this node record"
                        onClick={() => onDelete(node.id)}
                        style={{ background: "transparent", border: "1px solid rgba(239,68,68,0.3)", color: "#ef4444", cursor: "pointer", padding: "4px 8px", borderRadius: "4px", display: "inline-flex", alignItems: "center", gap: "4px", fontSize: "11px" }}
                      >
                        <Trash2 size={12} /> Remove
                      </button>
                    )}
                  </div>
                </footer>
              </article>
            );
          })
        )}
      </section>
    </>
  );
}

function WorkloadsView({
  token,
  clusterId,
  nodes,
  workloads,
  jobs,
  onRefresh,
  onError,
  onNavigate,
}: {
  token: string;
  clusterId: string;
  nodes: Node[];
  workloads: Workload[];
  jobs: Job[];
  onRefresh: () => void;
  onError: (message: string) => void;
  onNavigate?: (tab: "overview" | "workloads" | "worker" | "network" | "audit" | "settings") => void;
}) {
  const approvedNodes = nodes.filter((n) => n.membership_status === "approved");
  const onlineApprovedNodes = approvedNodes.filter((n) => n.status === "online");
  const onlineGpuNodes = onlineApprovedNodes.filter((n) => n.gpu_model && n.gpu_model !== "None (CPU Compute)");
  const totalVramGB = (onlineApprovedNodes.reduce((acc, n) => acc + (n.gpu_vram_bytes || 0), 0) / 1e9).toFixed(1);
  const totalCores = onlineApprovedNodes.reduce((acc, n) => acc + (n.cpu_threads || 1), 0);
  const detectedGpus = onlineApprovedNodes.map((n) => n.gpu_model).filter((m) => m && m !== "None (CPU Compute)").join(", ");

  const [name, setName] = useState("Distributed Python Job");
  const [operation, setOperation] = useState("execute_script");
  const [backend, setBackend] = useState("cpu");
  const [file, setFile] = useState<File | null>(null);
  const [selectedWorkload, setSelectedWorkload] = useState("");
  const [dispatchMode, setDispatchMode] = useState<"single" | "auto" | "manual">("auto");
  const [singleTaskKey, setSingleTaskKey] = useState("main-run");
  const [autoTargetGpuOnly, setAutoTargetGpuOnly] = useState(true);
  const [autoSliceCount, setAutoSliceCount] = useState(2);
  const [hasUserCustomizedSlices, setHasUserCustomizedSlices] = useState(false);
  const [manualSelectedNodeIds, setManualSelectedNodeIds] = useState<string[]>([]);
  const [jobName, setJobName] = useState("Distributed Execution Batch");
  const [busy, setBusy] = useState(false);
  const [selectedJobId, setSelectedJobId] = useState("");
  const [tasks, setTasks] = useState<Task[]>([]);
  const [selectedErrorTask, setSelectedErrorTask] = useState<Task | null>(null);
  const headers = { Authorization: `Bearer ${token}` };

  // Dynamic Optimal Slice Count calculation based on active GPU/CPU hardware
  const recommendedSliceCount = useMemo(() => {
    if (autoTargetGpuOnly) {
      return Math.max(1, onlineGpuNodes.length || onlineApprovedNodes.length || 2);
    } else {
      return Math.max(1, onlineApprovedNodes.length || 2);
    }
  }, [autoTargetGpuOnly, onlineGpuNodes.length, onlineApprovedNodes.length]);

  // Keep autoSliceCount in sync with cluster optimal hardware capacity unless customized
  useEffect(() => {
    if (!hasUserCustomizedSlices && recommendedSliceCount > 0) {
      setAutoSliceCount(recommendedSliceCount);
    }
  }, [recommendedSliceCount, hasUserCustomizedSlices]);

  useEffect(() => {
    if (manualSelectedNodeIds.length === 0 && approvedNodes.length > 0) {
      setManualSelectedNodeIds(approvedNodes.map((n) => n.id));
    }
  }, [approvedNodes]);

  async function upload(event: React.FormEvent) {
    event.preventDefault();
    if (!file) { onError("Choose a notebook (.ipynb), script (.py), or ZIP package first"); return; }
    setBusy(true);
    try {
      const ext = file.name.split(".").pop()?.toLowerCase() || "";
      const isNb = ext === "ipynb";
      const isPy = ext === "py";
      const runtime = isNb ? "notebook" : isPy ? "script" : "builtin";
      const op = isNb ? "execute_notebook" : isPy ? "execute_script" : operation;

      const body = new FormData();
      body.append("manifest", JSON.stringify({
        schema_version: "1",
        name: name || file.name.replace(/\.[^/.]+$/, ""),
        runtime,
        backend,
        execution_mode: "task_parallel",
        operation: op,
        entrypoint: file.name,
        aggregation: "bundle"
      }));
      body.append("file", file);
      const created = await api<Workload>("/api/v1/workloads", { method: "POST", headers, body });
      setFile(null);
      if (created?.id) {
        setSelectedWorkload(created.id);
        const autoName = name || file.name.replace(/\.[^/.]+$/, "");
        setJobName(autoName);
      }
      onRefresh();
    } catch (requestError) {
      onError(requestError instanceof Error ? requestError.message : "Upload failed");
    } finally {
      setBusy(false);
    }
  }

  async function launch(event: React.FormEvent) {
    event.preventDefault();
    if (!clusterId) { onError("Select or create a cluster first"); return; }
    try {
      let parsedTasks: any[] = [];
      let finalPolicy: "automatic" | "manual" | "hybrid" = "automatic";
      let finalConfig: Record<string, any> = {};

      if (dispatchMode === "single") {
        parsedTasks = [{
          task_key: singleTaskKey.trim() || "main-run",
          payload: { worker_index: 0, total_workers: 1, mode: "single" }
        }];
        finalPolicy = "automatic";
      } else if (dispatchMode === "auto") {
        const count = Math.max(1, Math.min(64, autoSliceCount || 2));
        parsedTasks = Array.from({ length: count }, (_, i) => ({
          task_key: `slice-${i + 1}`,
          payload: {
            worker_index: i,
            total_workers: count,
            slice: i + 1,
            target_hardware: autoTargetGpuOnly ? "gpu" : "any"
          }
        }));
        finalPolicy = "automatic";
      } else if (dispatchMode === "manual") {
        const targetNodes = approvedNodes.filter((n) => manualSelectedNodeIds.includes(n.id));
        if (targetNodes.length === 0) {
          onError("Select at least one worker node from the list");
          return;
        }
        parsedTasks = targetNodes.map((n, i) => ({
          task_key: `worker-${i + 1}-${n.node_name.toLowerCase().replace(/[^a-z0-9]/g, "_")}`,
          payload: {
            worker_index: i,
            total_workers: targetNodes.length,
            assigned_node: n.node_name
          },
          pinned_node_id: n.id
        }));
        finalPolicy = "manual";
      }

      await api<Job>("/api/v1/jobs", {
        method: "POST",
        headers,
        body: JSON.stringify({
          cluster_id: clusterId,
          workload_id: selectedWorkload || null,
          name: jobName.trim() || "Distributed Job",
          scheduling_policy: finalPolicy,
          scheduling_config: finalConfig,
          tasks: parsedTasks,
        }),
      });
      onRefresh();
    } catch (requestError) {
      onError(requestError instanceof Error ? requestError.message : "Job launch failed");
    }
  }

  async function inspectJob(jobId: string) {
    setSelectedJobId(jobId);
    try {
      setTasks(await api<Task[]>(`/api/v1/jobs/${jobId}/tasks`, { headers }));
    } catch (requestError) {
      onError(requestError instanceof Error ? requestError.message : "Unable to load tasks");
    }
  }

  useEffect(() => {
    if (!selectedJobId) return;
    const timer = window.setInterval(() => void inspectJob(selectedJobId), 5000);
    return () => window.clearInterval(timer);
  }, [selectedJobId]);

  async function controlJob(action: "pause" | "resume" | "cancel") {
    if (!selectedJobId) return;
    if (action === "cancel" && !window.confirm("Cancel this job and all unfinished tasks?")) return;
    try {
      await api<Job>(`/api/v1/jobs/${selectedJobId}/${action}`, { method: "POST", headers });
      await inspectJob(selectedJobId);
      onRefresh();
    } catch (requestError) {
      onError(requestError instanceof Error ? requestError.message : `Unable to ${action} job`);
    }
  }

  async function downloadResult() {
    if (!selectedJobId) return;
    try {
      const result = await api<FinalResult>(`/api/v1/jobs/${selectedJobId}/result`, { headers });
      const link = document.createElement("a");
      link.href = URL.createObjectURL(new Blob([JSON.stringify(result, null, 2)], { type: "application/json" }));
      link.download = `nexora-${selectedJobId}-result.json`;
      link.click();
      URL.revokeObjectURL(link.href);
    } catch (requestError) {
      onError(requestError instanceof Error ? requestError.message : "Final result is not available");
    }
  }

  async function downloadReport(format: "markdown" | "json") {
    if (!selectedJobId) return;
    try {
      const endpoint = format === "markdown" ? `/api/v1/jobs/${selectedJobId}/report?format=markdown` : `/api/v1/jobs/${selectedJobId}/report`;
      const reportData = await api<string | Record<string, unknown>>(endpoint, { headers });
      const content = typeof reportData === "string" ? reportData : JSON.stringify(reportData, null, 2);
      const link = document.createElement("a");
      link.href = URL.createObjectURL(new Blob([content], { type: format === "markdown" ? "text/markdown" : "application/json" }));
      link.download = `nexora-${selectedJobId}-report.${format === "markdown" ? "md" : "json"}`;
      link.click();
      URL.revokeObjectURL(link.href);
    } catch (requestError) {
      onError(requestError instanceof Error ? requestError.message : "Unable to download execution report");
    }
  }

  async function downloadBundle(jobId: string) {
    try {
      const resp = await fetch(`/api/v1/jobs/${jobId}/bundle`, { headers });
      if (!resp.ok) throw new Error("Output bundle is not available yet");
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `nexora-${jobId.slice(0, 8)}-output-bundle.zip`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (requestError) {
      onError(requestError instanceof Error ? requestError.message : "Unable to download output bundle");
    }
  }

  async function downloadTaskArtifacts(taskId: string, taskKey: string) {
    try {
      const resp = await fetch(`/api/v1/tasks/${taskId}/artifacts`, { headers });
      if (!resp.ok) throw new Error("Artifacts not available for this task");
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `nexora-${taskKey}-artifacts.zip`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (requestError) {
      onError(requestError instanceof Error ? requestError.message : "Unable to download task artifacts");
    }
  }

  async function deleteJob(jobId: string) {
    if (!window.confirm("Permanently delete this job and all associated task history?")) return;
    try {
      await api(`/api/v1/jobs/${jobId}`, { method: "DELETE", headers });
      if (selectedJobId === jobId) {
        setSelectedJobId("");
        setTasks([]);
      }
      onRefresh();
    } catch (requestError) {
      onError(requestError instanceof Error ? requestError.message : "Unable to delete job");
    }
  }



  const selectedJob = jobs.find((job) => job.id === selectedJobId);

  return (
    <>
      <section className="hero-strip compact-hero">
        <div>
          <span className="eyebrow">Execution workspace</span>
          <h2>Intelligent Scheduler & Dispatch</h2>
          <p>Task parallelism across verified GPU/CPU nodes with Automatic, Manual, or Hybrid distribution.</p>
        </div>
        <Zap size={42} className="hero-accent" />
      </section>

      {/* Cluster Readiness Banner */}
      {onlineApprovedNodes.length === 0 ? (
        <div style={{ background: "rgba(234, 179, 8, 0.12)", border: "1px solid rgba(234, 179, 8, 0.35)", borderRadius: "8px", padding: "12px 16px", marginBottom: "16px", display: "flex", alignItems: "center", justifyContent: "space-between", gap: "12px", flexWrap: "wrap" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
            <span style={{ fontSize: "20px" }}>⚠️</span>
            <div>
              <strong style={{ color: "#facc15", fontSize: "14px", display: "block" }}>No Active Workers Online in Cluster</strong>
              <p style={{ margin: "2px 0 0 0", color: "#cbd5e1", fontSize: "12px" }}>
                There are currently 0 approved workers online. Any dispatched jobs will wait in <code>queued</code> state until a worker connects from the Worker Console.
              </p>
            </div>
          </div>
          {onNavigate && (
            <button
              type="button"
              onClick={() => onNavigate("worker")}
              style={{
                background: "#facc15",
                color: "#0f172a",
                border: "none",
                borderRadius: "6px",
                padding: "8px 14px",
                fontWeight: 700,
                cursor: "pointer",
                fontSize: "12px",
                display: "inline-flex",
                alignItems: "center",
                gap: "6px",
                flexShrink: 0
              }}
            >
              Open Worker Console →
            </button>
          )}
        </div>
      ) : (
        <div style={{ background: "rgba(34, 197, 94, 0.12)", border: "1px solid rgba(34, 197, 94, 0.35)", borderRadius: "8px", padding: "12px 16px", marginBottom: "16px", display: "flex", alignItems: "center", gap: "12px" }}>
          <span style={{ fontSize: "20px" }}>🟢</span>
          <div>
            <strong style={{ color: "#4ade80", fontSize: "14px", display: "block" }}>
              Cluster Ready: {onlineApprovedNodes.length} Active Worker{onlineApprovedNodes.length > 1 ? "s" : ""} Online ({totalCores} CPU Threads · {totalVramGB} GB VRAM)
            </strong>
            <p style={{ margin: "2px 0 0 0", color: "#cbd5e1", fontSize: "12px" }}>
              {detectedGpus ? `Acceleration hardware: ${detectedGpus}. ` : "CPU Compute Nodes. "}Tasks will be leased and executed immediately in parallel!
            </p>
          </div>
        </div>
      )}

      <div className="workload-columns">
        <section className="form-card">
          <div className="card-title">
            <FileUp size={18} />
            <div><span className="eyebrow">01 / Notebook & Script Package</span><h2>Upload code / notebook</h2></div>
          </div>
          <p className="muted" style={{ fontSize: "12px", margin: "0 0 12px 0" }}>
            Upload <strong>Jupyter Notebook (.ipynb)</strong>, <strong>Python script (.py)</strong>, or a <strong>ZIP bundle</strong>. Distributed worker GPUs will execute tasks in parallel and merge all output models/artifacts into a single downloadable ZIP!
          </p>
          <form onSubmit={upload}>
            <label className="file-input">
              Choose Notebook / Script / ZIP
              <input
                type="file"
                accept=".zip,.ipynb,.py,application/zip"
                onChange={(event) => {
                  const f = event.target.files?.[0] ?? null;
                  setFile(f);
                  if (f) {
                    const base = f.name.replace(/\.[^/.]+$/, "");
                    setName(base);
                    setJobName(base);
                    const ext = f.name.split(".").pop()?.toLowerCase() || "";
                    if (ext === "ipynb") setOperation("execute_notebook");
                    else if (ext === "py") setOperation("execute_script");
                  }
                }}
              />
            </label>

            {file && (
              <div style={{ background: "rgba(56, 189, 248, 0.1)", border: "1px solid rgba(56, 189, 248, 0.3)", borderRadius: "6px", padding: "8px 12px", margin: "10px 0", fontSize: "12px", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <div>
                  <strong style={{ color: "#38bdf8", display: "block" }}>
                    {file.name.endsWith(".ipynb") ? "📓 Jupyter Notebook" : file.name.endsWith(".py") ? "🐍 Python Script" : "📦 ZIP Archive"} Selected
                  </strong>
                  <span style={{ color: "#cbd5e1", fontSize: "11px" }}>{file.name} ({(file.size / 1024).toFixed(1)} KB)</span>
                </div>
                <button
                  type="button"
                  onClick={() => { setFile(null); }}
                  style={{ background: "transparent", border: "none", color: "#94a3b8", cursor: "pointer", fontSize: "14px", fontWeight: 700 }}
                  title="Remove selected file"
                >
                  ✕
                </button>
              </div>
            )}

            <label>Package / Algorithm name<input value={name} onChange={(event) => setName(event.target.value)} /></label>

            {!file || (!file.name.endsWith(".ipynb") && !file.name.endsWith(".py")) ? (
              <div className="form-row">
                <label>Operation
                  <select value={operation} onChange={(event) => setOperation(event.target.value)}>
                    <option value="execute_script">execute_script (Python Script)</option>
                    <option value="execute_notebook">execute_notebook (Jupyter Notebook)</option>
                  </select>
                </label>
                <label>Backend
                  <select value={backend} onChange={(event) => setBackend(event.target.value)}>
                    <option value="cpu">CPU</option>
                    <option value="opencl">OpenCL</option>
                  </select>
                </label>
              </div>
            ) : (
              <div style={{ margin: "8px 0 12px 0", fontSize: "11px", color: "#4ade80" }}>
                ✓ Custom code execution mode active: All generated models, weights, tables, plots & output notebook will be captured.
              </div>
            )}

            <button className="primary-button" disabled={busy} type="submit">
              {busy ? "Validating & Uploading…" : (file ? "Upload & Register Workload" : "Validate and store")}
            </button>
          </form>
        </section>

        <section className="form-card">
          <div className="card-title">
            <Activity size={18} />
            <div><span className="eyebrow">02 / dispatch</span><h2>Launch job</h2></div>
          </div>
          <form onSubmit={launch}>
            <label>Job name<input value={jobName} onChange={(event) => setJobName(event.target.value)} /></label>
            <label>Validated workload
              <select value={selectedWorkload} onChange={(event) => setSelectedWorkload(event.target.value)}>
                <option value="">No package (direct demo)</option>
                {workloads.map((workload) => <option key={workload.id} value={workload.id}>{workload.name} · {workload.manifest.backend}</option>)}
              </select>
            </label>
            {/* Dispatch Strategy Selector */}
            <div style={{ marginTop: "14px", marginBottom: "12px" }}>
              <span style={{ fontSize: "11px", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.08em", color: "#94a3b8", display: "block", marginBottom: "6px" }}>
                Distribution Strategy
              </span>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: "6px", marginBottom: "10px" }}>
                <button
                  type="button"
                  onClick={() => setDispatchMode("single")}
                  style={{
                    padding: "9px 6px",
                    borderRadius: "6px",
                    border: dispatchMode === "single" ? "1px solid #d3f36b" : "1px solid #334155",
                    background: dispatchMode === "single" ? "rgba(211, 243, 107, 0.15)" : "#0f172a",
                    color: dispatchMode === "single" ? "#d3f36b" : "#94a3b8",
                    fontWeight: 700,
                    fontSize: "12px",
                    cursor: "pointer",
                    textAlign: "center"
                  }}
                >
                  ⚡ Single Run
                </button>
                <button
                  type="button"
                  onClick={() => setDispatchMode("auto")}
                  style={{
                    padding: "9px 6px",
                    borderRadius: "6px",
                    border: dispatchMode === "auto" ? "1px solid #d3f36b" : "1px solid #334155",
                    background: dispatchMode === "auto" ? "rgba(211, 243, 107, 0.15)" : "#0f172a",
                    color: dispatchMode === "auto" ? "#d3f36b" : "#94a3b8",
                    fontWeight: 700,
                    fontSize: "12px",
                    cursor: "pointer",
                    textAlign: "center"
                  }}
                >
                  🤖 Auto-Split
                </button>
                <button
                  type="button"
                  onClick={() => setDispatchMode("manual")}
                  style={{
                    padding: "9px 6px",
                    borderRadius: "6px",
                    border: dispatchMode === "manual" ? "1px solid #d3f36b" : "1px solid #334155",
                    background: dispatchMode === "manual" ? "rgba(211, 243, 107, 0.15)" : "#0f172a",
                    color: dispatchMode === "manual" ? "#d3f36b" : "#94a3b8",
                    fontWeight: 700,
                    fontSize: "12px",
                    cursor: "pointer",
                    textAlign: "center"
                  }}
                >
                  🎛️ Manual Pick
                </button>
              </div>

              {/* Mode 1: Single Run */}
              {dispatchMode === "single" && (
                <div style={{ background: "rgba(15, 23, 42, 0.6)", border: "1px dashed #334155", borderRadius: "8px", padding: "12px 14px", marginBottom: "8px" }}>
                  <label style={{ display: "grid", gap: "6px", fontSize: "12px", color: "#94a3b8" }}>
                    <span>Run Tag / Label (Optional)</span>
                    <input
                      value={singleTaskKey}
                      onChange={(e) => setSingleTaskKey(e.target.value)}
                      placeholder="e.g. main-run or experiment-1"
                      style={{ width: "100%" }}
                    />
                  </label>
                  <small style={{ color: "#a3e635", fontSize: "11px", display: "block", marginTop: "8px" }}>
                    ✓ 1-Click Execution: Master will automatically lease this task to the best online GPU/CPU worker. All output files (.pkl, .pt, .png, etc.) will be bundled into a final ZIP.
                  </small>
                </div>
              )}

              {/* Mode 2: Auto-Distribute (Smart GPU aware) */}
              {dispatchMode === "auto" && (
                <div style={{ background: "rgba(15, 23, 42, 0.6)", border: "1px solid #334155", borderRadius: "8px", padding: "12px 14px", marginBottom: "8px" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "10px" }}>
                    <span style={{ fontSize: "12px", fontWeight: 700, color: "#38bdf8" }}>
                      🤖 Smart Auto-Distribution
                    </span>
                    <span style={{ fontSize: "11px", color: "#94a3b8" }}>
                      Online: {onlineApprovedNodes.length} Nodes ({detectedGpus || "CPU nodes"})
                    </span>
                  </div>

                  {/* Recommendation Callout */}
                  <div style={{ background: "rgba(56, 189, 248, 0.1)", border: "1px solid rgba(56, 189, 248, 0.3)", borderRadius: "6px", padding: "8px 12px", marginBottom: "10px", display: "flex", justifyContent: "space-between", alignItems: "center", gap: "10px" }}>
                    <div>
                      <span style={{ color: "#38bdf8", fontSize: "12px", fontWeight: 700, display: "flex", alignItems: "center", gap: "6px" }}>
                        ✨ Best Recommendation: {recommendedSliceCount} Parallel Slices
                      </span>
                      <span style={{ color: "#cbd5e1", fontSize: "11px", display: "block", marginTop: "2px" }}>
                        {autoTargetGpuOnly
                          ? (onlineGpuNodes.length > 0
                              ? `1 parallel slice per active GPU (${onlineGpuNodes.map((n) => n.node_name).join(", ")}) for zero idle time`
                              : `Auto-balanced across online CPU nodes`)
                          : `1 parallel slice per online worker node (${onlineApprovedNodes.length} active machines)`}
                      </span>
                    </div>
                    {autoSliceCount !== recommendedSliceCount && (
                      <button
                        type="button"
                        onClick={() => {
                          setAutoSliceCount(recommendedSliceCount);
                          setHasUserCustomizedSlices(false);
                        }}
                        style={{
                          background: "#38bdf8",
                          color: "#0f172a",
                          border: "none",
                          borderRadius: "4px",
                          padding: "4px 8px",
                          fontSize: "11px",
                          fontWeight: 700,
                          cursor: "pointer",
                          flexShrink: 0,
                          whiteSpace: "nowrap"
                        }}
                      >
                        Use {recommendedSliceCount}
                      </button>
                    )}
                  </div>

                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "10px", marginBottom: "10px" }}>
                    <label style={{ display: "grid", gap: "4px", fontSize: "12px", color: "#cbd5e1" }}>
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                        <span>Parallel Slices (Tasks)</span>
                        {autoSliceCount === recommendedSliceCount && (
                          <span style={{ fontSize: "10px", color: "#38bdf8", fontWeight: 700, background: "rgba(56, 189, 248, 0.2)", padding: "1px 6px", borderRadius: "10px" }}>
                            Optimal
                          </span>
                        )}
                      </div>
                      <div style={{ display: "flex", gap: "6px" }}>
                        <button
                          type="button"
                          onClick={() => {
                            setHasUserCustomizedSlices(true);
                            setAutoSliceCount(Math.max(1, autoSliceCount - 1));
                          }}
                          style={{ padding: "0 10px", background: "#1e293b", color: "#f1f5f9", border: "1px solid #334155", borderRadius: "6px", cursor: "pointer", fontWeight: 700 }}
                        >
                          -
                        </button>
                        <input
                          type="number"
                          min="1"
                          max="64"
                          value={autoSliceCount}
                          onChange={(e) => {
                            setHasUserCustomizedSlices(true);
                            setAutoSliceCount(Number(e.target.value));
                          }}
                          style={{ width: "100%", textAlign: "center" }}
                        />
                        <button
                          type="button"
                          onClick={() => {
                            setHasUserCustomizedSlices(true);
                            setAutoSliceCount(Math.min(64, autoSliceCount + 1));
                          }}
                          style={{ padding: "0 10px", background: "#1e293b", color: "#f1f5f9", border: "1px solid #334155", borderRadius: "6px", cursor: "pointer", fontWeight: 700 }}
                        >
                          +
                        </button>
                      </div>
                    </label>
                    <label style={{ display: "grid", gap: "4px", fontSize: "12px", color: "#cbd5e1" }}>
                      <span>Hardware Target</span>
                      <select
                        value={autoTargetGpuOnly ? "gpu" : "any"}
                        onChange={(e) => setAutoTargetGpuOnly(e.target.value === "gpu")}
                        style={{ width: "100%" }}
                      >
                        <option value="gpu">GPU Nodes Only (Fastest)</option>
                        <option value="any">All Nodes (GPU + CPU)</option>
                      </select>
                    </label>
                  </div>

                  <small style={{ color: "#4ade80", fontSize: "11px", display: "block" }}>
                    ✓ Zero JSON: Master will automatically scan node load & VRAM capacity, then balance all {autoSliceCount} slices across available workers in parallel.
                  </small>
                </div>
              )}

              {/* Mode 3: Manual Visual Pick (Checkboxes) */}
              {dispatchMode === "manual" && (
                <div style={{ background: "rgba(15, 23, 42, 0.6)", border: "1px solid #334155", borderRadius: "8px", padding: "12px 14px", marginBottom: "8px" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
                    <span style={{ fontSize: "12px", fontWeight: 700, color: "#facc15" }}>
                      🎛️ Select Target Workers:
                    </span>
                    <span style={{ fontSize: "11px", color: "#94a3b8" }}>
                      {manualSelectedNodeIds.length} of {approvedNodes.length} selected
                    </span>
                  </div>

                  {approvedNodes.length === 0 ? (
                    <small className="muted" style={{ display: "block", padding: "8px 0" }}>
                      No approved workers connected in cluster. Please connect workers first.
                    </small>
                  ) : (
                    <div style={{ display: "grid", gap: "6px", maxHeight: "170px", overflowY: "auto", margin: "8px 0" }}>
                      {approvedNodes.map((n) => {
                        const isSelected = manualSelectedNodeIds.includes(n.id);
                        return (
                          <div
                            key={n.id}
                            onClick={() => {
                              if (isSelected) {
                                setManualSelectedNodeIds(manualSelectedNodeIds.filter((id) => id !== n.id));
                              } else {
                                setManualSelectedNodeIds([...manualSelectedNodeIds, n.id]);
                              }
                            }}
                            style={{
                              display: "flex",
                              alignItems: "center",
                              justifyContent: "space-between",
                              padding: "8px 10px",
                              borderRadius: "6px",
                              border: isSelected ? "1px solid #facc15" : "1px solid #334155",
                              background: isSelected ? "rgba(250, 204, 21, 0.08)" : "#0f172a",
                              cursor: "pointer",
                              transition: "all 0.15s ease"
                            }}
                          >
                            <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                              <input
                                type="checkbox"
                                checked={isSelected}
                                onChange={() => {}}
                                style={{ minHeight: "auto", cursor: "pointer" }}
                              />
                              <div>
                                <strong style={{ fontSize: "12px", color: "#f1f5f9", display: "block" }}>
                                  {n.node_name}
                                </strong>
                                <span style={{ fontSize: "10px", color: "#94a3b8" }}>
                                  {n.gpu_model || "CPU Compute"} · {n.gpu_vram_bytes ? `${(n.gpu_vram_bytes / 1e9).toFixed(1)} GB VRAM` : `${n.cpu_threads || 1} Threads`}
                                </span>
                              </div>
                            </div>
                            <span className={`job-state ${n.status}`} style={{ fontSize: "10px", padding: "2px 6px" }}>
                              {n.status}
                            </span>
                          </div>
                        );
                      })}
                    </div>
                  )}

                  <small style={{ color: "#facc15", fontSize: "11px", display: "block", marginTop: "6px" }}>
                    ✓ 1 Task will be pinned and dispatched directly to each selected worker.
                  </small>
                </div>
              )}


            </div>

            <button className="primary-button" type="submit" style={{ marginTop: "6px" }}>Create task-parallel job</button>
          </form>
        </section>
      </div>

      <section className="job-section">
        <div className="section-heading">
          <div><span className="eyebrow">Execution history</span><h2>Jobs</h2></div>
          <button className="icon-button" title="Refresh jobs" onClick={onRefresh}><RefreshCw size={17} /></button>
        </div>
        {jobs.length === 0 ? (
          <div className="empty-state">
            <Database size={24} />
            <strong>No jobs yet</strong>
            <p>Upload a package or use the direct demo task contract to create your first job.</p>
          </div>
        ) : (
          <div className="job-list">
            {jobs.map((job) => (
              <div className={`job-row ${selectedJobId === job.id ? "selected" : ""}`} key={job.id} role="button" tabIndex={0} onClick={() => void inspectJob(job.id)} onKeyDown={(event) => { if (event.key === "Enter") void inspectJob(job.id); }}>
                <div>
                  <strong>{job.name}</strong>
                  <span>{job.execution_mode} · {job.scheduling_policy || "automatic"} · {job.task_count} tasks</span>
                </div>
                <div className="job-progress">
                  <span>{job.succeeded_count}/{job.task_count} complete</span>
                  <div><i style={{ width: `${job.task_count ? (job.succeeded_count / job.task_count) * 100 : 0}%` }} /></div>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                  <span className={`job-state ${job.state}`}>{job.state}</span>
                  {job.state === "completed" && (
                    <button
                      type="button"
                      title="Download combined Output Bundle (.zip)"
                      onClick={(e) => { e.stopPropagation(); void downloadBundle(job.id); }}
                      style={{ background: "rgba(211, 243, 107, 0.15)", border: "1px solid #d3f36b", color: "#d3f36b", cursor: "pointer", padding: "3px 8px", borderRadius: "4px", display: "inline-flex", alignItems: "center", gap: "4px", fontSize: "11px", fontWeight: 700 }}
                    >
                      <Download size={12} /> Output .zip
                    </button>
                  )}
                  <button
                    type="button"
                    title="Delete job record"
                    onClick={(e) => { e.stopPropagation(); void deleteJob(job.id); }}
                    style={{ background: "transparent", border: "none", color: "#ef4444", cursor: "pointer", padding: "4px", borderRadius: "4px", display: "inline-flex", alignItems: "center" }}
                  >
                    <Trash2 size={15} />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}

        {selectedJobId && (
          <div className="task-inspector">
            <div className="section-heading">
              <div><span className="eyebrow">Selected job: {selectedJob?.name}</span><h2>Task detail & artifacts</h2></div>
              <div className="job-controls">
                {selectedJob?.state === "paused" && (
                  <button className="secondary-button" style={{ display: "inline-flex", alignItems: "center", gap: "6px", whiteSpace: "nowrap" }} onClick={() => void controlJob("resume")}>
                    Resume
                  </button>
                )}
                {(selectedJob?.state === "queued" || selectedJob?.state === "running") && (
                  <button className="secondary-button" style={{ display: "inline-flex", alignItems: "center", gap: "6px", whiteSpace: "nowrap" }} onClick={() => void controlJob("pause")}>
                    Pause
                  </button>
                )}
                {selectedJob && !["completed", "failed", "canceled"].includes(selectedJob.state) && (
                  <button className="revoke-button" style={{ display: "inline-flex", alignItems: "center", gap: "6px", whiteSpace: "nowrap" }} onClick={() => void controlJob("cancel")}>
                    Cancel job
                  </button>
                )}
                {selectedJob?.state === "completed" && (
                  <button
                    className="primary-button"
                    style={{
                      display: "inline-flex",
                      alignItems: "center",
                      gap: "6px",
                      background: "#d3f36b",
                      color: "#0f172a",
                      fontWeight: 700,
                      boxShadow: "0 0 14px rgba(211,243,107,0.3)",
                      whiteSpace: "nowrap",
                      minHeight: "36px",
                      padding: "8px 14px",
                      fontSize: "12px",
                      flexShrink: 0
                    }}
                    onClick={() => void downloadBundle(selectedJobId)}
                  >
                    <Download size={15} /> Download Output Bundle (.zip)
                  </button>
                )}
                {selectedJob?.state === "completed" && (
                  <button
                    className="secondary-button"
                    style={{ display: "inline-flex", alignItems: "center", gap: "6px", whiteSpace: "nowrap" }}
                    onClick={() => void downloadResult()}
                  >
                    <Download size={14} /> Result (.json)
                  </button>
                )}
                <button
                  className="secondary-button"
                  style={{ display: "inline-flex", alignItems: "center", gap: "6px", whiteSpace: "nowrap" }}
                  onClick={() => void downloadReport("markdown")}
                >
                  <FileText size={14} /> Report (.md)
                </button>
                <button
                  className="secondary-button"
                  style={{ display: "inline-flex", alignItems: "center", gap: "6px", whiteSpace: "nowrap" }}
                  onClick={() => void downloadReport("json")}
                >
                  <FileText size={14} /> Report (.json)
                </button>
                <button
                  className="revoke-button"
                  style={{ display: "inline-flex", alignItems: "center", gap: "6px", whiteSpace: "nowrap" }}
                  onClick={() => void deleteJob(selectedJobId)}
                >
                  <Trash2 size={14} /> Delete job
                </button>
              </div>
            </div>
            <div className="task-table">
              {tasks.map((task) => (
                <div className="task-row" key={task.id}>
                  <span style={{ fontWeight: 600 }}>{task.task_key}</span>
                  <span className={`job-state ${task.state}`}>{task.state}</span>
                  <span>Att {task.attempt}</span>
                  <span title={task.assigned_node_id || undefined} style={{ display: "inline-flex", alignItems: "center", gap: "5px", overflow: "hidden" }}>
                    <Cpu size={13} style={{ color: "#38bdf8", flexShrink: 0 }} />
                    <strong style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: "#e2e8f0" }}>
                      {task.assigned_node_name || (task.assigned_node_id ? task.assigned_node_id.slice(0, 8) : "Unassigned")}
                    </strong>
                  </span>
                  <span>{task.duration_seconds != null ? `${task.duration_seconds}s` : "—"}</span>
                  <div style={{ display: "flex", alignItems: "center", gap: "8px", overflow: "hidden" }}>
                    {task.state === "failed" ? (
                      <button
                        type="button"
                        onClick={() => setSelectedErrorTask(task)}
                        style={{
                          background: "rgba(239, 68, 68, 0.15)",
                          color: "#f87171",
                          border: "1px solid rgba(239, 68, 68, 0.4)",
                          borderRadius: "4px",
                          padding: "2px 8px",
                          fontSize: "11px",
                          cursor: "pointer",
                          fontWeight: 600,
                          display: "inline-flex",
                          alignItems: "center",
                          gap: "4px",
                          flexShrink: 0
                        }}
                      >
                        ⚠️ View Error / Log
                      </button>
                    ) : (
                      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {task.result ? JSON.stringify(task.result) : "No result"}
                      </span>
                    )}
                    {task.has_artifacts && (
                      <button
                        type="button"
                        onClick={() => void downloadTaskArtifacts(task.id, task.task_key)}
                        title="Download task artifact (.zip)"
                        style={{ fontSize: "11px", padding: "2px 6px", borderRadius: "4px", background: "#1e293b", color: "#38bdf8", border: "1px solid #38bdf8", cursor: "pointer", display: "inline-flex", alignItems: "center", gap: "4px", flexShrink: 0 }}
                      >
                        <Download size={11} /> Artifacts (.zip)
                      </button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Failed Task Error Modal */}
        {selectedErrorTask && (
          <div style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0, 0, 0, 0.75)",
            backdropFilter: "blur(4px)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 1000,
            padding: "20px"
          }}>
            <div style={{
              background: "#1e293b",
              border: "1px solid rgba(239, 68, 68, 0.4)",
              borderRadius: "10px",
              maxWidth: "650px",
              width: "100%",
              boxShadow: "0 20px 25px -5px rgba(0, 0, 0, 0.5)",
              overflow: "hidden"
            }}>
              <div style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                padding: "14px 18px",
                background: "rgba(239, 68, 68, 0.1)",
                borderBottom: "1px solid rgba(239, 68, 68, 0.2)"
              }}>
                <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                  <span style={{ fontSize: "18px" }}>⚠️</span>
                  <div>
                    <strong style={{ color: "#f87171", fontSize: "14px", display: "block" }}>
                      Task Failed: {selectedErrorTask.task_key}
                    </strong>
                    <span style={{ color: "#94a3b8", fontSize: "11px" }}>
                      Worker: {selectedErrorTask.assigned_node_name || selectedErrorTask.assigned_node_id || "Unknown"} · Attempt #{selectedErrorTask.attempt}
                    </span>
                  </div>
                </div>
                <button
                  onClick={() => setSelectedErrorTask(null)}
                  style={{ background: "transparent", border: "none", color: "#cbd5e1", fontSize: "20px", cursor: "pointer", lineHeight: 1 }}
                >
                  ×
                </button>
              </div>
              <div style={{ padding: "16px" }}>
                <span style={{ fontSize: "12px", color: "#94a3b8", display: "block", marginBottom: "6px", fontWeight: 600 }}>
                  Error Output & Traceback:
                </span>
                <pre style={{
                  background: "#0f172a",
                  color: "#fca5a5",
                  padding: "12px",
                  borderRadius: "6px",
                  fontSize: "12px",
                  fontFamily: "monospace",
                  overflow: "auto",
                  maxHeight: "300px",
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-all",
                  border: "1px solid #334155"
                }}>
                  {selectedErrorTask.error || "No specific error text was returned by the worker."}
                </pre>
              </div>
              <div style={{ padding: "10px 16px", background: "#0f172a", display: "flex", justifyContent: "flex-end" }}>
                <button
                  className="secondary-button"
                  onClick={() => setSelectedErrorTask(null)}
                  style={{ padding: "6px 14px", fontSize: "12px" }}
                >
                  Close
                </button>
              </div>
            </div>
          </div>
        )}
      </section>
    </>
  );
}

function WorkerConsoleView({
  token,
  user,
  clusters = [],
  selectedClusterId = "",
  onError,
}: {
  token: string;
  user?: User;
  clusters?: Cluster[];
  selectedClusterId?: string;
  onError: (message: string) => void;
}) {
  const [myNodes, setMyNodes] = useState<Node[]>([]);
  const currentHost = typeof window !== "undefined" && window.location.hostname ? window.location.hostname : "127.0.0.1";
  const currentPort = typeof window !== "undefined" && window.location.port ? window.location.port : "5000";
  const [masterHost, setMasterHost] = useState(currentHost);
  const [masterPort, setMasterPort] = useState(currentPort);
  const [detectedIps, setDetectedIps] = useState<string[]>([]);
  const [pairingCode, setPairingCode] = useState("");
  const [codeExpiresAt, setCodeExpiresAt] = useState<string | null>(null);
  const [isGeneratingCode, setIsGeneratingCode] = useState(false);
  const [copiedUrl, setCopiedUrl] = useState(false);
  const [copiedCode, setCopiedCode] = useState(false);
  const headers = { Authorization: `Bearer ${token}` };

  async function loadMyNodes() {
    try {
      setMyNodes(await api<Node[]>("/api/v1/worker/my-nodes", { headers }));
    } catch (requestError) {
      onError(requestError instanceof Error ? requestError.message : "Unable to load worker nodes");
    }
  }

  // Preflight to auto-discover Master's real private LAN IP
  useEffect(() => {
    async function detectNetwork() {
      try {
        const report = await api<NetworkReport>("/api/v1/network/preflight", {
          method: "POST",
          headers,
          body: JSON.stringify({ master_host: null, master_port: Number(masterPort) || 5000 }),
        });
        if (report?.local_private_ipv4 && report.local_private_ipv4.length > 0) {
          setDetectedIps(report.local_private_ipv4);
          if (masterHost === "localhost" || masterHost === "127.0.0.1") {
            setMasterHost(report.local_private_ipv4[0]);
          }
        }
      } catch {
        // Fallback to host
      }
    }
    void detectNetwork();
  }, []);

  useEffect(() => {
    void loadMyNodes();
    const timer = window.setInterval(() => void loadMyNodes(), 5000);
    return () => window.clearInterval(timer);
  }, []);

  async function drainNode(nodeId: string) {
    if (!window.confirm("Drain this Worker? It will finish in-flight work and stop taking new leases.")) return;
    try {
      await api(`/api/v1/worker/nodes/${nodeId}/drain`, { method: "POST", headers });
      await loadMyNodes();
    } catch (requestError) {
      onError(requestError instanceof Error ? requestError.message : "Unable to drain worker");
    }
  }

  async function deregisterNode(nodeId: string) {
    if (!window.confirm("Permanently remove this offline node record? This clears all telemetry and cluster membership for this device.")) return;
    try {
      await api(`/api/v1/worker/nodes/${nodeId}`, { method: "DELETE", headers });
      await loadMyNodes();
    } catch (requestError) {
      onError(requestError instanceof Error ? requestError.message : "Unable to deregister node");
    }
  }

  async function generatePairingCode() {
    const targetClusterId = selectedClusterId || clusters[0]?.id;
    if (!targetClusterId) {
      onError("No active cluster found to generate pairing code. Please create a cluster first in Master Workspace.");
      return;
    }
    setIsGeneratingCode(true);
    try {
      const resp = await api<PairingCode>(`/api/v1/clusters/${targetClusterId}/pairing-codes`, {
        method: "POST",
        headers,
      });
      setPairingCode(resp.code);
      setCodeExpiresAt(new Date(resp.expires_at).toLocaleTimeString());
    } catch (err) {
      onError(err instanceof Error ? err.message : "Failed to generate pairing code");
    } finally {
      setIsGeneratingCode(false);
    }
  }

  const effectiveHost = masterHost.trim() || currentHost;
  const effectivePort = masterPort.trim() || currentPort;
  const masterUrl = `http://${effectiveHost}:${effectivePort}`;

  function copyMasterUrl() {
    navigator.clipboard.writeText(masterUrl);
    setCopiedUrl(true);
    setTimeout(() => setCopiedUrl(false), 2000);
  }

  function copyCode() {
    if (!pairingCode) return;
    navigator.clipboard.writeText(pairingCode);
    setCopiedCode(true);
    setTimeout(() => setCopiedCode(false), 2000);
  }

  return (
    <section className="worker-console-view">
      {/* Compact subtitle bar — avoids duplicate heading with topbar */}
      <div style={{ display: "flex", alignItems: "center", gap: "8px", padding: "0 0 18px 0", borderBottom: "1px solid rgba(255,255,255,0.06)", marginBottom: "20px" }}>
        <Cpu size={15} style={{ color: "#d3f36b", flexShrink: 0 }} />
        <span style={{ fontSize: "13px", color: "#64748b" }}>
          Contribute your PC&apos;s GPU &amp; CPU compute to the distributed cluster — no programming or developer tools needed.
        </span>
      </div>

      {/* Primary 1-Click Desktop App Download Card */}
      <section className="worker-action-card" style={{ border: "1px solid rgba(56, 189, 248, 0.4)", background: "linear-gradient(135deg, rgba(14, 165, 233, 0.1) 0%, rgba(15, 23, 42, 0.6) 100%)" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: "16px" }}>
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "6px" }}>
              <span style={{ background: "#38bdf8", color: "#0f172a", fontSize: "11px", fontWeight: 700, padding: "2px 8px", borderRadius: "4px", textTransform: "uppercase" }}>Recommended</span>
              <h2 style={{ margin: 0, fontSize: "20px", color: "#f8fafc" }}>Standalone Desktop Worker App</h2>
            </div>
            <p style={{ margin: "0 0 14px 0", color: "#94a3b8", fontSize: "14px", maxWidth: "620px" }}>
              The easiest way to connect any PC or laptop. Standalone executable with zero prerequisites — no Python, Git, or command-line setup required. Auto-detects NVIDIA/AMD GPUs and pairs instantly.
            </p>
          </div>
          <a
            href="/api/v1/worker/download-agent"
            download="NEXORA-Worker.exe"
            className="primary-button"
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: "10px",
              padding: "14px 24px",
              fontSize: "15px",
              fontWeight: 700,
              borderRadius: "8px",
              textDecoration: "none",
              background: "#38bdf8",
              color: "#0f172a",
              boxShadow: "0 4px 14px rgba(56, 189, 248, 0.35)",
            }}
          >
            <Download size={20} /> Download NEXORA-Worker.exe (~28 MB)
          </a>
        </div>

        {/* 3 Simple Steps */}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: "12px", marginTop: "16px", paddingTop: "16px", borderTop: "1px solid rgba(255, 255, 255, 0.08)" }}>
          <div style={{ background: "rgba(0, 0, 0, 0.3)", padding: "12px 14px", borderRadius: "6px", border: "1px solid rgba(255, 255, 255, 0.05)" }}>
            <span style={{ color: "#38bdf8", fontWeight: 700, fontSize: "12px" }}>STEP 1</span>
            <h4 style={{ margin: "4px 0 2px 0", fontSize: "14px", color: "#f1f5f9" }}>Download & Run</h4>
            <p style={{ margin: 0, fontSize: "12px", color: "#94a3b8" }}>Download the <code>.exe</code> file above and double-click to open. Works on Windows 10 & 11.</p>
          </div>
          <div style={{ background: "rgba(0, 0, 0, 0.3)", padding: "12px 14px", borderRadius: "6px", border: "1px solid rgba(255, 255, 255, 0.05)" }}>
            <span style={{ color: "#38bdf8", fontWeight: 700, fontSize: "12px" }}>STEP 2</span>
            <h4 style={{ margin: "4px 0 2px 0", fontSize: "14px", color: "#f1f5f9" }}>Enter Connection Details</h4>
            <p style={{ margin: 0, fontSize: "12px", color: "#94a3b8" }}>Copy the Master URL below and enter your credentials (or leave default for auto-registration).</p>
          </div>
          <div style={{ background: "rgba(0, 0, 0, 0.3)", padding: "12px 14px", borderRadius: "6px", border: "1px solid rgba(255, 255, 255, 0.05)" }}>
            <span style={{ color: "#38bdf8", fontWeight: 700, fontSize: "12px" }}>STEP 3</span>
            <h4 style={{ margin: "4px 0 2px 0", fontSize: "14px", color: "#f1f5f9" }}>Click Connect</h4>
            <p style={{ margin: 0, fontSize: "12px", color: "#94a3b8" }}>Your GPU/CPU will be detected automatically and begin contributing compute to the cluster!</p>
          </div>
        </div>
      </section>

      {/* Connection Credentials Card */}
      <section className="worker-action-card">
        <div className="card-title">
          <Wifi size={18} />
          <div><span className="eyebrow">Connection Information</span><h2>Master Cluster Credentials</h2></div>
        </div>
        <p className="muted">Enter these connection parameters into your Desktop Worker App to pair with this master server:</p>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: "16px", margin: "16px 0" }}>
          {/* Master Server URL Box */}
          <div style={{ background: "#111827", padding: "14px", borderRadius: "8px", border: "1px solid #1f2937" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "6px" }}>
              <span style={{ fontSize: "12px", color: "#94a3b8", fontWeight: 600 }}>MASTER SERVER URL</span>
              <button
                type="button"
                onClick={copyMasterUrl}
                style={{ display: "inline-flex", alignItems: "center", gap: "4px", fontSize: "11px", padding: "3px 8px", borderRadius: "4px", background: copiedUrl ? "#10b981" : "#374151", color: "#fff", border: "none", cursor: "pointer" }}
              >
                {copiedUrl ? <Check size={12} /> : <Copy size={12} />}
                {copiedUrl ? "Copied!" : "Copy"}
              </button>
            </div>
            <code style={{ fontSize: "14px", color: "#38bdf8", wordBreak: "break-all", display: "block", marginBottom: "8px" }}>{masterUrl}</code>
            {detectedIps.length > 0 && (
              <div style={{ display: "flex", gap: "5px", flexWrap: "wrap", alignItems: "center" }}>
                <span style={{ fontSize: "11px", color: "#64748b" }}>Quick IP switch:</span>
                {detectedIps.map((ip) => (
                  <button
                    key={ip}
                    type="button"
                    onClick={() => setMasterHost(ip)}
                    style={{
                      fontSize: "11px",
                      padding: "2px 6px",
                      borderRadius: "4px",
                      background: masterHost === ip ? "#38bdf8" : "#1e293b",
                      color: masterHost === ip ? "#0f172a" : "#cbd5e1",
                      border: "1px solid #334155",
                      cursor: "pointer",
                      fontWeight: masterHost === ip ? 700 : 400,
                    }}
                  >
                    {ip}
                  </button>
                ))}
                <button
                  type="button"
                  onClick={() => setMasterHost("localhost")}
                  style={{
                    fontSize: "11px",
                    padding: "2px 6px",
                    borderRadius: "4px",
                    background: masterHost === "localhost" ? "#38bdf8" : "#1e293b",
                    color: masterHost === "localhost" ? "#0f172a" : "#cbd5e1",
                    border: "1px solid #334155",
                    cursor: "pointer",
                    fontWeight: masterHost === "localhost" ? 700 : 400,
                  }}
                >
                  localhost
                </button>
              </div>
            )}
          </div>

          {/* Pairing Code Box */}
          <div style={{ background: "#111827", padding: "14px", borderRadius: "8px", border: "1px solid #1f2937" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "6px" }}>
              <span style={{ fontSize: "12px", color: "#94a3b8", fontWeight: 600 }}>PAIRING CODE (OPTIONAL)</span>
              <div style={{ display: "flex", gap: "6px" }}>
                {(clusters.length > 0 || user?.roles.includes("master")) && (
                  <button
                    type="button"
                    onClick={generatePairingCode}
                    disabled={isGeneratingCode}
                    title="Generate a fresh, live pairing code"
                    style={{ fontSize: "11px", padding: "3px 8px", borderRadius: "4px", background: "#0284c7", color: "#fff", border: "none", cursor: "pointer" }}
                  >
                    {isGeneratingCode ? "..." : "⚡ Generate Code"}
                  </button>
                )}
                {pairingCode && (
                  <button
                    type="button"
                    onClick={copyCode}
                    style={{ display: "inline-flex", alignItems: "center", gap: "4px", fontSize: "11px", padding: "3px 8px", borderRadius: "4px", background: copiedCode ? "#10b981" : "#374151", color: "#fff", border: "none", cursor: "pointer" }}
                  >
                    {copiedCode ? <Check size={12} /> : <Copy size={12} />}
                    {copiedCode ? "Copied!" : "Copy"}
                  </button>
                )}
              </div>
            </div>
            {pairingCode ? (
              <div>
                <div style={{ display: "flex", alignItems: "baseline", gap: "8px" }}>
                  <code style={{ fontSize: "20px", color: "#4ade80", letterSpacing: "2px", fontWeight: 700 }}>{pairingCode}</code>
                  <button
                    type="button"
                    onClick={() => { setPairingCode(""); setCodeExpiresAt(null); }}
                    style={{ background: "transparent", border: "none", color: "#94a3b8", fontSize: "11px", cursor: "pointer", textDecoration: "underline" }}
                  >
                    Clear (Switch to Open Join)
                  </button>
                </div>
                <small style={{ color: "#64748b", fontSize: "11px" }}>Expires at {codeExpiresAt ?? "in 10 min"}</small>
              </div>
            ) : (
              <div>
                <div style={{ color: "#4ade80", fontSize: "14px", fontWeight: 600, marginTop: "2px" }}>Open Pairing Active</div>
                <small style={{ color: "#94a3b8", fontSize: "11px" }}>No pairing code required. The Desktop App will pair directly with the active cluster.</small>
              </div>
            )}
          </div>

          {/* Worker Account Box */}
          <div style={{ background: "#111827", padding: "14px", borderRadius: "8px", border: "1px solid #1f2937" }}>
            <span style={{ fontSize: "12px", color: "#94a3b8", fontWeight: 600, display: "block", marginBottom: "6px" }}>YOUR WORKER ACCOUNT</span>
            <div style={{ fontSize: "14px", color: "#f8fafc", fontWeight: 600 }}>{user?.username ?? "worker1"}</div>
            <small style={{ color: "#94a3b8", fontSize: "11px", display: "block", marginTop: "4px" }}>
              Enter your username and password in the Desktop App to link your compute node to your account.
            </small>
          </div>
        </div>
      </section>

      {/* Connected Workstations */}
      <div className="section-heading">
        <div><span className="eyebrow">Local Workstations</span><h2>My Connected Nodes</h2></div>
        <button className="icon-button" title="Refresh my nodes" onClick={() => void loadMyNodes()}><RefreshCw size={17} /></button>
      </div>

      {myNodes.length === 0 ? (
        <div className="empty-state">
          <Cpu size={24} />
          <strong>No Worker Nodes connected under this account</strong>
          <p>Download <strong>NEXORA-Worker.exe</strong> above and click Connect. Your computer's GPU & CPU will appear here and in the Master's cluster live.</p>
        </div>
      ) : (
        <div className="node-grid">
          {myNodes.map((node) => (
            <article className="node-card" key={node.id}>
              <div className="node-header">
                <div className="node-icon"><Cpu size={18} /></div>
                <div>
                  <h3>{node.node_name}</h3>
                  <span>{node.hostname}{node.private_ipv4 ? ` · ${node.private_ipv4}` : ""}</span>
                </div>
                <span className={`status ${node.status}`}>{node.status}</span>
              </div>
              <div className="node-gpu">
                <span>GPU Acceleration</span>
                <strong>{node.gpu_model ?? "CPU Mode"}</strong>
                <small>{node.compute_backend ?? "No backend"} · {node.gpu_vram_bytes ? `${(node.gpu_vram_bytes / 1e9).toFixed(1)} GB VRAM` : "VRAM unavailable"}</small>
              </div>
              <div className="node-metrics">
                <Metric label="CPU" value={node.cpu_utilization == null ? "Unavailable" : `${node.cpu_utilization.toFixed(1)}%`} />
                <Metric label="RAM" value={node.ram_utilization == null ? "Unavailable" : `${node.ram_utilization.toFixed(1)}%`} />
                <Metric label="Status" value={node.status} tone={node.status === "online" ? "green" : "muted"} />
              </div>
              {/* Task Contribution Stats */}
              <div style={{ display: "flex", alignItems: "center", gap: "10px", padding: "8px 0 4px 0", borderTop: "1px solid rgba(255,255,255,0.05)", marginTop: "4px" }}>
                <span style={{ fontSize: "12px", color: "#64748b" }}>Tasks Processed:</span>
                <span style={{ fontSize: "12px", fontWeight: 700, color: (node.tasks_completed ?? 0) > 0 ? "#4ade80" : "#64748b" }}>
                  {node.tasks_completed ?? 0} completed
                  {(node.tasks_failed ?? 0) > 0 && <span style={{ color: "#f87171", marginLeft: "6px" }}>· {node.tasks_failed} failed</span>}
                </span>
                <span style={{ marginLeft: "auto", fontSize: "11px", padding: "2px 7px", borderRadius: "12px", background: (node.tasks_completed ?? 0) > 0 ? "rgba(74,222,128,0.12)" : "rgba(100,116,139,0.12)", color: (node.tasks_completed ?? 0) > 0 ? "#4ade80" : "#64748b", fontWeight: 600 }}>
                  {(node.tasks_completed ?? 0) > 0 ? "Active Contributor" : "Standby"}
                </span>
              </div>
              <footer>
                <span>Cluster: {node.membership_status}</span>
                {node.status === "online" ? (
                  <button className="drain-btn" onClick={() => void drainNode(node.id)}>Safe Drain</button>
                ) : (
                  <button
                    className="revoke-button"
                    onClick={() => void deregisterNode(node.id)}
                    style={{ display: "inline-flex", alignItems: "center", gap: "5px", fontSize: "12px", padding: "5px 10px" }}
                  >
                    <Trash2 size={13} /> Deregister
                  </button>
                )}
              </footer>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}

function NetworkView({ token, onError }: { token: string; onError: (message: string) => void }) {
  const [host, setHost] = useState("127.0.0.1"); const [port, setPort] = useState("5000"); const [report, setReport] = useState<NetworkReport | null>(null); const [busy, setBusy] = useState(false);
  async function run(event: React.FormEvent) { event.preventDefault(); setBusy(true); try { setReport(await api<NetworkReport>("/api/v1/network/preflight", { method: "POST", headers: { Authorization: `Bearer ${token}` }, body: JSON.stringify({ master_host: host || null, master_port: Number(port) }) })); } catch (requestError) { onError(requestError instanceof Error ? requestError.message : "Preflight failed"); } finally { setBusy(false); } }
  return <section className="network-view"><section className="hero-strip compact-hero"><div><span className="eyebrow">Trust boundary</span><h2>Check the LAN before pairing.</h2><p>Reachability is evidence for diagnosis, not permission to join a cluster.</p></div><Network size={42} className="hero-accent" /></section><div className="network-columns"><section className="form-card"><div className="card-title"><Network size={18} /><div><span className="eyebrow">Endpoint test</span><h2>Master address</h2></div></div><form onSubmit={run}><label>Hostname or private IPv4<input value={host} onChange={(event) => setHost(event.target.value)} placeholder="192.168.0.15" /></label><label>TCP port<input type="number" min="1" max="65535" value={port} onChange={(event) => setPort(event.target.value)} /></label><button className="primary-button" disabled={busy} type="submit">{busy ? "Testing…" : "Run preflight"}</button></form></section><section className="form-card diagnostic-card"><span className="eyebrow">Current machine</span><h2>Private addresses</h2>{report?.local_private_ipv4.length ? <div className="address-list">{report.local_private_ipv4.map((address) => <span key={address}>{address}</span>)}</div> : <p className="muted">Run a test to collect local interface evidence.</p>}</section></div>{report && <section className="diagnostic-result"><div><span className="eyebrow">Result</span><h2>{report.tcp_state === "succeeded" ? "Endpoint reachable" : "Endpoint not reachable"}</h2><p>{report.detail}</p></div><div className="diagnostic-stats"><Metric label="Resolution" value={report.resolution} tone={report.resolution === "succeeded" ? "green" : "muted"} /><Metric label="TCP" value={report.tcp_state} tone={report.tcp_state === "succeeded" ? "green" : "muted"} /><Metric label="Latency" value={report.latency_ms == null ? "Unavailable" : `${report.latency_ms} ms`} tone={report.latency_ms == null ? "muted" : "green"} /></div><footer>Resolved: {report.resolved_addresses.join(", ") || "none"} · Firewall rules are not changed automatically.</footer></section>}</section>;
}

function AuditView({ token, onError }: { token: string; onError: (message: string) => void }) {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  async function load() { try { setEvents(await api<AuditEvent[]>("/api/v1/audit-events", { headers: { Authorization: `Bearer ${token}` } })); } catch (requestError) { onError(requestError instanceof Error ? requestError.message : "Unable to load audit events"); } }
  useEffect(() => { void load(); }, []);
  return <section className="audit-view"><section className="hero-strip compact-hero"><div><span className="eyebrow">Security record</span><h2>Actions, accounted for.</h2><p>Recent authenticated actions from this account and their scopes.</p></div><ShieldCheck size={42} className="hero-accent" /></section><div className="section-heading"><div><span className="eyebrow">Latest 200 events</span><h2>Audit history</h2></div><button className="icon-button" title="Refresh audit history" onClick={() => void load()}><RefreshCw size={17} /></button></div>{events.length === 0 ? <div className="empty-state"><ShieldCheck size={24} /><strong>No audit events yet</strong><p>Security-relevant activity will appear here after you create a cluster or pair a Worker.</p></div> : <div className="audit-list">{events.map((event) => <div className="audit-row" key={event.id}><div className="audit-mark"><ShieldCheck size={15} /></div><div><strong>{event.action}</strong><span>{event.scope_type}{event.scope_id ? ` · ${event.scope_id}` : ""}</span></div><span className={`job-state ${event.outcome}`}>{event.outcome}</span><code>{Object.keys(event.metadata).length ? JSON.stringify(event.metadata) : "—"}</code><time>{new Date(event.created_at).toLocaleString()}</time></div>)}</div>}</section>;
}

function SettingsView({ token, onChanged, onError }: { token: string; onChanged: () => void; onError: (message: string) => void }) {
  const [currentPassword, setCurrentPassword] = useState(""); const [newPassword, setNewPassword] = useState(""); const [confirmPassword, setConfirmPassword] = useState(""); const [busy, setBusy] = useState(false); const [message, setMessage] = useState("");
  async function submit(event: React.FormEvent) { event.preventDefault(); setMessage(""); if (newPassword !== confirmPassword) { onError("New passwords do not match"); return; } setBusy(true); try { await api("/api/v1/auth/change-password", { method: "POST", headers: { Authorization: `Bearer ${token}` }, body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }) }); setMessage("Password changed. All sessions were revoked."); setCurrentPassword(""); setNewPassword(""); setConfirmPassword(""); window.setTimeout(onChanged, 900); } catch (requestError) { onError(requestError instanceof Error ? requestError.message : "Unable to change password"); } finally { setBusy(false); } }
  return <section className="settings-view"><section className="hero-strip compact-hero"><div><span className="eyebrow">Account security</span><h2>Keep access deliberate.</h2><p>Changing your password revokes every active session on this account.</p></div><Settings size={42} className="hero-accent" /></section><section className="form-card settings-card"><div className="card-title"><ShieldCheck size={18} /><div><span className="eyebrow">Credentials</span><h2>Change password</h2></div></div><form onSubmit={submit}><label>Current password<input type="password" value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} required /></label><label>New password<input type="password" minLength={12} value={newPassword} onChange={(event) => setNewPassword(event.target.value)} required /><small className="field-note">Use at least 12 characters.</small></label><label>Confirm new password<input type="password" minLength={12} value={confirmPassword} onChange={(event) => setConfirmPassword(event.target.value)} required /></label>{message && <p className="success-text">{message}</p>}<button className="primary-button" disabled={busy} type="submit">{busy ? "Updating…" : "Change password"}</button></form></section></section>;
}

function Dashboard({ token, user, onLogout }: { token: string; user: User; onLogout: () => void }) {
  const isMaster = user.roles.includes("master");
  const headers = { Authorization: `Bearer ${token}` };
  const [view, setView] = useState<"overview" | "workloads" | "worker" | "network" | "audit" | "settings">(
    isMaster ? "overview" : "worker"
  );
  const [clusters, setClusters] = useState<Cluster[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [summary, setSummary] = useState<ClusterSummary | null>(null);
  const [nodes, setNodes] = useState<Node[]>([]);
  const [telemetry, setTelemetry] = useState<Telemetry[]>([]);
  const [workloads, setWorkloads] = useState<Workload[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [clusterName, setClusterName] = useState("");
  const [pairing, setPairing] = useState<PairingCode | null>(null);
  const [error, setError] = useState("");
  const selected = useMemo(() => clusters.find((cluster) => cluster.id === selectedId), [clusters, selectedId]);

  async function loadClusters() {
    try {
      const result = await api<Cluster[]>("/api/v1/clusters", { headers });
      setClusters(result);
      setSelectedId((current) => current || result[0]?.id || "");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Unable to load clusters");
    }
  }

  async function loadNodes() {
    if (!selectedId) {
      setNodes([]); setTelemetry([]); setSummary(null); return;
    }
    try {
      setNodes(await api<Node[]>(`/api/v1/clusters/${selectedId}/nodes`, { headers }));
      setTelemetry(await api<Telemetry[]>(`/api/v1/clusters/${selectedId}/telemetry`, { headers }));
      setSummary(await api<ClusterSummary>(`/api/v1/clusters/${selectedId}/summary`, { headers }).catch(() => null));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Unable to load nodes");
    }
  }

  async function loadWorkloads() {
    try {
      setWorkloads(await api<Workload[]>("/api/v1/workloads", { headers }));
      setJobs(await api<Job[]>("/api/v1/jobs", { headers }));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Unable to load workloads");
    }
  }

  useEffect(() => { void loadClusters(); void loadWorkloads(); }, []);
  useEffect(() => {
    void loadNodes();
    const timer = window.setInterval(() => void loadNodes(), 5000);
    return () => window.clearInterval(timer);
  }, [selectedId]);

  async function createCluster(event: React.FormEvent) {
    event.preventDefault();
    if (!clusterName.trim()) return;
    try {
      const cluster = await api<Cluster>("/api/v1/clusters", { method: "POST", headers, body: JSON.stringify({ name: clusterName }) });
      setClusters((current) => [...current, cluster]);
      setSelectedId(cluster.id);
      setClusterName("");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Unable to create cluster");
    }
  }

  async function createPairingCode() {
    if (!selectedId) { setError("Select or create a cluster first"); return; }
    try {
      setPairing(await api<PairingCode>(`/api/v1/clusters/${selectedId}/pairing-codes`, { method: "POST", headers }));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Unable to create pairing code");
    }
  }

  async function approveNode(nodeId: string) {
    try {
      await api(`/api/v1/clusters/${selectedId}/members/${nodeId}/approve`, { method: "POST", headers });
      await loadNodes();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Unable to approve node");
    }
  }

  async function revokeNode(nodeId: string) {
    if (!window.confirm("Revoke this Worker? It will stop receiving heartbeats and tasks.")) return;
    try {
      await api(`/api/v1/clusters/${selectedId}/members/${nodeId}/revoke`, { method: "POST", headers });
      await loadNodes();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Unable to revoke node");
    }
  }

  async function deleteNode(nodeId: string) {
    if (!window.confirm("Permanently remove this offline node from the cluster? This deletes all telemetry and membership records for this device.")) return;
    try {
      await api(`/api/v1/clusters/${selectedId}/members/${nodeId}`, { method: "DELETE", headers });
      await loadNodes();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Unable to remove node");
    }
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="logo"><img src="/logo-64.png" alt="NEXORA" className="sidebar-logo-img" /><strong>NEXORA</strong></div>
        <div className="side-label">{isMaster ? "Master Workspace" : "Worker Station"}</div>
        {isMaster && (
          <>
            <button className={`nav-item ${view === "overview" ? "active" : ""}`} onClick={() => { setError(""); setView("overview"); }}><Activity size={17} /> Overview</button>
            <button className={`nav-item ${view === "workloads" ? "active" : ""}`} onClick={() => { setError(""); setView("workloads"); }}><Database size={17} /> Workloads & Jobs</button>
          </>
        )}
        <button className={`nav-item ${view === "worker" ? "active" : ""}`} onClick={() => { setError(""); setView("worker"); }}><Cpu size={17} /> Worker Console</button>
        <button className={`nav-item ${view === "network" ? "active" : ""}`} onClick={() => { setError(""); setView("network"); }}><Network size={17} /> Network map</button>
        {isMaster && (
          <button className={`nav-item ${view === "audit" ? "active" : ""}`} onClick={() => { setError(""); setView("audit"); }}><ShieldCheck size={17} /> Security log</button>
        )}
        <button className={`nav-item ${view === "settings" ? "active" : ""}`} onClick={() => { setError(""); setView("settings"); }}><Settings size={17} /> Settings</button>
        <div className="sidebar-foot">
          <div className="user-chip">
            <div className="avatar">{user.full_name[0]}</div>
            <div><strong>{user.full_name}</strong><span>{user.roles.join(" / ")}</span></div>
          </div>
          <button className="icon-button" title="Sign out" onClick={onLogout}><LogOut size={17} /></button>
        </div>
      </aside>

      <main className="content">
        <header className="topbar">
          <div>
            <p className="eyebrow">
              {isMaster ? "Master Console" : "Worker Operator"} / {
                view === "overview" ? "Live Overview" :
                view === "workloads" ? "Workload & Scheduler" :
                view === "worker" ? "Local Compute Station" :
                view === "network" ? "Network Diagnostics" :
                view === "audit" ? "Security Log" : "Account Settings"
              }
            </p>
            <h1>{isMaster ? (selected?.name ?? "Your cluster") : "Worker Compute Station"}</h1>
          </div>
          <div className="top-actions">
            <span className="connection"><span className="pulse" /> API connected</span>
            <button className="icon-button" title="Refresh" onClick={() => { void loadNodes(); void loadWorkloads(); }}><RefreshCw size={17} /></button>
          </div>
        </header>

        {error && <div className="alert">{error}<button onClick={() => setError("")}>Dismiss</button></div>}

        {pairing && (
          <div className="pairing-panel">
            <div>
              <span className="eyebrow">Worker pairing code</span>
              <strong>{pairing.code}</strong>
              <span>Expires {new Date(pairing.expires_at).toLocaleTimeString()}</span>
            </div>
            <button className="icon-button" title="Close pairing code" onClick={() => setPairing(null)}>×</button>
          </div>
        )}

        {view === "overview" ? (
          <NodeOverview
            nodes={nodes}
            summary={summary}
            telemetry={telemetry}
            jobs={jobs}
            selectedId={selectedId}
            clusters={clusters}
            clusterName={clusterName}
            setSelectedId={setSelectedId}
            setClusterName={setClusterName}
            onCreateCluster={createCluster}
            onRefresh={() => void loadNodes()}
            onPair={() => void createPairingCode()}
            onApprove={(nodeId) => void approveNode(nodeId)}
            onRevoke={(nodeId) => void revokeNode(nodeId)}
            onDelete={(nodeId) => void deleteNode(nodeId)}
          />
        ) : view === "workloads" ? (
          <WorkloadsView
            token={token}
            clusterId={selectedId}
            nodes={nodes}
            workloads={workloads}
            jobs={jobs}
            onRefresh={() => { void loadWorkloads(); }}
            onError={setError}
            onNavigate={(v) => setView(v)}
          />
        ) : view === "worker" ? (
          <WorkerConsoleView
            token={token}
            user={user}
            clusters={clusters}
            selectedClusterId={selectedId}
            onError={setError}
          />
        ) : view === "network" ? (
          <NetworkView token={token} onError={setError} />
        ) : view === "audit" ? (
          <AuditView token={token} onError={setError} />
        ) : (
          <SettingsView token={token} onChanged={onLogout} onError={setError} />
        )}
      </main>
    </div>
  );
}

export default function App() {
  const [token, setToken] = useState(() => localStorage.getItem("nexora_token"));
  const [user, setUser] = useState<User | null>(() => {
    const raw = localStorage.getItem("nexora_user");
    return raw ? JSON.parse(raw) as User : null;
  });

  function login(nextToken: string, nextUser: User) {
    localStorage.setItem("nexora_token", nextToken);
    localStorage.setItem("nexora_user", JSON.stringify(nextUser));
    setToken(nextToken);
    setUser(nextUser);
  }

  async function logout() {
    if (token) await api("/api/v1/auth/logout", { method: "POST", headers: { Authorization: `Bearer ${token}` } }).catch(() => undefined);
    localStorage.clear();
    setToken(null);
    setUser(null);
  }

  return token && user ? <Dashboard token={token} user={user} onLogout={() => void logout()} /> : <Login onLogin={login} />;
}
