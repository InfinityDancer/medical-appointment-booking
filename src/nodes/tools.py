import json
import requests
from langchain.tools import tool
import os
from dotenv import load_dotenv
from redis_config import get_redis_client
from src.utils.supabase_utils import supabase, semantic_search, get_organisation_id
load_dotenv()

token = os.getenv("BEARER_TOKEN") 

# print(token)

redis_client = get_redis_client()

SUPABASE_BASE_URL = "https://vdllmuxwkqenluqvezzn.supabase.co/functions/v1"
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")

@tool
def check_doctor_availability(doctor_id:str,start_date:str,start_time:str,end_time:str,end_date:str,location:str,organisation_id:str):
    """This tool checks the availability of a doctor using Supabase API, so a user can book an appointment."""
    location = location.lower() if location else location
    print("doctor availability", doctor_id, start_date, start_time, end_time, end_date, location)
    try:
        from src.services.doctor_availability_service import fetch_doctor_availability_data
        from src.utils.timezone_resolver import get_location_timezone
        
        dynamic_tz = get_location_timezone(organisation_id, location)
        
        data = fetch_doctor_availability_data(
            doctor_id=doctor_id,
            start_date=start_date,
            start_time=start_time,
            end_date=end_date,
            end_time=end_time,
            location=location,
            organisation_id=organisation_id,
            timezone_str=dynamic_tz
        )
        if data.get("result_code") == 101:
            print("✅ Doctor availability fetched successfully:", data)
            return {"status": "success", "data": data.get("Doctor Availability", [])}
        else:
            code = data.get('result_code')
            err = data.get('error', 'Unknown error')
            if code == 500:
                print(f"❌ Local function error (code {code}): {err}")
            elif code == 409:
                print(f"⚠️ Booking conflict (code {code}): {err}")
            else:
                print(f"ℹ️ Booking status (code {code}): {err}")
            return {"status": "error", "message": err, "raw": data}
    except Exception as e:
        print("❌ Something went wrong while fetching doctor availability:", e)
        return {"status": "error", "message": str(e)}

@tool
def get_appointment_list(patient_phone_number, organisation_id):
    """
    Fetch all appointments for a patient using Supabase Edge Function.
    Args:
        patient_phone_number (str): Patient's phone number
        timezone (str): Timezone string (e.g., 'Asia/Kolkata')
        organisation_id (str): Organisation UUID
    Returns:
        dict: Response from Supabase function
    """
    try:
        from src.services.fetch_all_appointments_service import fetch_all_appointments
        # For cross-location lists, we pass a default or resolve if a primary location is known. 
        # ideally the service handles it per appointment. Let's pass the default or None.
        data = fetch_all_appointments(
            patient_phone_number=patient_phone_number,
            organisation_id=organisation_id,
            timezone="Asia/Kolkata"
        )
        if data.get("result_code") == 101:
            print("✅ Fetched appointments successfully locally:", data)
            return data
        else:
            code = data.get('result_code')
            err = data.get('error', 'Unknown error')
            if code == 500:
                print(f"❌ Appointment fetch error (code {code}): {err}")
            elif code == 409:
                print(f"⚠️ Appointment fetch conflict (code {code}): {err}")
            else:
                print(f"ℹ️ Appointment fetch status (code {code}): {err}")
            return {"error": err, "status_code": code}
    except Exception as e:
        print("❌ Something went wrong while fetching appointments locally:", e)
        return {"error": str(e), "status_code": 500}
    except Exception as e:
        print("❌ Something went wrong:", e)
        return {"error": str(e)}


# getting data from the sheet
@tool
def symptom_mapping(symptom_name):
    """ return symtomps """
    from src.services.langgraph_service import fetch_sheet
    rows = fetch_sheet("1ZgxSH4eiCpbA68RfOC0gdB6thRve_xmcfq0IF-Ad5nw")
    if rows and len(rows) > 1:
        headers = rows[0]
        mapped_data = [dict(zip(headers, row)) for row in rows[1:]]
        return mapped_data
    return []


