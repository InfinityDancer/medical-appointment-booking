import re
from typing import Dict, Any
from src.services.supabase_service import supabase_service
from src.services.api_service import create_ticket
from src.services.api_service import create_ticket_API

def normalize_phone_number(phone: str) -> str:
    return re.sub(r'\D', '', phone)

def is_valid_phone_number(phone: str) -> bool:
    # Remove all non-digit characters except +
    cleaned = re.sub(r'[^\d+]', '', phone)
    
    # Check if it has at least 10 digits (excluding +)
    digits_only = cleaned.replace('+', '')
    if len(digits_only) < 10 or len(digits_only) > 15:
        return False
        
    # Valid phone pattern: optional + followed by 10-15 digits
    return bool(re.match(r'^\+?\d{10,15}$', cleaned))

def fetch_patient_details(phone_number: str) -> Dict[str, Any]:
    if not phone_number:
        return {"result_code": 400, "error": "patient phone number is required"}
        
    if not is_valid_phone_number(phone_number):
        return {"result_code": 400, "error": "patient phone number is not valid, please check again the phone number"}
        
    normalized = normalize_phone_number(phone_number)
    client = supabase_service.client
    
    try:
        # Utilizing eq constraint instead of TypeScript's .single() to avoid crashing on miss.
        res = client.table("patient").select("*").eq("patient_phone_no", normalized).limit(1).execute()
        patient_details = res.data[0] if res.data else None
        
        if not patient_details:
            return {"result_code": 404, "error": "failed to fetch patient details"}
            
        return {
            "result_code": 101,
            "message": "patients details fetched succesfully",
            "patientDetail": patient_details
        }
    except Exception as e:
        print("patient fetched failed", e)
        error_description = (
                f"[API ERROR] Fetch patient details API failed.\n"
                f"error: {str(e)}\n"
                )
        create_ticket_API(
            ticket_description=error_description
            )
        return {"result_code": 500, "error": str(e) or "Internal server error"}
