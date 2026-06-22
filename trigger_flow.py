"""
Trigger published WhatsApp Flows directly — standalone test script.

Usage:
    python trigger_flow.py doctor <recipient_number>
    # python trigger_flow.py slot <recipient_number>
    # python trigger_flow.py reschedule <recipient_number>
    python trigger_flow.py location <recipient_number>
    python trigger_flow.py patient <recipient_number>

Example:
    python trigger_flow.py doctor 917483586800
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
VERSION_ID = os.getenv("VERSION_ID", "v22.0")

FLOWS = {
    "doctor": {
        "flow_id": os.getenv("DOCTOR_FLOW_ID"),
        "header": "Select a Doctor",
        "body": "Choose a doctor from the list below.",
        "cta": "Select Doctor",
        "paflow_action": "navigate",
        "flow_action_payload": {
            "screen": "DOCTOR_SELECTION",
            "data": {
                "doctors": [
                    {"id": "doc_1", "title": "Dr Neelesh Gupta — Glaucoma"},
                    {"id": "doc_2", "title": "Dr Nilesh Kumar — Retina"},
                ],
                "header_text": "Please select a doctor for your appointment:",
            },
        },
    },
    "slot": {
        "flow_id": os.getenv("SLOT_PICKER_FLOW_ID"),
        "header": "Pick a Date & Time",
        "body": "Select your preferred appointment date and time slot.",
        "cta": "Pick Slot",
        "flow_action": "navigate",
        "flow_action_payload": {
            "screen": "SLOT_SELECTION",
            "data": {
                "doctor_name": "Dr Neelesh Gupta",
                "min_date": "2026-04-09",
                "max_date": "2026-04-23",
                "slots": [
                    {"id": "09:00", "title": "9:00 AM"},
                    {"id": "10:00", "title": "10:00 AM"},
                    {"id": "14:00", "title": "2:00 PM"},
                ],
            },
        },
    },
    "reschedule": {
        "flow_id": os.getenv("RESCHEDULE_FLOW_ID"),
        "header": "Reschedule Appointment",
        "body": "Select the appointment you want to reschedule.",
        "cta": "Reschedule",
        "flow_action": "navigate",
        "flow_action_payload": {
            "screen": "RESCHEDULE",
            "data": {
                "appointments": [
                    {"id": "evt_1", "title": "Dr Neelesh: Apr 10, 2:00 PM"},
                ],
                "header_text": "Select the appointment to reschedule and pick a new date",
                "min_date": "2026-04-09",
                "max_date": "2026-04-23",
                "slots": [
                    {"id": "09:00", "title": "9:00 AM"},
                    {"id": "10:00", "title": "10:00 AM"},
                    {"id": "14:00", "title": "2:00 PM"},
                ],
            },
        },
    },
    "location": {
        "flow_id": os.getenv("LOCATION_FLOW_ID"),
        "header": "Select Clinic",
        "body": "Choose your preferred clinic location.",
        "cta": "Select Location",
        "flow_action": "navigate",
        "flow_action_payload": {
            "screen": "LOCATION_SELECTION",
            "data": {
                "locations": [
                    {"id": "surat", "title": "Surat"},
                    {"id": "mumbai", "title": "Mumbai"},
                ],
                "header_text": "Please select your preferred clinic location:",
            },
        },
    },
    "patient": {
        "flow_id": os.getenv("PATIENT_ONBOARDING_FLOW_ID"),
        "header": "Patient Onboarding",
        "body": "Please provide your details to book an appointment.",
        "cta": "Start",
        "flow_action": "navigate",
        "flow_action_payload": {
        "screen": "PATIENT_DETAILS",
        },
    },
}


def send_flow(flow_key: str, recipient: str):
    config = FLOWS.get(flow_key)
    if not config:
        print(f"❌ Unknown flow: {flow_key}")
        print(f"   Available: {', '.join(FLOWS.keys())}")
        sys.exit(1)

    flow_id = config["flow_id"]
    if not flow_id:
        print(f"❌ {flow_key.upper()}_FLOW_ID not set in .env")
        sys.exit(1)

    # Strip + prefix if present
    recipient = recipient.lstrip("+")

    url = f"https://graph.facebook.com/{VERSION_ID}/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }

    parameters = {
        "flow_message_version": "3",
        "flow_id": flow_id,
        "flow_token": f"test_{flow_key}_{recipient}",
        "flow_cta": config["cta"],
        "flow_action": config["flow_action"],
    }

    # Add flow_action_payload for navigate flows
    if "flow_action_payload" in config:
        parameters["flow_action_payload"] = config["flow_action_payload"]

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": recipient,
        "type": "interactive",
        "interactive": {
            "type": "flow",
            "header": {"type": "text", "text": config["header"]},
            "body": {"text": config["body"]},
            "footer": {"text": "Powered by MediSync"},
            "action": {
                "name": "flow",
                "parameters": parameters,
            },
        },
    }

    print(f"📤 Sending '{flow_key}' flow to {recipient}...")
    print(f"   Flow ID: {flow_id}")
    print(f"   Action:  {config['flow_action']}")

    resp = requests.post(url, headers=headers, json=payload)
    result = resp.json()

    # ---------- Save request + response to JSON ----------
    out_dir = Path(__file__).parent / "debug_responses"
    out_dir.mkdir(exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_file = out_dir / f"{flow_key}_{ts}.json"

    debug_data = {
        "timestamp": datetime.now().isoformat(),
        "flow_key": flow_key,
        "recipient": recipient,
        "request": {
            "url": url,
            "payload": payload,
        },
        "response": {
            "status_code": resp.status_code,
            "body": result,
        },
    }

    out_file.write_text(json.dumps(debug_data, indent=2, default=str))
    print(f"💾 Response saved to {out_file}")

    # ---------- Pretty-print response ----------
    print("\n--- Response JSON ---")
    print(json.dumps(result, indent=2))
    print("--- End ---\n")

    if "error" in result:
        print(f"❌ Error: {result['error'].get('message', result)}")
    else:
        print(f"✅ Flow sent! Message ID: {result.get('messages', [{}])[0].get('id', 'N/A')}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python trigger_flow.py <flow_type> <recipient_number>")
        print(f"  flow_type: {', '.join(FLOWS.keys())}")
        print("  recipient: phone number with country code (e.g. 917483586800)")
        sys.exit(1)

    send_flow(sys.argv[1], sys.argv[2])
