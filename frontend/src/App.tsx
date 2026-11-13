import { useEffect, useMemo, useState } from "react";
import { Activity, Check, Copy, Cpu, Database, Download, FileText, FileUp, LogOut, Network, Plus, RefreshCw, Settings, ShieldCheck, Wifi, Zap } from "lucide-react";

type User = { full_name: string; username: string; email: string; roles: string[] };
type Cluster = { id: string; name: string; status: string; created_at: string };
type PairingCode = { code: string; expires_at: string };
type Workload = { id: string; name: string; content_hash: string; size_bytes: number; validation_state: string; manifest: { operation: string; backend: string; execution_mode: string }; created_at: string };
type Job = { id: string; cluster_id: string; workload_id: string | null; name: string; execution_mode: string; scheduling_policy?: string; scheduling_config?: Record<string, unknown>; state: string; task_count: number; succeeded_count: number; failed_count: number; queued_count: number; created_at: string };
type Task = { id: string; task_key: string; state: string; attempt: number; assigned_node_id: string | null; pinned_node_id?: string | null; started_at?: string | null; completed_at?: string | null; duration_seconds?: number | null; result: Record<string, unknown> | null; error: string | null };
type FinalResult = { job_id: string; aggregation: string; task_count: number; result_hash: string; results: Array<{ task_key: string; result: Record<string, unknown> }> };
type Node = { id: string; node_name: string; hostname: string; status: string; membership_status: string; private_ipv4: string | null; gpu_vendor?: string | null; gpu_model: string | null; gpu_vram_bytes: number | null; compute_backend: string | null; cpu_utilization: number | null; ram_utilization: number | null; gpu_utilization: number | null; gpu_used_vram_bytes?: number | null; last_heartbeat_at: string | null };
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
  return <main className="login-shell"><div className="login-panel"><div className="brand-mark">N</div><p className="eyebrow">Distributed compute console</p><h1>{mode === "login" ? "Welcome back." : "Create your account."}</h1><p className="muted">{mode === "login" ? "Sign in to inspect your trusted LAN cluster." : "Choose a role now. You can operate as both Master and Worker later."}</p><form onSubmit={submit}>{mode === "register" && <label>Full name<input value={fullName} onChange={(event) => setFullName(event.target.value)} required /></label>}<label>Username<input value={username} onChange={(event) => setUsername(event.target.value)} required /></label>{mode === "register" && <label>Email<input type="email" value={email} onChange={(event) => setEmail(event.target.value)} required /></label>}<label>Password<input type="password" minLength={12} value={password} onChange={(event) => setPassword(event.target.value)} required /></label>{mode === "register" && <label>Primary role<select value={role} onChange={(event) => setRole(event.target.value)}><option value="master">Master</option><option value="worker_operator">Worker Operator</option></select></label>}{error && <p className="error-text">{error}</p>}<button className="primary-button" type="submit">{mode === "login" ? "Open console" : "Create account"}</button></form><button className="mode-button" onClick={() => { setMode(mode === "login" ? "register" : "login"); setError(""); }}>{mode === "login" ? "Need an account? Register" : "Already registered? Sign in"}</button></div><div className="login-aside"><span>01 / control plane</span><strong>Every node.<br />Visible.</strong><p>Capacity, trust, and execution state in one measured surface.</p></div></main>;
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

function NodeOverview({ nodes, summary, telemetry, jobs, clusters, selectedId, clusterName, setSelectedId, setClusterName, onCreateCluster, onRefresh, onPair, onApprove, onRevoke }: { nodes: Node[]; summary: ClusterSummary | null; telemetry: Telemetry[]; jobs: Job[]; clusters: Cluster[]; selectedId: string; clusterName: string; setSelectedId: (value: string) => void; setClusterName: (value: string) => void; onCreateCluster: (event: React.FormEvent) => void; onRefresh: () => void; onPair: () => void; onApprove: (nodeId: string) => void; onRevoke: (nodeId: string) => void }) {
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
                  {node.membership_status === "pending" ? (
                    <button className="approve-button" onClick={() => onApprove(node.id)}>Approve</button>
                  ) : node.membership_status === "approved" ? (
                    <button className="revoke-button" onClick={() => onRevoke(node.id)}>Revoke</button>
                  ) : (
                    <span>Access revoked</span>
                  )}
                </footer>
              </article>
            );
          })
        )}
      </section>
    </>
  );
}

