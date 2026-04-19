"""
Read-only access to BirdNET-Go SQLite database.
Supports v2 schema (detections+labels) and legacy schema (notes).
"""
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Generator, Optional


def _db_uri(db_path: str) -> str:
    """Build SQLite URI for read-only (works on Windows and Linux)."""
    p = Path(db_path).resolve()
    if not p.is_file():
        raise FileNotFoundError(f"Database file not found: {p}")
    # file:///C:/path (Windows) or file:///home/... (Linux)
    uri = p.as_uri()
    return f"{uri}?mode=ro"


@contextmanager
def get_connection(db_path: str) -> Generator[sqlite3.Connection, None, None]:
    uri = _db_uri(db_path)
    conn = sqlite3.connect(uri, uri=True)
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


class SchemaError(Exception):
    """Raised when the database schema is not supported (v2 or legacy)."""
    pass


def _parse_legacy_datetime(date_str: str, time_str: str) -> str:
    """Build ISO timestamp from notes date (YYYY-MM-DD) and time (HH:MM:SS or similar)."""
    if not date_str:
        return ""
    try:
        if time_str:
            # time can be "12:30:00" or "12:30:00.123"
            time_part = time_str.strip()[:8]
            dt = datetime.strptime(f"{date_str} {time_part}", "%Y-%m-%d %H:%M:%S")
        else:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return f"{date_str}T00:00:00Z"


def get_detections_legacy(
    conn: sqlite3.Connection,
    date_start: Optional[str] = None,
    date_end: Optional[str] = None,
    common_name: Optional[str] = None,
    limit: int = 100,
    after_id: Optional[int] = None,
) -> list[dict]:
    """Query detections from legacy notes table. Same JSON shape as v2."""
    sql = """
        SELECT n.id, n.date, n.time, n.scientific_name, n.common_name,
               n.confidence, n.clip_name,
               ic.url AS image_url
        FROM notes n
        LEFT JOIN labels lab ON lab.scientific_name = n.scientific_name
        LEFT JOIN image_caches ic ON ic.label_id = lab.id
        WHERE 1=1
    """
    params: list[Any] = []
    if date_start:
        sql += " AND date >= ?"
        params.append(date_start)
    if date_end:
        sql += " AND date <= ?"
        params.append(date_end)
    if common_name:
        sql += " AND (common_name LIKE ? OR scientific_name LIKE ?)"
        params.append(f"%{common_name}%")
        params.append(f"%{common_name}%")
    if after_id is not None:
        sql += " AND id > ?"
        params.append(after_id)
    sql += " ORDER BY date DESC, time DESC LIMIT ?"
    params.append(min(limit, 500))

    cur = conn.execute(sql, params)
    rows = cur.fetchall()
    out = []
    for row in rows:
        r = _row_to_dict(row)
        ts = _parse_legacy_datetime(r.get("date") or "", r.get("time") or "")
        common = (r.get("common_name") or "").strip() or (r.get("scientific_name") or "")
        scientific = (r.get("scientific_name") or "").strip()
        out.append({
            "id": str(r.get("id", "")),
            "timestamp": ts,
            "common_name": common,
            "scientific_name": scientific,
            "confidence": float(r.get("confidence") or 0),
            "audio_path": (r.get("clip_name") or "") or "",
            "image_url": r.get("image_url") or "",
        })
    return out


def get_stats_legacy(
    conn: sqlite3.Connection,
    date_start: Optional[str] = None,
    date_end: Optional[str] = None,
) -> list[dict]:
    """Species counts from legacy notes table. Same JSON shape as v2."""
    sql = """
        SELECT n.scientific_name, MAX(n.common_name) AS common_name,
               COUNT(*) AS count, MAX(ic.url) AS image_url
        FROM notes n
        LEFT JOIN labels lab ON lab.scientific_name = n.scientific_name
        LEFT JOIN image_caches ic ON ic.label_id = lab.id
        WHERE 1=1
    """
    params: list[Any] = []
    if date_start:
        sql += " AND date >= ?"
        params.append(date_start)
    if date_end:
        sql += " AND date <= ?"
        params.append(date_end)
    sql += " GROUP BY n.scientific_name ORDER BY count DESC"

    cur = conn.execute(sql, params)
    return [
        {
            "common_name": (r := _row_to_dict(row)).get("common_name") or r.get("scientific_name") or "",
            "scientific_name": r.get("scientific_name") or "",
            "count": int(r.get("count") or 0),
            "image_url": r.get("image_url") or "",
        }
        for row in cur.fetchall()
    ]


