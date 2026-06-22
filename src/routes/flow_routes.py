"""
Flow Routes — /flow-data endpoint for WhatsApp Flow data exchange.

Handles encrypted requests from WhatsApp Flows, routes to the correct
handler based on screen ID, and returns encrypted responses with the
dynamic data that populates dropdowns.
"""

import os
import json
import base64
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse
from cryptography.hazmat.primitives.asymmetric.padding import OAEP, MGF1
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from src.services.flow_service import get_flow_context
from src.services.doctor_service import fetch_all_doctors_data
from src.services.doctor_availability_service import fetch_doctor_availability_data

router = APIRouter()

# Load private key for decryption
PRIVATE_KEY_PATH = os.path.join(os.path.dirname(__file__), "../../private.pem")

def _load_private_key():
    with open(PRIVATE_KEY_PATH, "rb") as f:
        return serialization.load_pem_private_key(f.read(), password=b"laksh123")


def _decrypt_request(body: dict) -> dict:
    """Decrypt a WhatsApp Flow data exchange request."""
    encrypted_flow_data = body.get("encrypted_flow_data")
    encrypted_aes_key = body.get("encrypted_aes_key")
    initial_vector = body.get("initial_vector")

    private_key = _load_private_key()

    # Decrypt AES key with RSA private key
    aes_key = private_key.decrypt(
        base64.b64decode(encrypted_aes_key),
        OAEP(mgf=MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None)
    )
    
    # Encode and decode AES key to ensure proper format (matches working commented approach)
    aes_key_b64 = base64.b64encode(aes_key).decode('utf-8')
    aes_key = base64.b64decode(aes_key_b64)

    # Decrypt flow data with AES-GCM
    iv = base64.b64decode(initial_vector)
    encrypted_data = base64.b64decode(encrypted_flow_data)
    aesgcm = AESGCM(aes_key)
    decrypted = aesgcm.decrypt(iv, encrypted_data, None)
    return json.loads(decrypted.decode("utf-8")), aes_key, iv


def _encrypt_response(response_data: dict, aes_key: bytes, iv: bytes) -> str:
    """Encrypt a response to send back to WhatsApp."""
    # Flip the IV for reply
    flipped_iv = bytes(~b & 0xFF for b in iv)
    aesgcm = AESGCM(aes_key)
    plaintext = json.dumps(response_data).encode("utf-8")
    encrypted = aesgcm.encrypt(flipped_iv, plaintext, None)
    return base64.b64encode(encrypted).decode("utf-8")


# ─── Screen Handlers ─────────────────────────────────────────────────────────

def _handle_doctor_selection_init(context: dict) -> dict:
    """Populate doctor dropdown from Supabase."""
    org_id = context.get("organisation_id")
    location = context.get("location")

    result = fetch_all_doctors_data(org_id, location)
    doctors = result.get("doctors", [])

    dropdown_items = []
    for doc in doctors:
        doc_id = doc.get("doctor_id", "")
        name = doc.get("doctor_name", "Unknown")
        specialty = doc.get("doctor_specialty", "")
        title = f"{name} — {specialty}" if specialty else name
        dropdown_items.append({"id": doc_id, "title": title[:80]})

    if not dropdown_items:
        dropdown_items.append({"id": "none", "title": "No doctors available"})

    return {
        "screen": "DOCTOR_SELECTION",
        "data": {
            "doctors": dropdown_items,
            "header_text": "Please select a doctor for your appointment:",
        },
    }


def _handle_slot_picker_date_exchange(context: dict, payload: dict) -> dict:
    """User picked a date — fetch available slots for that date."""
    doctor_id = context.get("doctor_id")
    doctor_name = context.get("doctor_name", "the doctor")
    location = context.get("location")
    org_id = context.get("organisation_id")
    selected_date_str = payload.get("selected_date", "")

    # DatePicker returns YYYY-MM-DD format
    if selected_date_str:
        try:
            dt = datetime.strptime(selected_date_str, "%Y-%m-%d").replace(tzinfo=ZoneInfo("Asia/Kolkata"))
        except ValueError:
            dt = datetime.now(ZoneInfo("Asia/Kolkata"))
        date_str = dt.strftime("%Y-%m-%d")
        date_display = dt.strftime("%B %d, %Y")
    else:
        dt = datetime.now(ZoneInfo("Asia/Kolkata"))
        date_str = dt.strftime("%Y-%m-%d")
        date_display = dt.strftime("%B %d, %Y")

    # Fetch availability for the whole day
    result = fetch_doctor_availability_data(
        doctor_id=doctor_id,
        start_date=date_str,
        start_time="00:00",
        end_date=date_str,
        end_time="23:59",
        organisation_id=org_id,
        location=location,
        timezone_str="Asia/Kolkata",
    )

    slots = result.get("Doctor Availability", [])
    slot_items = []
    for s in slots:
        start = s.get("start_time", "")
        # Format for display: "9:00 AM"
        try:
            t = datetime.strptime(start, "%H:%M")
            display = t.strftime("%-I:%M %p")
        except Exception:
            display = start
        slot_items.append({"id": start, "title": display})

    if not slot_items:
        slot_items.append({"id": "none", "title": "No slots available"})

    return {
        "screen": "SLOT_SELECTION",
        "data": {
            "slots": slot_items,
            "date_display": date_display,
            "doctor_name": doctor_name,
        },
    }


def _handle_slot_picker_init(context: dict) -> dict:
    """Initial screen for slot picker — provide date constraints."""
    doctor_name = context.get("doctor_name", "the doctor")
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    min_date = now.strftime("%Y-%m-%d")
    max_date = (now + timedelta(days=14)).strftime("%Y-%m-%d")

    return {
        "screen": "DATE_SELECTION",
        "data": {
            "doctor_name": doctor_name,
            "min_date": min_date,
            "max_date": max_date,
        },
    }




