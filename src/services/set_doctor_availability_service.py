from datetime import datetime
import zoneinfo
from zoneinfo import ZoneInfo, available_timezones
from typing import Dict, Any, List
from src.services.supabase_service import supabase_service
from src.utils.location_resolver import resolve_location_id


def _parse_time(time_str: str) -> datetime:
    """parse hh:mm (24hr) or hh:mm am/pm to naive dt.
    params: time_str (str)
    output: datetime object
    """
    t = time_str.strip().upper()
    try:
        return datetime.strptime(t, "%I:%M %p") if ("AM" in t or "PM" in t) else datetime.strptime(t, "%H:%M")
    except ValueError:
        raise ValueError(f"bad time fmt: '{time_str}'. expected hh:mm or hh:mm am/pm")


def _validate_timezone(tz_str: str) -> str | None:
    """check tz in iana db and constructable. 
    params: tz_str (str)
    output: err msg or none
    """
    if not tz_str or not tz_str.strip():
        return "timezone required"
    if tz_str not in available_timezones():
        return f"unrecognised tz: '{tz_str}'. use iana fmt e.g. 'asia/kolkata'"
    try:
        ZoneInfo(tz_str)
    except Exception:
        return f"tz '{tz_str}' recognized but unavailable locally"
    return None


def _validate_date(date_str: str) -> tuple[datetime | None, str | None]:
    """check date fmt and cal logic.
    params: date_str (str)
    output: (parsed_dt, err_msg)
    """
    if not date_str or not date_str.strip():
        return None, "date required"
    try:
        dt = datetime.strptime(date_str.strip(), "%Y-%m-%d")
        return dt, None
    except ValueError:
        return None, f"invalid date: '{date_str}'. expected yyyy-mm-dd with real cal date"


def _overlaps(s1: datetime, e1: datetime, s2: datetime, e2: datetime) -> bool:
    """true if [s1,e1) and [s2,e2) intersect.
    params: s1, e1, s2, e2 (datetime)
    output: bool
    """
    return s1 < e2 and e1 > s2


def update_doctor_availability(
    doctor_id: str,
    organisation_id: str,
    date_str: str,
    timezone_str: str,
    blocks: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """main write path for doc avail overrides.
    params: doctor_id, organisation_id, date_str, timezone_str, blocks
    output: dict with success status/msg
    """
    if not doctor_id or not doctor_id.strip():
        return {"success": False, "error_code": "MISSING_DOCTOR_ID", "message": "doctor_id required"}
    if not organisation_id or not organisation_id.strip():
        return {"success": False, "error_code": "MISSING_ORG_ID", "message": "organisation_id required"}
    if not blocks:
        return {"success": False, "error_code": "MISSING_BLOCKS", "message": "blocks array empty"}

    tz_err = _validate_timezone(timezone_str)
    if tz_err:
        return {"success": False, "error_code": "INVALID_TIMEZONE", "message": tz_err}
    tz = ZoneInfo(timezone_str)

    date_naive, date_err = _validate_date(date_str)
    if date_err:
        return {"success": False, "error_code": "INVALID_DATE", "message": date_err}

    client = supabase_service.client

    # fetch day records in one shot
    day_start = datetime(date_naive.year, date_naive.month, date_naive.day, 0, 0, 0).replace(tzinfo=tz).isoformat()
    day_end = datetime(date_naive.year, date_naive.month, date_naive.day, 23, 59, 59).replace(tzinfo=tz).isoformat()

    try:
        res = client.table("doctor_date_specific_availability") \
            .select("available_date_start_time, available_date_end_time, unavailable, location") \
            .eq("doctor", doctor_id) \
            .eq("organisation_id", organisation_id) \
            .gte("available_date_start_time", day_start) \
            .lte("available_date_start_time", day_end) \
            .execute()
        existing = res.data or []
    except Exception as e:
        return {"success": False, "error_code": "DB_READ_ERROR", "message": f"failed to fetch day records: {e}"}

    # check day-level lock: any unavailable=true record blocks inserts
    if any(r.get("unavailable") is True for r in existing):
        return {"success": False, "error_code": "DAY_LOCKED", "message": "doc marked fully unavailable for this day. no slots can be added."}

    pre_parsed_existing = []
    for r in existing:
        pre_parsed_existing.append({
            "start": datetime.fromisoformat(r["available_date_start_time"].replace("Z", "+00:00")),
            "end": datetime.fromisoformat(r["available_date_end_time"].replace("Z", "+00:00")),
            "unavailable": r.get("unavailable"),
            "location": r.get("location")
        })

    to_insert = []

    for block in blocks:
        loc_name = block.get("location", "").strip()
        if not loc_name:
            return {"success": False, "error_code": "MISSING_LOCATION", "message": "location required in each block"}

        location_id = resolve_location_id(organisation_id, loc_name)
        if not location_id:
            return {"success": False, "error_code": "LOCATION_NOT_FOUND", "message": f"location '{loc_name}' not found for this org"}

        try:
            ps = _parse_time(block["start_time"])
            pe = _parse_time(block["end_time"])
        except ValueError as e:
            return {"success": False, "error_code": "INVALID_TIME", "message": str(e)}

        start_dt = datetime(date_naive.year, date_naive.month, date_naive.day, ps.hour, ps.minute).replace(tzinfo=tz)
        end_dt = datetime(date_naive.year, date_naive.month, date_naive.day, pe.hour, pe.minute).replace(tzinfo=tz)

        if end_dt <= start_dt:
            return {"success": False, "error_code": "INVALID_TIME_RANGE", "message": f"end_time must be after start_time: {block['start_time']} -> {block['end_time']}"}

        is_unavail = block.get("is_unavailable", False)

        # block unavail=true if any avail already exists
        if is_unavail and existing:
            return {"success": False, "error_code": "DAY_HAS_AVAILABILITY", "message": "cannot mark day as unavailable when availability records exist"}

        # conflict check: dup or overlap vs db records
        for ex in pre_parsed_existing:
            if _overlaps(start_dt, end_dt, ex["start"], ex["end"]):
                exact_match = (start_dt == ex["start"] and end_dt == ex["end"] and is_unavail == ex["unavailable"])
                if exact_match:
                    return {"success": False, "error_code": "DUPLICATE_BLOCK", "message": f"identical block {block['start_time']}-{block['end_time']} exists"}
                return {"success": False, "error_code": "OVERLAP_CONFLICT", "message": f"block {block['start_time']}-{block['end_time']} overlaps with existing record"}

        to_insert.append({
            "doctor": doctor_id,
            "organisation_id": organisation_id,
            "location": location_id,
            "available_date_start_time": start_dt.isoformat(),
            "available_date_end_time": end_dt.isoformat(),
            "unavailable": is_unavail
        })

    try:
        client.table("doctor_date_specific_availability").insert(to_insert).execute()
    except Exception as e:
        return {"success": False, "error_code": "DB_WRITE_ERROR", "message": f"insert failed: {e}"}

    return {"success": True, "message": "availability updated."}
