import json
import requests
import os
from dotenv import load_dotenv

load_dotenv()

SUPABASE_FUNCTION_URL = "https://vdllmuxwkqenluqvezzn.supabase.co/functions/v1"
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")

def cancel_appointment(appointment_id):
    """
    Cancel an appointment using Supabase Edge Function.
    Args:
        appointment_id (str): UUID of the appointment
        timezone (str): Timezone string (e.g., 'Asia/Kolkata')
    Returns:
        dict: Response from Supabase function
    """
    try:
        from src.services.cancel_appointment_service import cancel_appointment_natively
        # Note: timezone resolution for cancel is ideally handled inside the native boundary
        # since appointment_id must be fetched first to know the org/location.
        data = cancel_appointment_natively(
            appointment_id=appointment_id,
            timezone="Asia/Kolkata" # We will update native service to lookup if needed
        )
        if data.get("result_code") == 101:
            print("✅ Cancel appointment successfully natively:", data)
            return data
        else:
            code = data.get('result_code')
            err = data.get('error', 'Unknown error')
            if code == 500:
                print(f"❌ Cancellation error (code {code}): {err}")
            elif code == 409:
                print(f"⚠️ Cancellation conflict (code {code}): {err}")
            else:
                print(f"ℹ️ Cancellation status (code {code}): {err}")
            return {"error": err, "status_code": code}
    except Exception as e:
        print("❌ Something went wrong:", e)
        return {"error": str(e)}


def create_ticket(state, ticket_description=None, doctor_name=None, patient_name=None):
    """
    Create a support ticket using Supabase Edge Function.
    Args:
        state (dict): State containing organisation_id and patient phone number
        ticket_description (str, optional): Description of the issue
        doctor_name (str, optional): Doctor's name
        patient_name (str, optional): Patient's name
    Returns:
        dict: Response from Supabase function
    """
    try:
        # Extract required fields from state
        organisation_id = state["graph_state"].get("organisation_details").get("organisation_id")
        patient_phone_no = state["graph_state"].get("sender")
        
        from src.services.create_ticket_service import create_ticket_natively
        data = create_ticket_natively(
            patient_phone_no=patient_phone_no,
            ticket_description="Some bot related issue",
            organisation_id=organisation_id,
            patient_name=patient_name,
            doctor_name=doctor_name
        )
        if data.get("result_code") == 101:
            print("✅ Ticket created successfully natively:", data)
            return {"status": "success", "data": data}
        else:
            code = data.get('result_code')
            err = data.get('error', 'Unknown error')
            if code == 500:
                print(f"❌ Ticket creation error (code {code}): {err}")
            elif code == 409:
                print(f"⚠️ Ticket creation conflict (code {code}): {err}")
            else:
                print(f"ℹ️ Ticket creation status (code {code}): {err}")
            return {"status": "error", "message": err, "raw": data}
    except Exception as e:
        print("❌ Something went wrong while creating ticket natively:", e)
        return {"status": "error", "message": str(e)}



def reschedule_appointment(id:str,new_date:str,new_time:str,location:str,organisation_id:str):
    location = location.lower() if location else location
    print("reschedule_appointment", id, new_date, new_time, location,organisation_id)
    url = f"{SUPABASE_FUNCTION_URL}/reschedule_appointment"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {SUPABASE_ANON_KEY}"
    }

    try:
        from src.services.reschedule_appointment_service import reschedule_appointment_natively
        from src.utils.timezone_resolver import get_location_timezone
        
        dynamic_tz = get_location_timezone(organisation_id, location)
        
        data = reschedule_appointment_natively(
            appointment_id=id,
            new_start_date=new_date,
            new_start_time=new_time,
            organisation_id=organisation_id,
            location=location,
            timezone=dynamic_tz
        )
        print("Raw Reschedule API response:", data)
        result_code = data.get("result_code")
        error_message = data.get("error")
        message = data.get("message")
        
        if result_code == 101:
            print("✅ Appointment rescheduled successfully natively")
            return {
                "status": "success",
                "message": message or "Appointment rescheduled successfully",
                "appointment": data.get("appointment"),
                "raw": data
            }
        else:
            err = error_message or message or "Failed to reschedule appointment"
            if result_code == 500:
                print(f"❌ Rescheduling error (code {result_code}): {err}")
            elif result_code == 409:
                print(f"⚠️ Rescheduling conflict (code {result_code}): {err}")
            else:
                print(f"ℹ️ Rescheduling status (code {result_code}): {err}")
            return {
                "status": "error",
                "result_code": result_code,
                "message": err,
                "raw": data
            }
            
    except Exception as e:
        print("❌ Something went wrong computing reschedule natively:", e)
        return {
            "status": "error",
            "message": str(e)
        }


def book_appointment(slot_start_date: str, slot_start_time: str, doctor_id: str, patient_phone_number: str, organisation_id: str, location: str, patient_name: str = None, patient_dob: str = None, patient_gender: str = None, patient_email: str = None, timezone: str = "Asia/Kolkata"):
    """
    Book an appointment for the patient using Supabase Edge Function.
    Args:
        slot_start_date (str): Date in YYYY-MM-DD format (required)
        slot_start_time (str): Time in HH:MM format, 24-hour (required)
        doctor_id (str): UUID of the doctor (required)
        patient_phone_number (str): Phone number with country code (required)
        organisation_id (str): Organisation UUID (required)
        location (str): Location/clinic name (required)
        patient_name (str, optional): Patient's full name
        patient_dob (str, optional): Date of birth in YYYY-MM-DD format
        patient_gender (str, optional): 'male', 'female', or 'other'
        patient_email (str, optional): Patient's email address
        timezone (str): IANA timezone (defaults to Asia/Kolkata)
    Returns:
        dict: Response from Supabase function with appointment details or error
    """
    location = location.lower() if location else location
    url = f"{SUPABASE_FUNCTION_URL}/book_appointment"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {SUPABASE_ANON_KEY}"
    }
    
    try:
        from src.services.book_appointment_service import book_appointment_natively
        from src.utils.timezone_resolver import get_location_timezone
        
        # Override the hardcoded timezone if a dynamic one isn't cleanly passed, 
        # or just resolve it dynamically explicitly for safety:
        dynamic_tz = get_location_timezone(organisation_id, location)
        
        data = book_appointment_natively(
            slot_start_date=slot_start_date,
            slot_start_time=slot_start_time,
            doctor_id=doctor_id,
            patient_phone_number=patient_phone_number,
            organisation_id=organisation_id,
            location=location,
            patient_name=patient_name,
            patient_dob=patient_dob,
            patient_gender=patient_gender,
            patient_email=patient_email,
            timezone=dynamic_tz
        )
        print("Raw Book Appointment Local response:", data)
        
        result_code = data.get("result_code")
        error_message = data.get("error")
        appointment = data.get("appointment")
        message = data.get("message")
        
        if result_code == 101:
            # Success
            print("✅ Appointment successfully booked")
            return {
                "status": "success",
                "message": message or "Appointment successfully booked",
                "appointment": appointment,
                "raw": data
            }
        else:
            err = error_message or message or "Failed to book appointment"
            if result_code == 500:
                print(f"❌ Booking error (code {result_code}): {err}")
            elif result_code == 409:
                print(f"⚠️ Booking conflict (code {result_code}): {err}")
            else:
                print(f"ℹ️ Booking status (code {result_code}): {err}")
            return {
                "status": "error",
                "result_code": result_code,
                "message": err,
                "raw": data
            }
    except Exception as e:
        print("❌ Exception while booking appointment:", e)
        return {"status": "error", "message": str(e)}