def get_detections_v2(
    conn: sqlite3.Connection,
    date_start: Optional[str] = None,
    date_end: Optional[str] = None,
    common_name: Optional[str] = None,
    limit: int = 100,
    after_id: Optional[int] = None,
) -> list[dict]:
    """Query detections (v2 or legacy schema). Same JSON shape for both."""
    schema = detect_schema(conn)
    if schema == "legacy":
        return get_detections_legacy(
            conn, date_start=date_start, date_end=date_end, common_name=common_name,
            limit=limit, after_id=after_id,
        )
    if schema != "v2":
        raise SchemaError(
            "Database schema is not v2 (detections+labels) nor legacy (notes)."
        )
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
               l.scientific_name,
               ic.url AS image_url
        FROM detections d
        JOIN labels l ON l.id = d.label_id
        LEFT JOIN image_caches ic ON ic.label_id = l.id
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
                "image_url": r.get("image_url") or "",
            }
        )
    return out


def get_stats_v2(
    conn: sqlite3.Connection,
    date_start: Optional[str] = None,
    date_end: Optional[str] = None,
) -> list[dict]:
    """Species counts for date range (v2 or legacy schema)."""
    schema = detect_schema(conn)
    if schema == "legacy":
        return get_stats_legacy(conn, date_start=date_start, date_end=date_end)
    if schema != "v2":
        raise SchemaError(
            "Database schema is not v2 (detections+labels) nor legacy (notes)."
        )
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
        SELECT l.scientific_name, COUNT(*) AS count,
               MAX(ic.url) AS image_url
        FROM detections d
        JOIN labels l ON l.id = d.label_id
        LEFT JOIN image_caches ic ON ic.label_id = l.id
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
            "image_url": r.get("image_url") or "",
        }
        for row in rows
    ]


def get_hourly_detections(conn: sqlite3.Connection, date_str: str) -> dict:
    """Hourly detection counts per species for a given date (YYYY-MM-DD).
    Returns sunrise/sunset as Unix timestamps for the JS client to convert to local time.
    """
    schema = detect_schema(conn)

    if schema == "legacy":
        sql = """
            SELECT n.scientific_name, MAX(n.common_name) AS common_name,
                   CAST(substr(n.time, 1, 2) AS INTEGER) AS hour,
                   COUNT(*) AS count,
                   MAX(ic.url) AS image_url
            FROM notes n
            LEFT JOIN labels lab ON lab.scientific_name = n.scientific_name
            LEFT JOIN image_caches ic ON ic.label_id = lab.id
            WHERE n.date = ?
            GROUP BY n.scientific_name, hour
        """
        rows = [_row_to_dict(r) for r in conn.execute(sql, [date_str]).fetchall()]
    elif schema == "v2":
        sql = """
            SELECT l.scientific_name,
                   CAST(strftime('%H', datetime(d.detected_at, 'unixepoch', 'localtime')) AS INTEGER) AS hour,
                   COUNT(*) AS count,
                   MAX(ic.url) AS image_url
            FROM detections d
            JOIN labels l ON l.id = d.label_id
            LEFT JOIN image_caches ic ON ic.label_id = l.id
            WHERE date(datetime(d.detected_at, 'unixepoch', 'localtime')) = ?
            GROUP BY l.scientific_name, hour
        """
        rows = [_row_to_dict(r) for r in conn.execute(sql, [date_str]).fetchall()]
    else:
        return {"date": date_str, "sunrise": None, "sunset": None, "species": []}

    # Aggregate into species dict
    species_map: dict[str, dict] = {}
    for r in rows:
        name = r["scientific_name"]
        if name not in species_map:
            species_map[name] = {
                "scientific_name": name,
                "common_name": r.get("common_name") or name,
                "image_url": r.get("image_url") or "",
                "hourly_counts": [0] * 24,
                "total": 0,
            }
        hour = int(r.get("hour") or 0)
        count = int(r.get("count") or 0)
        if 0 <= hour <= 23:
            species_map[name]["hourly_counts"][hour] = count
        species_map[name]["total"] += count
        if r.get("image_url"):
            species_map[name]["image_url"] = r["image_url"]

    species_list = sorted(species_map.values(), key=lambda x: x["total"], reverse=True)

    # Sunrise / sunset for daylight bar (Unix timestamps)
    de_row = conn.execute(
        "SELECT sunrise, sunset FROM daily_events WHERE date = ? LIMIT 1", [date_str]
    ).fetchone()
    de = _row_to_dict(de_row) if de_row else {}

    return {
        "date": date_str,
        "sunrise": de.get("sunrise"),
        "sunset": de.get("sunset"),
        "species": species_list,
    }


