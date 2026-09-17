"""SQLite storage for the Prahari edge node.

Why SQLite and not Postgres at the edge: a border outpost node must survive
power loss and run for days with no operator and no network. A single-file
WAL database with synchronous=FULL on the event path gives durable, crash-safe
writes with no server process to babysit. The sector core uses the same schema
and can be pointed at Postgres later; nothing above this layer knows which.

Raw sqlite3 is used rather than an ORM because the offline/durability semantics
(WAL, checkpointing, the append-only evidence ledger) are the point of this
module, and an ORM would hide exactly the behaviour we need to control.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from prahari.common.models import (
    Camera,
    CapabilityCertificate,
    Event,
    PatrolProfile,
    SyncState,
    Zone,
)

SCHEMA_VERSION = 3

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cameras (
    camera_id   TEXT PRIMARY KEY,
    payload     TEXT NOT NULL,          -- full Camera JSON incl. credentials
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS zones (
    zone_id     TEXT PRIMARY KEY,
    camera_id   TEXT NOT NULL,
    payload     TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_zones_camera ON zones(camera_id);

CREATE TABLE IF NOT EXISTS certificates (
    certificate_id TEXT PRIMARY KEY,
    camera_id      TEXT NOT NULL,
    version        INTEGER NOT NULL,
    issued_at      TEXT NOT NULL,
    digest         TEXT NOT NULL,
    payload        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cert_camera ON certificates(camera_id, version DESC);

CREATE TABLE IF NOT EXISTS events (
    event_id      TEXT PRIMARY KEY,
    camera_id     TEXT NOT NULL,
    node_id       TEXT NOT NULL,
    event_type    TEXT NOT NULL,
    priority      TEXT NOT NULL,
    priority_rank INTEGER NOT NULL,
    score         REAL NOT NULL,
    ts            TEXT NOT NULL,           -- ISO8601 UTC
    ts_epoch      REAL NOT NULL,           -- for fast range queries
    monotonic_ns  INTEGER NOT NULL DEFAULT 0,
    clock_synced  INTEGER NOT NULL DEFAULT 1,
    track_id      INTEGER,
    object_class  TEXT,
    zone_id       TEXT,
    sync_state    TEXT NOT NULL,
    acknowledged  INTEGER NOT NULL DEFAULT 0,
    alerted       INTEGER NOT NULL DEFAULT 1,
    feedback      TEXT,
    ledger_index  INTEGER NOT NULL DEFAULT 0,
    prev_hash     TEXT NOT NULL DEFAULT '',
    entry_hash    TEXT NOT NULL DEFAULT '',
    evidence_bytes INTEGER NOT NULL DEFAULT 0,
    payload       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_ts     ON events(ts_epoch DESC);
CREATE INDEX IF NOT EXISTS idx_events_sync   ON events(sync_state, ts_epoch);
CREATE INDEX IF NOT EXISTS idx_events_camera ON events(camera_id, ts_epoch DESC);
CREATE INDEX IF NOT EXISTS idx_events_prio   ON events(priority_rank DESC, ts_epoch DESC);

-- Learnt pattern of life. One row per camera x zone x hour-of-week bucket.
-- This is what lets an open-border deployment treat the 10:00 market crowd as
-- normal and the 02:00 single walker as worth a look.
-- Declared friendly-force movements. Configuration, not learnt state: a
-- patrol is here because somebody with authority at the post wrote it down,
-- never because the node decided some nightly movement looked routine.
CREATE TABLE IF NOT EXISTS patrols (
    patrol_id   TEXT PRIMARY KEY,
    active      INTEGER NOT NULL DEFAULT 1,
    payload     TEXT NOT NULL,          -- full PatrolProfile JSON
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS normalcy (
    camera_id   TEXT NOT NULL,
    zone_id     TEXT NOT NULL DEFAULT '',
    object_class TEXT NOT NULL,
    hour_of_week INTEGER NOT NULL,        -- 0..167
    count       REAL NOT NULL DEFAULT 0,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (camera_id, zone_id, object_class, hour_of_week)
);

-- Operator feedback, aggregated per camera+event type, used to retune
-- thresholds so the false-alarm rate actually falls over time.
CREATE TABLE IF NOT EXISTS feedback_stats (
    camera_id    TEXT NOT NULL,
    event_type   TEXT NOT NULL,
    true_positive INTEGER NOT NULL DEFAULT 0,
    false_alarm   INTEGER NOT NULL DEFAULT 0,
    updated_at   TEXT NOT NULL,
    PRIMARY KEY (camera_id, event_type)
);

CREATE TABLE IF NOT EXISTS users (
    username     TEXT PRIMARY KEY,
    role         TEXT NOT NULL,           -- admin | operator | viewer
    salt         TEXT NOT NULL,
    pw_hash      TEXT NOT NULL,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,
    actor      TEXT NOT NULL,
    action     TEXT NOT NULL,
    target     TEXT NOT NULL DEFAULT '',
    detail     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts DESC);

CREATE TABLE IF NOT EXISTS incidents (
    incident_id TEXT PRIMARY KEY,
    payload     TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS global_entities (
    entity_id   TEXT PRIMARY KEY,
    payload     TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS plates (
    plate       TEXT PRIMARY KEY,
    payload     TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS node_state (
    key         TEXT PRIMARY KEY,
    payload     TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
"""


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


