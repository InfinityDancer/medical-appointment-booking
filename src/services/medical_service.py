"""
Medical appointment management functions for voice agent.
Adapted from medisync for the voice agent to handle:
- Book appointment
- Cancel appointment  
- General inquiry
"""

import json
import re
import requests
import os
from dotenv import load_dotenv
from datetime import datetime
from src.utils.redis_client import get_redis_client
import gspread
from rapidfuzz import fuzz
from google.oauth2.service_account import Credentials

load_dotenv()

BEARER_TOKEN = os.getenv("BEARER_TOKEN")
API_BASE_URL = "https://medisync-60328.bubbleapps.io/version-test/api/1.1/wf"

# Initialize Redis client
try:
    redis_client = get_redis_client()
    redis_client.ping()  # Eagerly verify connectivity
    print("Redis client initialized successfully")
except Exception as e:
    print(f"Redis unavailable (will operate without cache): {e}")
    redis_client = None

# Appointment Status
APPOINTMENTS_DB = {}

# In-memory doctor list cache (populated on first successful get_all_doctors call)
_doctors_cache: list = []

def warm_doctors_cache():
    """Pre-fetch doctors list at startup so fuzzy matching works on the very first call."""
    global _doctors_cache
    if _doctors_cache:
        return  # already warm
    try:
        result = get_all_doctors()
        data = json.loads(result)
        if data.get("status") == "success":
            doctors = data.get("data", {}).get("Doctors", [])
            if doctors:
                _doctors_cache = doctors
                print(f"Doctor cache pre-warmed with {len(_doctors_cache)} entries")
            else:
                print("Warning: get_all_doctors returned empty list during cache warm-up")
        else:
            print(f"Warning: get_all_doctors failed during cache warm-up: {data.get('message')}")
    except Exception as e:
        print(f"Failed to pre-warm doctor cache: {e}")

# Google Sheets Configuration
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
SERVICE_ACCOUNT_FILE = os.path.join(os.path.dirname(__file__), "../../medisync/medisync-bot.json")
CLINIC_SHEET_ID = "1jHmZYp3OilWW0pid0fw2Xyw0XsWdmR6mt9TpznnwTuc"

def fetch_clinic_info():
    """
    Fetch clinic information from Google Sheets.
    Returns a dictionary with clinic data (cached in Redis).
    Falls back to Google Sheets directly when Redis is unavailable.
    
    Returns:
        dict: Clinic information with keys like clinic_hours, contact, services, etc.
    """
    cache_key = f"sheet:{CLINIC_SHEET_ID}"
    
    # --- Try Redis cache first (isolated so failure doesn't block Sheets) ---
    try:
        if redis_client:
            cached = redis_client.get(cache_key)
            if cached:
                print("Using cached clinic data from Redis")
                rows = json.loads(cached)
                if rows and len(rows) > 1:
                    headers = rows[0]
                    values = rows[1]
                    return dict(zip(headers, values))
    except Exception as e:
        print(f"Redis cache unavailable, falling back to Google Sheets: {e}")
    
    # --- Fetch from Google Sheets ---
    try:
        print("Fetching clinic data from Google Sheets...")
        creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
        client = gspread.authorize(creds)
        sheet = client.open_by_key(CLINIC_SHEET_ID).sheet1
        rows = sheet.get_all_values()
        
        if rows and len(rows) > 1:
            # Try to cache in Redis (best-effort)
            try:
                if redis_client:
                    redis_client.set(cache_key, json.dumps(rows), ex=3600)
                    print("Clinic data cached in Redis")
            except Exception as cache_err:
                print(f"~Could not cache clinic data in Redis: {cache_err}")
            
            print("Clinic data fetched from Google Sheets successfully")
            headers = rows[0]
            values = rows[1]
            return dict(zip(headers, values))
        else:
            print("No data found in Google Sheets")
            return {}
    
    except Exception as e:
        print(f"Error fetching clinic data from Google Sheets: {e}")
        return {}

def _merge_slots_into_ranges(slot_strings: list) -> list:
    """Merge consecutive 30-min slot start times into human-readable ranges.
    
    Input:  ["Mar 2, 2026 8:00 am", "Mar 2, 2026 8:30 am", "Mar 2, 2026 9:00 am",
             "Mar 2, 2026 2:00 pm", "Mar 2, 2026 2:30 pm"]
    Output: ["8:00 AM to 9:30 AM", "2:00 PM to 3:00 PM"]
    """
    if not slot_strings:
        return []

    from datetime import timedelta

    # Parse each slot string into a datetime
    parsed = []
    for s in slot_strings:
        for fmt in ("%b %d, %Y %I:%M %p", "%b %d, %Y %I:%M%p"):
            try:
                parsed.append(datetime.strptime(s, fmt))
                break
            except ValueError:
                continue

    if not parsed:
        return []

    parsed.sort()

    # Group consecutive slots (30 min apart)
    ranges = []
    range_start = parsed[0]
    range_end = parsed[0]

    for i in range(1, len(parsed)):
        if parsed[i] - range_end == timedelta(minutes=30):
            range_end = parsed[i]
        else:
            # Close current range (end = last slot start + 30 min)
            ranges.append((range_start, range_end + timedelta(minutes=30)))
            range_start = parsed[i]
            range_end = parsed[i]

    # Close final range
    ranges.append((range_start, range_end + timedelta(minutes=30)))

    # Format as human-readable strings
    def fmt_time(dt):
        if dt.minute == 0:
            return dt.strftime("%-I %p")  # "8 AM"
        return dt.strftime("%-I:%M %p")   # "8:30 AM"

    return [f"{fmt_time(s)} to {fmt_time(e)}" for s, e in ranges]