function WorkloadsView({ token, clusterId, nodes, workloads, jobs, onRefresh, onError }: { token: string; clusterId: string; nodes: Node[]; workloads: Workload[]; jobs: Job[]; onRefresh: () => void; onError: (message: string) => void }) {
  const [name, setName] = useState("Square batch");
  const [operation, setOperation] = useState("square");
  const [backend, setBackend] = useState("cpu");
  const [file, setFile] = useState<File | null>(null);
  const [selectedWorkload, setSelectedWorkload] = useState("");
  const [taskJson, setTaskJson] = useState('[{"task_key":"item-1","payload":{"operation":"square","value":2}}]');
  const [jobName, setJobName] = useState("First distributed batch");
  const [schedulingPolicy, setSchedulingPolicy] = useState<"automatic" | "manual" | "hybrid">("automatic");
  const [allocations, setAllocations] = useState<Record<string, number>>({});
  const [busy, setBusy] = useState(false);
  const [selectedJobId, setSelectedJobId] = useState("");
  const [tasks, setTasks] = useState<Task[]>([]);
  const headers = { Authorization: `Bearer ${token}` };

  async function upload(event: React.FormEvent) {
    event.preventDefault();
    if (!file) { onError("Choose a ZIP package first"); return; }
    setBusy(true);
    try {
      const body = new FormData();
      body.append("manifest", JSON.stringify({ schema_version: "1", name, runtime: "builtin", backend, execution_mode: "task_parallel", operation, aggregation: "collect" }));
      body.append("file", file);
      await api<Workload>("/api/v1/workloads", { method: "POST", headers, body });
      setFile(null);
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
      const parsedTasks = JSON.parse(taskJson);
      await api<Job>("/api/v1/jobs", {
        method: "POST",
        headers,
        body: JSON.stringify({
          cluster_id: clusterId,
          workload_id: selectedWorkload || null,
          name: jobName,
          scheduling_policy: schedulingPolicy,
          scheduling_config: { allocations },
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

  const selectedJob = jobs.find((job) => job.id === selectedJobId);
  const approvedNodes = nodes.filter((n) => n.membership_status === "approved");

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

      <div className="workload-columns">
        <section className="form-card">
          <div className="card-title">
            <FileUp size={18} />
            <div><span className="eyebrow">01 / package</span><h2>Upload workload</h2></div>
          </div>
          <form onSubmit={upload}>
            <label>Package name<input value={name} onChange={(event) => setName(event.target.value)} /></label>
            <div className="form-row">
              <label>Operation
                <select value={operation} onChange={(event) => setOperation(event.target.value)}>
                  <option value="square">square</option>
                  <option value="sum">sum</option>
                  <option value="matmul">matmul (Matrix Multiply)</option>
                  <option value="identity">identity</option>
                </select>
              </label>
              <label>Backend
                <select value={backend} onChange={(event) => setBackend(event.target.value)}>
                  <option value="cpu">CPU</option>
                  <option value="opencl">OpenCL</option>
                </select>
              </label>
            </div>
            <label className="file-input">ZIP package<input type="file" accept=".zip,application/zip" onChange={(event) => setFile(event.target.files?.[0] ?? null)} /></label>
            <button className="primary-button" disabled={busy} type="submit">{busy ? "Validating…" : "Validate and store"}</button>
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
            <label>Scheduling Mode
              <select value={schedulingPolicy} onChange={(event) => setSchedulingPolicy(event.target.value as any)}>
                <option value="automatic">Mode A: Automatic (Dynamic based on VRAM/GPU/CPU)</option>
                <option value="manual">Mode B: Manual Distribution (Target specific Workers)</option>
                <option value="hybrid">Mode C: Hybrid (Target Workers for subset, auto-distribute rest)</option>
              </select>
            </label>
            {schedulingPolicy !== "automatic" && (
              <div className="allocation-box">
                <span className="eyebrow">Worker Allocation Targets (% or task count)</span>
                {approvedNodes.length === 0 ? <small className="muted">No approved workers in cluster.</small> : approvedNodes.map((n) => (
                  <div key={n.id} className="allocation-item">
                    <span>{n.node_name} ({n.gpu_model || "CPU"})</span>
                    <input
                      type="number"
                      min="0"
                      placeholder="Share / %"
                      value={allocations[n.id] ?? ""}
                      onChange={(e) => setAllocations({ ...allocations, [n.id]: Number(e.target.value) })}
                    />
                  </div>
                ))}
              </div>
            )}
            <label>Task JSON<textarea value={taskJson} onChange={(event) => setTaskJson(event.target.value)} rows={4} /></label>
            <button className="primary-button" type="submit">Create task-parallel job</button>
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
                <span className={`job-state ${job.state}`}>{job.state}</span>
              </div>
            ))}
          </div>
        )}

        {selectedJobId && (
          <div className="task-inspector">
            <div className="section-heading">
              <div><span className="eyebrow">Selected job: {selectedJob?.name}</span><h2>Task detail & artifacts</h2></div>
              <div className="job-controls">
                {selectedJob?.state === "paused" && <button className="small-button" onClick={() => void controlJob("resume")}>Resume</button>}
                {(selectedJob?.state === "queued" || selectedJob?.state === "running") && <button className="small-button" onClick={() => void controlJob("pause")}>Pause</button>}
                {selectedJob && !["completed", "failed", "canceled"].includes(selectedJob.state) && <button className="revoke-button" onClick={() => void controlJob("cancel")}>Cancel job</button>}
                {selectedJob?.state === "completed" && <button className="small-button" onClick={() => void downloadResult()}><Download size={14} /> Result</button>}
                <button className="secondary-button" onClick={() => void downloadReport("markdown")}><FileText size={14} /> Report (.md)</button>
                <button className="secondary-button" onClick={() => void downloadReport("json")}><FileText size={14} /> Report (.json)</button>
              </div>
            </div>
            <div className="task-table">
              {tasks.map((task) => (
                <div className="task-row" key={task.id}>
                  <span>{task.task_key}</span>
                  <span className={`job-state ${task.state}`}>{task.state}</span>
                  <span>Att {task.attempt}</span>
                  <span>{task.assigned_node_id ?? "Unassigned"}</span>
                  <span>{task.duration_seconds != null ? `${task.duration_seconds}s` : "—"}</span>
                  <span>{task.error ?? (task.result ? JSON.stringify(task.result) : "No result")}</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </section>
    </>
  );
}

function WorkerConsoleView({ token, onError }: { token: string; onError: (message: string) => void }) {
  const [myNodes, setMyNodes] = useState<Node[]>([]);
  const [masterHost, setMasterHost] = useState(typeof window !== "undefined" && window.location.hostname !== "localhost" && window.location.hostname !== "127.0.0.1" ? window.location.hostname : "10.229.205.230");
  const [pairingCode, setPairingCode] = useState("5A0C1B8F");
  const [nodeName, setNodeName] = useState("Souvik-PC");
  const [copied, setCopied] = useState(false);
  const headers = { Authorization: `Bearer ${token}` };

  async function loadMyNodes() {
    try {
      setMyNodes(await api<Node[]>("/api/v1/worker/my-nodes", { headers }));
    } catch (requestError) {
      onError(requestError instanceof Error ? requestError.message : "Unable to load worker nodes");
    }
  }

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

  const psCommand = `$env:NEXORA_MASTER_URL = "http://${masterHost}:5000"\n$env:NEXORA_ACCESS_TOKEN = "${token}"\n.\\scripts\\start-worker.ps1 -PairingCode "${pairingCode.trim()}" -NodeName "${nodeName.trim()}"`;

  function copyCommand() {
    navigator.clipboard.writeText(psCommand);
    setCopied(true);
    setTimeout(() => setCopied(false), 2500);
  }

  function downloadBat() {
    const batContent = `@echo off\r\ncd /d "%~dp0"\r\necho Starting NEXORA Worker Node: ${nodeName}...\r\nset NEXORA_MASTER_URL=http://${masterHost}:5000\r\nset NEXORA_ACCESS_TOKEN=${token}\r\npowershell -ExecutionPolicy Bypass -File .\\scripts\\start-worker.ps1 -MasterUrl "http://${masterHost}:5000" -PairingCode "${pairingCode.trim()}" -NodeName "${nodeName.trim()}"\r\npause\r\n`;
    const blob = new Blob([batContent], { type: "application/x-bat" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `connect-${nodeName.toLowerCase().replace(/[^a-z0-9]/g, "-") || "worker"}.bat`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  return (
    <section className="worker-console-view">
      <section className="hero-strip compact-hero">
        <div>
          <span className="eyebrow">Worker operator</span>
          <h2>Local Compute Station</h2>
          <p>Register your PC and contribute your GPU/CPU power to the NEXORA cluster.</p>
        </div>
        <Cpu size={42} className="hero-accent" />
      </section>

      <section className="worker-action-card">
        <div className="card-title">
          <Wifi size={18} />
          <div><span className="eyebrow">Quick Connect Station</span><h2>Pair This Computer as a Worker</h2></div>
        </div>
        <p className="muted">Enter the details below to generate a 1-click startup script or copy the ready-to-run command:</p>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: "12px", margin: "16px 0" }}>
          <label style={{ display: "flex", flexDirection: "column", gap: "6px", fontSize: "13px" }}>
            <span style={{ fontWeight: 600 }}>Master IP Address</span>
            <input
              type="text"
              value={masterHost}
              onChange={(e) => setMasterHost(e.target.value)}
              placeholder="10.229.205.230"
              style={{ padding: "8px 12px", borderRadius: "6px", border: "1px solid #333", background: "#111", color: "#fff" }}
            />
          </label>
          <label style={{ display: "flex", flexDirection: "column", gap: "6px", fontSize: "13px" }}>
            <span style={{ fontWeight: 600 }}>Pairing Code</span>
            <input
              type="text"
              value={pairingCode}
              onChange={(e) => setPairingCode(e.target.value)}
              placeholder="e.g. 5A0C1B8F"
              style={{ padding: "8px 12px", borderRadius: "6px", border: "1px solid #333", background: "#111", color: "#fff" }}
            />
          </label>
          <label style={{ display: "flex", flexDirection: "column", gap: "6px", fontSize: "13px" }}>
            <span style={{ fontWeight: 600 }}>Worker Node Name</span>
            <input
              type="text"
              value={nodeName}
              onChange={(e) => setNodeName(e.target.value)}
              placeholder="e.g. Souvik-PC"
              style={{ padding: "8px 12px", borderRadius: "6px", border: "1px solid #333", background: "#111", color: "#fff" }}
            />
          </label>
        </div>

        <div style={{ display: "flex", gap: "10px", flexWrap: "wrap", marginBottom: "16px" }}>
          <button className="primary-button" onClick={downloadBat} type="button" style={{ display: "inline-flex", alignItems: "center", gap: "8px" }}>
            <Download size={16} /> Download 1-Click connect.bat
          </button>
          <button className="secondary-button" onClick={copyCommand} type="button" style={{ display: "inline-flex", alignItems: "center", gap: "8px" }}>
            {copied ? <Check size={16} color="#d3f36b" /> : <Copy size={16} />}
            {copied ? "Copied to Clipboard!" : "Copy PowerShell Command"}
          </button>
        </div>

        <div className="allocation-box">
          <small style={{ color: "#888", display: "block", marginBottom: "6px" }}>Preview Command:</small>
          <code>{psCommand}</code>
        </div>
      </section>

      <div className="section-heading">
        <div><span className="eyebrow">Local Workstations</span><h2>My Connected Nodes</h2></div>
        <button className="icon-button" title="Refresh my nodes" onClick={() => void loadMyNodes()}><RefreshCw size={17} /></button>
      </div>

      {myNodes.length === 0 ? (
        <div className="empty-state">
          <Cpu size={24} />
          <strong>No Worker Nodes registered under this account</strong>
          <p>Download the <code>connect.bat</code> above and run it on this PC. Your machine will appear here and in the Master's cluster.</p>
        </div>
      ) : (
        <div className="node-grid">
          {myNodes.map((node) => (
            <article className="node-card" key={node.id}>
              <div className="node-header">
                <div className="node-icon"><Cpu size={18} /></div>
                <div>
                  <h3>{node.node_name}</h3>
                  <span>{node.hostname} · {node.private_ipv4 ?? "No IP"}</span>
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
              <footer>
                <span>Cluster: {node.membership_status}</span>
                {node.status === "online" && (
                  <button className="drain-btn" onClick={() => void drainNode(node.id)}>Safe Drain</button>
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
  const headers = { Authorization: `Bearer ${token}` };
  const [view, setView] = useState<"overview" | "workloads" | "worker" | "network" | "audit" | "settings">("overview");
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

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="logo"><span>N</span><strong>NEXORA</strong></div>
        <div className="side-label">Workspace</div>
        <button className={`nav-item ${view === "overview" ? "active" : ""}`} onClick={() => setView("overview")}><Activity size={17} /> Overview</button>
        <button className={`nav-item ${view === "workloads" ? "active" : ""}`} onClick={() => setView("workloads")}><Database size={17} /> Workloads</button>
        <button className={`nav-item ${view === "worker" ? "active" : ""}`} onClick={() => setView("worker")}><Cpu size={17} /> Worker Console</button>
        <button className={`nav-item ${view === "network" ? "active" : ""}`} onClick={() => setView("network")}><Network size={17} /> Network map</button>
        <button className={`nav-item ${view === "audit" ? "active" : ""}`} onClick={() => setView("audit")}><ShieldCheck size={17} /> Security log</button>
        <button className={`nav-item ${view === "settings" ? "active" : ""}`} onClick={() => setView("settings")}><Settings size={17} /> Settings</button>
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
              {view === "overview" ? "Master Console / Live Overview" :
               view === "workloads" ? "Master Console / Workload & Scheduler" :
               view === "worker" ? "Worker Operator Console" :
               view === "network" ? "Network Diagnostics" :
               view === "audit" ? "Security Log" : "Account Settings"}
            </p>
            <h1>{selected?.name ?? "Your cluster"}</h1>
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
          />
        ) : view === "worker" ? (
          <WorkerConsoleView token={token} onError={setError} />
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
