"""
Read-only access to BirdNET-Go SQLite database (v2 schema).
Tables: detections (id, label_id, detected_at, confidence, clip_name), labels (id, scientific_name).
"""
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Generator, Optional


@contextmanager
def get_connection(db_path: str) -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row) if row else {}


def detect_schema(conn: sqlite3.Connection) -> str:
    """Return 'v2' if detections+labels exist, else 'legacy' or 'unknown'."""
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('detections','labels','notes')"
    )
    tables = {r[0] for r in cur.fetchall()}
    if "detections" in tables and "labels" in tables:
        return "v2"
    if "notes" in tables:
        return "legacy"
    return "unknown"


def get_detections_v2(
    conn: sqlite3.Connection,
    date_start: Optional[str] = None,
    date_end: Optional[str] = None,
    common_name: Optional[str] = None,
    limit: int = 100,
    after_id: Optional[int] = None,
) -> list[dict]:
    """Query detections with labels (v2 schema). detected_at is Unix timestamp."""
    # Parse dates to Unix range
    start_ts, end_ts = None, None
    if date_start:
        try:
            start_ts = int(datetime.strptime(date_start, "%Y-%m-%d").timestamp())
        except ValueError:
            pass
    if date_end:
        try:
            end_ts = int(
                datetime.strptime(date_end + " 23:59:59", "%Y-%m-%d %H:%M:%S").timestamp()
            )
        except ValueError:
            pass

    sql = """
        SELECT d.id, d.detected_at, d.confidence, d.clip_name,
               l.scientific_name
        FROM detections d
        JOIN labels l ON l.id = d.label_id
        WHERE 1=1
    """
    params: list[Any] = []
    if start_ts is not None:
        sql += " AND d.detected_at >= ?"
        params.append(start_ts)
    if end_ts is not None:
        sql += " AND d.detected_at <= ?"
        params.append(end_ts)
    if common_name:
        sql += " AND l.scientific_name LIKE ?"
        params.append(f"%{common_name}%")
    if after_id is not None:
        sql += " AND d.id > ?"
        params.append(after_id)
    sql += " ORDER BY d.detected_at DESC LIMIT ?"
    params.append(min(limit, 500))

    cur = conn.execute(sql, params)
    rows = cur.fetchall()
    out = []
    for row in rows:
        r = _row_to_dict(row)
        ts = r.get("detected_at")
        if ts is not None:
            r["timestamp"] = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%SZ")
        r["common_name"] = r.get("scientific_name") or ""
        r["scientific_name"] = r.get("scientific_name") or ""
        out.append(
            {
                "id": str(r.get("id", "")),
                "timestamp": r.get("timestamp", ""),
                "common_name": r["common_name"],
                "scientific_name": r["scientific_name"],
                "confidence": float(r.get("confidence") or 0),
                "audio_path": (r.get("clip_name") or "") or "",
            }
        )
    return out


def get_stats_v2(
    conn: sqlite3.Connection,
    date_start: Optional[str] = None,
    date_end: Optional[str] = None,
) -> list[dict]:
    """Species counts for date range (v2 schema)."""
    start_ts, end_ts = None, None
    if date_start:
        try:
            start_ts = int(datetime.strptime(date_start, "%Y-%m-%d").timestamp())
        except ValueError:
            pass
    if date_end:
        try:
            end_ts = int(
                datetime.strptime(date_end + " 23:59:59", "%Y-%m-%d %H:%M:%S").timestamp()
            )
        except ValueError:
            pass

    sql = """
        SELECT l.scientific_name, COUNT(*) AS count
        FROM detections d
        JOIN labels l ON l.id = d.label_id
        WHERE 1=1
    """
    params: list[Any] = []
    if start_ts is not None:
        sql += " AND d.detected_at >= ?"
        params.append(start_ts)
    if end_ts is not None:
        sql += " AND d.detected_at <= ?"
        params.append(end_ts)
    sql += " GROUP BY l.scientific_name ORDER BY count DESC"

    cur = conn.execute(sql, params)
    rows = cur.fetchall()
    return [
        {
            "common_name": (r := _row_to_dict(row)).get("scientific_name") or "",
            "scientific_name": r.get("scientific_name") or "",
            "count": int(r.get("count") or 0),
        }
        for row in rows
    ]


def get_max_detection_id(conn: sqlite3.Connection) -> int:
    """Return max(id) from detections for MQTT bridge polling."""
    cur = conn.execute("SELECT COALESCE(MAX(id), 0) FROM detections")
    return int(cur.fetchone()[0])