def get_doctor_availability(doctor_name: str, start_date: str, start_time: str, end_time: str, end_date: str):
    """
    Check doctor availability for appointment booking.
    Args:
        doctor_name: Name of the doctor
        start_date: Start date for availability check (YYYY-MM-DD)
        start_time: Start time (HH:MM)
        end_time: End time (HH:MM)
        end_date: End date (YYYY-MM-DD)
    
    Returns:
        dict: Availability data or error message
    """
    try:
        # Ensure dates are zero-padded (e.g. 2026-3-4 -> 2026-03-04)
        def _pad_date(d_str: str) -> str:
            if not d_str:
                return d_str
            parts = d_str.split('-')
            if len(parts) == 3:
                return f"{parts[0]}-{int(parts[1]):02d}-{int(parts[2]):02d}"
            return d_str
            
        start_date = _pad_date(start_date)
        end_date = _pad_date(end_date)
        
        # --- Enforce 14-day booking limit ---
        if start_date:
            from datetime import date, timedelta
            today = date.today()
            max_allowed_date = today + timedelta(days=14)
            
            # Parse start_date to check limit
            try:
                s_date_obj = datetime.strptime(start_date, "%Y-%m-%d").date()
                if s_date_obj > max_allowed_date:
                    return json.dumps({
                        "status": "success",
                        "bookable_slots": [],
                        "message": f"Validation Error: Cannot check availability more than 14 days in advance ({max_allowed_date.strftime('%b %d')}) due to clinic policy."
                    })
            except ValueError:
                pass # let the API handle bad formats
                
            # Clamp end_date if it's too far out
            if end_date:
                try:
                    e_date_obj = datetime.strptime(end_date, "%Y-%m-%d").date()
                    if e_date_obj > max_allowed_date:
                        end_date = max_allowed_date.strftime("%Y-%m-%d")
                except ValueError:
                    pass
        # ------------------------------------
        
        url = f"{API_BASE_URL}/getAllSlots_copy"
        headers = {
            "Authorization": f"Bearer {BEARER_TOKEN}",
            "Content-Type": "application/json"
        }
        payload = {
            "doctor_name": doctor_name,
            "slot_start_time": start_time,
            "slot_end_time": end_time,
            "slot_start_date": start_date,
            "slot_end_date": end_date,
            "location": "surat"
        }
        
        response = requests.post(url=url, headers=headers, data=json.dumps(payload), timeout=30)
        
        if response.status_code in [200, 201, 101]:
            print(f"Doctor availability fetched successfully for {doctor_name}")
            data = response.json()
            
            # The API might deeply nest the actual response
            api_resp = data.get("response", data) if isinstance(data, dict) else data
            
            result_code = str(api_resp.get("result_code", ""))
            result_message = api_resp.get("result_message", "")
            
            if result_code == "102" or result_message == "Slot not available":
                # Ensure the LLM explicitly sees "no slots"
                return json.dumps({
                    "status": "success", 
                    "bookable_slots": [],
                    "message": "Doctor UNAVAILABLE on this date."
                })
                
            # If available (result_code == 101), extract the start times so the LLM has a clean list
            slots = api_resp.get("Doctor Availability", [])
            bookable_slots = []
            for slot in slots:
                if "Doctor Availability Start Time" in slot:
                    bookable_slots.append(slot["Doctor Availability Start Time"])

            # Merge consecutive 30-min slots into ranges for voice-friendly output
            available_ranges = _merge_slots_into_ranges(bookable_slots)

            return json.dumps({
                "status": "success", 
                "bookable_slots": bookable_slots,
                "available_ranges": available_ranges,
                "raw_data": api_resp
            })
        else:
            print(f"API call failed with status code {response.status_code}")
            return json.dumps({"status": "error", "message": response.text})
            
    except Exception as e:
        print(f"Error fetching doctor availability: {e}")
        return json.dumps({"status": "error", "message": str(e)})


