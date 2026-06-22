import re
from typing import Dict, Any
from src.services.supabase_service import supabase_service

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

def serialize_message(msg) -> Dict[str, Any]:
    """Convert LangChain message objects (or plain dicts) to JSON-serializable dicts."""
    if isinstance(msg, dict):
        return msg
    # Handles HumanMessage, AIMessage, SystemMessage, etc.
    print(f"Serializing message of type {msg.__class__.__name__}")
    return {
        "role": msg.__class__.__name__.replace("Message", "").lower(),  # "human", "ai", "system"
        "content": msg.content
    }


def create_ticket_natively(
    patient_phone_no: str,
    ticket_description: str,
    organisation_id: str,
    patient_name: str = None,
    doctor_name: str = None,
    message_history: list = None
) -> Dict[str, Any]:
    if not patient_phone_no: return {"result_code": 400, "error": "patient phone number is required"}
    if not ticket_description: return {"result_code": 400, "error": "ticket description is required"}
    if not organisation_id: return {"result_code": 400, "error": "organisation id is required"}
    
    if not is_valid_phone_number(patient_phone_no):
        return {"result_code": 400, "error": "not a valid patient phone number, please check again"}
        
    normalized_phone = normalize_phone_number(patient_phone_no)
    
    client = supabase_service.client
    
    try:
        res = client.table("tickets").select("ticket_id").order("ticket_id", desc=True).limit(1).execute()
        
        next_id = 1
        if res.data and len(res.data) > 0:
            last_id = res.data[0].get("ticket_id")
            if last_id is not None:
                next_id = int(last_id) + 1

        # Serialize message history to plain dicts before inserting
        serialized_history = [serialize_message(m) for m in message_history[-10:]] if message_history else []
                
        new_ticket = {
            "ticket_id": next_id,
            "patient_name": patient_name,
            "doctor_name": doctor_name,
            "patient_phone_number": normalized_phone,
            "ticket_description": ticket_description,
            "ticket_status": "open",
            "organisation_id": organisation_id,
            "issue_log": serialized_history
        }
        
        insert_res = client.table("tickets").insert(new_ticket).execute()
        if not insert_res.data:
            return {"result_code": 500, "error": "Failed to insert ticket"}
            
        return {
            "result_code": 101,
            "message": "Ticket created successfully",
            "ticket": insert_res.data[0]
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"result_code": 500, "error": str(e) or "Internal server error"}

def create_ticket_natively_API(
    ticket_description: str
) -> Dict[str, Any]:
    if not ticket_description: return {"result_code": 400, "error": "ticket description is required"}    
    client = supabase_service.client
    
    try:
        res = client.table("tickets").select("ticket_id").order("ticket_id", desc=True).limit(1).execute()
        
        next_id = 1
        if res.data and len(res.data) > 0:
            last_id = res.data[0].get("ticket_id")
            if last_id is not None:
                next_id = int(last_id) + 1

              
        new_ticket = {
            "ticket_id": next_id,
            "patient_name": None,
            "doctor_name": None,
            "patient_phone_number": None,
            "ticket_description": ticket_description,
            "ticket_status": "open",
            "organisation_id": None,
            "issue_log": None
        }
        
        insert_res = client.table("tickets").insert(new_ticket).execute()
        if not insert_res.data:
            return {"result_code": 500, "error": "Failed to insert ticket"}
            
        return {
            "result_code": 101,
            "message": "Ticket created successfully",
            "ticket": insert_res.data[0]
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"result_code": 500, "error": str(e) or "Internal server error"}

