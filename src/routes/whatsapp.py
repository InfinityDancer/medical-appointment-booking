from fastapi import APIRouter, Request, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field
from src.utils.utils import clean_message
from src.services.whatsapp_service import send_whatsapp_message
from fastapi.responses import JSONResponse
from supabase_config import get_supabase_client
from src.services.langgraph_service import get_langgraph_response
from src.services.supabase_service import supabase_service
from dotenv import load_dotenv
from redis_config import get_redis_client
import os
import time
load_dotenv()
router = APIRouter()

r= get_redis_client()

VERIFY_TOKEN = os.getenv("NGROK_VERIFY_TOKEN")
@router.get("/webhook")
async def verify_webhook(request: Request):
    """
    Webhook verification endpoint for Meta
    """
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        return int(challenge)  # response with hub.challenge
    else:
        return JSONResponse(content={"error": "Invalid verification here"}, status_code=403)


@router.post("/webhook")
async def whatsapp_webhook(request: Request, background_tasks: BackgroundTasks):
    """Handle incoming WhatsApp messages and Meta template_status events"""
    data = await request.json()
    try:
        changes = data.get("entry", [{}])[0].get("changes", [{}])[0]
        value = changes.get("value", {})
        #print("Webhook event", data)
        
        # ==================== TEMPLATE STATUS EVENTS ====================
        if "message_template_id" in value:  # This is a template_status event
            print(f"📋 Template status event received: {value}")
            background_tasks.add_task(supabase_service.handle_template_status_update, value)
            return {"status": "accepted"}
        
        # ==================== MESSAGE EVENTS ====================
        if "messages" in value:  # This is a message event
            message_obj = value["messages"][0]
            metadata = value.get("metadata", {})
            #print(f"📩 Message event received: {value}")
            
            sender = message_obj["from"]  # Customer's phone number
            agent_phonenumber = metadata.get("display_phone_number")  # Agent's WhatsApp number
            message_timestamp = int(message_obj.get("timestamp", 0))
            current_timestamp = int(time.time())
            phone_number_id = metadata.get("phone_number_id")
            print(f"Extracted message details - sender: {sender}, agent_phonenumber: {agent_phonenumber}, message_timestamp: {message_timestamp}, phone_number_id: {phone_number_id}")

            if current_timestamp - message_timestamp > 3600:  
                print(f"⚠️ Stale message ignored (age: {current_timestamp - message_timestamp}s)")
                return {"status": "accepted"}
            # Enforce ONLY text messages
            if message_obj.get("type") != "text":
                print("Ignored non-text message:", message_obj.get("type"))
                send_whatsapp_message(sender,f"Currently we do not support this feature {message_obj.get('type')}, we will add this soon, please send a text message for any booking or any general inquiry!")
                return {"status": "accepted"}
            
            message_id = message_obj["id"]
            message = message_obj["text"]["body"]
            already_processed = r.set(f"processed:{message_id}", 1, nx=True, ex=86400)
            
            # Deduplication: ignore if already processed
            if not already_processed:
                print(f"Duplicate webhook ignored (message_id={message_id})")
                return {"status": "duplicate_ignored"}
          

        # Respond IMMEDIATELY to Meta so it doesn't retry
            background_tasks.add_task(process_whatsapp_message, message, sender, agent_phonenumber, phone_number_id)
            return {"status": "accepted"}  # quick response
        
        # ================== OTHER EVENTS (STATUS UPDATES, ETC) ==================
        print(f"Webhook event not processed: {changes.get('field', 'unknown')}")
        return {"status": "accepted"}

    except Exception as e:
        print("Webhook error:", e)
        return {"status": "error", "message": str(e)}

def process_whatsapp_message(message: str, sender: str, agent_phonenumber: str, phone_number_id: str):
    """Run heavy logic in background"""
    try:
        reply = get_langgraph_response(message, sender, agent_phonenumber, phone_number_id)
        # Optionally send message back via WhatsApp API
        # send_whatsapp_message(sender, reply)
        print(f"Reply sent to {sender}: {reply}")
    except Exception as e:
        print("❌ Processing error:", e)

supabase = get_supabase_client()

class SendWhatsAppMessageRequest(BaseModel):
    organisation_id: str
    text_message: str
    phone_number: str

@router.post("/send")
async def send_whatsapp_message_endpoint(payload: SendWhatsAppMessageRequest):
    """
    Endpoint that takes organisation_id, text_message, and phone_number,
    fetches WABA credentials from Supabase, and triggers the Meta WhatsApp API.
    """
    org_id = payload.organisation_id
    message = payload.text_message
    recipient = payload.phone_number

    try:
        # 1. Fetch access token
        token_response = supabase.table("whatsapp_integration_data") \
            .select("access_token") \
            .eq("organisation_id", org_id) \
            .execute()

        if not token_response.data:
            raise HTTPException(status_code=404, detail=f"No access token found for organisation_id: {org_id}")

        access_token = token_response.data[0]["access_token"]

        # 2. Fetch WABA ID and phone_number_id
        phone_response = supabase.table("organisation_whatsapp_integration") \
            .select("waba_id, phone_number_id") \
            .eq("organisation_id", org_id) \
            .execute()

        if not phone_response.data:
            raise HTTPException(status_code=404, detail=f"No WhatsApp integration details found for organisation_id: {org_id}")

        config_data = phone_response.data[0]
        phone_number_id = config_data["phone_number_id"]
        waba_id = config_data["waba_id"]

        # 3. Trigger WhatsApp API
        api_version = "v22.0"
        url = f"https://graph.facebook.com/{api_version}/{phone_number_id}/messages"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        data = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient,
            "type": "text",
            "text": {"body": message}
        }

        import requests as req
        response = req.post(url, headers=headers, json=data)
        response_json = response.json()

        if response.status_code != 200:
            return {
                "success": False,
                "whatsapp_error": response_json,
                "status_code": response.status_code
            }

        return {
            "success": True,
            "message_id": response_json.get("messages", [{}])[0].get("id"),
            "waba_id": waba_id,
            "whatsapp_response": response_json
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"Error in send_whatsapp_message_endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