def _handle_location_selection_init(context: dict) -> dict:
    """Populate location dropdown."""
    locations = context.get("locations", [])

    dropdown_items = []
    for loc in locations:
        name = loc.get("location_name", "") if isinstance(loc, dict) else str(loc)
        dropdown_items.append({"id": name.lower(), "title": name.title()})

    if not dropdown_items:
        dropdown_items.append({"id": "none", "title": "No locations available"})

    return {
        "screen": "LOCATION_SELECTION",
        "data": {
            "locations": dropdown_items,
            "header_text": "Please select your preferred clinic location:",
        },
    }


# ─── Main Endpoint ──────────────────────────────────────────────────────────

@router.post("/flows")
async def flow_data_exchange(request: Request):
    """
    Handle WhatsApp Flow data exchange requests.
    Decrypts incoming request, routes to the right handler,
    and returns an encrypted response.
    """

    try:
        body = await request.json()
        decrypted_data, aes_key, iv = _decrypt_request(body)

        action = decrypted_data.get("action", "")
        version = decrypted_data.get("version", "3.0")
        screen = decrypted_data.get("screen")
        data = decrypted_data.get("data", {})
        flow_token = decrypted_data.get("flow_token", "")

        print(f"🔐 Flow data exchange — action: {action}, version: {version}, screen: {screen}, flow_token: {flow_token}")

        # Extract sender from flow_token (format: type_sender_extra)
        token_parts = flow_token.split("_", 2)
        flow_type = token_parts[0] if len(token_parts) > 0 else ""
        sender = token_parts[1] if len(token_parts) > 1 else ""

        # Map flow_type prefix to full type for context lookup
        type_map = {
            "doctor": "doctor_selection",
            "slot": "slot_picker",
            "loc": "location_selection",
            "onboard": "patient_onboarding",
        }
        full_flow_type = type_map.get(flow_type, flow_type)
        context = get_flow_context(sender, full_flow_type) or {}

        # Handle PING action (health check from WhatsApp)
        if action == "ping":
            response_data = {"version": version, "data": {"status": "active"}}
            encrypted = _encrypt_response(response_data, aes_key, iv)
            return PlainTextResponse(encrypted, status_code=200)

        # Route based on action and screen
        if action == "INIT":
            if screen == "DOCTOR_SELECTION" or full_flow_type == "doctor_selection":
                response_data = _handle_doctor_selection_init(context)
            elif screen == "DATE_SELECTION" or full_flow_type == "slot_picker":
                response_data = _handle_slot_picker_init(context)
            elif screen == "LOCATION_SELECTION" or full_flow_type == "location_selection":
                response_data = _handle_location_selection_init(context)
            elif screen == "PATIENT_DETAILS" or full_flow_type == "patient_onboarding":
                # Patient onboarding — no dynamic data needed, just show the form
                response_data = {"screen": "PATIENT_DETAILS", "data": {}}
            else:
                # Fallback: default to patient onboarding (simplest form, no dynamic data)
                print(f"⚠️ Unknown INIT — screen: {screen}, flow_type: {full_flow_type}, defaulting to PATIENT_DETAILS")
                response_data = {"screen": "PATIENT_DETAILS", "data": {}}

        elif action == "data_exchange":
            print(f"📊 Data exchange payload: {data}")
            # Route by which screen sent the data_exchange request
            if screen == "DATE_SELECTION":
                # Slot picker: user picked a date → fetch slots
                response_data = _handle_slot_picker_date_exchange(context, data)
            elif screen == "PATIENT_DETAILS":
                # Patient onboarding: user submitted form → close flow
                response_data = {
                    "screen": "SUCCESS",
                    "data": {
                        "extension_message_response": {
                            "params": {
                                "flow_token": flow_token,
                                **data  # pass all form fields back
                            }
                        }
                    }
                }
            else:
                response_data = {"screen": screen or "ERROR", "data": {}}

            # Update flow context with any mutations (e.g., selected appointment)
            if context:
                from src.services.flow_service import store_flow_context
                store_flow_context(sender, full_flow_type, context)

        elif action == "complete":
            # Terminal action — close the flow with SUCCESS
            response_data = {
                "screen": "SUCCESS",
                "data": {
                    "extension_message_response": {
                        "params": {
                            "flow_token": flow_token,
                            **data
                        # {
                        #   "full_name": "Jane Doe",
                        #   "email": "jane.doe@example.com",
                        #   "date_of_birth": "2000-02-07",
                        #   "gender": "female"
                        # }
                        # send the form fields filled by the user, send to chat webhook
                        # Meta receives SUCCESS reponse --> form closed (submitted on user's phone) --> interactive message hit's /webhook
                        }
                    }
                }
            }

        elif action == "back":
            print(f"Back action received for screen: {screen}")
            if screen == "DATE_SELECTION" or full_flow_type == "slot_picker":
                response_data = _handle_slot_picker_init(context)
            elif screen == "DOCTOR_SELECTION" or full_flow_type == "doctor_selection":
                response_data = _handle_doctor_selection_init(context)
            elif screen == "LOCATION_SELECTION" or full_flow_type == "location_selection":
                response_data = _handle_location_selection_init(context)
            else:
                response_data = {"screen": screen or "PATIENT_DETAILS", "data": {}}

        else:
            response_data = {"screen": screen or "ERROR", "data": {}}

        # Inject version into every response
        response_data["version"] = version

        encrypted = _encrypt_response(response_data, aes_key, iv)
        return PlainTextResponse(encrypted, status_code=200)

    except Exception as e:
        import traceback
        print(f"❌ Flow data exchange error: {e}")
        traceback.print_exc()
        return PlainTextResponse(str(e), status_code=500)
