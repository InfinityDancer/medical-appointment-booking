from fastapi import APIRouter, Request,BackgroundTasks
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.asymmetric.padding import OAEP, MGF1
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from base64 import b64decode, b64encode
from fastapi.responses import PlainTextResponse
from src.utils.utils import clean_message
from src.services.whatsapp_service import send_whatsapp_message
from fastapi.responses import JSONResponse
from src.services.langgraph_service import get_langgraph_response
from src.services.supabase_service import supabase_service
from dotenv import load_dotenv
from redis_config import get_redis_client
import os
import time
import json
import base64
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

            message_type = message_obj.get("type")
            message_id = message_obj["id"]
            
            # ==================== INTERACTIVE (FLOW) RESPONSES ====================
            if message_type == "interactive":
                interactive_data = message_obj.get("interactive", {})
                nfm_reply = interactive_data.get("nfm_reply")
                
                if nfm_reply:
                    try:
                        response_json_str = nfm_reply.get("response_json", "{}")
                        flow_response = json.loads(response_json_str) if isinstance(response_json_str, str) else response_json_str
                        flow_type = flow_response.get("flow_type", "")
                        print(f"📋 Flow response received - type: {flow_type}, data: {flow_response}")
                        
                        if flow_type in ["patient_onboarding", "doctor_selection", "location_selection", "slot_picker"]:
                            # Deduplication
                            already_processed = r.set(f"processed:{message_id}", 1, nx=True, ex=86400)
                            if not already_processed:
                                print(f"Duplicate flow webhook ignored (message_id={message_id})")
                                return {"status": "duplicate_ignored"}
                            
                            # Pass flow response data directly to the graph via state
                            synthetic_message = f"__FLOW_RESPONSE__{flow_type}"
                            background_tasks.add_task(process_whatsapp_message, synthetic_message, sender, agent_phonenumber, phone_number_id, flow_response)
                            return {"status": "accepted"}
                        else:
                            print(f"Unknown flow_type: {flow_type}")
                            return {"status": "accepted"}
                    except Exception as flow_err:
                        print(f"Error processing flow response: {flow_err}")
                        return {"status": "accepted"}
                else:
                    print("Ignored interactive message without nfm_reply")
                    return {"status": "accepted"}
            
            # ==================== TEXT MESSAGES ====================
            if message_type != "text":
                print("Ignored non-text message:", message_type)
                send_whatsapp_message(sender,f"Currently we do not support this feature {message_type}, we will add this soon, please send a text message for any booking or any general inquiry!")
                return {"status": "accepted"}
            
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


        # Your dictionary
        # data = {"status": "accepted"}
        data = {"data": {
        "status": "active"
    }
}

        # 1. Convert dictionary to a JSON string
        json_string = json.dumps(data)

        # 2. Convert string to bytes
        json_bytes = json_string.encode('utf-8')

        # 3. Encode to Base64
        base64_bytes = base64.b64encode(json_bytes)

        # 4. Convert Base64 bytes back to a string for display
        base64_string = base64_bytes.decode('utf-8')

        print(base64_string)


        # return {"status": "accepted"}
        return base64_string

    except Exception as e:
        print("Webhook error:", e)
        return {"status": "error", "message": str(e)}

def process_whatsapp_message(message: str, sender: str, agent_phonenumber: str, phone_number_id: str, flow_data: dict = None):
    """Run heavy logic in background"""
    try:
        reply = get_langgraph_response(message, sender, agent_phonenumber, phone_number_id, flow_data=flow_data)
        # Optionally send message back via WhatsApp API
        # send_whatsapp_message(sender, reply)
        print(f"Reply sent to {sender}: {reply}")
    except Exception as e:
        print("❌ Processing error:", e)