@tool
def get_appointments(phone_number: str, organisation_id: str = "") -> dict:
    """Retrieves appointment mappings for the user from Redis. Requires organisation_id for proper data isolation."""
    cache_key = f"appointments_{phone_number}_{organisation_id}" if organisation_id else f"appointments_{phone_number}"

    try:
        cached = redis_client.get(cache_key)
        if cached:
            # Decode bytes → str → JSON
            # decoded = json.loads(cached.decode("utf-8"))
            print(f"✅ Retrieved appointments from Redis for {phone_number} (org: {organisation_id})")
            return cached
        print(f"ℹ️ No appointments found in Redis for {phone_number} (org: {organisation_id})")
        return {"appointments": None}
    except Exception as e:
        print(f"❌ Error retrieving appointments from Redis: {e}")
        return {"error": str(e)}

@tool
def set_appointments(phone_number: str, appointment_data: dict | None = None, organisation_id: str = "", ttl: int = 300):
    """Stores user appointment mappings in Redis under their phone number and organisation, only if not already present."""
    # print("set: ",phone_number)
    print("apmt data",appointment_data)
    cache_key = f"appointments_{phone_number}_{organisation_id}" if organisation_id else f"appointments_{phone_number}"
    
    if not appointment_data:
        print("⚠️ No appointment data provided to set_appointments.")
        return {"success": False, "message": "No appointment data provided"}

    try:    
        if redis_client.exists(cache_key):
            print(f"ℹ️ Appointments already exist in Redis for {phone_number} (org: {organisation_id}). Skipping set.")
            return {"success": True, "message": "Already exists", "cache_key": cache_key}
        redis_client.set(cache_key, json.dumps(appointment_data), ex=ttl)
        print(f"✅ Saved appointments to Redis for {phone_number} (org: {organisation_id}): {appointment_data}")
        return {"success": True, "cache_key": cache_key}
    except Exception as e:
        print(f"❌ Error saving appointments to Redis: {e}")
        return {"success": False, "error": str(e)}
    

@tool
def get_all_doctors(organisation_id: str, location: str = None):
    """
    Fetch all doctors for an organisation using Supabase API, with Redis caching.
    Args:
        organisation_id (str): Organisation UUID (required)
        location (str, optional): Location to filter doctors
    Returns:
        dict: Response with doctors list and their schedules
    """
    location = location.lower() if location else location
    cache_key = f"all_doctors_{organisation_id}_{location or 'all'}"
    ttl = 21600  # 6 hours in seconds
    print("get_all_doctors", organisation_id, location)
    
    try:
        cached = redis_client.get(cache_key)
        if cached:
            print("✅ Retrieved doctors from Redis cache.")
            cached_data = json.loads(cached)
            # Ensure we return only the doctors array to match the fresh API fetch behavior
            doctors_list = cached_data.get("doctors", []) if isinstance(cached_data, dict) else cached_data
            return {"status": "success", "data": doctors_list}
    except Exception as e:
        print(f"⚠️ Error retrieving doctors from Redis: {e}")

    try:
        from src.services.doctor_service import fetch_all_doctors_data
        data = fetch_all_doctors_data(organisation_id=organisation_id, location=location)
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception as parse_err:
                print(f"❌ Failed to parse response: {parse_err}")
                return {"status": "error", "message": "Invalid response from doctor service"}
        if data.get("result_code") == 101:
            print("✅ Doctors fetched successfully locally.")
            try:
                redis_client.set(cache_key, json.dumps(data), ex=ttl)
                print("✅ Saved doctors to Redis cache.")
            except Exception as e:
                print(f"⚠️ Error saving doctors to Redis: {e}")
            return {"status": "success", "data": data.get("doctors", [])}
        else:
            code = data.get('result_code')
            err = data.get('error', 'Unknown error')
            if code == 500:
                print(f"❌ Local function error (code {code}): {err}")
            elif code == 409:
                print(f"⚠️ Doctor fetch conflict (code {code}): {err}")
            else:
                print(f"ℹ️ Doctor fetch status (code {code}): {err}")
            return {"status": "error", "message": err, "raw": data}
    except Exception as e:
        print("❌ Something went wrong:", e)
        return {"status": "error", "message": str(e)}



