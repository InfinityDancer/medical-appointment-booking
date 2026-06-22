import re
from datetime import datetime, timedelta, timezone as dt_timezone
from typing import Dict, Any, List, Optional
try:
    from zoneinfo import ZoneInfo
except ImportError:
    pass

from src.services.supabase_service import supabase_service
from src.utils.location_resolver import resolve_location_id

def is_valid_timezone(timezone: str) -> bool:
    try:
        ZoneInfo(timezone)
        return True
    except Exception:
        return False

def is_valid_email(email: str) -> bool:
    return bool(re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email))

def is_valid_phone_number(phone: str) -> bool:
    cleaned = re.sub(r'[^\d+]', '', phone)
    digits_only = cleaned.replace('+', '')
    if len(digits_only) < 10 or len(digits_only) > 15:
        return False
    return bool(re.match(r'^\+?\d{10,15}$', cleaned))

def normalize_phone_number(phone: str) -> str:
    cleaned = re.sub(r'[^\d+]', '', phone)
    if not cleaned.startswith('+'):
        cleaned = '+' + cleaned
    return cleaned

def is_valid_date(date_str: str) -> bool:
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return True
    except ValueError:
        return False

def is_valid_time(time_str: str) -> bool:
    return bool(re.match(r'^([01]?[0-9]|2[0-3]):([0-5][0-9])$', time_str))

def combine_datetime(date_str: str, time_str: str, timezone_str: str) -> datetime:
    tz = ZoneInfo(timezone_str)
    naive_dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
    aware_dt = naive_dt.replace(tzinfo=tz)
    return aware_dt.astimezone(dt_timezone.utc)

def format_in_timezone(dt: datetime, timezone_str: str) -> str:
    tz_aware_dt = dt.astimezone(ZoneInfo(timezone_str))
    month = tz_aware_dt.strftime('%b')
    day = tz_aware_dt.day
    year = tz_aware_dt.strftime('%Y')
    
    time_str = tz_aware_dt.strftime('%I:%M %p') 
    if time_str.startswith("0"):
        time_str = time_str[1:]
        
    return f"{month} {day} {year} {time_str}".lower()

def get_day_in_timezone(dt: datetime, timezone_str: str) -> str:
    tz_aware_dt = dt.astimezone(ZoneInfo(timezone_str))
    return tz_aware_dt.strftime('%A').lower()

def calculate_interval(requested_time: datetime, slot_duration: int, timezone_str: str):
    tz = ZoneInfo(timezone_str)
    local_time = requested_time.astimezone(tz)
    
    minutes_since_midnight = local_time.hour * 60 + local_time.minute
    interval_index = minutes_since_midnight // slot_duration
    
    interval_start_mins = interval_index * slot_duration
    interval_end_mins = interval_start_mins + slot_duration
    
    start_hour = interval_start_mins // 60
    start_minute = interval_start_mins % 60
    end_hour = interval_end_mins // 60
    end_minute = interval_end_mins % 60
    
    start_time_str = f"{start_hour:02d}:{start_minute:02d}"
    
    if end_hour >= 24:
        next_day = local_time + timedelta(days=1)
        end_date_str = next_day.strftime("%Y-%m-%d")
        end_time_str = f"{(end_hour - 24):02d}:{end_minute:02d}"
    else:
        end_date_str = local_time.strftime("%Y-%m-%d")
        end_time_str = f"{end_hour:02d}:{end_minute:02d}"
        
    date_str = local_time.strftime("%Y-%m-%d")
    
    interval_start = combine_datetime(date_str, start_time_str, timezone_str)
    interval_end = combine_datetime(end_date_str, end_time_str, timezone_str)
    
    return interval_start, interval_end

def minutes_to_time(base_dt: datetime, mins: int, timezone_str: str) -> datetime:
    tz = ZoneInfo(timezone_str)
    local_time = base_dt.astimezone(tz)
    date_str = local_time.strftime("%Y-%m-%d")
    
    hours = mins // 60
    minutes = mins % 60
    time_str = f"{hours:02d}:{minutes:02d}"
    
    return combine_datetime(date_str, time_str, timezone_str)

