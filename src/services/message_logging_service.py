import json
import uuid
from datetime import datetime, timedelta
from typing import Optional
from supabase_config import get_supabase_client

# Fixed namespace for deterministic UUID v5 generation
MEDISYNC_LOG_NAMESPACE = uuid.UUID('6ba7b810-9ecb-11d1-8014-00c04fd430c8')

def log_message(
    user_phonenumber: str,
    organisation_id: str,
    sender: str,
    text: str,
    agent_name: Optional[str] = None,
    error: Optional[str] = None
):
    """
    Log a message to the user_message_logs table.
    Upserts the record and purges messages older than 14 days.
    """
    try:
        supabase = get_supabase_client()
        
        # Generate deterministic UUID v5
        composite_name = f"{user_phonenumber}_{organisation_id}"
        composite_id = str(uuid.uuid5(MEDISYNC_LOG_NAMESPACE, composite_name))
        
        # Create new message object
        timestamp_now = datetime.utcnow()
        message_obj = {
            "sender": sender,
            "text": text,
            "timestamp": timestamp_now.isoformat() + "Z",
        }
        
        if sender == "agent" and agent_name:
            message_obj["agent_name"] = agent_name
            
        if error:
            message_obj["error"] = error
            
        # Get existing conversation
        response = supabase.table("user_message_logs").select("messages").eq(
            "id", composite_id
        ).execute()
        
        # Determine threshold for purging
        purge_threshold = timestamp_now - timedelta(days=14)
        filtered_messages = []
        
        if response.data and len(response.data) > 0:
            existing_messages = response.data[0].get("messages", []) or []
            # Lazy Purging: Keep only messages newer than 14 days
            for msg in existing_messages:
                try:
                    msg_time_str = msg.get("timestamp", "").replace("Z", "")
                    # handle possible timezone offsets
                    if "+" in msg_time_str:
                        msg_time_str = msg_time_str.split("+")[0]
                    msg_time = datetime.fromisoformat(msg_time_str)
                    if msg_time >= purge_threshold:
                        filtered_messages.append(msg)
                except Exception as parse_e:
                    # If we can't parse the date, keep the message to be safe
                    print(f"⚠️ Error parsing message timestamp: {parse_e}")
                    filtered_messages.append(msg)
        
        # Append the new message
        filtered_messages.append(message_obj)
        
        # Upsert the new record
        upsert_data = {
            "id": composite_id,
            "user_phonenumber": user_phonenumber,
            "organisation_id": organisation_id,
            "messages": filtered_messages,
            "updated_at": timestamp_now.isoformat() + "Z"
        }
        
        supabase.table("user_message_logs").upsert(upsert_data).execute()
        
        if error:
            print(f"⚠️ Message logged with error to conversation: {composite_id}")
        else:
            print(f"✅ Message logged to conversation: {composite_id}")
            
    except Exception as e:
        print(f"❌ Error logging message: {str(e)}")