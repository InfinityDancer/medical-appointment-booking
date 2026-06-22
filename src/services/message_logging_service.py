import json
from datetime import datetime
from typing import Optional
from supabase_config import get_supabase_client


def create_conversation(
    user_phonenumber: str,
    agent_phonenumber: str
) -> str:
    """
    Create a new conversation.
    
    Args:
        user_phonenumber: User's phone number
        agent_phonenumber: Agent/clinic phone number
    
    Returns:
        id (auto-generated ID from database)
    """
    try:
        supabase = get_supabase_client()
        
        response = supabase.table("message_logs").insert({
            "user_phonenumber": user_phonenumber,
            "agent_phonenumber": agent_phonenumber,
            "messages": []
        }).execute()
        
        conversation_id = response.data[0]["id"] if response.data else None
        print(f"✅ New conversation created: {conversation_id}")
        return conversation_id
    
    except Exception as e:
        print(f"❌ Error creating conversation: {str(e)}")
        return None


def log_message(
    conversation_id: str,
    sender: str,
    text: str,
    agent_name: Optional[str] = None,
    error: Optional[str] = None
):
    """
    Log a message to an existing conversation.
    Appends message to the messages array.
    
    Args:
        conversation_id: ID of the conversation (auto-generated database id)
        sender: "user" or "agent"
        text: Message content
        agent_name: Name of the agent/bot responding
        error: Error message if an error occurred (optional)
    """
    try:
        supabase = get_supabase_client()
        
        # Create message object
        message_obj = {
            "sender": sender,
            "text": text,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
        
        # Add agent_name if provided and sender is agent
        if sender == "agent" and agent_name:
            message_obj["agent_name"] = agent_name
        
        # Add error if provided
        if error:
            message_obj["error"] = error
        
        # Get existing conversation
        response = supabase.table("message_logs").select("*").eq(
            "id", conversation_id
        ).execute()
        
        if response.data and len(response.data) > 0:
            existing_record = response.data[0]
            existing_messages = existing_record.get("messages", []) or []
            
            # Append new message
            existing_messages.append(message_obj)
            
            # Update the record
            supabase.table("message_logs").update({
                "messages": existing_messages
            }).eq("id", conversation_id).execute()
            
            if error:
                print(f"⚠️ Message logged with error to conversation: {conversation_id}")
            else:
                print(f"✅ Message logged to conversation: {conversation_id}")
        else:
            print(f"⚠️ Conversation not found: {conversation_id}")
    
    except Exception as e:
        print(f"❌ Error logging message: {str(e)}")