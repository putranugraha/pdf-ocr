from fastapi.testclient import TestClient
import pytest

import app.main as main_module
from app.main import app
from app.tracking import ScanTracker


def test_tracker_detects_duplicates_without_double_counting(tmp_path):
    tracker = ScanTracker(tmp_path / "history.sqlite3")
    tracker.set_active_period("2026-10", 1000)

    first = tracker.complete("697", "220208010926Y003452", "697 220208010926Y003452.pdf")
    duplicate = tracker.complete("697", "220208010926Y003452", "697 220208010926Y003452.pdf")
    another_visit = tracker.complete("697", "022600040926Y000202", "697 022600040926Y000202.pdf")

    assert first["newly_completed"] is True
    assert duplicate["created"] is False
    assert another_visit["created"] is True
    assert another_visit["newly_completed"] is False
    assert tracker.summary()["completed_count"] == 1

    exact = tracker.check("697", "220208010926Y003452")
    assert exact["record_match"] is True
    assert exact["exact_match"] is True

    different = tracker.check("697", "999999999999Y999999")
    assert different["record_match"] is True
    assert different["exact_match"] is False


def test_new_month_keeps_old_month_as_archive(tmp_path):
    tracker = ScanTracker(tmp_path / "history.sqlite3")
    tracker.set_active_period("2026-09", 1000)
    tracker.complete("12", "220208010926Y003452", "12 220208010926Y003452.pdf")

    october = tracker.set_active_period("2026-10", 1200)
    assert october["completed_count"] == 0
    assert october["target_count"] == 1200
    assert october["active_period"] == "2026-10"

    september = tracker.summary("2026-09")
    assert september["completed_count"] == 1
    assert september["is_active"] is False
    assert {item["period"] for item in september["periods"]} == {"2026-09", "2026-10"}


def test_tracker_can_undo_one_record(tmp_path):
    tracker = ScanTracker(tmp_path / "history.sqlite3")
    tracker.set_active_period("2026-10")
    tracker.complete("697", "220208010926Y003451", "697 220208010926Y003451.pdf")
    tracker.complete("697", "220208010926Y003452", "697 220208010926Y003452.pdf")

    assert tracker.remove_record("697") == 2
    assert tracker.summary()["completed_count"] == 0
    assert tracker.remove_record("697") == 0


def test_tracking_api_uses_persistent_store(monkeypatch, tmp_path):
    tracker = ScanTracker(tmp_path / "api-history.sqlite3")
    monkeypatch.setattr(main_module, "scan_tracker", tracker)
    client = TestClient(app)

    period = client.post(
        "/api/tracking/period", data={"period": "2026-10", "target_count": "1000"}
    )
    assert period.status_code == 200

    complete = client.post(
        "/api/tracking/complete",
        data={
            "record_number": "697",
            "visit_number": "220208010926Y003452",
            "filename": "697 220208010926Y003452.pdf",
        },
    )
    assert complete.status_code == 200
    assert complete.json()["newly_completed"] is True

    check = client.get(
        "/api/tracking/check",
        params={"record_number": "697", "visit_number": "220208010926Y003452"},
    )
    assert check.status_code == 200
    assert check.json()["exact_match"] is True

    removed = client.delete("/api/tracking/record/697", params={"period": "2026-10"})
    assert removed.status_code == 200
    assert removed.json()["removed"] == 1


@pytest.mark.parametrize("number", ["220208010926Y00345", "220208010926Y0034529"])
def test_tracking_rejects_wrong_length_before_saving(monkeypatch, tmp_path, number):
    tracker = ScanTracker(tmp_path / "invalid-history.sqlite3")
    monkeypatch.setattr(main_module, "scan_tracker", tracker)
    response = TestClient(app).post("/api/tracking/complete", data={
        "record_number": "697", "visit_number": number, "filename": f"697 {number}.pdf",
    })
    assert response.status_code == 400
    assert "19 karakter" in response.json()["detail"]
    assert tracker.summary()["completed_count"] == 0

