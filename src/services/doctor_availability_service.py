from datetime import datetime, timedelta
import zoneinfo
from zoneinfo import ZoneInfo
from typing import Dict, List, Any, Optional
from src.services.supabase_service import supabase_service
from src.utils.location_resolver import resolve_location_id
from src.services.api_service import create_ticket_API
import traceback

def combine_datetime(date_str: str, time_str: str, tz_str: str) -> datetime:
    tz = ZoneInfo(tz_str)
    # Parse date and time (naive)
    dt_naive = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
    # Localize into the target timezone
    return dt_naive.replace(tzinfo=tz)

def fetch_doctor_availability_data(
    doctor_id: Optional[str] = None,
    doctor_name: Optional[str] = None,
    start_date: Optional[str] = None,
    start_time: Optional[str] = None,
    end_date: Optional[str] = None,
    end_time: Optional[str] = None,
    specialisation: Optional[str] = None,
    organisation_id: Optional[str] = None,
    location: Optional[str] = None,
    timezone_str: str = "UTC"
) -> Dict[str, Any]:
    # 1. Validations
    if not doctor_name and not doctor_id:
        return {"result_code": 400, "error": "doctor_name or doctor_id required"}
    if not location:
        return {"result_code": 400, "error": "location is required"}
    if not start_date or not start_time:
        return {"result_code": 400, "error": "start_date and start_time required"}
    if not organisation_id:
        return {"result_code": 400, "error": "organisation id is required"}

    # Resolve location text to UUID for roster/availability tables
    location_id = resolve_location_id(organisation_id, location)
    if not location_id:
        return {"result_code": 404, "error": f"Location '{location}' not found for this organization"}
        
    try:
        tz = ZoneInfo(timezone_str)
    except zoneinfo.ZoneInfoNotFoundError:
        return {"result_code": 400, "error": f"Invalid timezone: {timezone_str}. Use IANA format like 'Asia/Kolkata'"}
        
    now = datetime.now(tz)
    today_str = now.strftime("%Y-%m-%d")
    
    if start_date < today_str:
         return {"result_code": 400, "error": "Past day is not allowed"}
         
    start_ts = combine_datetime(start_date, start_time, timezone_str)
    
    if end_date and end_time:
        end_ts = combine_datetime(end_date, end_time, timezone_str)
    else:
        end_ts = start_ts + timedelta(days=1)
        
    if end_ts <= start_ts:
        return {"result_code": 400, "error": "End time must be greater than start time"}
        
    if (end_ts - start_ts).days > 7:
        return {"result_code": 400, "error": "Range cannot exceed 7 days"}

    client = supabase_service.client
    
    try:
        # 2. Find doctor
        query = client.table("users_profile").select("user_id,user_name,user_specialization,slot_duration,max_slot").eq("user_role", "doctor").eq("organisation_id", organisation_id)
        if doctor_id:
            query = query.eq("user_id", doctor_id)
        if doctor_name:
            query = query.ilike("user_name", f"%{doctor_name}%")
        if specialisation:
            query = query.eq("user_specialization", specialisation)
            
        doctors_res = query.execute()
        doctors = doctors_res.data
        if not doctors:
            return {"result_code": 404, "error": "Doctor not found"}
            
        doctor = doctors[0]
        doc_id = doctor["user_id"]
        slot_duration = int(doctor.get("slot_duration") or 0)
        max_slots = int(doctor.get("max_slot") or 0)
        
        if slot_duration <= 0:
            return {"result_code": 400, "error": "Doctor has not configured slot_duration"}
        if max_slots <= 0:
            return {"result_code": 400, "error": "Doctor has not configured max_slot"}
            
        # 3. Get roster (uses UUID location)
        roster_res = client.table("new_doctor_roster_config_duplicate").select("*") \
            .eq("doctor", doc_id) \
            .eq("location", location_id) \
            .eq("organisation_id", organisation_id).execute()
            
        roster = roster_res.data
        if not roster:
            return {"result_code": 404, "error": "No roster configured"}
            
        availability = {}
        for row in roster:
            weekday = row.get("weekday")
            avail = row.get("availability")
            if weekday and avail and "timing" in avail:
                day = weekday.lower()
                if day not in availability:
                    availability[day] = []
                availability[day].append(avail)

        # 4. Get specific availability blocks (uses UUID location)
        spec_avail_res = client.table("doctor_date_specific_availability") \
            .select("available_date_start_time, available_date_end_time, unavailable") \
            .eq("doctor", doc_id) \
            .eq("organisation_id", organisation_id) \
            .eq("location", location_id).execute()
        date_specific_configs = spec_avail_res.data or []
        
        # 5. Get existing appointments
        apt_res = client.table("appointments") \
            .select("appointment_start_date, appointment_end_date") \
            .eq("doctor", doc_id) \
            .eq("organisation_id", organisation_id) \
            .eq("location", location) \
            .neq("appointment_status", "cancelled") \
            .lt("appointment_start_date", end_ts.isoformat()) \
            .gt("appointment_end_date", start_ts.isoformat()).execute()
        existing_appointments = apt_res.data or []
        
        # 6. Generate slots
        slots = []
        cursor = start_ts
        
        def parse_iso(iso_str: str) -> datetime:
            if iso_str.endswith('Z'):
                 iso_str = iso_str[:-1] + '+00:00'
            dt = datetime.fromisoformat(iso_str)
            if dt.tzinfo is None:
                 dt = dt.replace(tzinfo=zoneinfo.ZoneInfo("UTC"))
            return dt

        parsed_unavail = []
        parsed_avail = []
        for c in date_specific_configs:
            s = parse_iso(c["available_date_start_time"])
            e = parse_iso(c["available_date_end_time"])
            if c.get("unavailable"):
                parsed_unavail.append((s, e))
            else:
                parsed_avail.append((s, e))
            
        parsed_appts = []
        for a in existing_appointments:
            s = parse_iso(a["appointment_start_date"])
            parsed_appts.append(s)

        def is_overlapping_unavailable(s_time: datetime, e_time: datetime) -> bool:
            for bs, be in parsed_unavail:
                if s_time < be and e_time > bs:
                    return True
            return False

        def count_appointments_in_interval(s_time: datetime, e_time: datetime) -> int:
            count = 0
            for apt_start in parsed_appts:
                if s_time <= apt_start < e_time:
                    count += 1
            return count
            
        def minutes_to_time_on_date(base_date: datetime, mins: int) -> datetime:
            hours = mins // 60
            minutes = mins % 60
            dt_naive = datetime(base_date.year, base_date.month, base_date.day, hours, minutes)
            return dt_naive.replace(tzinfo=tz)
            
        def format_in_timezone(dt: datetime) -> str:
            dt_local = dt.astimezone(tz)
            month = dt_local.strftime("%b").lower()
            day = dt_local.day
            year = dt_local.year
            hour = dt_local.strftime("%I")
            if hour.startswith("0"): 
                hour = hour[1:]
            minute = dt_local.strftime("%M")
            ampm = dt_local.strftime("%p").lower()
            return f"{month} {day} {year} {hour}:{minute} {ampm}"

        while cursor < end_ts:
            day_start = cursor
            day_end = cursor + timedelta(days=1)
            
            # Check if there are any availability overrides (unavailable=False) for this specific day
            day_avail_overrides = []
            for a_s, a_e in parsed_avail:
                if a_s >= day_start and a_s < day_end:
                    day_avail_overrides.append((a_s, a_e))
                    
            def generate_slots_for_range(block_start: datetime, block_end: datetime):
                slot_start = block_start
                while slot_start < block_end:
                    slot_end = slot_start + timedelta(minutes=slot_duration)
                    if slot_end > block_end:
                        break
                        
                    curr_now = datetime.now(tz)
                    if slot_start >= start_ts and slot_end <= end_ts and slot_start > curr_now:
                        if not is_overlapping_unavailable(slot_start, slot_end):
                            booked_count = count_appointments_in_interval(slot_start, slot_end)
                            if booked_count < max_slots:
                                slots_remaining = max_slots - booked_count
                                slots.append({
                                    "Doctor Name": doctor.get("user_name"),
                                    "Doctor Id": doc_id,
                                    "start_time": slot_start.astimezone(tz).strftime("%H:%M"),
                                    "end_time": slot_end.astimezone(tz).strftime("%H:%M"),
                                    "Doctor Availability Start Time": format_in_timezone(slot_start),
                                    "Doctor Availability End Time": format_in_timezone(slot_end),
                                    "Slots Booked": booked_count,
                                    "Slots Remaining": slots_remaining,
                                    "Max Slots": max_slots
                                })
                    slot_start = slot_end

            if day_avail_overrides:
                # Date-specific data takes precedence over roster
                for a_s, a_e in day_avail_overrides:
                    generate_slots_for_range(a_s, a_e)
            else:
                # Fallback to roster
                day_name = cursor.strftime("%A").lower()
                day_configs = availability.get(day_name)
                
                if day_configs:
                    for day_config in day_configs:
                        start_mins = day_config["timing"][0]
                        end_mins = day_config["timing"][1]
                        
                        block_start = minutes_to_time_on_date(cursor, start_mins)
                        block_end = minutes_to_time_on_date(cursor, end_mins)
                        
                        generate_slots_for_range(block_start, block_end)

            # Advance to next day at midnight
            next_day = cursor + timedelta(days=1)
            cursor_naive = datetime(next_day.year, next_day.month, next_day.day, 0, 0)
            cursor = cursor_naive.replace(tzinfo=tz)

        if not slots:
            return {"result_code": 404, "error": "No slot available"}
            
        return {
            "result_code": 101,
            "timezone": timezone_str,
            "location": location,
            "organisation_id": organisation_id,
            "Doctor Availability": slots
        }
    except Exception as e:
        traceback.print_exc()
        error_description = (
                f"[API ERROR] Fetch doctor availability API failed.\n"
                f"error: {str(e)}\n"
                )
        create_ticket_API(
            ticket_description=error_description
            )
        return {"result_code": 500, "error": str(e) or "Cannot fetch doctor availability"}
