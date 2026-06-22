import requests
from dotenv import load_dotenv
import os
from supabase import create_client, Client

load_dotenv()
VERSION_ID = os.getenv("VERSION_ID")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def get_whatsapp_config(state: dict) -> dict:
    """
    Fetch WhatsApp config from two separate Supabase tables
    filtered by organisation_id.
    """
    # Try both top-level state and nested graph_state for resilience
    organisation_id = state.get("organisation_details", {}).get("organisation_id") or \
                      state.get("graph_state", {}).get("organisation_details", {}).get("organisation_id")
                      
    if not organisation_id:
        raise ValueError("organisation_id not found in state.")
    # Fetch access token for the given organisation
    token_response = supabase.table("whatsapp_integration_data") \
        .select("access_token") \
        .eq("organisation_id", organisation_id) \
        .execute()

    if not token_response.data:
        raise ValueError(f"No access token found for organisation_id: {organisation_id}")

    # Fetch phone number for the given organisation
    phone_response = supabase.table("organisation_whatsapp_integration") \
        .select("phone_number_id") \
        .eq("organisation_id", organisation_id) \
        .execute()

    if not phone_response.data:
        raise ValueError(f"No phone number found for organisation_id: {organisation_id}")

    token_data = token_response.data[0]
    phone_data = phone_response.data[0]
    print(f"📊 Supabase query result for whatsapp_integration_data: {token_data}")
    print(f"📊 Supabase query result for organisation_whatsapp_integration: {phone_data}")
    WHATSAPP_ACCESS_TOKEN = token_data["access_token"]
    PHONE_NUMBER_ID = phone_data["phone_number_id"]
    
    return WHATSAPP_ACCESS_TOKEN, PHONE_NUMBER_ID


# RECIPIENT_NUMBER = os.getenv("RECIPIENT_NUMBER")
def send_whatsapp_message(to:str,msg:str, state: dict):
    # print(to)
    # print(msg)
    # print(WHATSAPP_ACCESS_TOKEN,VERSION_ID,PHONE_NUMBER_ID)
    """
    Sends a WhatsApp message using Meta API.
    """
    """
    Sends a WhatsApp message using Meta API.
    """
    # print("to:", to)
    # print("msg before cast:", type(msg), msg)
    WHATSAPP_ACCESS_TOKEN, PHONE_NUMBER_ID = get_whatsapp_config(state)
    print(f"Using WhatsApp config - Access Token: {WHATSAPP_ACCESS_TOKEN[:10]}..., Phone Number ID: {PHONE_NUMBER_ID}"  )
    # Force msg to be a string
    if not isinstance(msg, str):
        msg = str(msg)
    url = f"https://graph.facebook.com/v22.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
        "Content-Type":"application/json"
    }
    data = {
        "messaging_product": "whatsapp", 
        "to": to,
        "type": "text",
        "text": {"body": msg}
    }
    response = requests.post(url, headers=headers, json=data)
    print(response.json())
    return response

