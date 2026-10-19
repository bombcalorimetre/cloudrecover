
import pytest
from fastapi.testclient import TestClient
import main


@pytest.fixture
def client(tmp_path, monkeypatch):
    test_db = tmp_path / "test_cloudrecover.db"
    monkeypatch.setattr(main, "DB_PATH", test_db)
    main.initialize_database()

    with TestClient(main.app) as test_client:
        yield test_client


def get_service(client, service_id="payments-api"):
    response = client.get("/api/dashboard")
    assert response.status_code == 200

    services = response.json()["services"]
    return next(
        (service for service in services
         if service["id"] == service_id),
        None
    )


def create_failure(client, service_id="payments-api"):
    response = client.post(
        "/api/failures",
        json={"service_id": service_id}
    )
    assert response.status_code == 200
    return response.json()["incident_id"]


def test_health_endpoint(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_initial_services_have_readiness_fields(client):
    response = client.get("/api/dashboard")
    assert response.status_code == 200

    services = response.json()["services"]
    assert len(services) == 3

    for service in services:
        assert "id" in service
        assert "readiness_score" in service
        assert "readiness_reason" in service
        assert "latest_backup" in service
        assert "rpo_met" in service
        assert "rto_target_seconds" in service


def test_fresh_backup_is_ready(client):
    service = get_service(client)
    assert service is not None
    assert service["readiness_score"] == 100
    assert service["rpo_met"] is True


def test_stale_backup_is_not_ready(client):
    response = client.post(
        "/api/backups/payments-api/make-stale"
    )
    assert response.status_code == 200

    service = get_service(client)
    assert service["readiness_score"] == 25
    assert service["rpo_met"] is False


def test_corrupted_backup_is_not_ready(client):
    service = get_service(client)
    backup_id = service["latest_backup"]["id"]

    response = client.post(
        f"/api/backups/{backup_id}/corrupt"
    )
    assert response.status_code == 200

    service = get_service(client)
    assert service["readiness_score"] == 0
    assert service["rpo_met"] is False


def test_unknown_service_failure_returns_404(client):
    response = client.post(
        "/api/failures",
        json={"service_id": "unknown-service"}
    )
    assert response.status_code == 404


def test_duplicate_failure_is_rejected(client):
    first = client.post(
        "/api/failures",
        json={"service_id": "payments-api"}
    )
    assert first.status_code == 200

    second = client.post(
        "/api/failures",
        json={"service_id": "payments-api"}
    )
    assert second.status_code == 409


def test_successful_recovery(client):
    incident_id = create_failure(client)

    response = client.post(
        f"/api/recoveries/{incident_id}/execute"
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "SUCCEEDED"
    assert data["rto_met"] is True
    assert data["rpo_met"] is True
    assert data["recovery_point"] is not None

    service = get_service(client)
    assert service["status"] == "HEALTHY"


def test_recovery_is_blocked_for_stale_backup(client):
    response = client.post(
        "/api/backups/payments-api/make-stale"
    )
    assert response.status_code == 200

    incident_id = create_failure(client)

    response = client.post(
        f"/api/recoveries/{incident_id}/execute"
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "FAILED"
    assert data["readiness_score"] == 25
    assert data["rpo_met"] is False


def test_recovery_is_blocked_for_corrupted_backup(client):
    service = get_service(client)
    backup_id = service["latest_backup"]["id"]

    response = client.post(
        f"/api/backups/{backup_id}/corrupt"
    )
    assert response.status_code == 200

    incident_id = create_failure(client)

    response = client.post(
        f"/api/recoveries/{incident_id}/execute"
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "FAILED"
    assert data["readiness_score"] == 0
    assert data["rpo_met"] is False


def test_recovery_cannot_be_executed_twice(client):
    incident_id = create_failure(client)

    first = client.post(
        f"/api/recoveries/{incident_id}/execute"
    )
    assert first.status_code == 200
    assert first.json()["status"] == "SUCCEEDED"

    second = client.post(
        f"/api/recoveries/{incident_id}/execute"
    )
    assert second.status_code == 409


def test_missing_backup_blocks_recovery(client):
    service = get_service(client)
    backup_id = service["latest_backup"]["id"]

    with main.get_db() as db:
        db.execute(
            "DELETE FROM backups WHERE id = ?",
            (backup_id,)
        )

    incident_id = create_failure(client)

    response = client.post(
        f"/api/recoveries/{incident_id}/execute"
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "FAILED"
    assert data["readiness_score"] == 0
    assert data["rpo_met"] is False
