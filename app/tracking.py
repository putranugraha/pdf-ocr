from __future__ import annotations

import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from .visit_numbers import is_valid_visit_number


PERIOD_PATTERN = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class TrackingError(ValueError):
    pass


class ScanTracker:
    """Persistent monthly checklist for successfully shared scan PDFs."""

    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path)
        self._init_lock = threading.Lock()
        self._initialized = False

    def _connect(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def _ensure_schema(self) -> None:
        if self._initialized:
            return
        with self._init_lock:
            if self._initialized:
                return
            with self._connect() as connection:
                connection.execute("PRAGMA journal_mode = WAL")
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS periods (
                        period TEXT PRIMARY KEY,
                        target_count INTEGER NOT NULL DEFAULT 1000,
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS settings (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS scan_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        period TEXT NOT NULL REFERENCES periods(period),
                        record_number TEXT NOT NULL,
                        visit_number TEXT NOT NULL,
                        filename TEXT NOT NULL,
                        shared_at TEXT NOT NULL,
                        UNIQUE(period, record_number, visit_number)
                    );
                    CREATE INDEX IF NOT EXISTS idx_scan_events_period_record
                        ON scan_events(period, record_number);
                    """
                )
            self._initialized = True

    @staticmethod
    def _default_period() -> str:
        return datetime.now().strftime("%Y-%m")

    @staticmethod
    def _validate_period(period: str) -> str:
        period = period.strip()
        if not PERIOD_PATTERN.fullmatch(period):
            raise TrackingError("Periode harus memakai format YYYY-MM.")
        return period

    @staticmethod
    def _normalize_record(value: str) -> str:
        record = re.sub(r"\D", "", value or "")
        if not record:
            raise TrackingError("Nomor kartu / pasien wajib diisi.")
        return record

    @staticmethod
    def _normalize_visit(value: str) -> str:
        visit = re.sub(r"[^A-Z0-9]", "", (value or "").upper())
        if not visit:
            raise TrackingError("Nomor kunjungan / resep wajib diisi.")
        if not is_valid_visit_number(visit):
            raise TrackingError("Nomor kunjungan / resep harus tepat 19 karakter huruf dan angka.")
        return visit

    def _active_period_in(self, connection: sqlite3.Connection) -> str:
        row = connection.execute(
            "SELECT value FROM settings WHERE key = 'active_period'"
        ).fetchone()
        period = row["value"] if row else self._default_period()
        now = datetime.now(timezone.utc).isoformat()
        connection.execute(
            "INSERT OR IGNORE INTO periods(period, target_count, created_at) VALUES (?, 1000, ?)",
            (period, now),
        )
        connection.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES ('active_period', ?)",
            (period,),
        )
        return period

    def active_period(self) -> str:
        self._ensure_schema()
        with self._connect() as connection:
            return self._active_period_in(connection)

    def set_active_period(self, period: str, target_count: int = 1000) -> dict:
        self._ensure_schema()
        period = self._validate_period(period)
        if not 1 <= target_count <= 100_000:
            raise TrackingError("Target harus antara 1 dan 100000.")
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO periods(period, target_count, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(period) DO UPDATE SET target_count = excluded.target_count
                """,
                (period, target_count, now),
            )
            connection.execute(
                """
                INSERT INTO settings(key, value) VALUES ('active_period', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (period,),
            )
        return self.summary(period)

    def check(self, record_number: str, visit_number: str = "") -> dict:
        self._ensure_schema()
        record = self._normalize_record(record_number)
        visit = re.sub(r"[^A-Z0-9]", "", (visit_number or "").upper())
        with self._connect() as connection:
            period = self._active_period_in(connection)
            rows = connection.execute(
                """
                SELECT id, record_number, visit_number, filename, shared_at
                FROM scan_events
                WHERE period = ? AND record_number = ?
                ORDER BY id DESC
                """,
                (period, record),
            ).fetchall()
        entries = [dict(row) for row in rows]
        exact = next((item for item in entries if visit and item["visit_number"] == visit), None)
        return {
            "period": period,
            "record_number": record,
            "visit_number": visit,
            "record_match": bool(entries),
            "exact_match": exact is not None,
            "latest": exact or (entries[0] if entries else None),
            "entries": entries,
        }

    def complete(self, record_number: str, visit_number: str, filename: str) -> dict:
        self._ensure_schema()
        record = self._normalize_record(record_number)
        visit = self._normalize_visit(visit_number)
        clean_filename = Path((filename or "").replace("\\", "/")).name.strip()
        if not clean_filename:
            raise TrackingError("Nama file wajib diisi.")
        shared_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            period = self._active_period_in(connection)
            already_completed = connection.execute(
                "SELECT 1 FROM scan_events WHERE period = ? AND record_number = ? LIMIT 1",
                (period, record),
            ).fetchone() is not None
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO scan_events(
                    period, record_number, visit_number, filename, shared_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (period, record, visit, clean_filename, shared_at),
            )
            created = cursor.rowcount == 1
        return {
            "period": period,
            "created": created,
            "newly_completed": created and not already_completed,
            "record_number": record,
            "visit_number": visit,
            "filename": clean_filename,
            "shared_at": shared_at,
        }

    def remove_record(self, record_number: str, period: str | None = None) -> int:
        self._ensure_schema()
        record = self._normalize_record(record_number)
        with self._connect() as connection:
            selected_period = (
                self._validate_period(period) if period else self._active_period_in(connection)
            )
            cursor = connection.execute(
                "DELETE FROM scan_events WHERE period = ? AND record_number = ?",
                (selected_period, record),
            )
            return cursor.rowcount

    def summary(self, period: str | None = None) -> dict:
        self._ensure_schema()
        with self._connect() as connection:
            active = self._active_period_in(connection)
            selected = self._validate_period(period) if period else active
            period_row = connection.execute(
                "SELECT target_count FROM periods WHERE period = ?", (selected,)
            ).fetchone()
            target = period_row["target_count"] if period_row else 1000
            rows = connection.execute(
                """
                SELECT e.record_number, e.visit_number, e.filename, e.shared_at
                FROM scan_events e
                JOIN (
                    SELECT record_number, MAX(id) AS latest_id
                    FROM scan_events WHERE period = ? GROUP BY record_number
                ) latest ON latest.latest_id = e.id
                ORDER BY CAST(e.record_number AS INTEGER), e.record_number
                """,
                (selected,),
            ).fetchall()
            periods = connection.execute(
                """
                SELECT p.period, p.target_count,
                       COUNT(DISTINCT e.record_number) AS completed_count
                FROM periods p
                LEFT JOIN scan_events e ON e.period = p.period
                GROUP BY p.period, p.target_count
                ORDER BY p.period DESC
                """
            ).fetchall()
        entries = [dict(row) for row in rows]
        return {
            "active_period": active,
            "period": selected,
            "is_active": selected == active,
            "target_count": target,
            "completed_count": len(entries),
            "remaining_count": max(0, target - len(entries)),
            "entries": entries,
            "periods": [dict(row) for row in periods],
        }

