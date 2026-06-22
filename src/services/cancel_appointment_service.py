from datetime import datetime
try:
    from zoneinfo import ZoneInfo
except ImportError:
    pass

from src.services.supabase_service import supabase_service
from src.services.api_service import create_ticket

def is_valid_timezone(timezone: str) -> bool:
    try:
        ZoneInfo(timezone)
        return True
    except Exception:
        return False

def format_in_timezone(date_str: str, timezone: str) -> str:
    # "2024-03-25T12:00:00+00" native parsing handling Javascript lowercase rules dropping commas
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        tz_aware_dt = dt.astimezone(ZoneInfo(timezone))
        
        month = tz_aware_dt.strftime('%b')
        day = tz_aware_dt.day
        year = tz_aware_dt.strftime('%Y')
        
        time_str = tz_aware_dt.strftime('%I:%M %p') 
        if time_str.startswith("0"):
            time_str = time_str[1:]
            
        return f"{month} {day} {year} {time_str}".lower()
    except Exception:
        return date_str

def cancel_appointment_natively(appointment_id: str, timezone: str = "UTC") -> dict:
    if not appointment_id: return {"result_code": 400, "error": "appointment_id is required"}
    if not is_valid_timezone(timezone): return {"result_code": 400, "error": "Invalid timezone"}
    
    client = supabase_service.client
    
    try:
        res = client.table("appointments").select("*").eq("id", appointment_id).limit(1).execute()
        if not res.data:
            return {"result_code": 404, "error": "appointment id is not valid"}
            
        fetched = res.data[0]
        start_date_str = fetched.get("appointment_start_date")
        end_date_str = fetched.get("appointment_end_date")
        
        # Dynamically resolve timezone based on the fetched appointment's location and organisation
        org_id = fetched.get("organisation_id")
        loc = fetched.get("location")
        if org_id and loc:
            from src.utils.timezone_resolver import get_location_timezone
            timezone = get_location_timezone(org_id, loc)
        
        end_dt = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
        now_dt = datetime.now(end_dt.tzinfo) 
        
        if end_dt < now_dt:
            return {"result_code": 400, "error": "Cannot cancel a past appointment"}
            
        # Idempotent response
        if fetched.get("appointment_status") == "cancelled":
            fetched["appointment_start_local"] = format_in_timezone(start_date_str, timezone)
            fetched["appointment_end_local"] = format_in_timezone(end_date_str, timezone)
            return {
                "result_code": 101,
                "message": "Appointment already cancelled",
                "appointment": fetched
            }
            
        update_res = client.table("appointments").update({"appointment_status": "cancelled"}).eq("id", appointment_id).execute()
        if not update_res.data:
            return {"result_code": 500, "error": "Failed to cancel appointment"}
            
        updated = update_res.data[0]
        # Generate the specific string formatting requested by nodes dynamically
        updated["appointment_start_local"] = format_in_timezone(updated.get("appointment_start_date"), timezone)
        updated["appointment_end_local"] = format_in_timezone(updated.get("appointment_end_date"), timezone)
        
        return {
            "result_code": 101,
            "message": "Appointment cancelled successfully",
            "updated_appointment": updated
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"result_code": 500, "error": "cannot cancel the appointment"}
        print("failed to cancel appointment", e)
