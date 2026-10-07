"""SQLite storage: jobs (customer orders/lots), runs (one per battery), samples."""

import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer TEXT NOT NULL,
    po_number TEXT DEFAULT '',
    lot TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    cert_no TEXT
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER REFERENCES jobs(id),
    serial TEXT NOT NULL,
    profile TEXT NOT NULL,
    model TEXT DEFAULT '',
    channel TEXT NOT NULL,
    operator TEXT DEFAULT '',
    instrument TEXT DEFAULT '',
    rated_ah REAL, cells_series INTEGER, discharge_a REAL,
    start_soc REAL, target_soc REAL, ah_target REAL,
    status TEXT NOT NULL,               -- running | resting | complete | aborted
    result TEXT,                        -- PASS | FAIL | NULL while running
    fail_reason TEXT,
    started_at TEXT, discharge_end_at TEXT, ended_at TEXT,
    start_v REAL, end_v_loaded REAL, ocv_v REAL,
    ah_removed REAL DEFAULT 0, wh_removed REAL DEFAULT 0,
    final_soc REAL, ocv_soc_est REAL, max_temp_c REAL,
    cert_no TEXT,
    soc_limit REAL, capacity_factor REAL, worst_soc REAL, uncertainty_pts REAL,
    ah_load REAL,                       -- load's own Ah counter, when the preset reports one
    warnings TEXT,                      -- advisory notes (OCV window, cross-check), not failures
    manufacturer TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_runs_serial ON runs(serial);
CREATE INDEX IF NOT EXISTS idx_runs_job ON runs(job_id);
CREATE TABLE IF NOT EXISTS samples (
    run_id INTEGER NOT NULL REFERENCES runs(id),
    t_s REAL NOT NULL,
    phase TEXT NOT NULL,                -- discharge | rest
    voltage REAL, current REAL, ah REAL, soc REAL, temp_c REAL
);
CREATE INDEX IF NOT EXISTS idx_samples_run ON samples(run_id);
"""


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Database:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.lock = threading.Lock()
        self._migrate()

    # Columns added after the first release. CREATE TABLE IF NOT EXISTS does not
    # touch an existing table, so add them one by one when missing.
    _RUN_COLUMNS_ADDED = {
        "soc_limit": "REAL", "capacity_factor": "REAL", "worst_soc": "REAL",
        "uncertainty_pts": "REAL", "ah_load": "REAL", "warnings": "TEXT",
        "manufacturer": "TEXT DEFAULT ''",
    }

    def _migrate(self) -> None:
        have = {r[1] for r in self.conn.execute("PRAGMA table_info(runs)")}
        for col, typ in self._RUN_COLUMNS_ADDED.items():
            if col not in have:
                self.conn.execute(f"ALTER TABLE runs ADD COLUMN {col} {typ}")
        self.conn.commit()

    def _exec(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self.lock:
            cur = self.conn.execute(sql, args)
            self.conn.commit()
            return cur

    def _all(self, sql: str, args: tuple = ()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def _one(self, sql: str, args: tuple = ()) -> Optional[dict]:
        rows = self._all(sql, args)
        return rows[0] if rows else None

    # jobs
    def create_job(self, customer: str, po_number: str = "", lot: str = "", notes: str = "") -> int:
        return self._exec(
            "INSERT INTO jobs (customer, po_number, lot, notes, created_at) VALUES (?,?,?,?,?)",
            (customer, po_number, lot, notes, now_iso()),
        ).lastrowid

    def list_jobs(self) -> list[dict]:
        return self._all("""
            SELECT j.*,
              (SELECT COUNT(*) FROM runs r WHERE r.job_id=j.id) AS run_count,
              (SELECT COUNT(*) FROM runs r WHERE r.job_id=j.id AND r.result='PASS') AS pass_count,
              (SELECT COUNT(*) FROM runs r WHERE r.job_id=j.id AND r.result='FAIL') AS fail_count
            FROM jobs j ORDER BY j.id DESC""")

    def get_job(self, job_id: int) -> Optional[dict]:
        return self._one("SELECT * FROM jobs WHERE id=?", (job_id,))

    def set_job_cert(self, job_id: int, cert_no: str) -> None:
        self._exec("UPDATE jobs SET cert_no=? WHERE id=?", (cert_no, job_id))

    # runs
    def create_run(self, **fields: Any) -> int:
        cols = ", ".join(fields)
        qs = ", ".join("?" for _ in fields)
        return self._exec(f"INSERT INTO runs ({cols}) VALUES ({qs})", tuple(fields.values())).lastrowid

    def update_run(self, run_id: int, **fields: Any) -> None:
        sets = ", ".join(f"{k}=?" for k in fields)
        self._exec(f"UPDATE runs SET {sets} WHERE id=?", (*fields.values(), run_id))

    def get_run(self, run_id: int) -> Optional[dict]:
        return self._one("SELECT * FROM runs WHERE id=?", (run_id,))

    def list_runs(self, job_id: Optional[int] = None, serial: Optional[str] = None,
                  limit: int = 500) -> list[dict]:
        sql, args = "SELECT * FROM runs WHERE 1=1", []
        if job_id is not None:
            sql += " AND job_id=?"
            args.append(job_id)
        if serial:
            sql += " AND serial LIKE ?"
            args.append(f"%{serial}%")
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        return self._all(sql, tuple(args))

    def passed_runs_for_serial(self, serial: str) -> list[dict]:
        return self._all("SELECT * FROM runs WHERE serial=? AND result='PASS'", (serial,))

    def next_cert_seq(self, prefix: str) -> int:
        row = self._one(
            "SELECT COUNT(*) AS n FROM (SELECT cert_no FROM runs WHERE cert_no LIKE ? "
            "UNION ALL SELECT cert_no FROM jobs WHERE cert_no LIKE ?)",
            (prefix + "%", prefix + "%"),
        )
        return row["n"] + 1

    # samples
    def add_sample(self, run_id: int, t_s: float, phase: str, voltage: float, current: float,
                   ah: float, soc: float, temp_c: Optional[float]) -> None:
        self._exec(
            "INSERT INTO samples (run_id, t_s, phase, voltage, current, ah, soc, temp_c) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (run_id, round(t_s, 1), phase, voltage, current, ah, soc, temp_c),
        )

    def samples(self, run_id: int) -> list[dict]:
        return self._all("SELECT * FROM samples WHERE run_id=? ORDER BY t_s", (run_id,))
