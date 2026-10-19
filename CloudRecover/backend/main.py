import asyncio
import csv
import io
import json
import sqlite3
import uuid
import importlib.util
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "cloudrecover.db"

RPO_TARGET_HOURS = {
    "payments-api": 4,
    "user-database": 1,
    "media-storage": 24,
}

RTO_TARGET_SECONDS = {
    "payments-api": 30,
    "user-database": 15,
    "media-storage": 60,
}

MAX_BACKUP_AGE_HOURS = 24
SIMULATED_RECOVERY_SECONDS = 12.0

# Automatic, database-backed demo activity. Set to False to disable it.
AUTO_SIMULATION_ENABLED = True  # Default state when the API starts
AUTO_SIMULATION_INTERVAL_SECONDS = 12
_auto_simulation_enabled = AUTO_SIMULATION_ENABLED
_AUTO_SIMULATION_TASK = None


def current_time():
    return datetime.now(timezone.utc)


def get_db():
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    return db


def log_event(db, event, details):
    db.execute(
        "INSERT INTO audit (event, details, created_at) VALUES (?, ?, ?)",
        (event, details, current_time().isoformat()),
    )


def initialize_database():
    with get_db() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS services (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                status TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS backups (
                id TEXT PRIMARY KEY,
                service_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                integrity TEXT NOT NULL,
                recovery_point TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS incidents (
                id TEXT PRIMARY KEY,
                service_id TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                recovery_status TEXT NOT NULL,
                recovery_duration REAL
            );

            CREATE TABLE IF NOT EXISTS audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event TEXT NOT NULL,
                details TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS recovery_drills (
                id TEXT PRIMARY KEY,
                service_id TEXT NOT NULL,
                result TEXT NOT NULL,
                started_at TEXT NOT NULL,
                duration_seconds REAL NOT NULL,
                backup_id TEXT,
                report_json TEXT NOT NULL
            );
        """)

        count = db.execute("SELECT COUNT(*) FROM services").fetchone()[0]
        if count == 0:
            services = [
                ("payments-api", "Payments API", "HEALTHY"),
                ("user-database", "User Database", "HEALTHY"),
                ("media-storage", "Media Storage", "HEALTHY"),
            ]
            db.executemany("INSERT INTO services VALUES (?, ?, ?)", services)
            for service_id, _, _ in services:
                db.execute(
                    """
                    INSERT INTO backups
                    (id, service_id, created_at, integrity, recovery_point)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4())[:8],
                        service_id,
                        (current_time() - timedelta(hours=2)).isoformat(),
                        "VALID",
                        f"snapshot-{service_id}-001",
                    ),
                )
            log_event(db, "SYSTEM_INITIALIZED",
                      "CloudRecover local simulation initialized")


initialize_database()


class FailureRequest(BaseModel):
    service_id: str


class BackupCreateRequest(BaseModel):
    service_id: str
    recovery_point: str | None = None


class RecoveryDrillRequest(BaseModel):
    service_id: str


def check_backup(db, service_id):
    backup = db.execute(
        """
        SELECT * FROM backups
        WHERE service_id = ?
        ORDER BY created_at DESC LIMIT 1
        """,
        (service_id,),
    ).fetchone()
    rpo_target = RPO_TARGET_HOURS.get(service_id, 4)

    if backup is None:
        return {
            "score": 0,
            "reason": "No backup found",
            "backup": None,
            "backup_age_hours": None,
            "rpo_target_hours": rpo_target,
            "rpo_met": False,
        }

    backup_data = dict(backup)
    backup_time = datetime.fromisoformat(backup["created_at"])
    if backup_time.tzinfo is None:
        backup_time = backup_time.replace(tzinfo=timezone.utc)

    age_hours = max(
        0,
        (current_time() - backup_time).total_seconds() / 3600,
    )

    if backup["integrity"] != "VALID":
        score, reason = 0, "Backup integrity validation failed"
    elif age_hours > MAX_BACKUP_AGE_HOURS:
        score, reason = 25, "Backup is stale"
    elif age_hours > rpo_target:
        score, reason = 60, "Backup is valid but misses the RPO target"
    else:
        score, reason = 100, "Backup is valid and meets the RPO target"

    rpo_met = (
        backup["integrity"] == "VALID"
        and age_hours <= rpo_target
        and age_hours <= MAX_BACKUP_AGE_HOURS
    )
    return {
        "score": score,
        "reason": reason,
        "backup": backup_data,
        "backup_age_hours": round(age_hours, 2),
        "rpo_target_hours": rpo_target,
        "rpo_met": rpo_met,
    }


