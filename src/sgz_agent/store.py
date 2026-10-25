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
        CREATE INDEX IF NOT EXISTS observations_region ON observations(world,layer,x,y,captured_at DESC);
        CREATE TABLE IF NOT EXISTS roster_snapshots(
          id TEXT PRIMARY KEY, world TEXT NOT NULL, snapshot TEXT NOT NULL,
          evidence TEXT NOT NULL, captured_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS roster_world ON roster_snapshots(world,captured_at DESC);
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

    def save_api_scan(self, job_id, world, rows, evidence):
        """Commit a fully verified API batch atomically with one capture timestamp."""
        self.get_job(job_id)
        stamp = utc_now()
        observations = [(uuid.uuid4().hex, job_id, world, 'surface', row['x'], row['y'],
                         json.dumps(row['fields'], ensure_ascii=False, allow_nan=False), evidence, stamp)
                        for row in rows]
        with self.lock, self.db:
            self.db.executemany('INSERT OR IGNORE INTO map_targets VALUES(?,?,?,?)',
                               [(world, 'surface', r['x'], r['y']) for r in rows])
            self.db.executemany('INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?)', observations)
            self.db.executemany("INSERT OR REPLACE INTO job_targets VALUES(?,?,?,'verified')",
                               [(job_id, r['x'], r['y']) for r in rows])

    def target_count(self):
        with self.lock:
            return self.db.execute("SELECT count(*) FROM map_targets").fetchone()[0]

    def search_map(self, world, x0, y0, x1, y1, **filters):
        from .map_query import bounds, search_rows
        if not isinstance(world, str) or not world.strip():
            raise ValueError('world_required')
        bounds(x0, y0, x1, y1)
        # Select latest BEFORE applying target/freshness predicates.
        with self.lock:
            rows = [dict(row) for row in self.db.execute('''
                SELECT * FROM (
                  SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY world,layer,x,y ORDER BY captured_at DESC,rowid DESC) AS latest
                  FROM observations WHERE world=? AND layer='surface'
                    AND x BETWEEN ? AND ? AND y BETWEEN ? AND ?
                ) WHERE latest=1 ORDER BY y,x
                ''', (world, x0, x1, y0, y1))]
        for row in rows:
            row['fields'] = json.loads(row['fields'])
        return search_rows(rows, world=world, x0=x0, y0=y0, x1=x1, y1=y1, **filters)

    def save_roster(self, world, snapshot, evidence):
        identity = uuid.uuid4().hex
        with self.lock, self.db:
            self.db.execute('INSERT INTO roster_snapshots VALUES(?,?,?,?,?)',
                (identity, world, json.dumps(snapshot, ensure_ascii=False, allow_nan=False),
                 json.dumps(evidence, ensure_ascii=False), utc_now()))
        return identity

    def query_roster(self, world, *, city_id=None, city_kind=None, status=None, max_age_seconds=300):
        from .map_query import freshness, integer
        from .roster import STATUSES
        if not isinstance(world, str) or not world.strip():
            raise ValueError('world_required')
        if city_id is not None and (not isinstance(city_id, str) or not city_id.strip()):
            raise ValueError('invalid_city_id')
        if city_kind not in (None, 'main', 'sub'):
            raise ValueError('invalid_city_kind')
        if status is not None and status not in (*STATUSES.values(), 'unknown'):
            raise ValueError('invalid_army_status')
        integer(max_age_seconds, 'max_age_seconds', 0, 31_536_000)
        with self.lock:
            row = self.db.execute('SELECT * FROM roster_snapshots WHERE world=? '
                                  'ORDER BY captured_at DESC,rowid DESC LIMIT 1', (world,)).fetchone()
            refresh = self.db.execute("SELECT id,status,reason FROM jobs WHERE kind='roster' "
                "AND json_extract(payload,'$.world')=? ORDER BY updated_at DESC,rowid DESC LIMIT 1", (world,)).fetchone()
        refresh_info = {'refresh_status': refresh['status'] if refresh else None,
                        'refresh_job_id': refresh['id'] if refresh else None,
                        'refresh_error': refresh['reason'] if refresh and refresh['status'] in {'paused','failed'} else None}
        if row is None:
            return {'world': world, 'observed': False, 'complete': False, 'stale': True,
                    'cities': [], 'armies': [], 'reason': 'roster_not_observed',
                    'refresh_required': True, **refresh_info}
        snapshot = json.loads(row['snapshot'])
        cities = [c for c in snapshot['cities'] if (city_id is None or c['id'] == city_id)
                  and (city_kind is None or c['kind'] == city_kind)]
        ids = {c['id'] for c in cities}
        armies = [a for a in snapshot['armies'] if
                  ((city_id is None and city_kind is None) or a['city_id'] in ids)
                  and (status is None or a.get('status', 'unknown') == status)]
        age = freshness(row['captured_at'], max_age_seconds)
        return {**{k: v for k,v in snapshot.items() if k != 'pages'},
                'world': world, 'observed': True, 'cities': cities, 'armies': armies,
                'snapshot_id': row['id'], 'captured_at': row['captured_at'],
                'evidence': json.loads(row['evidence']), **age, **refresh_info,
                'refresh_required': (age['stale'] or not snapshot['complete']
                    or not snapshot.get('position_complete',False) or bool(refresh and refresh['status'] != 'complete')),
                'caution': '队伍可能已移动；当前位置与目的地分别记录，行动前刷新。'}

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