def get_all_doctors():
    """
    Retrieve list of all available doctors.
    
    Returns:
        dict: List of doctors or error message
    """
    global _doctors_cache
    try:
        url = f"{API_BASE_URL}/get_all_doctors"
        headers = {
            "Authorization": f"Bearer {BEARER_TOKEN}",
            "Content-Type": "application/json"
        }
        
        payload = {"location": "surat"}
        response = requests.post(url, headers=headers, data=json.dumps(payload), timeout=30)
        
        if response.status_code in [200, 201, 101]:
            print("Doctors fetched successfully")
            data = response.json()
            # Warm the in-memory cache so fuzzy matching doesn't need a
            # network round-trip on the first availability check
            doctors = data.get("Doctors", [])
            if doctors:
                _doctors_cache = doctors
                print(f"Doctor cache warmed with {len(_doctors_cache)} entries")
            return json.dumps({"status": "success", "data": data})
        else:
            print(f"API call failed with status code {response.status_code}")
            return json.dumps({"status": "error", "message": response.text})
            
    except Exception as e:
        print(f"Error fetching doctors: {e}")
        return json.dumps({"status": "error", "message": str(e)})

def _strip_dr_prefix(name: str) -> str:
    """Remove 'Dr.', 'Dr ', 'Dr' prefix for comparison."""
    return re.sub(r'^dr\.?\s*', '', name.strip(), flags=re.IGNORECASE).strip()

def fuzzy_match_doctor_name(spoken_name: str) -> str | None:
    """
    Match a spoken/transcribed doctor name against the real doctor list.

    Uses rapidfuzz token_set_ratio for phonetically-aware comparison.
    Returns the official doctor name if a match is found (score >= 75),
    or None if no confident match exists.
    """
    try:
        doctors_json = get_all_doctors()
        doctors_data = json.loads(doctors_json)

        if doctors_data.get("status") != "success":
            print("Fuzzy match: Could not fetch doctor list")
            return None

        doctors = doctors_data.get("data", {}).get("Doctors", [])
        if not doctors:
            print("Fuzzy match: Doctor list is empty")
            return None

        spoken_clean = _strip_dr_prefix(spoken_name).lower()
        best_match = None
        best_score = 0

        for doc in doctors:
            official_name = doc.get("Doctor Name", "")
            official_clean = _strip_dr_prefix(official_name).lower()
            score = fuzz.token_set_ratio(spoken_clean, official_clean)

            if score > best_score:
                best_score = score
                best_match = official_name

        if best_score >= 75:
            print(f'Fuzzy matched doctor name: "{spoken_name}" -> "{best_match}" (score={best_score})')
            return best_match
        else:
            print(f'Fuzzy match: No confident match for "{spoken_name}" (best score={best_score})')
            return None

    except Exception as e:
        print(f"Fuzzy match error: {e}")
        return None

def fuzzy_match_doctor_name_with_score(spoken_name: str) -> tuple[str | None, int]:
    """
    Same as fuzzy_match_doctor_name but also returns the best score for metrics.

    Uses the in-memory _doctors_cache if already populated (avoids a blocking
    HTTP round-trip on the first call after server start).

    Returns:
        tuple: (matched_name or None, best_score)
    """
    global _doctors_cache
    try:
        # Use cached list if available (warmed by get_all_doctors at startup)
        if _doctors_cache:
            doctors = _doctors_cache
            print(f"Fuzzy match (scored): Using cached doctor list ({len(doctors)} entries)")
        else:
            # Cache cold — fetch from API and warm it
            doctors_json = get_all_doctors()
            doctors_data = json.loads(doctors_json)

            if doctors_data.get("status") != "success":
                print("Fuzzy match (scored): Could not fetch doctor list")
                return None, 0

            doctors = doctors_data.get("data", {}).get("Doctors", [])
            if not doctors:
                print("Fuzzy match (scored): Doctor list is empty")
                return None, 0

        spoken_clean = _strip_dr_prefix(spoken_name).lower()
        best_match = None
        best_score = 0

        for doc in doctors:
            official_name = doc.get("Doctor Name", "")
            official_clean = _strip_dr_prefix(official_name).lower()
            score = fuzz.token_set_ratio(spoken_clean, official_clean)

            if score > best_score:
                best_score = score
                best_match = official_name

        if best_score >= 75:
            print(f'Fuzzy matched (scored): "{spoken_name}" -> "{best_match}" (score={best_score})')
            return best_match, best_score
        else:
            print(f'Fuzzy match (scored): No confident match for "{spoken_name}" (best score={best_score})')
            return None, best_score

    except Exception as e:
        print(f"Fuzzy match (scored) error: {e}")
        return None, 0

