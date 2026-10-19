CloudRecover — Backup Management Update

Files in this patch (overlay onto your existing CloudRecover project root):
- backend/main.py
- frontend/src/App.jsx
- frontend/src/App.css

Features added:
- Create a local simulated backup record for any known service.
- Optional recovery-point label; if omitted, a unique local snapshot label is generated.
- List backup records, their IDs, integrity state, recovery point, timestamp, and latest-backup marker.
- Mark a backup CORRUPT and mark it VALID again for simulation purposes.
- Mark the latest backup for a service as 48 hours old, preserving the existing endpoint.
- Add audit events for backup creation and repair.
- Existing endpoints and SQLite schema are retained; no cloud services are provisioned.

Apply the patch:
1. Back up your current backend/main.py and frontend/src/App.jsx + App.css.
2. From the CloudRecover project root, unzip this patch and allow the three files to overwrite the existing versions.
3. Start backend:
   source .venv/bin/activate
   uvicorn main:app --app-dir backend --reload
4. Start frontend in another terminal:
   cd frontend
   npm run dev
5. Open http://localhost:5173 and use Backup Management.
6. API documentation: http://127.0.0.1:8000/docs

API additions:
- POST /api/backups   body: {"service_id":"payments-api","recovery_point":"optional-label"}
- GET  /api/backups
- POST /api/backups/{backup_id}/repair

Existing backup endpoints retained:
- POST /api/backups/{backup_id}/corrupt
- POST /api/backups/{service_id}/make-stale

Important semantics:
- These are local SQLite simulation records, not actual files or AWS backups.
- “Mark valid” changes the simulated integrity flag only. It does not repair real backup data.
- A backup marked valid can still miss its service's RPO if it is too old.
- The project remains local-first and does not create billable AWS resources.


Dashboard simulation controls (added):
- Run once: triggers exactly one simulation step immediately, even when auto simulation is paused.
- Pause: stops future periodic simulation steps; it does not stop the API or dashboard refresh.
- Resume: restarts periodic simulation at the configured 12-second interval.
- The dashboard displays whether automatic simulation is running or paused.
- Pause, resume, and Run Once actions are recorded in the SQLite audit trail.
- The selected running/paused state is runtime-only and resets to the configured default when the backend restarts.
- Dashboard polling remains active every 12 seconds.


Recovery Analytics update:
- Adds a Recovery Analytics section and navigation link.
- Shows current service RPO compliance based on the existing latest-backup checks.
- Shows RTO compliance only for successful incidents with a recorded recovery_duration.
- Shows recovery success rate across terminal incidents in the dashboard's returned incident history (up to 100 records).
- Keeps the existing SQLite schema and API routes unchanged.
- Metrics describe local simulation data; recovery durations are simulated, not real AWS measurements.
- No dependencies or cloud resources are added.
