from typing import Dict, Any, Optional
from src.services.supabase_service import supabase_service
from src.services.api_service import create_ticket
from src.services.api_service import create_ticket_API

def fetch_doctor_details(doctor_id: str, organisation_id: str) -> Dict[str, Any]:
    if not doctor_id:
        return {"result_code": 404, "error": "not a valid doctor id"}
        
    if not organisation_id:
        return {"result_code": 400, "error": "organisation id is required"}

    client = supabase_service.client
    
    try:
        query = client.table("users_profile").select("*") \
            .eq("user_id", doctor_id) \
            .eq("organisation_id", organisation_id) \
            .limit(1)
            
        res = query.execute()
        doctor = res.data[0] if res.data else None
        
        if not doctor:
            return {"result_code": 404, "error": "failed to fetch the doctor"}
        print("doctor details fetched successfully", doctor)  
        return {
            "result_code": 101,
            "message": "succesfully fetched the doctor details",
            "doctor": doctor
        }
    except Exception as e:
        print("failed to fetch doctor details", e)
        error_description = (
                f"[API ERROR] Fetch doctor details API failed.\n"
                f"error: {str(e)}\n"
                )
        create_ticket_API(
            ticket_description=error_description
            )
        return {"result_code": 500, "error": str(e) or "Internal server error"}