def is_overlapping_unavailable(slot_start: datetime, slot_end: datetime, blocks: List[Dict]) -> bool:
    for block in blocks:
        block_start = datetime.fromisoformat(block["available_date_start_time"].replace("Z", "+00:00"))
        block_end = datetime.fromisoformat(block["available_date_end_time"].replace("Z", "+00:00"))
        if block_end <= block_start:
            continue
        if slot_start < block_end and slot_end > block_start:
            return True
    return False

def is_within_working_hours(requested_time: datetime, interval_end: datetime, roster: List[Dict], timezone_str: str) -> Dict:
    availability = {}
    for row in roster:
        if row.get("weekday") and row.get("availability") and row["availability"].get("timing"):
            day = row["weekday"].lower()
            if day not in availability:
                availability[day] = []
            availability[day].append(row["availability"])
            
    day_name = get_day_in_timezone(requested_time, timezone_str)
    day_configs = availability.get(day_name, [])
    
    if not day_configs:
        return {"valid": False, "message": f"Doctor is not available on {day_name}"}
        
    fits = False
    for config in day_configs:
        day_start = minutes_to_time(requested_time, config["timing"][0], timezone_str)
        day_end = minutes_to_time(requested_time, config["timing"][1], timezone_str)
        if requested_time >= day_start and interval_end <= day_end:
            fits = True
            break
            
    if not fits:
        intervals_str = ", ".join([f"{format_in_timezone(minutes_to_time(requested_time, dc['timing'][0], timezone_str), timezone_str)} - {format_in_timezone(minutes_to_time(requested_time, dc['timing'][1], timezone_str), timezone_str)}" for dc in day_configs])
        return {"valid": False, "message": f"Requested slot is outside doctor's working hours. Available intervals on {day_name}: {intervals_str}"}
        
    return {"valid": True}

