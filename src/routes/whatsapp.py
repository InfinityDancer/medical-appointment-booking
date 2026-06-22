from fastapi import APIRouter, Request,BackgroundTasks
from src.utils.utils import clean_message
from src.services.whatsapp_service import send_whatsapp_message
from fastapi.responses import JSONResponse
from src.services.langgraph_service import get_langgraph_response
from dotenv import load_dotenv
from redis_config import get_redis_client
import os
load_dotenv()
router = APIRouter()

r= get_redis_client()

VERIFY_TOKEN = os.getenv("NGROK_VERIFY_TOKEN")
@router.get("/webhook")
async def verify_webhook(request: Request):
    """
    Webhook verification endpoint for Meta
    """
    # print("webhook token",VERIFY_TOKEN)
    # print("webhook json",await request.json())
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        return int(challenge)  # ✅ respond with hub.challenge
    else:
        return JSONResponse(content={"error": "Invalid verification here"}, status_code=403)


@router.post("/webhook")
async def whatsapp_webhook(request: Request, background_tasks: BackgroundTasks):
    """Handle incoming WhatsApp messages"""
    data = await request.json()
    try:
        # ✅ Extract message ID, text, sender, and phone_number_id safely
        value = data["entry"][0]["changes"][0]["value"]
        message_obj = value["messages"][0]
        sender = message_obj["from"]
        phone_number_id = value.get("metadata", {}).get("display_phone_number", "")
         # Enforce ONLY text messages
        if message_obj.get("type") != "text":
            print("Ignored non-text message:", message_obj.get("type"))
            send_whatsapp_message(sender,f"Currently we do not support this feature {message_obj.get('type')}, we will add this soon, please send a text message for any booking or any general inquiry 😊")
            return;
        message_id = message_obj["id"]
        message = message_obj["text"]["body"]

        
    
        # ✅ Deduplication: ignore if already processed
        if r.exists(f"processed:{message_id}"):
            print(f"⚠️ Duplicate webhook ignored (message_id={message_id})")
            return {"status": "duplicate_ignored"}
        r.setex(f"processed:{message_id}", 120, 1)  # expire after 2 min

        # ✅ Respond IMMEDIATELY to Meta so it doesn't retry
        background_tasks.add_task(process_whatsapp_message, message, sender, phone_number_id)
        return {"status": "accepted"}  # quick response

    except Exception as e:
        print("❌ Webhook error:", e)
        return {"status": "error", "message": str(e)}


def process_whatsapp_message(message: str, sender: str, phone_number_id: str = ""):
    """Run heavy logic in background"""
    try:
        reply = get_langgraph_response(message, sender, phone_number_id)
        # Optionally send message back via WhatsApp API
        # send_whatsapp_message(sender, reply)
        print(f"📤 Reply sent to {sender}: {reply}")
    except Exception as e:
        print("❌ Processing error:", e)