def get_patient_details(phone_number: str):
    """
    Retrieve patient details from database.
    
    Args:
        phone_number: Patient's phone number
    
    Returns:
        dict: Patient details or error message
    """
    try:
        url = f"{API_BASE_URL}/get_patient_details"
        headers = {
            "Authorization": f"Bearer {BEARER_TOKEN}",
            "Content-Type": "application/json"
        }
        payload = {"phone_number": phone_number}
        
        response = requests.post(url, headers=headers, data=json.dumps(payload), timeout=30)
        
        if response.status_code in [200, 201, 101]:
            print(f"Patient details fetched successfully")
            return json.dumps({"status": "success", "data": response.json()})
        else:
            print(f"API call failed with status code {response.status_code}")
            return json.dumps({"status": "error", "message": response.text})
            
    except Exception as e:
        print(f"Error fetching patient details: {e}")
        return json.dumps({"status": "error", "message": str(e)})

def book_appointment(patient_name: str, patient_email: str, doctor_id: str, 
                    slot_start_time: str, slot_start_date: str, patient_phone: str,
                    patient_dob: str = "", patient_gender: str = ""):
    """
    Book an appointment for a patient.
    
    Args:
        patient_name: Full name of patient
        patient_email: Email address
        patient_dob: Date of birth (YYYY-MM-DD)
        patient_phone: Phone number
        patient_gender: Gender (M/F/Other)
        doctor_id: ID of the doctor
        slot_start_time: Appointment time (HH:MM)
        slot_start_date: Appointment date (YYYY-MM-DD)
    
    Returns:
        dict: Booking confirmation or error message
    """
    try:
        # Ensure dates are zero-padded
        def _pad_date(d_str: str) -> str:
            if not d_str:
                return d_str
            parts = d_str.split('-')
            if len(parts) == 3:
                return f"{parts[0]}-{int(parts[1]):02d}-{int(parts[2]):02d}"
            return d_str
            
        slot_start_date = _pad_date(slot_start_date)
        if patient_dob:
            patient_dob = _pad_date(patient_dob)

        url = f"{API_BASE_URL}/bookappointment"
        headers = {
            "Authorization": f"Bearer {BEARER_TOKEN}",
            "Content-Type": "application/json"
        }
        payload = {
            "slot_start_time": slot_start_time,
            "slot_start_date": slot_start_date,
            "doctorId": doctor_id,
            "patient_name": patient_name,
            "patient_phone_number": patient_phone,
            "patient_dob_date": patient_dob,
            "patient_gender": patient_gender,
            "patient_email": patient_email,
            "location": "surat"
        }
        
        response = requests.post(url, headers=headers, data=json.dumps(payload), timeout=30)
        print(f"book_appointment raw API status={response.status_code}, body={response.text[:500]}")
        data = response.json()
        
        # Extract the real response part
        api_response = data.get("response", {})
        result_code = str(api_response.get("result_code", ""))
        result_message = api_response.get("result_response", "Unknown error")
        
        # Handle result codes
        if result_code == "101":
            print(f"Appointment successfully booked for {patient_name}")
            return json.dumps({
                "status": "success",
                "message": "Appointment successfully booked",
                "appointment_details": api_response
            })
        elif result_code == "103":
            print("Slot not available")
            return json.dumps({
                "status": "error",
                "message": "Selected slot is not available. Please choose another time.",
                "error_code": "SLOT_UNAVAILABLE"
            })
        elif result_code == "105":
            print("Invalid patient data")
            return json.dumps({
                "status": "error",
                "message": "Invalid phone number or patient data provided",
                "error_code": "INVALID_DATA"
            })
        else:
            print(f"Unexpected response: {result_message}")
            return json.dumps({
                "status": "error",
                "message": result_message,
                "error_code": f"CODE_{result_code}"
            })
            
    except Exception as e:
        print(f"Exception while booking appointment: {e}")
        return json.dumps({
            "status": "error",
            "message": f"Failed to book appointment: {str(e)}",
            "error_code": "EXCEPTION"
        })

def get_appointments(phone_number: str, include_cancelled: bool = False):
    """
    Retrieve all active appointments for a patient.
    
    Args:
        phone_number: Patient's phone number
        include_cancelled: If True, include cancelled appointments (default: False)
    
    Returns:
        dict: List of active appointments or error message
    """
    try:
        url = f"{API_BASE_URL}/fetch_all_appointments"
        headers = {
            "Authorization": f"Bearer {BEARER_TOKEN}",
            "Content-Type": "application/json"
        }
        
        # Extract last 10 digits if necessary
        if phone_number.startswith("91") and len(phone_number) > 10:
            phone_number = phone_number[-10:]
        
        payload = {"phone_number": phone_number}
        
        response = requests.post(url, headers=headers, data=json.dumps(payload), timeout=30)
        
        if response.status_code in [200, 201, 101]:
            print(f"Appointments fetched successfully for {phone_number}")
            data = response.json()
            
            # Filter out cancelled appointments unless explicitly requested
            if not include_cancelled:
                all_appointments = data.get("Appointment", [])
                active_appointments = [
                    apt for apt in all_appointments 
                    if apt.get("Appointment Status", "").lower() != "cancelled"
                ]
                data["Appointment"] = active_appointments
                print(f"Filtered to {len(active_appointments)} active appointment(s)")
            
            return json.dumps({"status": "success", "data": data})
        else:
            print(f"API call failed with status code {response.status_code}")
            return json.dumps({"status": "error", "message": response.text})
            
    except Exception as e:
        print(f"Error fetching appointments: {e}")
        return json.dumps({"status": "error", "message": str(e)})

