from datetime import datetime
import pytz
import re
import json

def current_time():
     # set timezone if needed
    tz = pytz.timezone("Asia/Kolkata")
    now = datetime.now(tz)

    formatted_full = now.strftime("%Y-%m-%d %H:%M:%S")  # "2025-10-06 12:48:32"
    formatted_ref = now.strftime("%Y-%m-%d") + " (" + now.strftime("%A, %B %d, %Y") + ")"

    return {
        "current_time": formatted_full,
        "current_date_reference": formatted_ref
    }


def clean_message(message: str) -> str:
    """
    Cleans incoming WhatsApp message by removing extra spaces, 
    emojis, and unnecessary characters.
    """
    text = message.strip()
    text = re.sub(r"\s+", " ", text)   # remove multiple spaces
    return text

def get_agent_response(agent_output: dict, agent: str):
    try:
        print(f"\n📥 get_agent_response() called for agent: {agent}")

        agent_dict = agent_output.get(agent, {})
        print(f"   agent_dict keys: {list(agent_dict.keys()) if isinstance(agent_dict, dict) else type(agent_dict).__name__}")
        print(f"   agent_dict preview: {str(agent_dict)[:300]}")

        # Check if agent output has an error key (both providers failed)
        if isinstance(agent_dict, dict) and "error" in agent_dict:
            print(f"🔴 get_agent_response: agent_dict contains 'error' key: {agent_dict['error']}")
            # If there's also a content key (our fix), use it; otherwise return error message
            if "content" in agent_dict:
                print(f"   ✅ Found 'content' key alongside 'error', attempting to parse it")
            else:
                print(f"   ❌ No 'content' key! This is the root cause of empty responses.")
                return "something went wrong, please try again"

        agent_response = agent_dict.get("content", "") if isinstance(agent_dict, dict) else ""
        
        # Check if response is empty
        if not agent_response or not agent_response.strip():
            print(f"⚠️  Empty 'content' from {agent}")
            print(f"   Full agent_dict: {str(agent_dict)[:500]}")
            return "something went wrong, please try again"
        
        print(f"   Raw content (first 200 chars): {str(agent_response)[:200]!r}")
        
        parsed = json.loads(agent_response)
        agent_message = parsed.get("agent_response", "")
        
        # Check if agent_message is empty
        if not agent_message or not agent_message.strip():
            print(f"⚠️  Empty agent_message from {agent}")
            print(f"   Parsed JSON: {parsed}")
            return "something went wrong, please try again"
        
        print(f"   ✅ Successfully extracted agent_message (first 100 chars): {str(agent_message)[:100]!r}")
        return agent_message
    except json.JSONDecodeError as e:
        print(f"❌ Error in get_agent_response: JSON decode error - {e}")
        print(f"   Agent: {agent}, Response preview: {str(agent_response)[:200]}")
        # Try to use raw text as-is if it looks like a conversational message
        if agent_response and len(agent_response.strip()) > 5 and not agent_response.strip().startswith('{'):
            print(f"   ↪ Raw text doesn't look like JSON, returning it as-is")
            return agent_response.strip()
        return "something went wrong, please try again"
    except Exception as e:
        print(f"❌ Error in get_agent_response: {type(e).__name__} - {e}")
        import traceback
        print(f"   Traceback: {traceback.format_exc()}")
        return "something went wrong, please try again"


def extract_time_data(iso_string: str):
    try:
        date_part, time_and_zone_part = iso_string.split('T')
    except ValueError:
        print("Error: Input string is not in the expected format (YYYY-MM-DDTHH:MM:SS+ZZ:ZZ)")
        return {"slot_start_time": None, "slot_start_date": None, "slot_start_day_month": None}

    # 1. Extract slot_start_date: The date part is already in the 'yyyy-mm-dd' format (YYYY-MM-DD).
    slot_start_date = date_part

    # 2. Extract slot_start_time: We need "HH:MM". 
    # The time part (e.g., "10:00:00+05:30" or "10:00:00Z") needs the seconds and timezone removed.
    time_part = time_and_zone_part.split('+')[0].split('-')[0].rstrip('Z')
    slot_start_time = ':'.join(time_part.split(':')[:2])

    # 3. Extract day and month like "12 october"
    try:
        dt = datetime.strptime(date_part, "%Y-%m-%d")
        slot_start_day_month = dt.strftime("%d %B").lower()
    except Exception:
        slot_start_day_month = None

    return {
        "slot_start_time": slot_start_time,
        "slot_start_date": slot_start_date,
        "slot_start_day_month": slot_start_day_month
    }

def extract_time_data_message(datetime_str: str):
    """
    Extracts date and time in human-friendly format.
    Example:
        input: "2025-10-30T13:00:00+05:30"
        output: {"appointment_date": "30th October", "start_time": "1pm"}
    """
    # Parse ISO datetime (timezone-aware)
    dt = datetime.fromisoformat(datetime_str)

    # Format date (e.g. 30th October)
    day = dt.day
    month = dt.strftime("%B")
    
    # Determine ordinal suffix
    if 10 <= day % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    
    appointment_date = f"{day}{suffix} {month}"

    # Format time (e.g. 1pm, 10am)
    start_time = dt.strftime("%I:%M%p").lower().lstrip("0")
    if start_time.endswith(":00am") or start_time.endswith(":00pm"):
        start_time = start_time.replace(":00", "")
    
    return {
        "appointment_date": appointment_date,
        "start_time": start_time
    }

def extract_ai_reply(response_dict):
    """
    Extracts the last AIMessage content (the model reply) 
    from either Gemini or OpenAI structured responses.
    """
    try:
        # Ensure messages exist
        messages = response_dict.get("messages", [])
        if not messages:
            return None

        # Loop backward to find the last AIMessage
        for msg in reversed(messages):
            # Some LangChain message objects store type in the class name
            if hasattr(msg, "content") and msg.__class__.__name__ == "AIMessage":
                return msg.content

            # Or if messages are stored as dicts
            if isinstance(msg, dict) and msg.get("type") == "ai":
                return msg.get("content")

        return None
    except Exception as e:
        print(f"Error extracting AI reply: {e}")
        return "something went wrong, please try again"
