import re
from datetime import datetime
from typing import Dict, Any, Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:
    pass

from src.services.supabase_service import supabase_service

def is_valid_timezone(timezone: str) -> bool:
    try:
        ZoneInfo(timezone)
        return True
    except Exception:
        return False

def normalize_phone_number(phone: str) -> str:
    cleaned = re.sub(r'[^\d+]', '', phone)
    if not cleaned.startswith('+'):
        cleaned = '+' + cleaned
    return cleaned

def is_valid_phone_number(phone: str) -> bool:
    cleaned = re.sub(r'[^\d+]', '', phone)
    digits_only = cleaned.replace('+', '')
    if len(digits_only) < 10 or len(digits_only) > 15:
        return False
    return bool(re.match(r'^\+?\d{10,15}$', cleaned))

def format_in_timezone(date_str: str, timezone: str) -> str:
    # Safely convert arbitrary UTC date strings into specific TZ outputs dropping commas and caps
    # e.g., 'mar 25 2024 5:30 pm' natively targeting TS `date.toLocaleString('en-US').replace(',', '').toLowerCase()`
    if not date_str:
        return ""
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        tz_aware_dt = dt.astimezone(ZoneInfo(timezone))
        
        month = tz_aware_dt.strftime('%b')
        day = tz_aware_dt.day
        year = tz_aware_dt.strftime('%Y')
        
        time_str = tz_aware_dt.strftime('%I:%M %p') 
        if time_str.startswith("0"):
            time_str = time_str[1:]
            
        formatted = f"{month} {day} {year} {time_str}".lower()
        return formatted
    except Exception:
        return date_str

def fetch_all_appointments(patient_phone_number: str, organisation_id: str, timezone: str = "UTC") -> Dict[str, Any]:
    if not patient_phone_number:
        return {"result_code": 400, "error": "patient_phone_number is required"}
    if not organisation_id:
        return {"result_code": 400, "error": "organisation id is required"}
    if not is_valid_phone_number(patient_phone_number):
        return {"result_code": 400, "error": "Invalid patient_phone_number format"}
    if not is_valid_timezone(timezone):
        return {"result_code": 400, "error": "Invalid timezone"}
        
    normalized_phone = normalize_phone_number(patient_phone_number)
    client = supabase_service.client
    
    try:
        # Step 1: Patient lookup
        patient_res = client.table("patient").select("id").eq("patient_phone_no", normalized_phone).eq("organisation_id", organisation_id).limit(1).execute()
        if not patient_res.data:
            return {"result_code": 404, "error": "Patient not found"}
        patient_id = patient_res.data[0]["id"]
        
        # Step 2: Fetch future/current appointments
        now = datetime.utcnow().isoformat() + "+00:00"
        appts_res = client.table("appointments").select("*").eq("patient", patient_id).eq("organisation_id", organisation_id).gte("appointment_start_date", now).order("appointment_start_date", desc=True).execute()
        appts = appts_res.data or []
        
        # Step 3: Fetch related doctors to assemble names natively against UUID
        doctor_ids = list(set([a.get("doctor") for a in appts if a.get("doctor")]))
        doctor_map = {}
        
        if doctor_ids:
            docs_res = client.table("users_profile").select("user_id, user_name, user_specialization").in_("user_id", doctor_ids).execute()
            if docs_res.data:
                for d in docs_res.data:
                    doctor_map[d.get("user_id")] = d
                    
        # Step 4: Formatting
        formatted = []
        for a in appts:
            doc = doctor_map.get(a.get("doctor")) or {}
            formatted.append({
                "appointment_id": a.get("id"),
                "doctor_id": a.get("doctor"),
                "doctor_name": doc.get("user_name"),
                "doctor_specialization": doc.get("user_specialization"),
                "appointment_start": format_in_timezone(a.get("appointment_start_date"), timezone),
                "appointment_end": format_in_timezone(a.get("appointment_end_date"), timezone),
                "status": a.get("appointment_status"),
                "patient_no_show": a.get("patient_no_show"),
                "created_at": a.get("created_at"),
                "location": a.get("location")
            })
            
        return {
            "result_code": 101,
            "organisation_id": organisation_id,
            "appointments": formatted
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"result_code": 500, "error": str(e) or "Cannot fetch your appointments"}