def store_appointments_in_redis(phone_number: str, appointments: dict, ttl: int = 300):
    """
    Store appointments in Redis with TTL.
    Falls back to in-memory APPOINTMENTS_DB if Redis is unavailable.
    
    Args:
        phone_number: Patient's phone number
        appointments: Appointment data to store
        ttl: Time to live in seconds (default 5 minutes)
    
    Returns:
        bool: True if successful, False otherwise
    """
    if not redis_client:
        print("Redis client not available, using in-memory APPOINTMENTS_DB fallback")
        APPOINTMENTS_DB[phone_number] = appointments
        return True
    
    try:
        cache_key = f"appointments_{phone_number}"
        redis_client.set(cache_key, json.dumps(appointments), ex=ttl)
        print(f"Stored appointments in Redis for {phone_number}")
        return True
    except Exception as e:
        print(f"Error storing appointments in Redis, falling back to APPOINTMENTS_DB: {e}")
        APPOINTMENTS_DB[phone_number] = appointments
        return True

def get_appointments_from_redis(phone_number: str):
    """
    Retrieve appointments from Redis.
    Falls back to in-memory APPOINTMENTS_DB if Redis is unavailable.
    
    Args:
        phone_number: Patient's phone number
    
    Returns:
        dict or None: Appointment data if found, None otherwise
    """
    if not redis_client:
        print("Redis client not available, checking in-memory APPOINTMENTS_DB fallback")
        return APPOINTMENTS_DB.get(phone_number)
        
    try:
        cache_key = f"appointments_{phone_number}"
        cached = redis_client.get(cache_key)
        if cached:
            print(f"Retrieved appointments from Redis for {phone_number}")
            return json.loads(cached)
            
        # Also check fallback just in case it was stored there during an error
        if phone_number in APPOINTMENTS_DB:
            print(f"Retrieved appointments from APPOINTMENTS_DB fallback for {phone_number}")
            return APPOINTMENTS_DB[phone_number]
            
        print(f"No appointments found in Redis for {phone_number}")
        return None
    except Exception as e:
        print(f"Error retrieving appointments from Redis, checking APPOINTMENTS_DB: {e}")
        return APPOINTMENTS_DB.get(phone_number)

def clear_appointments_from_redis(phone_number: str):
    """
    Clear appointments from Redis after cancellation.
    Also clears from in-memory APPOINTMENTS_DB if used.
    
    Args:
        phone_number: Patient's phone number
    
    Returns:
        bool: True if successful, False otherwise
    """
    # Always clear the fallback dict if it exists
    if phone_number in APPOINTMENTS_DB:
        del APPOINTMENTS_DB[phone_number]
        print(f"Cleared appointments from APPOINTMENTS_DB for {phone_number}")
        
    if not redis_client:
        print("Redis client not available, cleared from in-memory fallback only")
        return True
    
    try:
        cache_key = f"appointments_{phone_number}"
        redis_client.delete(cache_key)
        print(f"Cleared appointments from Redis for {phone_number}")
        return True
    except Exception as e:
        print(f"Error clearing appointments from Redis: {e}")
        return False

