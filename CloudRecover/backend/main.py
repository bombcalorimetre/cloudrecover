import asyncio
import sqlite3
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
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
AUTO_SIMULATION_ENABLED = True
AUTO_SIMULATION_INTERVAL_SECONDS = 12
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
        if not AUTO_SIMULATION_ENABLED:
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
        "auto_simulation": AUTO_SIMULATION_ENABLED,
        "simulation_interval_seconds": AUTO_SIMULATION_INTERVAL_SECONDS,
    }


@app.get("/api/health")
def health():
    return {
        "status": "healthy",
        "application": "CloudRecover",
        "auto_simulation": AUTO_SIMULATION_ENABLED,
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
        total_services = len(services)
        return {
            "services": services,
            "incidents": incidents,
            "audit": audit,
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
                "enabled": AUTO_SIMULATION_ENABLED,
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


@app.post("/api/simulation/step")
def manual_simulation_step():
    """Trigger one simulation cycle manually for demos and testing."""
    return run_automatic_simulation_step()