class Database:
    """Thread-safe SQLite wrapper.

    One connection guarded by a lock. The workload is a handful of writes per
    second across a dozen camera pipelines, so a connection pool would add
    complexity for no measurable gain, and a single writer sidesteps SQLite's
    writer contention entirely.
    """

    def __init__(self, path: Path | str, secret_key: str = "") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        
        # Setup credential encryption
        import base64
        import hashlib
        from cryptography.fernet import Fernet
        
        # Derive a 32-byte urlsafe base64 key from the provided secret
        key = base64.urlsafe_b64encode(hashlib.sha256(secret_key.encode("utf-8")).digest())
        self._fernet = Fernet(key)

        # We need a re-entrant lock because app logic often calls multiple
        # methods in one conceptual transaction.
        self._lock = threading.RLock()
        
        # Apply migrations
        conn = sqlite3.connect(str(self.path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        with conn:
            conn.executescript("PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;")
            cursor = conn.execute("PRAGMA user_version")
            version = cursor.fetchone()[0]
            
            # v0 -> v1 (Initial tables)
            if version < 1:
                conn.executescript(SCHEMA)
                conn.execute("PRAGMA user_version = 1")
                version = 1
                
            # v1 -> v2 (Add audits and feedback)
            if version < 2:
                conn.executescript("""
                    CREATE TABLE IF NOT EXISTS audit_log (
                        id         INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp  TEXT NOT NULL,
                        actor      TEXT NOT NULL,
                        action     TEXT NOT NULL,
                        target     TEXT NOT NULL,
                        detail     TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS user_feedback (
                        camera_id       TEXT NOT NULL,
                        event_type      TEXT NOT NULL,
                        true_positives  INTEGER NOT NULL DEFAULT 0,
                        false_alarms    INTEGER NOT NULL DEFAULT 0,
                        PRIMARY KEY (camera_id, event_type)
                    );
                """)
                conn.execute("PRAGMA user_version = 2")
                version = 2
                
            # v2 -> v3 (Add incidents, global_entities, plates, node_state)
            if version < 3:
                conn.executescript("""
                    CREATE TABLE IF NOT EXISTS incidents (
                        incident_id TEXT PRIMARY KEY,
                        payload     TEXT NOT NULL,
                        created_at  TEXT NOT NULL,
                        updated_at  TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS global_entities (
                        entity_id   TEXT PRIMARY KEY,
                        payload     TEXT NOT NULL,
                        updated_at  TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS plates (
                        plate       TEXT PRIMARY KEY,
                        payload     TEXT NOT NULL,
                        updated_at  TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS node_state (
                        key         TEXT PRIMARY KEY,
                        payload     TEXT NOT NULL,
                        updated_at  TEXT NOT NULL
                    );
                """)
                conn.execute("PRAGMA user_version = 3")
                version = 3
        conn.close()

        self._conn = sqlite3.connect(
            str(self.path), check_same_thread=False, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            # Events are the thing we must not lose after an unclean shutdown.
            self._conn.execute("PRAGMA synchronous=FULL")
            self._conn.execute('''
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key   TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            ''')
            self._conn.execute(
                "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('version', ?)",
                (SCHEMA_VERSION,)
            )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- low level ------------------------------------------------------
    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, tuple(params))

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchall()

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def _encrypt_camera(self, cam: Camera) -> str:
        d = cam.model_dump(mode="json")
        if d.get("password"):
            d["password"] = self._fernet.encrypt(d["password"].encode("utf-8")).decode("ascii")
        return json.dumps(d)

    def _decrypt_camera(self, payload: str) -> Camera:
        d = json.loads(payload)
        pwd = d.get("password")
        if pwd and pwd.startswith("gAAAAA"):
            try:
                d["password"] = self._fernet.decrypt(pwd.encode("ascii")).decode("utf-8")
            except Exception:
                pass
        return Camera.model_validate(d)

    # -- cameras --------------------------------------------------------
    def upsert_camera(self, cam: Camera) -> None:
        now = _iso(datetime.now(timezone.utc))
        self.execute(
            """INSERT INTO cameras(camera_id, payload, created_at, updated_at)
               VALUES(?,?,?,?)
               ON CONFLICT(camera_id) DO UPDATE SET payload=excluded.payload,
                                                    updated_at=excluded.updated_at""",
            (cam.camera_id, self._encrypt_camera(cam), now, now),
        )

    def get_camera(self, camera_id: str) -> Camera | None:
        row = self.query_one("SELECT payload FROM cameras WHERE camera_id=?", (camera_id,))
        return self._decrypt_camera(row["payload"]) if row else None

    def list_cameras(self) -> list[Camera]:
        rows = self.query("SELECT payload FROM cameras ORDER BY camera_id")
        return [self._decrypt_camera(r["payload"]) for r in rows]

    def delete_camera(self, camera_id: str) -> bool:
        cur = self.execute("DELETE FROM cameras WHERE camera_id=?", (camera_id,))
        self.execute("DELETE FROM zones WHERE camera_id=?", (camera_id,))
        return cur.rowcount > 0

    # -- zones ----------------------------------------------------------
    def upsert_zone(self, zone: Zone) -> None:
        self.execute(
            """INSERT INTO zones(zone_id, camera_id, payload, updated_at)
               VALUES(?,?,?,?)
               ON CONFLICT(zone_id) DO UPDATE SET payload=excluded.payload,
                                                  camera_id=excluded.camera_id,
                                                  updated_at=excluded.updated_at""",
            (zone.zone_id, zone.camera_id, zone.model_dump_json(),
             _iso(datetime.now(timezone.utc))),
        )

    def list_zones(self, camera_id: str | None = None) -> list[Zone]:
        if camera_id:
            rows = self.query("SELECT payload FROM zones WHERE camera_id=?", (camera_id,))
        else:
            rows = self.query("SELECT payload FROM zones")
        return [Zone.model_validate_json(r["payload"]) for r in rows]

    def delete_zone(self, zone_id: str) -> bool:
        return self.execute("DELETE FROM zones WHERE zone_id=?", (zone_id,)).rowcount > 0

    # -- patrols ---------------------------------------------------------
    def upsert_patrol(self, profile: PatrolProfile) -> None:
        now = _iso(datetime.now(timezone.utc))
        self.execute(
            """INSERT INTO patrols(patrol_id, active, payload, created_at, updated_at)
               VALUES(?,?,?,?,?)
               ON CONFLICT(patrol_id) DO UPDATE SET active=excluded.active,
                                                    payload=excluded.payload,
                                                    updated_at=excluded.updated_at""",
            (profile.patrol_id, int(profile.active), profile.model_dump_json(),
             now, now),
        )

    def get_patrol(self, patrol_id: str) -> PatrolProfile | None:
        row = self.query_one("SELECT payload FROM patrols WHERE patrol_id=?",
                             (patrol_id,))
        return PatrolProfile.model_validate_json(row["payload"]) if row else None

    def list_patrols(self, active_only: bool = False) -> list[PatrolProfile]:
        sql = "SELECT payload FROM patrols"
        if active_only:
            sql += " WHERE active = 1"
        sql += " ORDER BY patrol_id"
        return [PatrolProfile.model_validate_json(r["payload"]) for r in self.query(sql)]

    def delete_patrol(self, patrol_id: str) -> bool:
        return self.execute("DELETE FROM patrols WHERE patrol_id=?",
                            (patrol_id,)).rowcount > 0

    # -- certificates ---------------------------------------------------
    def save_certificate(self, cert: CapabilityCertificate) -> None:
        self.execute(
            """INSERT OR REPLACE INTO certificates
               (certificate_id, camera_id, version, issued_at, digest, payload)
               VALUES(?,?,?,?,?,?)""",
            (cert.certificate_id, cert.camera_id, cert.version,
             _iso(cert.issued_at), cert.digest, cert.model_dump_json()),
        )

    def latest_certificate(self, camera_id: str) -> CapabilityCertificate | None:
        row = self.query_one(
            "SELECT payload FROM certificates WHERE camera_id=? ORDER BY version DESC LIMIT 1",
            (camera_id,),
        )
        return CapabilityCertificate.model_validate_json(row["payload"]) if row else None

    def next_certificate_version(self, camera_id: str) -> int:
        row = self.query_one(
            "SELECT MAX(version) AS v FROM certificates WHERE camera_id=?", (camera_id,)
        )
        return int((row["v"] or 0) + 1) if row else 1

    # -- events ---------------------------------------------------------
    def insert_event(self, ev: Event) -> None:
        ev_bytes = ev.evidence.size_bytes if ev.evidence else 0
        self.execute(
            """INSERT OR REPLACE INTO events
               (event_id, camera_id, node_id, event_type, priority, priority_rank,
                score, ts, ts_epoch, monotonic_ns, clock_synced, track_id,
                object_class, zone_id, sync_state, acknowledged, alerted, feedback,
                ledger_index, prev_hash, entry_hash, evidence_bytes, payload)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                ev.event_id, ev.camera_id, ev.node_id, ev.event_type.value,
                ev.priority.value, ev.priority.rank, ev.priority_score,
                _iso(ev.timestamp), ev.timestamp.timestamp(), ev.monotonic_ns,
                int(ev.clock_synced), ev.track_id, ev.object_class.value,
                ev.zone_id, ev.sync_state.value, int(ev.acknowledged),
                int(ev.alerted),
                ev.operator_feedback, ev.ledger_index, ev.prev_hash,
                ev.entry_hash, ev_bytes, ev.model_dump_json(),
            ),
        )

    def get_event(self, event_id: str) -> Event | None:
        row = self.query_one("SELECT payload FROM events WHERE event_id=?", (event_id,))
        return Event.model_validate_json(row["payload"]) if row else None

    def list_events(
        self,
        limit: int = 100,
        offset: int = 0,
        camera_id: str | None = None,
        min_rank: int = 0,
        unacknowledged_only: bool = False,
        sync_state: SyncState | None = None,
        alerted_only: bool = False,
    ) -> list[Event]:
        sql = "SELECT payload FROM events WHERE priority_rank >= ?"
        params: list[Any] = [min_rank]
        if alerted_only:
            sql += " AND alerted = 1"
        if camera_id:
            sql += " AND camera_id = ?"
            params.append(camera_id)
        if unacknowledged_only:
            sql += " AND acknowledged = 0"
        if sync_state:
            sql += " AND sync_state = ?"
            params.append(sync_state.value)
        sql += " ORDER BY ts_epoch DESC LIMIT ? OFFSET ?"
        params += [limit, offset]
        return [Event.model_validate_json(r["payload"]) for r in self.query(sql, params)]

    def update_event(self, ev: Event) -> None:
        self.insert_event(ev)

    def count_events(self, sync_state: SyncState | None = None) -> int:
        if sync_state:
            row = self.query_one(
                "SELECT COUNT(*) AS c FROM events WHERE sync_state=?", (sync_state.value,)
            )
        else:
            row = self.query_one("SELECT COUNT(*) AS c FROM events")
        return int(row["c"]) if row else 0

    def recent_event_count(self, camera_id: str, zone_id: str, object_class: str, since: datetime) -> int:
        # SQLite compares ISO8601 strings lexicographically, so we can use string comparison on timestamp
        row = self.query_one(
            """SELECT COUNT(*) AS c FROM events 
               WHERE camera_id=? AND IFNULL(zone_id, '')=? AND object_class=? AND ts >= ?""",
            (camera_id, zone_id, object_class, _iso(since))
        )
        return int(row["c"]) if row else 0

    def alerts_since(self, epoch: float, min_rank: int = 2) -> int:
        row = self.query_one(
            "SELECT COUNT(*) AS c FROM events WHERE ts_epoch >= ? "
            "AND priority_rank >= ? AND alerted = 1",
            (epoch, min_rank),
        )
        return int(row["c"]) if row else 0

    def last_ledger_entry(self) -> tuple[int, str]:
        """Highest ledger index and its hash, for chaining the next entry."""
        row = self.query_one(
            "SELECT ledger_index, entry_hash FROM events ORDER BY ledger_index DESC LIMIT 1"
        )
        if not row:
            return 0, ""
        return int(row["ledger_index"]), str(row["entry_hash"])

    def ledger_entries(self) -> list[tuple[int, str, str, str]]:
        rows = self.query(
            "SELECT ledger_index, prev_hash, entry_hash, event_id "
            "FROM events WHERE ledger_index > 0 ORDER BY ledger_index ASC"
        )
        return [(int(r["ledger_index"]), r["prev_hash"], r["entry_hash"], r["event_id"])
                for r in rows]

    # -- sync queue -----------------------------------------------------
    def pending_events(self, limit: int) -> list[Event]:
        # Only chained events are eligible to leave the node: an unchained event
        # is one whose evidence is still being captured, and shipping it would
        # hand the core a record it cannot verify.
        rows = self.query(
            "SELECT payload FROM events WHERE sync_state IN (?, ?) "
            "AND ledger_index > 0 "
            "ORDER BY priority_rank DESC, ts_epoch ASC LIMIT ?",
            (SyncState.PENDING.value, SyncState.FAILED.value, limit),
        )
        return [Event.model_validate_json(r["payload"]) for r in rows]

    def queue_bytes(self) -> int:
        row = self.query_one(
            "SELECT COALESCE(SUM(evidence_bytes),0) AS b FROM events WHERE sync_state != ?",
            (SyncState.SYNCED.value,),
        )
        return int(row["b"]) if row else 0

    def eviction_candidates(self, limit: int = 50) -> list[Event]:
        """Lowest-priority, oldest unsynced events that still hold evidence.

        Under disk pressure Prahari drops evidence clips, never event metadata.
        An event with no clip is degraded; an event that vanished is a gap in
        the record.
        """
        rows = self.query(
            "SELECT payload FROM events WHERE sync_state != ? AND evidence_bytes > 0 "
            "ORDER BY priority_rank ASC, ts_epoch ASC LIMIT ?",
            (SyncState.SYNCED.value, limit),
        )
        return [Event.model_validate_json(r["payload"]) for r in rows]

    # -- normalcy -------------------------------------------------------
    def bump_normalcy(self, camera_id: str, zone_id: str, object_class: str,
                      hour_of_week: int, amount: float = 1.0) -> None:
        self.execute(
            """INSERT INTO normalcy(camera_id, zone_id, object_class, hour_of_week,
                                    count, updated_at)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(camera_id, zone_id, object_class, hour_of_week)
               DO UPDATE SET count = count + excluded.count,
                             updated_at = excluded.updated_at""",
            (camera_id, zone_id, object_class, hour_of_week, amount,
             _iso(datetime.now(timezone.utc))),
        )

    def normalcy_count(self, camera_id: str, zone_id: str, object_class: str,
                       hour_of_week: int) -> float:
        row = self.query_one(
            "SELECT count FROM normalcy WHERE camera_id=? AND zone_id=? "
            "AND object_class=? AND hour_of_week=?",
            (camera_id, zone_id, object_class, hour_of_week),
        )
        return float(row["count"]) if row else 0.0

    def normalcy_total(self, camera_id: str, object_class: str,
                       zone_id: str = "") -> float:
        """Total learnt observations for this camera/class *within one zone*.

        Scoping matters: comparing a single zone's hourly count against a
        camera-wide total mixes two different denominators, and the ratio comes
        out enormous for every zone that sees less traffic than the camera as a
        whole - which is every zone. That made every event read as wildly
        unusual and pinned the entire event stream at maximum priority.
        """
        row = self.query_one(
            "SELECT COALESCE(SUM(count),0) AS c FROM normalcy "
            "WHERE camera_id=? AND object_class=? AND zone_id=?",
            (camera_id, object_class, zone_id or ""),
        )
        return float(row["c"]) if row else 0.0

    # -- feedback -------------------------------------------------------
    def record_feedback(self, camera_id: str, event_type: str, is_false_alarm: bool) -> None:
        col = "false_alarm" if is_false_alarm else "true_positive"
        self.execute(
            f"""INSERT INTO feedback_stats(camera_id, event_type, {col}, updated_at)
                VALUES(?,?,1,?)
                ON CONFLICT(camera_id, event_type)
                DO UPDATE SET {col} = {col} + 1, updated_at = excluded.updated_at""",
            (camera_id, event_type, _iso(datetime.now(timezone.utc))),
        )

    def feedback_for(self, camera_id: str, event_type: str) -> tuple[int, int]:
        row = self.query_one(
            "SELECT true_positive, false_alarm FROM feedback_stats "
            "WHERE camera_id=? AND event_type=?",
            (camera_id, event_type),
        )
        if not row:
            return 0, 0
        return int(row["true_positive"]), int(row["false_alarm"])

    def all_feedback(self) -> list[dict[str, Any]]:
        rows = self.query("SELECT * FROM feedback_stats")
        return [dict(r) for r in rows]

    def verify_ledger(self) -> dict[str, Any]:
        return self._ledger.verify() if hasattr(self, "_ledger") else {}

    # -- incidents ------------------------------------------------------
    def upsert_incident(self, incident: Any) -> None:
        now = _iso(datetime.now(timezone.utc))
        self.execute(
            """INSERT INTO incidents(incident_id, payload, created_at, updated_at)
               VALUES(?,?,?,?)
               ON CONFLICT(incident_id) DO UPDATE SET payload=excluded.payload,
                                                      updated_at=excluded.updated_at""",
            (incident.incident_id, incident.model_dump_json(), incident.created_at.isoformat(), now),
        )

    def get_incident(self, incident_id: str) -> Any | None:
        row = self.query_one("SELECT payload FROM incidents WHERE incident_id=?", (incident_id,))
        # Late import or model_validate_json
        from prahari.common.models import Incident
        return Incident.model_validate_json(row["payload"]) if row else None

    def list_incidents(self) -> list[Any]:
        rows = self.query("SELECT payload FROM incidents ORDER BY created_at DESC")
        from prahari.common.models import Incident
        return [Incident.model_validate_json(r["payload"]) for r in rows]

    # -- global entities ------------------------------------------------
    def upsert_global_entity(self, entity_id: str, payload: dict) -> None:
        now = _iso(datetime.now(timezone.utc))
        self.execute(
            """INSERT INTO global_entities(entity_id, payload, updated_at)
               VALUES(?,?,?)
               ON CONFLICT(entity_id) DO UPDATE SET payload=excluded.payload,
                                                    updated_at=excluded.updated_at""",
            (entity_id, json.dumps(payload), now),
        )

    def list_global_entities(self) -> dict[str, dict]:
        rows = self.query("SELECT entity_id, payload FROM global_entities")
        return {r["entity_id"]: json.loads(r["payload"]) for r in rows}

    def delete_global_entity(self, entity_id: str) -> None:
        self.execute("DELETE FROM global_entities WHERE entity_id=?", (entity_id,))

    # -- plates ---------------------------------------------------------
    def upsert_plate_sighting(self, plate: str, payload: dict) -> None:
        now = _iso(datetime.now(timezone.utc))
        self.execute(
            """INSERT INTO plates(plate, payload, updated_at)
               VALUES(?,?,?)
               ON CONFLICT(plate) DO UPDATE SET payload=excluded.payload,
                                                updated_at=excluded.updated_at""",
            (plate, json.dumps(payload), now),
        )

    def list_plates(self) -> dict[str, dict]:
        rows = self.query("SELECT plate, payload FROM plates")
        return {r["plate"]: json.loads(r["payload"]) for r in rows}
        
    def delete_plate(self, plate: str) -> None:
        self.execute("DELETE FROM plates WHERE plate=?", (plate,))

    # -- node state -----------------------------------------------------
    def set_node_state(self, key: str, payload: dict) -> None:
        now = _iso(datetime.now(timezone.utc))
        self.execute(
            """INSERT INTO node_state(key, payload, updated_at)
               VALUES(?,?,?)
               ON CONFLICT(key) DO UPDATE SET payload=excluded.payload,
                                              updated_at=excluded.updated_at""",
            (key, json.dumps(payload), now),
        )

    def get_node_state(self, key: str) -> dict | None:
        row = self.query_one("SELECT payload FROM node_state WHERE key=?", (key,))
        return json.loads(row["payload"]) if row else None

    # -- audit ----------------------------------------------------------
    def audit(self, actor: str, action: str, target: str = "", detail: Any = "") -> None:
        if not isinstance(detail, str):
            detail = json.dumps(detail, default=str)
        self.execute(
            "INSERT INTO audit_log(ts, actor, action, target, detail) VALUES(?,?,?,?,?)",
            (_iso(datetime.now(timezone.utc)), actor, action, target, detail),
        )

    def list_audit(self, limit: int = 200) -> list[dict[str, Any]]:
        return [dict(r) for r in
                self.query("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))]