def initiate_cancel_appointment(phone_number: str, requested_date: str = "", requested_time: str = ""):
    """
    Start the appointment cancellation process by fetching all appointments.
    This is step 1 of the cancel workflow.
    
    Args:
        phone_number: Patient's phone number
    
    Returns:
        dict: Appointment list and instructions for the agent
    """
    try:
        # Normalize phone number (remove leading 91 if present)
        if phone_number.startswith("91") and len(phone_number) > 10:
            phone_number = phone_number[-10:]
        
        # Fetch appointments from API
        # IMPORTANT: Use form-data format (as per Postman collection)
        url = f"{API_BASE_URL}/fetch_all_appointments"
        headers = {
            "Authorization": f"Bearer {BEARER_TOKEN}"
            # Do NOT set Content-Type - requests will set it automatically for form-data
        }
        payload = {"phone_number": phone_number}
        
        # Use data= for form-data, not json.dumps()
        response = requests.post(url, headers=headers, data=payload, timeout=30)
        
        if response.status_code in [200, 201, 101]:
            data = response.json()
            print(f"Fetched appointments for {phone_number}")
            print(f"API Response: {data}")
            
            # Check if appointments exist - API returns "Appointment" key at root level
            all_appointments = data.get("Appointment", [])
            
            # Filter out cancelled appointments - only show active ones
            appointments = [apt for apt in all_appointments if apt.get("Appointment Status", "").lower() != "cancelled"]
            
            if not appointments or len(appointments) == 0:
                return json.dumps({
                    "status": "success",
                    "message": "No active appointments found for this phone number.",
                    "appointments": []
                })
            
            # Store appointments in Redis
            store_appointments_in_redis(phone_number, data)
            
            # Format appointments for presentation
            # API returns different field names: "Doctor", "Appointment Start Date", "Appointment ID", etc.
            formatted_appointments = []
            for idx, apt in enumerate(appointments, 1):
                formatted_appointments.append({
                    "number": idx,
                    "appointment_id": apt.get("Appointment ID", ""),
                    "doctor": apt.get("Doctor", "Unknown"),
                    "date": apt.get("Appointment Start Date", "Unknown"),
                    "time": apt.get("Appointment Start Time", apt.get("Appointment Start Date", "Unknown")),
                    "status": apt.get("Appointment Status", "Unknown")
                })
            
            instruction = "Ask the user which appointment they would like to cancel by number or details."
            if requested_date or requested_time:
                instruction = f"You already have the context that they want to cancel the appointment on date ({requested_date}) and time ({requested_time}). Look at the fetched appointments list. If there is a clear match, ask for confirmation of that specific appointment instead of listing all of them."

            return json.dumps({
                "status": "success",
                "message": f"Found {len(appointments)} active appointment(s). Here are all your appointments:",
                "appointments": formatted_appointments,
                "phone_number": phone_number,
                "requested_date": requested_date,
                "requested_time": requested_time,
                "instruction": instruction
            })
        else:
            print(f"API call failed with status code {response.status_code}")
            return json.dumps({
                "status": "error",
                "message": "Unable to fetch appointments. Please try again.",
                "error_code": f"HTTP_{response.status_code}"
            })
            
    except Exception as e:
        print(f"Exception while fetching appointments: {e}")
        return json.dumps({
            "status": "error",
            "message": f"Failed to fetch appointments: {str(e)}",
            "error_code": "EXCEPTION"
        })

def confirm_cancel_appointment(appointment_id: str, phone_number: str):
    """
    Complete the appointment cancellation after user selection.
    This is step 2 of the cancel workflow.
    
    Args:
        appointment_id: ID of the appointment to cancel
        phone_number: Patient's phone number (for cleanup)
    
    Returns:
        dict: Cancellation confirmation or error message
    """
    try:
        # Normalize phone number
        if phone_number.startswith("91") and len(phone_number) > 10:
            phone_number = phone_number[-10:]
        
        # Cancel the appointment via API
        # IMPORTANT: Use form-data format, not JSON (as per Postman collection)
        url = f"{API_BASE_URL}/cancel_appointmement"
        headers = {
            "Authorization": f"Bearer {BEARER_TOKEN}"
            # Do NOT set Content-Type - requests will set it automatically for form-data
        }
        payload = {"appointment_id": appointment_id}
        
        # Use data= for form-data, not json.dumps()
        response = requests.post(url, headers=headers, data=payload, timeout=30)
        
        if response.status_code in [200, 201, 101]:
            print(f"Appointment {appointment_id} cancelled successfully")
            
            # Clear appointments from Redis
            clear_appointments_from_redis(phone_number)
            
            return json.dumps({
                "status": "success",
                "message": "Your appointment has been cancelled successfully.",
                "appointment_id": appointment_id
            })
        else:
            print(f"API call failed with status code {response.status_code}")
            return json.dumps({
                "status": "error",
                "message": "Unable to cancel appointment. Please try again or contact support.",
                "error_code": f"HTTP_{response.status_code}"
            })
            
    except Exception as e:
        print(f"Exception while cancelling appointment: {e}")
        return json.dumps({
            "status": "error",
            "message": f"Failed to cancel appointment: {str(e)}",
            "error_code": "EXCEPTION"
        })