def book_appointment_natively(
    slot_start_date: str,
    slot_start_time: str,
    doctor_id: str,
    patient_phone_number: str,
    organisation_id: str,
    location: str,
    patient_name: str = None,
    patient_dob: str = None,
    patient_gender: str = None,
    patient_email: str = None,
    timezone: str = "UTC"
) -> Dict[str, Any]:
    # Validations
    if not slot_start_date: return {"result_code": 400, "error": "slot_start_date is required"}
    if not location: return {"result_code": 400, "error": "location is required"}
    if not slot_start_time: return {"result_code": 400, "error": "slot_start_time is required"}
    if not doctor_id: return {"result_code": 400, "error": "doctor_id is required"}
    if not patient_phone_number: return {"result_code": 400, "error": "patient phone number is required"}
    if not organisation_id: return {"result_code": 400, "error": "organisation id is required"}
    if not is_valid_date(slot_start_date): return {"result_code": 400, "error": "Invalid slot_start_date format. Use YYYY-MM-DD"}
    if not is_valid_time(slot_start_time): return {"result_code": 400, "error": "Invalid slot_start_time format. Use HH:MM (24-hour)"}
    if patient_dob and not is_valid_date(patient_dob): return {"result_code": 400, "error": "Invalid patient_dob format. Use YYYY-MM-DD"}
    if patient_email and not is_valid_email(patient_email): return {"result_code": 400, "error": "Invalid patient_email format"}
    if not is_valid_phone_number(patient_phone_number): return {"result_code": 400, "error": "Invalid patient_phone_number. Must be 10-15 digits with optional country code"}
    if not is_valid_timezone(timezone): return {"result_code": 400, "error": f"Invalid timezone: {timezone}. Use IANA format like \"America/New_York\""}
    
    valid_genders = ["male", "female", "other"]
    if patient_gender and patient_gender.lower() not in valid_genders:
        return {"result_code": 400, "error": f"Invalid patient_gender. Must be one of: {', '.join(valid_genders)}"}
        
    normalized_phone = normalize_phone_number(patient_phone_number)
    requested_time = combine_datetime(slot_start_date, slot_start_time, timezone)
    
    if requested_time < datetime.now(dt_timezone.utc):
        return {"result_code": 400, "error": "Cannot book appointments in the past"}

    # Resolve location text to UUID for roster/availability tables
    location_id = resolve_location_id(organisation_id, location)
    if not location_id:
        return {"result_code": 404, "error": f"Location '{location}' not found for this organization"}

    client = supabase_service.client
    
    try:
        # Doctor Config
        doc_res = client.table("users_profile").select("user_id, user_name, user_specialization, slot_duration, max_slot").eq("user_id", doctor_id).eq("organisation_id", organisation_id).eq("user_role", "doctor").limit(1).execute()
        if not doc_res.data:
            return {"result_code": 404, "error": "Doctor not found"}
            
        doctor = doc_res.data[0]
        slot_duration = doctor.get("slot_duration")
        max_slots = doctor.get("max_slot")
        
        if not slot_duration or slot_duration <= 0: return {"result_code": 400, "error": "Doctor has not configured slot duration"}
        if not max_slots or max_slots <= 0: return {"result_code": 400, "error": "Doctor has not configured maximum slots per interval"}
        
        interval_start, interval_end = calculate_interval(requested_time, int(slot_duration), timezone)
        
        # Date-Specific Available Overrides (uses UUID location)
        tz_aware = interval_start.astimezone(ZoneInfo(timezone))
        day_start_local = tz_aware.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end_local = day_start_local + timedelta(days=1)
        
        avail_res = client.table("doctor_date_specific_availability") \
            .select("available_date_start_time, available_date_end_time") \
            .eq("doctor", doctor_id) \
            .eq("organisation_id", organisation_id) \
            .eq("location", location_id) \
            .eq("unavailable", False).execute()
            
        date_specific_avail_blocks = []
        for block in (avail_res.data or []):
            block_start = datetime.fromisoformat(block["available_date_start_time"].replace("Z", "+00:00"))
            block_end = datetime.fromisoformat(block["available_date_end_time"].replace("Z", "+00:00"))
            if block_start >= day_start_local and block_start < day_end_local:
                date_specific_avail_blocks.append((block_start, block_end))
                
        if date_specific_avail_blocks:
            fits = False
            intervals_str_list = []
            for b_s, b_e in date_specific_avail_blocks:
                intervals_str_list.append(f"{format_in_timezone(b_s, timezone)} - {format_in_timezone(b_e, timezone)}")
                if interval_start >= b_s and interval_end <= b_e:
                    fits = True
                    break
                    
            if not fits:
                day_name = get_day_in_timezone(interval_start, timezone)
                intervals_str = ", ".join(intervals_str_list)
                return {"result_code": 400, "error": f"Requested slot is outside doctor's working hours. Available intervals on {day_name} are strictly: {intervals_str}"}
        else:
            # Roster (uses UUID location)
            roster_res = client.table("new_doctor_roster_config_duplicate").select("*").eq("doctor", doctor_id).eq("organisation_id", organisation_id).eq("location", location_id).execute()
            if not roster_res.data:
                return {"result_code": 404, "error": "Doctor's schedule is not configured"}
                
            working_validation = is_within_working_hours(requested_time, interval_end, roster_res.data, timezone)
            if not working_validation["valid"]:
                return {"result_code": 400, "error": working_validation["message"]}
            
        # Unavailability Blocks (uses UUID location)
        int_start_str = interval_start.isoformat().replace("+00:00", "Z")
        int_end_str = interval_end.isoformat().replace("+00:00", "Z")
        
        unavail_res = client.table("doctor_date_specific_availability").select("available_date_start_time, available_date_end_time").eq("doctor", doctor_id).eq("unavailable", True).eq("organisation_id", organisation_id).eq("location", location_id).lte("available_date_start_time", int_end_str).gte("available_date_end_time", int_start_str).execute()
        
        if is_overlapping_unavailable(interval_start, interval_end, unavail_res.data or []):
            return {"result_code": 409, "error": "Doctor is not available during this time interval"}
            
        # Patient Upsert
        patient_id = None
        final_patient_name = patient_name
        
        or_clause = f"patient_phone_no.eq.{normalized_phone}"
        if patient_email:
            or_clause = f"patient_email.eq.{patient_email.lower()},{or_clause}"
            
        existing_patient_res = client.table("patient").select("*").eq("organisation_id", organisation_id).or_(or_clause).limit(1).execute()
        
        if existing_patient_res.data:
            existing = existing_patient_res.data[0]
            patient_id = existing["id"]
            final_patient_name = patient_name or existing.get("patient_name")
            
            update_data = {}
            if patient_name: update_data["patient_name"] = patient_name.strip()
            if patient_dob: update_data["patient_date_of_birth"] = patient_dob
            if patient_gender: update_data["patient_gender"] = patient_gender.lower()
            if patient_email: update_data["patient_email"] = patient_email.lower()
            if normalized_phone: update_data["patient_phone_no"] = normalized_phone
            
            if update_data:
                client.table("patient").update(update_data).eq("id", patient_id).execute()
        else:
            if not patient_name:
                return {"result_code": 400, "error": "patient_name is required for new patients"}
            new_patient = {
                "patient_name": patient_name.strip(),
                "patient_date_of_birth": patient_dob or None,
                "patient_gender": patient_gender.lower() if patient_gender else "other",
                "patient_email": patient_email.lower() if patient_email else None,
                "patient_phone_no": normalized_phone,
                "organisation_id": organisation_id
            }
            create_res = client.table("patient").insert(new_patient).execute()
            if not create_res.data:
                return {"result_code": 500, "error": "Failed to create patient record"}
            patient_id = create_res.data[0]["id"]
            
        # Dup check
        dup_res = client.table("appointments").select("id, appointment_start_date, appointment_end_date, appointment_status").eq("doctor", doctor_id).eq("organisation_id", organisation_id).eq("patient", patient_id).neq("appointment_status", "cancelled").gte("appointment_start_date", int_start_str).lt("appointment_start_date", int_end_str).execute()
        
        if dup_res.data:
            existing = dup_res.data[0]
            return {
                "result_code": 101,
                "message": "You already have an appointment in this interval",
                "appointment": {
                    "appointment_id": existing["id"],
                    "doctor_id": doctor_id,
                    "patient_id": patient_id,
                    "appointment_start": format_in_timezone(datetime.fromisoformat(existing["appointment_start_date"].replace("Z", "+00:00")), timezone),
                    "appointment_end": format_in_timezone(datetime.fromisoformat(existing["appointment_end_date"].replace("Z", "+00:00")), timezone),
                    "organisation_id": organisation_id,
                    "status": existing["appointment_status"]
                }
            }
            
        # Capacity Check
        cap_res = client.table("appointments").select("id, appointment_start_date, appointment_end_date").eq("doctor", doctor_id).eq("organisation_id", organisation_id).neq("appointment_status", "cancelled").gte("appointment_start_date", int_start_str).lt("appointment_start_date", int_end_str).execute()
        current_count = len(cap_res.data) if cap_res.data else 0
        
        if current_count >= max_slots:
            return {"result_code": 409, "error": f"This time interval ({format_in_timezone(interval_start, timezone)} - {format_in_timezone(interval_end, timezone)}) has reached maximum capacity ({max_slots} appointments). Please choose a different time."}
            
        # Create Appointment
        new_apt = {
            "doctor": doctor_id,
            "patient": patient_id,
            "appointment_start_date": int_start_str,
            "appointment_end_date": int_end_str,
            "patient_no_show": False,
            "appointment_status": "booked",
            "organisation_id": organisation_id,
            "location": location
        }
        apt_res = client.table("appointments").insert(new_apt).execute()
        if not apt_res.data:
            return {"result_code": 500, "error": "Failed to create appointment"}
            
        apt = apt_res.data[0]
        
        return {
            "result_code": 101,
            "message": "Appointment booked successfully",
            "appointment": {
                "appointment_id": apt["id"],
                "doctor_name": doctor.get("user_name"),
                "doctor_id": doctor.get("user_id"),
                "doctor_specialization": doctor.get("user_specialization"),
                "patient_name": final_patient_name,
                "patient_email": patient_email.lower() if patient_email else None,
                "patient_phone_number": normalized_phone,
                "interval_start": format_in_timezone(interval_start, timezone),
                "interval_end": format_in_timezone(interval_end, timezone),
                "timezone": timezone,
                "status": "booked",
                "created_at": apt.get("created_at"),
                "organisation_id": organisation_id,
                "slots_remaining_in_interval": int(max_slots) - current_count - 1,
                "location": location
            }
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"result_code": 500, "error": str(e) or "Internal server error"}
