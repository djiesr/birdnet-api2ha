"""
Read-only access to BirdNET-Go SQLite database.
Supports v2 schema (detections+labels) and legacy schema (notes).
"""
import sqlite3
from collections import defaultdict
from contextlib import contextmanager
from datetime import date as date_type
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, Generator, Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:  # Python < 3.9 (non prévu ici)
    ZoneInfo = None  # type: ignore


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


def _label_preferred_name_sql(conn: sqlite3.Connection, alias: str = "l") -> str:
    """
    Expression SQL pour le nom « commun » / localisé dans labels (schéma v2).
    BirdNET-Go peut exposer common_name, name, label, etc. selon la version.
    """
    rows = conn.execute("PRAGMA table_info(labels)").fetchall()
    col_by_lower = {str(r[1]).lower(): str(r[1]) for r in rows}
    candidates = (
        "common_name",
        "name",
        "label",
        "vernacular_name",
        "name_fr",
        "fr_name",
        "localized_name",
        "locale_name",
    )
    for c in candidates:
        actual = col_by_lower.get(c.lower())
        if actual:
            a = alias
            # Identifiant quoté pour éviter les mots réservés
            qcol = actual.replace('"', '""')
            return f'COALESCE(NULLIF(TRIM({a}."{qcol}"), \'\'), {a}.scientific_name)'
    return f"{alias}.scientific_name"


def _local_day_unix_range(tz_name: str, date_str: str) -> tuple[int, int]:
    """Minuit local → minuit+1j (IANA), bornes Unix pour filtrer detected_at."""
    if ZoneInfo is None:
        raise RuntimeError("zoneinfo indisponible (Python 3.9+ requis)")
    tz = ZoneInfo(tz_name.strip())
    d = date_type.fromisoformat(date_str)
    start = datetime.combine(d, time.min, tzinfo=tz)
    end = datetime.combine(d + timedelta(days=1), time.min, tzinfo=tz)
    return int(start.timestamp()), int(end.timestamp())


def _hourly_v2_with_timezone(
    conn: sqlite3.Connection, date_str: str, tz_name: str
) -> list[dict[str, Any]]:
    """Une ligne par détection ; agrégation par heure locale IANA (pas SQLite localtime)."""
    if ZoneInfo is None:
        raise RuntimeError("zoneinfo indisponible")
    tz = ZoneInfo(tz_name.strip())
    start_ts, end_ts = _local_day_unix_range(tz_name, date_str)
    name_sql = _label_preferred_name_sql(conn)
    sql = f"""
        SELECT l.scientific_name,
               {name_sql} AS common_name,
               d.detected_at,
               ic.url AS image_url
        FROM detections d
        JOIN labels l ON l.id = d.label_id
        LEFT JOIN image_caches ic ON ic.label_id = l.id
        WHERE d.detected_at >= ? AND d.detected_at < ?
    """
    rows = conn.execute(sql, [start_ts, end_ts]).fetchall()

    # (scientific_name, hour) -> count ; common_name et image par espèce
    counts: dict[tuple[str, int], int] = defaultdict(int)
    meta: dict[str, dict[str, str]] = {}

    for row in rows:
        r = _row_to_dict(row)
        ts = r.get("detected_at")
        if ts is None:
            continue
        try:
            local_dt = datetime.fromtimestamp(int(ts), tz=tz)
        except (ValueError, OSError, OverflowError):
            continue
        if local_dt.date() != date_type.fromisoformat(date_str):
            continue
        hour = local_dt.hour
        name = r.get("scientific_name") or ""
        if not name:
            continue
        counts[(name, hour)] += 1
        if name not in meta:
            meta[name] = {
                "common_name": (r.get("common_name") or "").strip() or name,
                "image_url": (r.get("image_url") or "").strip(),
            }
        else:
            if not meta[name]["image_url"] and r.get("image_url"):
                meta[name]["image_url"] = (r.get("image_url") or "").strip()
            cn = (r.get("common_name") or "").strip()
            if cn and meta[name]["common_name"] == name:
                meta[name]["common_name"] = cn

    out: list[dict[str, Any]] = []
    for (sci, hour), cnt in counts.items():
        m = meta.get(sci, {"common_name": sci, "image_url": ""})
        out.append(
            {
                "scientific_name": sci,
                "common_name": m["common_name"],
                "hour": hour,
                "count": cnt,
                "image_url": m["image_url"],
            }
        )
    return out


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

    name_sql = _label_preferred_name_sql(conn)
    sql = f"""
        SELECT l.scientific_name, MAX({name_sql}) AS common_name, COUNT(*) AS count,
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
            "common_name": (r := _row_to_dict(row)).get("common_name")
            or r.get("scientific_name")
            or "",
            "scientific_name": r.get("scientific_name") or "",
            "count": int(r.get("count") or 0),
            "image_url": r.get("image_url") or "",
        }
        for row in rows
    ]


def get_hourly_detections(
    conn: sqlite3.Connection, date_str: str, timezone: Optional[str] = None
) -> dict:
    """Hourly detection counts per species for a given date (YYYY-MM-DD).

    timezone: fuseau IANA (ex. America/Toronto). Si renseigné (schéma v2), les heures
    0–23 et le filtre « jour » sont calculés avec zoneinfo (indépendant du TZ du
    processus / Docker). Sinon, comportement historique : SQLite localtime.
    """
    schema = detect_schema(conn)
    tz_cfg = (timezone or "").strip()

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
        if tz_cfg and ZoneInfo is not None:
            try:
                ZoneInfo(tz_cfg)  # valide le nom
            except Exception as e:
                raise ValueError(f"timezone IANA invalide: {tz_cfg!r} ({e})") from e
            rows = _hourly_v2_with_timezone(conn, date_str, tz_cfg)
        else:
            name_sql = _label_preferred_name_sql(conn)
            sql = f"""
                SELECT l.scientific_name,
                       MAX({name_sql}) AS common_name,
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

    # Heures 0–23 : même fuseau que les comptes (IANA si timezone config, sinon serveur).
    sr_h = ss_h = None
    tz_for_sun = None
    if tz_cfg and ZoneInfo is not None:
        try:
            tz_for_sun = ZoneInfo(tz_cfg)
        except Exception:
            tz_for_sun = None
    if de.get("sunrise") is not None:
        try:
            ts = int(de["sunrise"])
            if tz_for_sun is not None:
                sr_h = int(datetime.fromtimestamp(ts, tz=tz_for_sun).hour)
            else:
                sr_h = int(datetime.fromtimestamp(ts).hour)
        except (ValueError, TypeError, OSError, OverflowError):
            sr_h = None
    if de.get("sunset") is not None:
        try:
            ts = int(de["sunset"])
            if tz_for_sun is not None:
                ss_h = int(datetime.fromtimestamp(ts, tz=tz_for_sun).hour)
            else:
                ss_h = int(datetime.fromtimestamp(ts).hour)
        except (ValueError, TypeError, OSError, OverflowError):
            ss_h = None

    out: dict[str, Any] = {
        "date": date_str,
        "sunrise": de.get("sunrise"),
        "sunset": de.get("sunset"),
        "sunrise_hour": sr_h,
        "sunset_hour": ss_h,
        "species": species_list,
    }
    if tz_cfg:
        out["timezone"] = tz_cfg
    return out


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
        name_sql = _label_preferred_name_sql(conn)
        sql = f"""
            SELECT l.scientific_name,
                   MAX({name_sql}) AS common_name,
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