def initiate_reschedule_appointment(phone_number: str, requested_date: str = "", requested_time: str = ""):
    """
    Start the appointment rescheduling process by fetching all appointments.
    This is step 1 of the reschedule workflow.
    
    Args:
        phone_number: Patient's phone number
        requested_date: Preferred new date (optional)
        requested_time: Preferred new time (optional)
    
    Returns:
        dict: Appointment list and instructions for the agent
    """
    try:
        # Normalize phone number (remove leading 91 if present)
        if phone_number.startswith("91") and len(phone_number) > 10:
            phone_number = phone_number[-10:]
        
        # Fetch appointments from API using form-data format
        url = f"{API_BASE_URL}/fetch_all_appointments"
        headers = {
            "Authorization": f"Bearer {BEARER_TOKEN}"
        }
        payload = {"phone_number": phone_number}
        
        response = requests.post(url, headers=headers, data=payload, timeout=30)
        
        if response.status_code in [200, 201, 101]:
            data = response.json()
            print(f"Fetched appointments for rescheduling: {phone_number}")
            print(f"API Response: {data}")
            
            # Check if appointments exist
            all_appointments = data.get("Appointment", [])
            
            # Filter out cancelled appointments - only show active ones
            appointments = [apt for apt in all_appointments if apt.get("Appointment Status", "").lower() != "cancelled"]
            
            if not appointments or len(appointments) == 0:
                return json.dumps({
                    "status": "success",
                    "message": "No active appointments found for this phone number.",
                    "appointments": []
                })
            
            # Store appointments in Redis for later reference
            store_appointments_in_redis(phone_number, data)
            
            # Format appointments for presentation
            formatted_appointments = []
            for idx, apt in enumerate(appointments, 1):
                formatted_appointments.append({
                    "number": idx,
                    "appointment_id": apt.get("Appointment ID", ""),
                    "doctor": apt.get("Doctor", "Unknown"),
                    "date": apt.get("Appointment Start Date", "Unknown"),
                    "time": apt.get("Appointment Start Time", apt.get("Appointment Start Date", "Unknown")),
                    "status": apt.get("Appointment Status", "Unknown")
                })
            
            instruction = "Ask the user which appointment they would like to reschedule."
            if requested_date or requested_time:
                instruction += f" You already have their requested date ({requested_date}) and time ({requested_time}), so DO NOT ask for them again."
            else:
                instruction += " Also ask what new date and time they prefer."
                
            return json.dumps({
                "status": "success",
                "message": f"Found {len(appointments)} active appointment(s). Here are all your appointments:",
                "appointments": formatted_appointments,
                "phone_number": phone_number,
                "requested_date": requested_date,
                "requested_time": requested_time,
                "instruction": instruction
            })
        else:
            print(f"API call failed with status code {response.status_code}")
            return json.dumps({
                "status": "error",
                "message": "Unable to fetch appointments. Please try again.",
                "error_code": f"HTTP_{response.status_code}"
            })
            
    except Exception as e:
        print(f"Exception while fetching appointments for rescheduling: {e}")
        return json.dumps({
            "status": "error",
            "message": f"Failed to fetch appointments: {str(e)}",
            "error_code": "EXCEPTION"
        })

def confirm_reschedule_appointment(appointment_id: str, new_date: str, new_time: str, phone_number: str = ""):
    """
    Complete the appointment rescheduling after user selection.
    This is step 2 of the reschedule workflow.
    
    Args:
        appointment_id: ID of the appointment to reschedule
        new_date: New appointment date (YYYY-MM-DD)
        new_time: New appointment time (HH:MM)
        phone_number: Patient's phone number (for cleanup, optional)
    
    Returns:
        dict: Rescheduling confirmation or error message
    """
    try:
        # Normalize phone number if provided
        if phone_number and phone_number.startswith("91") and len(phone_number) > 10:
            phone_number = phone_number[-10:]
        
        # Reschedule the appointment via API using form-data format
        url = f"{API_BASE_URL}/reschedule_appointment"
        headers = {
            "Authorization": f"Bearer {BEARER_TOKEN}"
        }
        payload = {
            "appointment_id": appointment_id,
            "new_date": new_date,
            "new_time": new_time,
            "location": "surat"
        }
        
        # Use data= for form-data format (matching Postman collection)
        response = requests.post(url, headers=headers, data=payload, timeout=30)
        print(f"confirm_reschedule raw status={response.status_code}, body={response.text[:500]}")
        data = response.json()

        # The API may nest the result under "response", or return it at the root level.
        # Try both so an unexpected structure doesn't silently produce CODE_.
        api_response = data.get("response") or data
        result_code = str(api_response.get("result_code", "")).strip()
        result_message = (
            api_response.get("result_response")
            or api_response.get("message")
            or api_response.get("error")
            or response.text[:200]
            or "Unknown error"
        )

        # Handle result codes
        if result_code == "101":
            print(f"Appointment {appointment_id} rescheduled successfully")
            
            # Clear appointments from Redis if phone number provided
            if phone_number:
                clear_appointments_from_redis(phone_number)
            
            return json.dumps({
                "status": "success",
                "message": f"Your appointment has been successfully rescheduled to {new_date} at {new_time}.",
                "appointment_id": appointment_id,
                "new_date": new_date,
                "new_time": new_time,
                "appointment_details": api_response
            })
        elif result_code == "102":
            print("Slot not available for rescheduling")
            return json.dumps({
                "status": "error",
                "message": "Selected slot is not available. Please choose a different time.",
                "error_code": "SLOT_UNAVAILABLE"
            })
        elif result_code == "103":
            print("Incorrect appointment ID for rescheduling")
            return json.dumps({
                "status": "error",
                "message": "Appointment ID not found.",
                "error_code": "INVALID_APPOINTMENT_ID"
            })
        else:
            print(f"Unexpected reschedule response — code='{result_code}', message='{result_message}', full_body={response.text[:300]}")
            return json.dumps({
                "status": "error",
                "message": f"Unable to reschedule: {result_message}",
                "error_code": f"CODE_{result_code}" if result_code else "NO_RESULT_CODE",
                "raw_response": response.text[:300]
            })
            
    except Exception as e:
        print(f"Exception while rescheduling: {e}")
        return json.dumps({
            "status": "error",
            "message": f"Failed to reschedule appointment: {str(e)}",
            "error_code": "EXCEPTION"
        })

