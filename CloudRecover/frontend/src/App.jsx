import { useCallback, useEffect, useState } from "react";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Clock3,
  Cloud,
  Database,
  FileClock,
  Play,
  RefreshCw,
  ShieldCheck,
  ScrollText,
  Server,
  ShieldAlert,
  Zap,
} from "lucide-react";
import "./App.css";

const API = "http://127.0.0.1:8000";

function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString();
}

function statusClass(value = "") {
  const status = String(value).toLowerCase();
  if (["healthy", "succeeded", "success", "ready", "completed"].includes(status)) return "healthy";
  if (["failed", "corrupted", "blocked"].includes(status)) return "failed";
  if (["pending", "running", "recovering"].includes(status)) return "pending";
  return "neutral";
}

function App() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  const loadDashboard = useCallback(async () => {
    try {
      const response = await fetch(`${API}/api/dashboard`);
      if (!response.ok) throw new Error("Could not load dashboard");
      setData(await response.json());
      setMessage("");
    } catch {
      setMessage("Cannot reach the backend. Check that FastAPI is running.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadDashboard();
  }, [loadDashboard]);

  async function post(path, body = {}) {
    setBusy(true);
    setMessage("");
    try {
      const response = await fetch(`${API}${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "Request failed");
      if (result.status === "FAILED") {
        setMessage(`Recovery blocked: ${result.reason}`);
      } else {
        setMessage(result.message || `Recovery status: ${result.status}`);
      }
      await loadDashboard();
      return result;
    } catch (error) {
      setMessage(error.message);
      return null;
    } finally {
      setBusy(false);
    }
  }

  async function simulateFailure(serviceId) {
    if (!window.confirm(`Simulate a failure for ${serviceId}?`)) return;
    const incident = await post("/api/failures", { service_id: serviceId });
    if (incident?.incident_id) {
      if (window.confirm("Failure detected. Run the recovery simulation now?")) {
        await post(`/api/recoveries/${incident.incident_id}/execute`);
      }
    }
  }

  if (loading && !data) {
    return (
      <main className="app loading-screen">
        <div className="brand-mark"><Cloud size={20} /></div>
        <p className="eyebrow">LOCAL DISASTER RECOVERY SIMULATOR</p>
        <h1>CloudRecover</h1>
        <p className="muted">Loading recovery environment…</p>
      </main>
    );
  }

  const summary = data?.summary;

  return (
    <main className="app">
      <header className="topbar">
        <a className="brand" href="#overview" aria-label="CloudRecover overview">
          <span className="brand-mark"><Cloud size={19} /></span>
          <span className="brand-name">CloudRecover<span className="brand-period">.</span></span>
        </a>
        <nav className="nav-links" aria-label="Main navigation">
          <a className="active" href="#overview">Overview</a>
          <a href="#services">Services</a>
          <a href="#incidents">Incidents</a>
          <a href="#audit">Audit reports</a>
        </nav>
        <div className="topbar-actions">
          <span className="local-indicator"><span /> Local simulation</span>
          <button className="button-primary" onClick={loadDashboard} disabled={busy}>
            <RefreshCw size={14} className={busy ? "spin" : ""} /> Refresh data
          </button>
        </div>
      </header>

      <section className="hero" id="overview">
        <div className="hero-copy">
          <p className="eyebrow"><span className="eyebrow-line" /> RESILIENCE CONTROL CENTER</p>
          <h1>Disaster recovery <span>readiness report</span></h1>
          <p className="hero-description">
            Monitor backup integrity, service readiness, and recovery activity across your local simulation environment.
          </p>
        </div>
        <div className="report-meta">
          <div className="meta-icon"><ShieldCheck size={19} /></div>
          <div>
            <span className="meta-label">ENVIRONMENT STATUS</span>
            <strong><span className="status-dot" /> Local-only mode</strong>
            <small>No live cloud resources are provisioned</small>
          </div>
        </div>
      </section>

      {message && (
        <div className={`notice ${message.toLowerCase().includes("blocked") || message.toLowerCase().includes("cannot") ? "notice-warning" : ""}`} role="status">
          <AlertTriangle size={16} />
          <span>{message}</span>
          <button className="notice-dismiss" onClick={() => setMessage("")} aria-label="Dismiss message">×</button>
        </div>
      )}

      {data && summary && (
        <>
          <section className="section-block" aria-label="Executive summary">
            <div className="section-title-row">
              <div>
                <p className="eyebrow">01 / EXECUTIVE SUMMARY</p>
                <h2>Recovery posture</h2>
              </div>
              <span className="section-note"><Activity size={13} /> Live from local API</span>
            </div>
            <div className="stats">
              <article className="metric-card">
                <div className="metric-top"><span>Monitored services</span><Server size={16} /></div>
                <div className="metric-value">{summary.total_services ?? 0}</div>
                <div className="metric-foot"><span className="metric-mark"><Activity size={12} /></span> Services tracked</div>
              </article>
              <article className="metric-card">
                <div className="metric-top"><span>Healthy services</span><CheckCircle2 size={16} /></div>
                <div className="metric-value">{summary.healthy_services ?? 0}<span className="metric-unit"> / {summary.total_services ?? 0}</span></div>
                <div className="metric-foot"><span className="green-text">●</span> Currently healthy</div>
              </article>
              <article className="metric-card">
                <div className="metric-top"><span>Open incidents</span><ShieldAlert size={16} /></div>
                <div className="metric-value">{summary.open_incidents ?? 0}</div>
                <div className="metric-foot"><span className="metric-mark amber"><AlertTriangle size={12} /></span> Requiring attention</div>
              </article>
              <article className="metric-card featured-metric">
                <div className="metric-top"><span>Average readiness</span><Database size={16} /></div>
                <div className="metric-value">{summary.average_readiness ?? 0}<span className="metric-unit">%</span></div>
                <div className="metric-progress"><span style={{ width: `${Math.max(0, Math.min(100, Number(summary.average_readiness) || 0))}%` }} /></div>
                <div className="metric-foot">Across monitored services</div>
              </article>
            </div>
          </section>

          <section className="section-block" id="services">
            <div className="section-title-row">
              <div>
                <p className="eyebrow">02 / SERVICE INVENTORY</p>
                <h2>Service readiness</h2>
                <p className="section-description">Inspect backup status and simulate a service outage to test recovery readiness.</p>
              </div>
              <span className="outline-tag"><span className="status-dot" /> SIMULATION CONTROLS</span>
            </div>
            <div className="table-panel">
              <div className="table-toolbar">
                <div className="toolbar-title"><Database size={15} /><span>Monitored services</span><span className="count-tag">{data.services?.length ?? 0}</span></div>
                <span className="table-caption">Read-only inventory · Actions affect local simulation data</span>
              </div>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Service</th>
                      <th>Service status</th>
                      <th>Readiness score</th>
                      <th>Latest backup</th>
                      <th>Recovery objectives</th>
                      <th className="action-column">Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(data.services ?? []).map((service) => (
                      <tr key={service.id}>
                        <td>
                          <div className="service-cell">
                            <span className="service-icon"><Database size={16} /></span>
                            <span><strong>{service.name}</strong><small>{service.id}</small></span>
                          </div>
                        </td>
                        <td><span className={`status-pill ${statusClass(service.status)}`}><span />{service.status}</span></td>
                        <td>
                          <div className="readiness-cell">
                            <div className="readiness-number">{service.readiness_score ?? 0}<span>%</span></div>
                            <div className="readiness-track"><span className={Number(service.readiness_score) < 60 ? "low" : ""} style={{ width: `${Math.max(0, Math.min(100, Number(service.readiness_score) || 0))}%` }} /></div>
                            <small>{service.readiness_reason}</small>
                          </div>
                        </td>
                        <td>
                          {service.latest_backup ? (
                            <div className="backup-cell">
                              <span className={`backup-icon ${statusClass(service.latest_backup.integrity)}`}><FileClock size={15} /></span>
                              <span><strong>{service.latest_backup.integrity ?? "Available"}</strong><small>{formatDate(service.latest_backup.created_at)}</small></span>
                            </div>
                          ) : <span className="muted-inline">No backup found</span>}
                        </td>
                        <td>
                          <div className="objective-cell">
                            <span><small>RPO</small><strong>{service.rpo_target_hours ?? "—"}<em>h</em></strong></span>
                            <span><small>RPO status</small><strong className={service.rpo_met ? "green-text" : "amber-text"}>{service.rpo_met == null ? "—" : service.rpo_met ? "Met" : "Missed"}</strong></span>
                            <span><small>RTO</small><strong>{service.rto_target_seconds ?? "—"}<em>s</em></strong></span>
                          </div>
                        </td>
                        <td className="action-column">
                          <button className="button-action" disabled={busy || service.status === "FAILED"} onClick={() => simulateFailure(service.id)}>
                            <Play size={12} /> Simulate
                          </button>
                        </td>
                      </tr>
                    ))}
                    {(!data.services || data.services.length === 0) && <tr><td colSpan="6" className="empty-state">No services returned by the API.</td></tr>}
                  </tbody>
                </table>
              </div>
              <div className="table-footer"><span><span className="footer-led" /> Connected to FastAPI</span><span>RPO = Recovery Point Objective · RTO = Recovery Time Objective</span></div>
            </div>
          </section>

          <div className="lower-grid">
            <section className="section-block lower-section" id="incidents">
              <div className="section-title-row">
                <div><p className="eyebrow">03 / EVENT HISTORY</p><h2>Recovery incidents</h2></div>
                <span className="small-count">{data.incidents?.length ?? 0} records</span>
              </div>
              <div className="activity-panel">
                {(data.incidents ?? []).length === 0 ? (
                  <div className="empty-state"><CheckCircle2 size={20} /><strong>No incidents recorded</strong><span>Run a service simulation to create an incident.</span></div>
                ) : (
                  <div className="incident-list">
                    {data.incidents.map((incident) => (
                      <article className="incident-row" key={incident.id}>
                        <span className={`event-icon ${statusClass(incident.recovery_status)}`}><Zap size={15} /></span>
                        <div className="incident-main">
                          <strong>{incident.service_id}</strong>
                          <span>Incident #{incident.id} · {formatDate(incident.created_at)}</span>
                        </div>
                        <div className="incident-end">
                          <span className={`status-pill ${statusClass(incident.recovery_status)}`}><span />{incident.recovery_status}</span>
                          <small>{incident.recovery_duration == null ? "Duration —" : `${incident.recovery_duration}s simulated`}</small>
                        </div>
                      </article>
                    ))}
                  </div>
                )}
              </div>
            </section>

            <section className="section-block lower-section" id="audit">
              <div className="section-title-row">
                <div><p className="eyebrow">04 / AUDIT TRAIL</p><h2>System activity</h2></div>
                <span className="small-count">{data.audit?.length ?? 0} events</span>
              </div>
              <div className="activity-panel audit-panel">
                {(data.audit ?? []).length === 0 ? (
                  <div className="empty-state"><ScrollText size={20} /><strong>No audit events</strong><span>Events will appear here as actions occur.</span></div>
                ) : (
                  <div className="audit-list">
                    {data.audit.map((event) => (
                      <article className="audit-row" key={event.id}>
                        <span className="audit-line"><span /></span>
                        <div className="audit-content">
                          <div className="audit-event-heading"><strong>{String(event.event ?? "Event").replaceAll("_", " ")}</strong><time>{formatDate(event.created_at)}</time></div>
                          <p>{event.details}</p>
                        </div>
                      </article>
                    ))}
                  </div>
                )}
              </div>
            </section>
          </div>
        </>
      )}

      <footer className="app-footer">
        <div className="footer-brand"><span className="brand-mark small"><Cloud size={13} /></span><strong>CloudRecover</strong><span>Local disaster recovery simulator</span></div>
        <div className="footer-right"><Clock3 size={13} /> Recovery durations are simulated <span className="footer-separator">/</span> No live AWS resources</div>
      </footer>
    </main>
  );
}

export default App;
