CloudRecover — Manual Service Recovery patch

WHAT CHANGES
- Adds POST /api/services/{service_id}/recover.
- Adds a Recover button for services whose simulated status is FAILED.
- Recovery reuses a pending incident where possible and applies the existing backup readiness/RPO checks.
- If no pending incident exists, it creates a recovery incident so a previously failed attempt can be retried.
- Successful recovery sets the simulated service status to HEALTHY, closes the incident, records the simulated recovery duration, and writes audit events.
- If backup readiness is not 100%, recovery is blocked and the response explains why.
- Does not restore real data or change a real application/cloud service.

INSTALL
1. Stop backend and frontend.
2. From the CloudRecover project root, back up files:
   cp backend/main.py backend/main.py.before-service-recovery
   cp frontend/src/App.jsx frontend/src/App.jsx.before-service-recovery
3. Extract this ZIP in the project root:
   unzip -o CloudRecover-service-recovery-patch.zip
4. Start backend:
   source .venv/bin/activate
   uvicorn main:app --app-dir backend --reload
5. Start frontend in another terminal:
   cd frontend
   npm run dev
6. Open http://localhost:5173. For a FAILED service, click Recover.

TEST
- With a FAILED service and valid, recent backup, click Recover. Expected: status becomes HEALTHY after dashboard refresh; incident and audit trail update.
- If backup is corrupted or misses the RPO target, recovery should be blocked with a reason. Repair/create a recent valid backup and retry.
- Healthy services continue to show Simulate.

This patch is based on the supplied latest CloudRecover Reports + AWS patch. Keep the backups above so you can roll back.