def handle_general_inquiry(inquiry_type: str, details: str = ""):
    """
    Handle general medical inquiries and support requests.
    Fetches data ONLY from Google Sheets - NO hardcoded fallbacks.
    
    Args:
        inquiry_type: Type of inquiry (symptoms, medications, clinic_hours, etc.)
        details: Additional details about the inquiry
    
    Returns:
        dict: Response to the inquiry
    """
    # Fetch clinic data from Google Sheets
    clinic_data = fetch_clinic_info()
    
    # Map inquiry types to Google Sheets column names
    # Mapped to actual column names in the Google Sheet
    sheet_column_mapping = {
        "clinic_hours": "Hours",
        "contact": "PhoneNumber",
        "services": "GeneralInformation",
        "appointment_fee": "GeneralInformation",  # No specific fee column, use general info
        "cancellation_policy": "CancellationPolicy",
        "address": "Address"
    }
    
    inquiry_type_lower = inquiry_type.lower()
    
    # ONLY use Google Sheets data - NO hardcoded fallbacks
    if not clinic_data:
        print(f"Google Sheets data unavailable for {inquiry_type}")
        return json.dumps({
            "status": "error",
            "message": "I'm unable to retrieve clinic information at the moment. Please try again later or contact us directly."
        })
    
    if inquiry_type_lower not in sheet_column_mapping:
        print(f"Unknown inquiry type: {inquiry_type}")
        return json.dumps({
            "status": "error",
            "message": f"I'm not sure how to help with that inquiry. Please contact our clinic directly for assistance."
        })
    
    column_name = sheet_column_mapping[inquiry_type_lower]
    sheet_value = clinic_data.get(column_name, "")
    
    if not sheet_value:
        print(f"No data found in Google Sheets column '{column_name}' for {inquiry_type}")
        return json.dumps({
            "status": "error",
            "message": "I don't have that information available right now. Please contact our clinic directly."
        })
    
    print(f"Using Google Sheets data for {inquiry_type}")
    return json.dumps({
        "status": "success",
        "message": sheet_value
    })

def create_ticket(patient_name: str = "", patient_phone_number: str = "",
                  ticket_desc: str = "", doctor_name: str = ""):
    """
    Create a support ticket when the user wants to speak to a real person.

    Args:
        patient_name: Name of the patient (if collected)
        patient_phone_number: Patient's phone number (if collected)
        ticket_desc: Description/reason for the ticket
        doctor_name: Doctor name if relevant to the issue

    Returns:
        dict: Ticket creation confirmation or error message
    """
    try:
        url = f"{API_BASE_URL}/create_ticket"
        headers = {
            "Authorization": f"Bearer {BEARER_TOKEN}",
            "Content-Type": "application/json"
        }
        payload = {
            "patient_name": patient_name,
            "patient_phone_number": patient_phone_number,
            "ticket_desc": ticket_desc,
            "ticket_status": "open",
            "doctor_name": doctor_name
        }

        response = requests.post(url, headers=headers, data=json.dumps(payload), timeout=25)

        if response.status_code in [200, 201, 101]:
            print(f"Ticket created successfully for {patient_name or 'unknown patient'}")
            return json.dumps({
                "status": "success",
                "message": "A support ticket has been created. An agent will get back to you shortly."
            })
        else:
            print(f"Ticket creation failed with status code {response.status_code}")
            return json.dumps({
                "status": "error",
                "message": "Unable to create the ticket at this time. Please try again or call the clinic directly."
            })

    except Exception as e:
        print(f"Error creating ticket: {e}")
        return json.dumps({"status": "error", "message": str(e)})

# Function mapping for voice agent
MEDICAL_FUNCTION_MAP = {
    'get_doctor_availability': get_doctor_availability,
    'get_all_doctors': get_all_doctors,
    'get_patient_details': get_patient_details,
    'book_appointment': book_appointment,
    'get_appointments': get_appointments,
    'initiate_cancel_appointment': initiate_cancel_appointment,
    'confirm_cancel_appointment': confirm_cancel_appointment,
    'initiate_reschedule_appointment': initiate_reschedule_appointment,
    'confirm_reschedule_appointment': confirm_reschedule_appointment,
    'handle_general_inquiry': handle_general_inquiry,
    'create_ticket': create_ticket
}

if __name__ == "__main__":
    # Test basic functionality
    print("Medical functions module loaded successfully")
    print(f"Available medical functions: {list(MEDICAL_FUNCTION_MAP.keys())}")