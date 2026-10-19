CloudRecover — Recovery Scenario Orchestration patch

WHAT THIS ADDS
- A Recovery Drills dashboard section and navigation link.
- POST /api/drills/run endpoint that runs a repeatable, non-disruptive simulated recovery drill for a selected service.
- Five scenario stages (the initialization is also recorded as a step): simulated failure detection, backup integrity/RPO validation, recovery-point selection, simulated restore, and RTO evaluation.
- PASS/FAIL reports with step-by-step explanations, readiness score, RPO/RTO checks, and simulated duration.
- Persistent drill history in a new SQLite table (recovery_drills). Existing databases are migrated automatically by CREATE TABLE IF NOT EXISTS.
- Audit events for each drill result.

SAFETY / SIMULATION SCOPE
- This is a local simulation only. It does not call AWS, touch cloud resources, restore actual files, or change a service's live/simulated health status.
- A drill only reports a simulated restore when the newest backup passes the existing readiness check (integrity valid and RPO met).
- PASS requires a ready backup and simulated duration within the service RTO. A failed readiness check skips the restore and records a FAIL report.
- The duration is simulated (currently 12 seconds as a metric), not a wall-clock wait or a real-world measurement.

INSTALL
1. Stop the backend and frontend dev servers.
2. From your project root, back up the files that will be replaced:
   cp backend/main.py backend/main.py.before-drills
   cp frontend/src/App.jsx frontend/src/App.jsx.before-drills
   cp frontend/src/App.css frontend/src/App.css.before-drills
3. Copy this ZIP into the project root (the folder containing backend/ and frontend/).
4. Extract/overlay it from that project root:
   unzip -o CloudRecover-recovery-drills-patch.zip
5. Start the backend from the project root:
   source .venv/bin/activate
   uvicorn main:app --app-dir backend --reload
6. In another terminal:
   cd frontend
   npm run dev
7. Open http://localhost:5173 and select “Drills” in the navigation.

TEST
- Run a drill for payments-api with a recent, valid backup. With the current defaults this should PASS (RPO target 4 hours, simulated recovery 12 seconds, RTO target 30 seconds).
- Run a drill for user-database with the initial seeded backup (2 hours old). Its RPO target is 1 hour, so the drill should FAIL at backup readiness and skip restore.
- Create a new valid backup for user-database, then rerun the drill. It should pass if the backup is still within its RPO target.
- Confirm prior simulation controls, backup management, analytics, incidents, and audit trail remain available.

ROLLBACK
Stop the app and restore the three .before-drills files if needed. The new recovery_drills table can remain in SQLite; older code ignores it.