def get_aggregate_detections(conn: sqlite3.Connection, mode: str) -> dict:
    """Detections par espèce agrégées par jour/semaine/mois.

    mode='daily'   → 30 derniers jours   (colonne = date YYYY-MM-DD)
    mode='weekly'  → 13 dernières semaines (colonne = YYYY-Www ISO)
    mode='monthly' → 12 derniers mois    (colonne = YYYY-MM)
    """
    schema = detect_schema(conn)
    if schema not in ("v2", "legacy"):
        return {"mode": mode, "columns": [], "species": []}

    if mode == "daily":
        interval_v2  = "'-30 days'"
        interval_leg = "'-30 days'"
        period_v2  = "date(datetime(d.detected_at, 'unixepoch', 'localtime'))"
        period_leg = "n.date"
    elif mode == "weekly":
        interval_v2  = "'-3 months'"
        interval_leg = "'-3 months'"
        period_v2  = "strftime('%Y-W%W', datetime(d.detected_at, 'unixepoch', 'localtime'))"
        period_leg = "strftime('%Y-W%W', n.date)"
    else:  # monthly
        interval_v2  = "'-12 months'"
        interval_leg = "'-12 months'"
        period_v2  = "strftime('%Y-%m', datetime(d.detected_at, 'unixepoch', 'localtime'))"
        period_leg = "strftime('%Y-%m', n.date)"

    if schema == "v2":
        sql = f"""
            SELECT l.scientific_name,
                   {period_v2} AS period,
                   COUNT(*) AS count,
                   MAX(ic.url) AS image_url
            FROM detections d
            JOIN labels l ON l.id = d.label_id
            LEFT JOIN image_caches ic ON ic.label_id = l.id
            WHERE d.detected_at >= strftime('%s', 'now', {interval_v2})
            GROUP BY l.scientific_name, period
            ORDER BY l.scientific_name, period
        """
    else:
        sql = f"""
            SELECT n.scientific_name, MAX(n.common_name) AS common_name,
                   {period_leg} AS period,
                   COUNT(*) AS count,
                   MAX(ic.url) AS image_url
            FROM notes n
            LEFT JOIN labels lab ON lab.scientific_name = n.scientific_name
            LEFT JOIN image_caches ic ON ic.label_id = lab.id
            WHERE n.date >= date('now', {interval_leg})
            GROUP BY n.scientific_name, period
            ORDER BY n.scientific_name, period
        """

    rows = [_row_to_dict(r) for r in conn.execute(sql).fetchall()]
    columns = sorted({r["period"] for r in rows})

    species_map: dict[str, dict] = {}
    for r in rows:
        name = r["scientific_name"]
        if name not in species_map:
            species_map[name] = {
                "scientific_name": name,
                "common_name": r.get("common_name") or name,
                "image_url": r.get("image_url") or "",
                "counts": {c: 0 for c in columns},
                "total": 0,
            }
        count = int(r.get("count") or 0)
        species_map[name]["counts"][r["period"]] = count
        species_map[name]["total"] += count
        if r.get("image_url"):
            species_map[name]["image_url"] = r["image_url"]

    species_list = [
        {
            "scientific_name": sp["scientific_name"],
            "common_name": sp["common_name"],
            "image_url": sp["image_url"],
            "counts": [sp["counts"].get(c, 0) for c in columns],
        }
        for sp in sorted(species_map.values(), key=lambda x: x["total"], reverse=True)
    ]

    return {"mode": mode, "columns": columns, "species": species_list}


def get_max_detection_id(conn: sqlite3.Connection) -> int:
    """Return max(id) for MQTT bridge polling (detections or notes)."""
    if detect_schema(conn) == "legacy":
        cur = conn.execute("SELECT COALESCE(MAX(id), 0) FROM notes")
    else:
        cur = conn.execute("SELECT COALESCE(MAX(id), 0) FROM detections")
    return int(cur.fetchone()[0])
