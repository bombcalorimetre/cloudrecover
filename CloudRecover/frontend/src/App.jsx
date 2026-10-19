
import { useCallback, useEffect, useState } from "react";
import {
  Activity,
  AlertTriangle,
  Database,
  RefreshCw,
  ShieldCheck,
  Play,
  Cloud,
  Clock,
  ScrollText,
} from "lucide-react";
import "./App.css";

const API = "http://127.0.0.1:8000";

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

      if (!response.ok) {
        throw new Error(result.detail || "Request failed");
      }

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
    const confirmed = window.confirm(
      `Simulate a failure for ${serviceId}?`
    );

    if (!confirmed) return;

    const incident = await post("/api/failures", {
      service_id: serviceId,
    });

    if (incident?.incident_id) {
      const runRecovery = window.confirm(
        "Failure detected. Run the recovery simulation now?"
      );

      if (runRecovery) {
        await post(
          `/api/recoveries/${incident.incident_id}/execute`
        );
      }
    }
  }

  if (loading && !data) {
    return <main className="app"><h1>CloudRecover</h1><p>Loading dashboard...</p></main>;
  }

  const summary = data?.summary;

  return (
    <main className="app">
      <header className="topbar">
        <div className="brand">
          <div className="brand-icon"><Cloud size={27} /></div>
          <div>
            <p className="eyebrow">CLOUD RESILIENCE PLATFORM</p>
            <h1>CloudRecover</h1>
            <p className="muted">Disaster recovery and backup readiness</p>
          </div>
        </div>
        <button className="secondary-button" onClick={loadDashboard} disabled={busy}>
          <RefreshCw size={16} /> Refresh
        </button>
      </header>

      {message && <div className="notice">{message}</div>}

      {data && (
        <>
          <section className="stats">
            {[
              { label: "Monitored services", value: summary.total_services, icon: Activity },
              { label: "Healthy services", value: summary.healthy_services, icon: ShieldCheck },
              { label: "Open incidents", value: summary.open_incidents, icon: AlertTriangle },
              { label: "Average readiness", value: `${summary.average_readiness}%`, icon: Database },
            ].map(({ label, value, icon: Icon }) => (
              <article className="panel stat" key={label}>
                <div className="stat-icon"><Icon size={20} /></div>
                <p>{label}</p>
                <h2>{value}</h2>
              </article>
            ))}
          </section>

          <section className="panel">
            <div className="section-heading">
              <div>
                <h2>Service health</h2>
                <p className="muted">Simulate an outage to test recovery readiness.</p>
              </div>
              <span className="demo-label">LOCAL SIMULATION</span>
            </div>

            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Service</th>
                    <th>Health</th>
                    <th>Readiness</th>
                    <th>Latest backup</th>
                    <th>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {data.services.map((service) => (
                    <tr key={service.id}>
                      <td>
                        <strong>{service.name}</strong>
                        <small>{service.id}</small>
                      </td>
                      <td>
                        <span className={`pill ${service.status.toLowerCase()}`}>
                          {service.status}
                        </span>
                      </td>
                      <td>
                        <div className="score">
                          <div className="track">
                            <div className="fill" style={{ width: `${service.readiness_score}%` }} />
                          </div>
                          <strong>{service.readiness_score}%</strong>
                        </div>
                        <small>{service.readiness_reason}</small>
                      </td>
                      <td>
                        {service.latest_backup
                          ? new Date(service.latest_backup.created_at).toLocaleString()
                          : "No backup"}
                      </td>
                      <td>
                        <button
                          disabled={busy || service.status === "FAILED"}
                          onClick={() => simulateFailure(service.id)}
                        >
                          <Play size={14} /> Simulate
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <div className="lower-grid">
            <section className="panel">
              <div className="section-heading">
                <h2><AlertTriangle size={18} /> Recovery incidents</h2>
              </div>
              {data.incidents.length === 0 ? (
                <p className="muted empty">No incidents yet. Start a simulation above.</p>
              ) : (
                <div className="incident-list">
                  {data.incidents.map((incident) => (
                    <article className="incident" key={incident.id}>
                      <div>
                        <strong>{incident.service_id}</strong>
                        <small>Incident {incident.id}</small>
                        <small>{new Date(incident.created_at).toLocaleString()}</small>
                      </div>
                      <div className="incident-result">
                        <span className={`pill ${incident.recovery_status.toLowerCase()}`}>
                          {incident.recovery_status}
                        </span>
                        <small>
                          {incident.recovery_duration == null
                            ? "Duration —"
                            : `${incident.recovery_duration}s simulated`}
                        </small>
                      </div>
                    </article>
                  ))}
                </div>
              )}
            </section>

            <section className="panel">
              <div className="section-heading">
                <h2><ScrollText size={18} /> Audit timeline</h2>
              </div>
              <div className="audit-list">
                {data.audit.map((event) => (
                  <article className="audit-event" key={event.id}>
                    <span className="audit-dot" />
                    <div>
                      <strong>{event.event.replaceAll("_", " ")}</strong>
                      <p>{event.details}</p>
                      <small>{new Date(event.created_at).toLocaleString()}</small>
                    </div>
                  </article>
                ))}
              </div>
            </section>
          </div>

          <footer>
            <span><Clock size={14} /> Recovery durations are simulated</span>
            <span>CloudRecover · Local development mode · No live AWS resources</span>
          </footer>
        </>
      )}
    </main>
  );
}

export default App;