def create_failure_in_db(db, service):
    """Create one incident for a healthy service and return its ID."""
    incident_id = str(uuid.uuid4())[:8]
    db.execute(
        "UPDATE services SET status = 'FAILED' WHERE id = ?",
        (service["id"],),
    )
    db.execute(
        """
        INSERT INTO incidents
        (id, service_id, status, created_at, recovery_status, recovery_duration)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            incident_id, service["id"], "OPEN", current_time().isoformat(),
            "PENDING", None,
        ),
    )
    log_event(
        db, "FAILURE_DETECTED",
        f"{service['name']} failed; incident {incident_id} (automatic simulation)",
    )
    return incident_id


def execute_recovery_in_db(db, incident_id, automatic=False):
    """Use the same backup validation and state transitions as the API."""
    incident = db.execute(
        "SELECT * FROM incidents WHERE id = ?", (incident_id,)
    ).fetchone()
    if incident is None or incident["recovery_status"] != "PENDING":
        return None

    service_id = incident["service_id"]
    backup_check = check_backup(db, service_id)
    score = backup_check["score"]
    reason = backup_check["reason"]
    backup = backup_check["backup"]
    prefix = "automatic simulation: " if automatic else ""

    if score != 100:
        db.execute(
            "UPDATE incidents SET recovery_status = 'FAILED' WHERE id = ?",
            (incident_id,),
        )
        log_event(db, "RECOVERY_BLOCKED", f"{prefix}{incident_id}: {reason}")
        return {
            "incident_id": incident_id,
            "status": "FAILED",
            "reason": reason,
            "readiness_score": score,
            "rpo_met": backup_check["rpo_met"],
        }

    db.execute(
        "UPDATE incidents SET recovery_status = 'RUNNING' WHERE id = ?",
        (incident_id,),
    )
    log_event(db, "RECOVERY_STARTED", f"{prefix}{incident_id}")

    simulated_duration = SIMULATED_RECOVERY_SECONDS
    rto_target = RTO_TARGET_SECONDS.get(service_id, 30)
    rto_met = simulated_duration <= rto_target

    db.execute("UPDATE services SET status = 'HEALTHY' WHERE id = ?", (service_id,))
    db.execute(
        """
        UPDATE incidents
        SET status = 'CLOSED', recovery_status = 'SUCCEEDED',
            recovery_duration = ?
        WHERE id = ?
        """,
        (simulated_duration, incident_id),
    )
    log_event(
        db, "RECOVERY_SUCCEEDED",
        f"{prefix}{incident_id}: restored from {backup['recovery_point']}",
    )
    return {
        "incident_id": incident_id,
        "status": "SUCCEEDED",
        "backup_id": backup["id"],
        "recovery_point": backup["recovery_point"],
        "simulated_duration_seconds": simulated_duration,
        "rto_target_seconds": rto_target,
        "rto_met": rto_met,
        "rpo_target_hours": backup_check["rpo_target_hours"],
        "rpo_met": backup_check["rpo_met"],
    }


def run_automatic_simulation_step():
    """
    Perform one deterministic simulation cycle:
    - If an automatic incident is pending, attempt its recovery.
    - Otherwise fail the next healthy service in a rotation.
    Every write is committed to the same SQLite database used by the dashboard.
    """
    with get_db() as db:
        pending = db.execute(
            """
            SELECT i.id
            FROM incidents i
            JOIN audit a ON a.details LIKE '%' || i.id || '%'
            WHERE i.recovery_status = 'PENDING'
              AND a.event = 'FAILURE_DETECTED'
              AND a.details LIKE '%automatic simulation%'
            ORDER BY i.created_at ASC
            LIMIT 1
            """
        ).fetchone()

        if pending:
            result = execute_recovery_in_db(db, pending["id"], automatic=True)
            if result and result["status"] == "FAILED":
                log_event(
                    db, "AUTO_SIMULATION_NOTE",
                    f"Incident {pending['id']} was not recovered because backup readiness failed",
                )
            return {"action": "recovery_attempt", **(result or {})}

        # Rotate service selection by counting automatic failure events.
        auto_failures = db.execute(
            """
            SELECT COUNT(*) FROM audit
            WHERE event = 'FAILURE_DETECTED'
              AND details LIKE '%automatic simulation%'
            """
        ).fetchone()[0]
        services = db.execute(
            "SELECT * FROM services WHERE status = 'HEALTHY' ORDER BY id"
        ).fetchall()

        if not services:
            log_event(
                db, "AUTO_SIMULATION_SKIPPED",
                "No healthy service available for a new automatic failure",
            )
            return {"action": "skipped", "reason": "No healthy service available"}

        service = services[auto_failures % len(services)]
        incident_id = create_failure_in_db(db, service)
        return {
            "action": "failure_created",
            "service_id": service["id"],
            "incident_id": incident_id,
        }


async def automatic_simulation_loop():
    # Initial delay gives the API time to finish startup and become available.
    while True:
        await asyncio.sleep(AUTO_SIMULATION_INTERVAL_SECONDS)
        if not _auto_simulation_enabled:
            continue
        try:
            run_automatic_simulation_step()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Keep the API alive if a demo simulation step fails.
            try:
                with get_db() as db:
                    log_event(db, "AUTO_SIMULATION_ERROR", str(exc)[:500])
            except Exception:
                pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _AUTO_SIMULATION_TASK
    initialize_database()
    if AUTO_SIMULATION_ENABLED:
        _AUTO_SIMULATION_TASK = asyncio.create_task(automatic_simulation_loop())
    try:
        yield
    finally:
        if _AUTO_SIMULATION_TASK is not None:
            _AUTO_SIMULATION_TASK.cancel()
            try:
                await _AUTO_SIMULATION_TASK
            except asyncio.CancelledError:
                pass
            _AUTO_SIMULATION_TASK = None


app = FastAPI(
    title="CloudRecover API",
    description="Cloud disaster recovery simulation and backup readiness",
    version="1.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root():
    return {
        "application": "CloudRecover",
        "status": "running",
        "docs": "/docs",
        "auto_simulation": _auto_simulation_enabled,
        "simulation_interval_seconds": AUTO_SIMULATION_INTERVAL_SECONDS,
    }


@app.get("/api/health")
def health():
    return {
        "status": "healthy",
        "application": "CloudRecover",
        "auto_simulation": _auto_simulation_enabled,
    }


@app.get("/api/dashboard")
def dashboard():
    with get_db() as db:
        services = [dict(row) for row in db.execute("SELECT * FROM services")]
        for service in services:
            backup_check = check_backup(db, service["id"])
            service["readiness_score"] = backup_check["score"]
            service["readiness_reason"] = backup_check["reason"]
            service["latest_backup"] = backup_check["backup"]
            service["backup_age_hours"] = backup_check["backup_age_hours"]
            service["rpo_target_hours"] = backup_check["rpo_target_hours"]
            service["rpo_met"] = backup_check["rpo_met"]
            service["rto_target_seconds"] = RTO_TARGET_SECONDS.get(service["id"], 30)

        backups = [
            dict(row) for row in db.execute(
                "SELECT * FROM backups ORDER BY created_at DESC, id DESC"
            )
        ]
        for backup in backups:
            service = next((item for item in services if item["id"] == backup["service_id"]), None)
            backup["service_name"] = service["name"] if service else backup["service_id"]
            backup["readiness"] = check_backup(db, backup["service_id"])
            backup["is_latest"] = bool(
                service and service.get("latest_backup") and service["id"] == backup["service_id"]
                and service["latest_backup"]["id"] == backup["id"]
            )

        incidents = [
            dict(row) for row in db.execute(
                "SELECT * FROM incidents ORDER BY created_at DESC LIMIT 100"
            )
        ]
        audit = [
            dict(row) for row in db.execute(
                "SELECT * FROM audit ORDER BY id DESC LIMIT 30"
            )
        ]
        drills = []
        for row in db.execute("SELECT * FROM recovery_drills ORDER BY started_at DESC LIMIT 20"):
            drill = dict(row)
            try:
                drill["steps"] = json.loads(drill.pop("report_json"))
            except (TypeError, json.JSONDecodeError):
                drill["steps"] = []
            drills.append(drill)
        total_services = len(services)
        return {
            "services": services,
            "backups": backups,
            "incidents": incidents,
            "audit": audit,
            "drills": drills,
            "summary": {
                "total_services": total_services,
                "healthy_services": sum(item["status"] == "HEALTHY" for item in services),
                "open_incidents": sum(
                    item["recovery_status"] in ("PENDING", "RUNNING")
                    for item in incidents
                ),
                "average_readiness": round(
                    sum(item["readiness_score"] for item in services) / max(total_services, 1)
                ),
            },
            "auto_simulation": {
                "enabled": _auto_simulation_enabled,
                "interval_seconds": AUTO_SIMULATION_INTERVAL_SECONDS,
            },
        }


@app.post("/api/failures")
def simulate_failure(request: FailureRequest):
    with get_db() as db:
        service = db.execute(
            "SELECT * FROM services WHERE id = ?", (request.service_id,)
        ).fetchone()
        if service is None:
            raise HTTPException(404, "Service not found")
        if service["status"] == "FAILED":
            raise HTTPException(409, "Service is already failed")
        incident_id = create_failure_in_db(db, service)
        return {
            "incident_id": incident_id,
            "service_id": request.service_id,
            "status": "PENDING",
            "message": "Failure simulated successfully",
        }


@app.post("/api/services/{service_id}/recover")
def recover_service(service_id: str):
    """Bring a failed service online in the local simulation using a valid, in-RPO backup."""
    with get_db() as db:
        service = db.execute("SELECT * FROM services WHERE id = ?", (service_id,)).fetchone()
        if service is None:
            raise HTTPException(404, "Service not found")
        if service["status"] == "HEALTHY":
            return {"status": "HEALTHY", "message": f"{service['name']} is already healthy"}

        # Reuse an existing pending incident when possible; otherwise create a recovery
        # incident so a service can be recovered even after an earlier attempt failed.
        incident = db.execute(
            "SELECT * FROM incidents WHERE service_id = ? AND recovery_status = 'PENDING' ORDER BY created_at DESC LIMIT 1",
            (service_id,),
        ).fetchone()
        if incident is None:
            incident_id = str(uuid.uuid4())[:8]
            db.execute(
                "INSERT INTO incidents (id, service_id, status, created_at, recovery_status, recovery_duration) VALUES (?, ?, ?, ?, ?, ?)",
                (incident_id, service_id, "OPEN", current_time().isoformat(), "PENDING", None),
            )
            log_event(db, "MANUAL_RECOVERY_REQUESTED", f"Manual recovery requested for {service['name']}")
        else:
            incident_id = incident["id"]

        result = execute_recovery_in_db(db, incident_id)
        if result is None:
            raise HTTPException(409, "No recoverable incident is available")
        if result.get("status") == "FAILED":
            result["message"] = f"Recovery blocked: {result.get('reason', 'backup is not ready')}"
            return result
        result["message"] = f"{service['name']} is back online in the local simulation"
        log_event(db, "SERVICE_MANUALLY_RECOVERED", f"{service['name']} returned to HEALTHY via incident {incident_id}")
        return result


@app.post("/api/recoveries/{incident_id}/execute")
def execute_recovery(incident_id: str):
    with get_db() as db:
        incident = db.execute(
            "SELECT * FROM incidents WHERE id = ?", (incident_id,)
        ).fetchone()
        if incident is None:
            raise HTTPException(404, "Incident not found")
        if incident["recovery_status"] != "PENDING":
            raise HTTPException(409, "Recovery already attempted")
        return execute_recovery_in_db(db, incident_id)


@app.post("/api/backups")
def create_backup(request: BackupCreateRequest):
    """Create a new valid local backup record for a known service."""
    with get_db() as db:
        service = db.execute(
            "SELECT * FROM services WHERE id = ?", (request.service_id,)
        ).fetchone()
        if service is None:
            raise HTTPException(404, "Service not found")

        backup_id = str(uuid.uuid4())[:8]
        recovery_point = (request.recovery_point or "").strip()
        if not recovery_point:
            recovery_point = f"snapshot-{request.service_id}-{backup_id}"
        db.execute(
            "INSERT INTO backups (id, service_id, created_at, integrity, recovery_point) VALUES (?, ?, ?, ?, ?)",
            (backup_id, request.service_id, current_time().isoformat(), "VALID", recovery_point),
        )
        log_event(db, "BACKUP_CREATED", f"Backup {backup_id} created for {request.service_id}; recovery point {recovery_point}")
        return {
            "backup_id": backup_id,
            "service_id": request.service_id,
            "created_at": current_time().isoformat(),
            "integrity": "VALID",
            "recovery_point": recovery_point,
            "message": "Local backup record created",
        }


@app.get("/api/backups")
def list_backups():
    """Return backup records with service labels and readiness assessment."""
    with get_db() as db:
        rows = db.execute("SELECT * FROM backups ORDER BY created_at DESC, id DESC").fetchall()
        result = []
        for row in rows:
            item = dict(row)
            service = db.execute("SELECT name FROM services WHERE id = ?", (item["service_id"],)).fetchone()
            item["service_name"] = service["name"] if service else item["service_id"]
            item["readiness"] = check_backup(db, item["service_id"])
            latest = db.execute("SELECT id FROM backups WHERE service_id = ? ORDER BY created_at DESC, id DESC LIMIT 1", (item["service_id"],)).fetchone()
            item["is_latest"] = bool(latest and latest["id"] == item["id"])
            result.append(item)
        return {"backups": result, "count": len(result)}


@app.post("/api/backups/{backup_id}/repair")
def repair_backup(backup_id: str):
    """Mark a simulated corrupted backup as valid; this does not restore real data."""
    with get_db() as db:
        backup = db.execute("SELECT * FROM backups WHERE id = ?", (backup_id,)).fetchone()
        if backup is None:
            raise HTTPException(404, "Backup not found")
        if backup["integrity"] == "VALID":
            raise HTTPException(409, "Backup is already marked valid")
        db.execute("UPDATE backups SET integrity = 'VALID' WHERE id = ?", (backup_id,))
        log_event(db, "BACKUP_REPAIRED", f"Backup {backup_id} marked valid in local simulation")
        return {"backup_id": backup_id, "integrity": "VALID", "message": "Backup marked valid in local simulation"}


@app.post("/api/backups/{backup_id}/corrupt")
def corrupt_backup(backup_id: str):
    with get_db() as db:
        result = db.execute(
            "UPDATE backups SET integrity = 'CORRUPT' WHERE id = ?", (backup_id,)
        )
        if result.rowcount == 0:
            raise HTTPException(404, "Backup not found")
        log_event(db, "BACKUP_CORRUPTED", backup_id)
        return {"backup_id": backup_id, "integrity": "CORRUPT"}


@app.post("/api/backups/{service_id}/make-stale")
def make_backup_stale(service_id: str):
    with get_db() as db:
        result = db.execute(
            """
            UPDATE backups SET created_at = ?
            WHERE id = (
                SELECT id FROM backups
                WHERE service_id = ?
                ORDER BY created_at DESC LIMIT 1
            )
            """,
            ((current_time() - timedelta(hours=48)).isoformat(), service_id),
        )
        if result.rowcount == 0:
            raise HTTPException(404, "No backup found for this service")
        log_event(db, "BACKUP_MARKED_STALE", service_id)
        return {"service_id": service_id, "age_hours": 48}


@app.post("/api/drills/run")
def run_recovery_drill(request: RecoveryDrillRequest):
    """Run a non-disruptive recovery drill against simulated backup metadata."""
    with get_db() as db:
        service = db.execute("SELECT * FROM services WHERE id = ?", (request.service_id,)).fetchone()
        if service is None:
            raise HTTPException(404, "Service not found")

        drill_id = str(uuid.uuid4())[:8]
        started_at = current_time()
        backup_check = check_backup(db, request.service_id)
        backup = backup_check["backup"]
        rto_target = RTO_TARGET_SECONDS.get(request.service_id, 30)
        simulated_duration = SIMULATED_RECOVERY_SECONDS
        rto_met = simulated_duration <= rto_target
        steps = [
            {"name": "Scenario initialized", "status": "PASSED", "detail": f"Non-disruptive drill prepared for {service['name']}"},
            {"name": "Failure detection simulated", "status": "PASSED", "detail": "A simulated service failure was detected; live service state was not changed"},
        ]
        if backup_check["score"] == 100 and backup:
            steps.append({"name": "Backup integrity and RPO validated", "status": "PASSED", "detail": f"Latest backup is valid and {backup_check['backup_age_hours']}h old; RPO target is {backup_check['rpo_target_hours']}h"})
            steps.append({"name": "Recovery point selected", "status": "PASSED", "detail": f"Selected {backup['recovery_point']} (backup {backup['id']})"})
            steps.append({"name": "Restore simulated", "status": "PASSED", "detail": f"Simulated restore completed in {simulated_duration:.1f}s; no real data was restored"})
            steps.append({"name": "RTO objective checked", "status": "PASSED" if rto_met else "FAILED", "detail": f"{simulated_duration:.1f}s simulated recovery against {rto_target}s RTO target"})
        else:
            steps.append({"name": "Backup integrity and RPO validated", "status": "FAILED", "detail": backup_check["reason"]})
            steps.append({"name": "Recovery point selected", "status": "SKIPPED", "detail": "No recovery point was selected because backup readiness failed"})
            steps.append({"name": "Restore simulated", "status": "SKIPPED", "detail": "Restore simulation was skipped to avoid implying recovery from an unready backup"})
            steps.append({"name": "RTO objective checked", "status": "SKIPPED", "detail": "RTO is not meaningful when the backup fails readiness checks"})

        passed = backup_check["score"] == 100 and rto_met
        result = "PASS" if passed else "FAIL"
        db.execute(
            "INSERT INTO recovery_drills (id, service_id, result, started_at, duration_seconds, backup_id, report_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (drill_id, request.service_id, result, started_at.isoformat(), simulated_duration if backup_check["score"] == 100 else 0.0, backup["id"] if backup else None, json.dumps(steps)),
        )
        log_event(db, "RECOVERY_DRILL_" + result, f"Drill {drill_id} for {request.service_id}: {result}; readiness={backup_check['score']}; RTO {'met' if rto_met else 'missed' if backup_check['score'] == 100 else 'not evaluated'}")
        return {
            "drill_id": drill_id,
            "service_id": request.service_id,
            "result": result,
            "started_at": started_at.isoformat(),
            "duration_seconds": simulated_duration if backup_check["score"] == 100 else 0.0,
            "backup_id": backup["id"] if backup else None,
            "readiness_score": backup_check["score"],
            "readiness_reason": backup_check["reason"],
            "rpo_met": backup_check["rpo_met"],
            "rto_target_seconds": rto_target,
            "rto_met": rto_met if backup_check["score"] == 100 else None,
            "steps": steps,
            "message": f"Recovery drill {result.lower()} for {service['name']}",
        }


@app.post("/api/simulation/step")
def manual_simulation_step():
    """Trigger one simulation cycle manually for demos and testing."""
    return run_automatic_simulation_step()


@app.post("/api/simulation/pause")
def pause_simulation():
    """Pause periodic simulation without stopping manual Run Once actions."""
    global _auto_simulation_enabled
    _auto_simulation_enabled = False
    with get_db() as db:
        log_event(db, "SIMULATION_PAUSED", "Automatic simulation paused from dashboard")
    return {"enabled": False, "message": "Automatic simulation paused"}


@app.post("/api/simulation/resume")
def resume_simulation():
    """Resume periodic simulation at the configured interval."""
    global _auto_simulation_enabled
    _auto_simulation_enabled = True
    with get_db() as db:
        log_event(db, "SIMULATION_RESUMED", "Automatic simulation resumed from dashboard")
    return {"enabled": True, "message": "Automatic simulation resumed"}


@app.post("/api/simulation/run-once")
def run_simulation_once():
    """Run one simulation step on demand, regardless of pause state."""
    result = run_automatic_simulation_step()
    with get_db() as db:
        log_event(db, "SIMULATION_RUN_ONCE", "One simulation step triggered from dashboard")
    return {"message": "One simulation step completed", "result": result,
            "enabled": _auto_simulation_enabled}


@app.get("/api/reports/drills")
def recovery_drill_history(limit: int = Query(default=100, ge=1, le=1000)):
    """Return persisted recovery-drill history for reports and trend views."""
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM recovery_drills ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
        reports = []
        for row in rows:
            item = dict(row)
            try:
                item["steps"] = json.loads(item.pop("report_json"))
            except (TypeError, json.JSONDecodeError):
                item["steps"] = []
            reports.append(item)
        total = db.execute("SELECT COUNT(*) FROM recovery_drills").fetchone()[0]
        passed = db.execute("SELECT COUNT(*) FROM recovery_drills WHERE result = 'PASS'").fetchone()[0]
        failed = total - passed
        return {
            "reports": reports,
            "total": total,
            "passed": passed,
            "failed": failed,
            "success_rate_percent": round((passed / total) * 100, 1) if total else None,
            "returned": len(reports),
        }


@app.get("/api/reports/drills.csv")
def export_recovery_drills_csv():
    """Export all persisted drill reports and their step summaries as CSV."""
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM recovery_drills ORDER BY started_at DESC"
        ).fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["drill_id", "service_id", "result", "started_at", "duration_seconds", "backup_id", "steps"])
    for row in rows:
        try:
            steps = json.loads(row["report_json"])
            steps_text = " | ".join(
                f"{step.get('name', 'Step')}: {step.get('status', 'UNKNOWN')} — {step.get('detail', '')}"
                for step in steps
            )
        except (TypeError, json.JSONDecodeError):
            steps_text = ""
        writer.writerow([row["id"], row["service_id"], row["result"], row["started_at"], row["duration_seconds"], row["backup_id"] or "", steps_text])
    output.seek(0)
    filename = f"cloudrecover-drill-reports-{current_time().date().isoformat()}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/aws/status")
def aws_integration_status():
    """Report optional AWS SDK/credential availability without exposing secrets."""
    if importlib.util.find_spec("boto3") is None:
        return {"available": False, "sdk_installed": False, "credentials_detected": False,
                "message": "AWS SDK is not installed. Install requirements-aws.txt to enable read-only inventory."}
    try:
        import boto3
        credentials_detected = boto3.Session().get_credentials() is not None
        return {"available": credentials_detected, "sdk_installed": True,
                "credentials_detected": credentials_detected,
                "message": "AWS SDK ready; credentials detected." if credentials_detected else
                           "AWS SDK installed, but no credentials were found in the standard AWS credential chain."}
    except Exception as exc:
        return {"available": False, "sdk_installed": True, "credentials_detected": False,
                "message": f"Could not inspect AWS credential configuration: {str(exc)[:240]}"}


@app.get("/api/aws/s3/buckets")
def aws_s3_buckets():
    """Read-only S3 bucket inventory. Uses the standard AWS credential chain."""
    if importlib.util.find_spec("boto3") is None:
        raise HTTPException(503, "AWS SDK missing. Install with: pip install -r requirements-aws.txt")
    try:
        import boto3
        client = boto3.client("s3")
        response = client.list_buckets()
        buckets = [{"name": item["Name"], "created_at": item.get("CreationDate").isoformat() if item.get("CreationDate") else None}
                   for item in response.get("Buckets", [])]
        with get_db() as db:
            log_event(db, "AWS_S3_BUCKET_INVENTORY", f"Read-only inventory requested; {len(buckets)} bucket(s) returned")
        return {"buckets": buckets, "count": len(buckets), "read_only": True}
    except Exception as exc:
        # Do not return SDK internals, request headers, or credentials to the browser.
        raise HTTPException(502, f"Could not list S3 buckets. Check AWS credentials and s3:ListAllMyBuckets permission. Details: {str(exc)[:240]}")


@app.get("/api/aws/s3/objects")
def aws_s3_objects(bucket: str = Query(min_length=3, max_length=255), prefix: str = Query(default="", max_length=1024)):
    """Read-only listing of up to 1000 S3 object metadata rows for a chosen bucket."""
    if importlib.util.find_spec("boto3") is None:
        raise HTTPException(503, "AWS SDK missing. Install with: pip install -r requirements-aws.txt")
    try:
        import boto3
        client = boto3.client("s3")
        response = client.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=1000)
        objects = [{"key": item["Key"], "size_bytes": item.get("Size", 0),
                    "last_modified": item.get("LastModified").isoformat() if item.get("LastModified") else None,
                    "etag": str(item.get("ETag", "")).strip('"')}
                   for item in response.get("Contents", [])]
        with get_db() as db:
            log_event(db, "AWS_S3_OBJECT_INVENTORY", f"Read-only inventory requested for bucket {bucket}, prefix {prefix!r}; {len(objects)} object(s) returned")
        return {"bucket": bucket, "prefix": prefix, "objects": objects,
                "count": len(objects), "truncated": bool(response.get("IsTruncated")), "read_only": True}
    except Exception as exc:
        raise HTTPException(502, f"Could not list S3 objects. Check bucket access and s3:ListBucket permission. Details: {str(exc)[:240]}")
