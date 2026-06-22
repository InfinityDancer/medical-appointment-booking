import requests
import os
import json
from dotenv import load_dotenv
from src.services.whatsapp_service import get_whatsapp_config
from redis_config import get_redis_client

load_dotenv()

VERSION_ID = os.getenv("VERSION_ID")
PATIENT_ONBOARDING_FLOW_ID = os.getenv("PATIENT_ONBOARDING_FLOW_ID")
DOCTOR_SELECTION_FLOW_ID = os.getenv("DOCTOR_SELECTION_FLOW_ID")
LOCATION_SELECTION_FLOW_ID = os.getenv("LOCATION_SELECTION_FLOW_ID")
SLOT_PICKER_FLOW_ID = os.getenv("SLOT_PICKER_FLOW_ID")

def send_doctor_selection_flow(recipient_phone: str, org_id: str, location: str, state: dict):
    """
    Sends the doctor selection WhatsApp Flow.
    """
    WHATSAPP_ACCESS_TOKEN, PHONE_NUMBER_ID = get_whatsapp_config(state)

    url = f"https://graph.facebook.com/{VERSION_ID}/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    data = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": recipient_phone,
        "type": "interactive",
        "interactive": {
            "type": "flow",
            "body": {
                "text": "Please select a doctor for your appointment from the available list."
            },
            "action": {
                "name": "flow",
                "parameters": {
                    "flow_message_version": "3",
                    "flow_id": DOCTOR_SELECTION_FLOW_ID,
                    "mode": "published",
                    "flow_cta": "Select Doctor",
                    "flow_action": "navigate",
                    "flow_token": f"doctor_{recipient_phone}",
                    "flow_action_payload": {
                        "screen": "DOCTOR_SELECTION"
                    }
                }
            }
        }
    }
    
    # Store flow context
    store_flow_context(recipient_phone, "doctor_selection", {
        "organisation_id": org_id,
        "location": location
    })

    response = requests.post(url, headers=headers, json=data)
    print(f"📋 Doctor selection flow sent to {recipient_phone}: {response.json()}")
    return response

def send_patient_onboarding_flow(recipient_phone: str, state: dict):
    """
    Sends the patient onboarding WhatsApp Flow to a new patient.
    The flow collects: full_name, email, date_of_birth, gender.
    """
    WHATSAPP_ACCESS_TOKEN, PHONE_NUMBER_ID = get_whatsapp_config(state)

    url = f"https://graph.facebook.com/{VERSION_ID}/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    data = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": recipient_phone,
        "type": "interactive",
        "interactive": {
            "type": "flow",
            "body": {
                "text": "Welcome! 😊 I couldn't find your record. Please fill in your details using the form below to proceed with the booking."
            },
            "action": {
                "name": "flow",
                "parameters": {
                    "flow_message_version": "3",
                    "flow_id": PATIENT_ONBOARDING_FLOW_ID,
                    "mode": "published",
                    "flow_cta": "Fill Details",
                    "flow_action": "navigate",
                    "flow_token": f"onboard_{recipient_phone}",
                    "flow_action_payload": {
                        "screen": "PATIENT_DETAILS"
                    }
                }
            }
        }
    }

    response = requests.post(url, headers=headers, json=data)
    print(f"📋 Patient onboarding flow sent to {recipient_phone}: {response.json()}")
# update log
    return response


def get_flow_response(sender: str, flow_type: str) -> dict | None:
    """
    Retrieve a flow response from Redis for a given sender and flow type.
    
    Args:
        sender: Phone number of the sender
        flow_type: Type of flow (e.g., "reschedule", "location_selection", "patient_onboarding")
    
    Returns:
        Flow response data as dict, or None if not found
    """
    try:
        r = get_redis_client()
        key = f"flow_response:{sender}:{flow_type}"
        data = r.get(key)
        if data:
            result = json.loads(data)
            # Delete after retrieval to avoid reprocessing
            r.delete(key)
            return result
        return None
    except Exception as e:
        print(f"⚠️ Error retrieving flow response: {e}")
        return None


