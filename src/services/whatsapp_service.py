import requests
from dotenv import load_dotenv
import os

load_dotenv()

WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN")
VERSION_ID = os.getenv("VERSION_ID")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
# RECIPIENT_NUMBER = os.getenv("RECIPIENT_NUMBER")
def send_whatsapp_message(to:str,msg:str):
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
    print(f"📤 WhatsApp API response [{response.status_code}]: {response.json()}")
    return response

