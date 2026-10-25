import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import threading
import uuid


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.directory / "agent.sqlite3", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS jobs(
          id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, kind TEXT NOT NULL,
          payload TEXT NOT NULL, status TEXT NOT NULL, reason TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS map_targets(
          world TEXT NOT NULL, layer TEXT NOT NULL, x INTEGER NOT NULL, y INTEGER NOT NULL,
          PRIMARY KEY(world,layer,x,y));
        CREATE TABLE IF NOT EXISTS observations(
          id TEXT PRIMARY KEY, job_id TEXT NOT NULL, world TEXT NOT NULL, layer TEXT NOT NULL,
          x INTEGER NOT NULL,y INTEGER NOT NULL,fields TEXT NOT NULL,evidence TEXT NOT NULL,captured_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS job_targets(
          job_id TEXT NOT NULL,x INTEGER NOT NULL,y INTEGER NOT NULL,status TEXT NOT NULL,
          PRIMARY KEY(job_id,x,y));
        """)

    @staticmethod
    def _job(row):
        if row is None:
            raise ValueError("job_not_found")
        result = dict(row)
        result["payload"] = json.loads(result["payload"])
        return result

    def create_job(self, kind, request_id, payload):
        if not isinstance(request_id, str) or not request_id.strip():
            raise ValueError("request_id_required")
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)
        with self.lock, self.db:
            existing = self.db.execute("SELECT * FROM jobs WHERE request_id=?", (request_id,)).fetchone()
            if existing:
                if existing["kind"] != kind or existing["payload"] != encoded:
                    raise ValueError("request_conflict")
                return self._job(existing)
            identity, now = uuid.uuid4().hex, utc_now()
            self.db.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?)",
                            (identity, request_id, kind, encoded, "pending", None, now, now))
            return self.get_job(identity)

    def get_job(self, job_id):
        with self.lock:
            return self._job(self.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())

    def list_jobs(self):
        with self.lock:
            return [self._job(r) for r in self.db.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT 20")]

    def update_job(self, job_id, status, reason=None):
        if status not in {"pending", "running", "waiting", "paused", "complete", "failed"}:
            raise ValueError("invalid_status")
        with self.lock, self.db:
            if self.db.execute("UPDATE jobs SET status=?,reason=?,updated_at=? WHERE id=?",
                               (status, reason, utc_now(), job_id)).rowcount != 1:
                raise ValueError("job_not_found")
        return self.get_job(job_id)

    def recover_jobs(self):
        with self.lock, self.db:
            self.db.execute("UPDATE jobs SET status='paused',reason='interrupted',updated_at=? WHERE status IN ('pending','running','waiting')", (utc_now(),))

    def save_observation(self, job_id, world, layer, x, y, fields, evidence):
        self.get_job(job_id)
        observation_id = uuid.uuid4().hex
        with self.lock, self.db:
            self.db.execute("INSERT OR IGNORE INTO map_targets VALUES(?,?,?,?)", (world, layer, x, y))
            self.db.execute("INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?)",
                            (observation_id, job_id, world, layer, x, y,
                             json.dumps(fields, ensure_ascii=False, allow_nan=False), evidence, utc_now()))
            self.db.execute("INSERT OR REPLACE INTO job_targets VALUES(?,?,?,'verified')", (job_id, x, y))
        return observation_id

    def verified_points(self, job_id):
        with self.lock:
            return {(r[0], r[1]) for r in self.db.execute("SELECT x,y FROM job_targets WHERE job_id=? AND status='verified'", (job_id,))}

    def target_count(self):
        with self.lock:
            return self.db.execute("SELECT count(*) FROM map_targets").fetchone()[0]

    def observations(self, job_id):
        with self.lock:
            result = [dict(r) for r in self.db.execute("SELECT * FROM observations WHERE job_id=? ORDER BY rowid", (job_id,))]
        for row in result:
            row["fields"] = json.loads(row["fields"])
        return result

    def export(self, job_id, format):
        job = self.get_job(job_id)
        if format not in {"json", "csv"}:
            raise ValueError("invalid_export_format")
        folder = self.directory / "exports"
        folder.mkdir(exist_ok=True)
        path = folder / f"{job['id']}.{format}"
        records = self.observations(job_id)
        if format == "json":
            path.write_text(json.dumps({"job": job, "observations": records}, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            with path.open("w", encoding="utf-8-sig", newline="") as file:
                writer = csv.writer(file)
                writer.writerow(["world", "layer", "x", "y", "captured_at", "fields", "evidence"])
                for row in records:
                    values = [row[k] for k in ["world", "layer", "x", "y", "captured_at"]]
                    values += [json.dumps(row["fields"], ensure_ascii=False), row["evidence"]]
                    writer.writerow([("'" + v if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@")) else v) for v in values])
        return path

    def close(self):
        with self.lock:
            self.db.close()