@tool
def get_patient_details(phone_number: str) -> dict:
    """ retrieve patient details """
    # Call Supabase Edge Function to fetch patient details by phone number
    # Call local backend Python service to fetch patient details by phone number
    try:
        from src.services.get_patient_details_service import fetch_patient_details
        
        target_phone = f"91{phone_number}"
        data = fetch_patient_details(phone_number=target_phone)
        
        # Supabase function responses use result_code 101 for success
        if data.get("result_code") == 101:
            print("✅ Patient details fetched successfully locally.")
            return {"status": "success", "data": data.get("patientDetail") or data}
        else:
            code = data.get('result_code')
            err = data.get('error') or data.get('message') or str(data)
            if code == 500:
                print(f"❌ Patient fetch error (code {code}): {err}")
            elif code == 409:
                print(f"⚠️ Patient fetch conflict (code {code}): {err}")
            else:
                print(f"ℹ️ Patient fetch status (code {code}): {err}")
            return {"status": "error", "message": err, "raw": data}
    except Exception as e:
        print("❌ Something went wrong while fetching patient details:", e)
        return {"status": "error", "message": str(e)}

@tool
def search_services(query: str, clinic_name: str) -> dict:
    """Search the clinic's knowledge base for information about services, treatments,
    procedures, and related documents. Use this when the user asks about specific
    medical services, treatments, procedures, pricing, or anything that might be
    documented in the clinic's service descriptions and related documents.
    IMPORTANT: Do NOT optimise, summarize, or rewrite the query. Let the query parameter be the user's exact original question.
    Requires the clinic_name to identify the correct organisation."""

    org_id = get_organisation_id(clinic_name)
    if not org_id:
        print(f"❌ [RAG] Could not resolve organisation_id for clinic: {clinic_name}")
        return {"status": "error", "message": "Could not resolve organisation for this clinic"}
    print(f"✅ [RAG] Resolved organisation_id: {org_id}")

    results = semantic_search(query, org_id)
    if results is None:
        print(f"❌ [RAG] Semantic search returned None (error)")
        return {"status": "error", "message": "Semantic search failed"}
    if not results:
        print(f"⚠️ [RAG] Semantic search returned 0 results")
        return {"status": "success", "data": [], "message": "No relevant results found"}
    return {"status": "success", "data": results}

@tool
def get_doctor_details(doctor_id: str, organisation_id: str) -> dict:
    """ Fetch the full specific profile details of a single doctor from the database. """
    try:
        from src.services.get_doctor_details_service import fetch_doctor_details
        
        data = fetch_doctor_details(doctor_id=doctor_id, organisation_id=organisation_id)
        
        if data.get("result_code") == 101:
            print(f"✅ Doctor details for {doctor_id} fetched successfully locally.")
            return {"status": "success", "data": data}
        else:
            code = data.get('result_code')
            err = data.get('error', 'Unknown error')
            if code == 500:
                print(f"❌ Doctor details error (code {code}): {err}")
            elif code == 409:
                print(f"⚠️ Doctor details conflict (code {code}): {err}")
            else:
                print(f"ℹ️ Doctor details status (code {code}): {err}")
            return {"status": "error", "message": err, "raw": data}
            
    except Exception as e:
        print("❌ Something went wrong while fetching doctor details locally:", e)
        return {"status": "error", "message": str(e)}

@tool
def get_timezone(doctor_time: str, organisation_id: str, location: str) -> dict:
    """ Convert UTC database timestamp string into the formatted local timezone string. Requires organisation_id and location. """
    try:
        from src.services.get_timezone_service import format_timezone
        from src.utils.timezone_resolver import get_location_timezone
        
        dynamic_tz = get_location_timezone(organisation_id, location)
        data = format_timezone(doctor_time=doctor_time, timezone_str=dynamic_tz)
        
        if "output" in data:
            print(f"✅ Timezone formatted successfully locally.")
            return {"status": "success", "data": data}
        else:
            err = data.get('error', 'Unknown error')
            print(f"ℹ️ Timezone format status: {err}")
            return {"status": "error", "message": err, "raw": data}
            
    except Exception as e:
        print("❌ Something went wrong while formatting timezone locally:", e)
        return {"status": "error", "message": str(e)}