def store_flow_response(sender: str, flow_type: str, flow_data: dict, ttl: int = 3600):
    """
    Store a flow response in Redis for a given sender and flow type.
    
    Args:
        sender: Phone number of the sender
        flow_type: Type of flow (e.g., "reschedule", "location_selection", "patient_onboarding")
        flow_data: Flow response data to store
        ttl: Time to live in seconds (default: 1 hour)
    """
    try:
        r = get_redis_client()
        key = f"flow_response:{sender}:{flow_type}"
        r.setex(key, ttl, json.dumps(flow_data))
        print(f"✅ Flow response stored: {key}")
    except Exception as e:
        print(f"⚠️ Error storing flow response: {e}")


def get_flow_context(sender: str, flow_type: str) -> dict | None:
    """
    Retrieve flow context from Redis (additional data sent during flow dispatch).
    
    Args:
        sender: Phone number of the sender
        flow_type: Type of flow
    
    Returns:
        Flow context data as dict, or None if not found
    """
    try:
        r = get_redis_client()
        key = f"flow_context:{sender}:{flow_type}"
        data = r.get(key)
        if data:
            return json.loads(data)
        return None
    except Exception as e:
        print(f"⚠️ Error retrieving flow context: {e}")
        return None


def store_flow_context(sender: str, flow_type: str, context: dict, ttl: int = 3600):
    """
    Store flow context in Redis (sent during flow dispatch for later retrieval).
    
    Args:
        sender: Phone number of the sender
        flow_type: Type of flow
        context: Context data to store
        ttl: Time to live in seconds (default: 1 hour)
    """
    try:
        r = get_redis_client()
        key = f"flow_context:{sender}:{flow_type}"
        r.setex(key, ttl, json.dumps(context))
        print(f"✅ Flow context stored: {key}")
    except Exception as e:
        print(f"⚠️ Error storing flow context: {e}")


def send_reschedule_flow(sender: str, appointments: list, org_id: str, state: dict):
    """
    Send a reschedule flow to the user with a list of appointments to choose from.
    
    Args:
        sender: Phone number of the recipient
        appointments: List of appointment dicts with keys: appointment_id, doctor_name, date_display, time_display, location
        org_id: Organisation ID
        state: Current conversation state (for WhatsApp config)
    """
    try:
        WHATSAPP_ACCESS_TOKEN, PHONE_NUMBER_ID = get_whatsapp_config(state)
        
        # Build flow payload
        flow_buttons = []
        for i, appt in enumerate(appointments[:10]):  # WhatsApp flow limit
            flow_buttons.append({
                "id": str(appt.get("appointment_id", "")),
                "title": f"{appt.get('doctor_name', 'Doctor')} - {appt.get('date_display', '')} {appt.get('time_display', '')}",
            })
        
        url = f"https://graph.facebook.com/{VERSION_ID}/{PHONE_NUMBER_ID}/messages"
        headers = {
            "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
            "Content-Type": "application/json"
        }
        
        data = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": sender,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {
                    "text": "Select an appointment to reschedule:"
                },
                "action": {
                    "buttons": [
                        {
                            "type": "reply",
                            "reply": {
                                "id": str(appt.get("appointment_id", "")),
                                "title": f"{appt.get('doctor_name', 'Doctor')} - {appt.get('date_display', '')} {appt.get('time_display', '')}"
                            }
                        }
                        for appt in appointments[:3]  # Button limit
                    ]
                }
            }
        }
        
        # Store appointments context for later retrieval
        store_flow_context(sender, "reschedule", {
            "appointments": appointments,
            "organisation_id": org_id
        })
        
        response = requests.post(url, headers=headers, json=data)
        print(f"📅 Reschedule flow sent to {sender}: {response.status_code}")
        return response
    except Exception as e:
        print(f"⚠️ Error sending reschedule flow: {e}")
        return None


def send_location_flow(sender: str, locations: list, doctor_name: str, org_id: str, state: dict):
    """
    Send a location selection flow to the user.
    
    Args:
        sender: Phone number of the recipient
        locations: List of location dicts with key: location_name
        doctor_name: Name of the doctor for context
        org_id: Organisation ID
        state: Current conversation state (for WhatsApp config)
    """
    try:
        WHATSAPP_ACCESS_TOKEN, PHONE_NUMBER_ID = get_whatsapp_config(state)
        
        url = f"https://graph.facebook.com/{VERSION_ID}/{PHONE_NUMBER_ID}/messages"
        headers = {
            "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
            "Content-Type": "application/json"
        }
        
        data = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": sender,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {
                    "text": f"Where would you like to see {doctor_name}?"
                },
                "action": {
                    "buttons": [
                        {
                            "type": "reply",
                            "reply": {
                                "id": loc.get("location_name", ""),
                                "title": loc.get("location_name", "")
                            }
                        }
                        for loc in locations[:3]  # Button limit
                    ]
                }
            }
        }
        
        # Store context
        store_flow_context(sender, "location_selection", {
            "locations": locations,
            "doctor_name": doctor_name,
            "organisation_id": org_id
        })
        
        response = requests.post(url, headers=headers, json=data)
        print(f"📍 Location flow sent to {sender}: {response.status_code}")
        return response
    except Exception as e:
        print(f"⚠️ Error sending location flow: {e}")
        return None

def send_location_selection_flow(sender: str, locations: list, doctor_name: str, org_id: str, state: dict):
    """
    Send a structured WhatsApp Flow for location selection.
    """
    try:
        WHATSAPP_ACCESS_TOKEN, PHONE_NUMBER_ID = get_whatsapp_config(state)
        
        url = f"https://graph.facebook.com/{VERSION_ID}/{PHONE_NUMBER_ID}/messages"
        headers = {
            "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
            "Content-Type": "application/json"
        }
        
        data = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": sender,
            "type": "interactive",
            "interactive": {
                "type": "flow",
                "body": {
                    "text": f"Where would you like to see {doctor_name}?"
                },
                "action": {
                    "name": "flow",
                    "parameters": {
                        "flow_message_version": "3",
                        "flow_id": LOCATION_SELECTION_FLOW_ID,
                        "mode": "published",
                        "flow_cta": "Select Location",
                        "flow_action": "navigate",
                        "flow_token": f"loc_{sender}",
                        "flow_action_payload": {
                            "screen": "LOCATION_SELECTION"
                        }
                    }
                }
            }
        }
        
        # Store context for data exchange
        store_flow_context(sender, "location_selection", {
            "locations": locations,
            "doctor_name": doctor_name,
            "organisation_id": org_id
        })
        
        response = requests.post(url, headers=headers, json=data)
        print(f"📍 Location Selection Flow sent to {sender}: {response.json()}")
        return response
    except Exception as e:
        print(f"⚠️ Error sending location selection flow: {e}")
        return None

def send_slot_picker_flow(sender: str, doctor_id: str, doctor_name: str, location: str, org_id: str, state: dict):
    """
    Send a structured WhatsApp Flow for picking a date and time slot.
    """
    try:
        WHATSAPP_ACCESS_TOKEN, PHONE_NUMBER_ID = get_whatsapp_config(state)
        
        url = f"https://graph.facebook.com/{VERSION_ID}/{PHONE_NUMBER_ID}/messages"
        headers = {
            "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
            "Content-Type": "application/json"
        }
        
        data = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": sender,
            "type": "interactive",
            "interactive": {
                "type": "flow",
                "body": {
                    "text": f"Please pick a date and time for your appointment with {doctor_name}."
                },
                "action": {
                    "name": "flow",
                    "parameters": {
                        "flow_message_version": "3",
                        "flow_id": SLOT_PICKER_FLOW_ID,
                        "mode": "published",
                        "flow_cta": "Pick a Slot",
                        "flow_action": "navigate",
                        "flow_token": f"slot_{sender}",
                        "flow_action_payload": {
                            "screen": "DATE_SELECTION"
                        }
                    }
                }
            }
        }
        
        # Store context for data exchange
        store_flow_context(sender, "slot_picker", {
            "doctor_id": doctor_id,
            "doctor_name": doctor_name,
            "location": location,
            "organisation_id": org_id
        })
        
        response = requests.post(url, headers=headers, json=data)
        print(f"📅 Slot Picker Flow sent to {sender}: {response.json()}")
        return response
    except Exception as e:
        print(f"⚠️ Error sending slot picker flow: {e}")
        return None