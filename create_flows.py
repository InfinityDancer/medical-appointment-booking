"""
Create, publish, or delete WhatsApp Flows via the Meta Graph API.

Usage:
    python create_flows.py --create  [flow_key ...]   # Create flows (all or specific)
    python create_flows.py --publish [flow_key ...]   # Publish flows
    python create_flows.py --delete  [flow_key ...]   # Delete flows by ID from .env

Flow keys: doctor_selection, slot_picker, reschedule, location_selection, patient_onboarding

After creation, copy the flow IDs printed to .env file.
"""

import os
import json
import sys
import requests
from dotenv import load_dotenv

load_dotenv()

WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN")
WHATSAPP_APP_ID = os.getenv("WHATSAPP_APP_ID")
VERSION_ID = os.getenv("VERSION_ID", "v22.0")

# WhatsApp Business Account ID — fetched from env or Supabase
# You can set this manually or fetch from your Supabase config
WABA_ID = os.getenv("WABA_ID", "")

FLOWS = {
    "doctor_selection": {
        "name": "Doctor Selection",
        "categories": ["OTHER"],
        "json_path": "flows/doctor_selection_flow.json",
        "env_key": "DOCTOR_FLOW_ID",
    },
    "slot_picker": {
        "name": "Appointment Slot Picker",
        "categories": ["APPOINTMENT_BOOKING"],
        "json_path": "flows/slot_picker_flow.json",
        "env_key": "SLOT_PICKER_FLOW_ID",
    },
    "reschedule": {
        "name": "Reschedule Appointment",
        "categories": ["APPOINTMENT_BOOKING"],
        "json_path": "flows/reschedule_flow.json",
        "env_key": "RESCHEDULE_FLOW_ID",
    },
    "location_selection": {
        "name": "Location Selection",
        "categories": ["OTHER"],
        "json_path": "flows/location_selection_flow.json",
        "env_key": "LOCATION_FLOW_ID",
    },
    "patient_onboarding": {
        "name": "Patient Onboarding Latest",
        "categories": ["OTHER"],
        "json_path": "flows/patient_onboarding_flow.json",
        "env_key": "PATIENT_ONBOARDING_FLOW_ID",
    },
}

HEADERS = {
    "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
    "Content-Type": "application/json",
}


def get_waba_id():
    """Get WABA ID from env or prompt."""
    waba_id = WABA_ID
    if not waba_id:
        waba_id = input("Enter your WhatsApp Business Account ID (WABA_ID): ").strip()
    return waba_id


def create_flow(waba_id: str, flow_key: str, flow_config: dict):
    """Create a single WhatsApp Flow."""
    print(f"\n{'='*50}")
    print(f"Creating flow: {flow_config['name']}")

    # Step 1: Create the flow
    url = f"https://graph.facebook.com/{VERSION_ID}/{waba_id}/flows"
    payload = {
        "name": flow_config["name"],
        "categories": flow_config["categories"],
    }
    resp = requests.post(url, headers=HEADERS, json=payload)
    result = resp.json()

    if "id" not in result:
        print(f"  ❌ Failed to create flow: {result}")
        return None

    flow_id = result["id"]
    print(f"  ✅ Flow created with ID: {flow_id}")
    print(f"  📝 Set {flow_config['env_key']}={flow_id} in .env")

    # Step 2: Upload the flow JSON
    json_path = flow_config["json_path"]
    if not os.path.exists(json_path):
        print(f"  ⚠️ Flow JSON not found at {json_path}")
        return flow_id

    with open(json_path, "r") as f:
        flow_json = f.read()

    update_url = f"https://graph.facebook.com/{VERSION_ID}/{flow_id}/assets"
    files = {
        "file": ("flow.json", flow_json, "application/json"),
        "name": (None, "flow.json"),
        "asset_type": (None, "FLOW_JSON"),
    }
    # For file upload, don't send JSON content-type header
    upload_headers = {"Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}"}
    resp = requests.post(update_url, headers=upload_headers, files=files)
    upload_result = resp.json()

    if upload_result.get("success"):
        print(f"  ✅ Flow JSON uploaded successfully")
    else:
        print(f"  ⚠️ Flow JSON upload result: {upload_result}")

    return flow_id


def publish_flow(flow_id: str, flow_name: str):
    """Publish a flow (move from DRAFT to PUBLISHED)."""
    url = f"https://graph.facebook.com/{VERSION_ID}/{flow_id}/publish"
    resp = requests.post(url, headers=HEADERS)
    result = resp.json()

    if result.get("success"):
        print(f"  ✅ Flow '{flow_name}' published successfully")
    else:
        print(f"  ❌ Failed to publish '{flow_name}': {result}")


def delete_flow(flow_id: str, flow_name: str):
    """Delete a flow permanently."""
    url = f"https://graph.facebook.com/{VERSION_ID}/{flow_id}"
    resp = requests.delete(url, headers=HEADERS)
    result = resp.json()

    if result.get("success"):
        print(f"  ✅ Flow '{flow_name}' (ID: {flow_id}) deleted successfully")
    else:
        print(f"  ❌ Failed to delete '{flow_name}': {result}")


def list_flows(waba_id: str):
    """Fetch and display all flows for this WABA."""
    url = f"https://graph.facebook.com/{VERSION_ID}/{waba_id}/flows"
    params = {"fields": "id,name,status,categories"}
    resp = requests.get(url, headers=HEADERS, params=params)
    result = resp.json()

    if "error" in result:
        print(f"❌ Failed to list flows: {result['error'].get('message', result)}")
        return

    flows = result.get("data", [])
    if not flows:
        print("No flows found for this WABA.")
        return

    print(f"\n{'='*60}")
    print(f"{'ID':<22} {'STATUS':<12} {'NAME'}")
    print(f"{'='*60}")
    for flow in flows:
        print(f"{flow['id']:<22} {flow.get('status', 'N/A'):<12} {flow.get('name', 'N/A')}")
    print(f"{'='*60}")
    print(f"Total: {len(flows)} flow(s)")


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("--create", "--publish", "--delete", "--list"):
        print("Usage:")
        print("  python create_flows.py --list                       # List all flows for WABA")
        print("  python create_flows.py --create  [flow_key ...]     # Create flows")
        print("  python create_flows.py --publish [flow_key ...]     # Publish flows")
        print("  python create_flows.py --delete  [flow_key ...]     # Delete flows")
        print()
        print(f"  Flow keys: {', '.join(FLOWS.keys())}")
        sys.exit(1)

    action = sys.argv[1]
    
    if action == "--list":
        waba_id = get_waba_id()
        if waba_id:
            list_flows(waba_id)
        sys.exit(0)

    target_keys = sys.argv[2:] if len(sys.argv) > 2 else FLOWS.keys()

    if action == "--create":
        waba_id = get_waba_id()
        if not waba_id:
            print("WABA_ID is required")
            sys.exit(1)

        print(f"\nUsing WABA_ID: {waba_id}")
        print(f"API Version: {VERSION_ID}")

        created_ids = {}
        for key in target_keys:
            if key not in FLOWS:
                print(f"  ⚠️ Skipping unknown flow: {key}")
                continue
            config = FLOWS[key]
            flow_id = create_flow(waba_id, key, config)
            if flow_id:
                created_ids[config["env_key"]] = flow_id

        print(f"\n{'='*50}")
        print("Add these to your .env file:")
        print(f"{'='*50}")
        for env_key, flow_id in created_ids.items():
            print(f'{env_key} = "{flow_id}"')

    elif action == "--publish":
        print("\nPublishing flows...")
        for key in target_keys:
            if key not in FLOWS:
                continue
            config = FLOWS[key]
            flow_id = os.getenv(config["env_key"])
            if not flow_id:
                print(f"  ⚠️ {config['env_key']} not set in .env — skipping {config['name']}")
                continue
            publish_flow(flow_id, config["name"])

    elif action == "--delete":
        print("\nDeleting flows...")
        for key in target_keys:
            if key not in FLOWS:
                print(f"  ⚠️ Unknown flow key: {key}")
                continue
            config = FLOWS[key]
            flow_id = os.getenv(config["env_key"])
            if not flow_id:
                print(f"  ⚠️ {config['env_key']} not set in .env — skipping {config['name']}")
                continue
            confirm = input(f"  Delete '{config['name']}' (ID: {flow_id})? [y/N]: ").strip().lower()
            if confirm == "y":
                delete_flow(flow_id, config["name"])
            else:
                print(f"  ⏭️  Skipped '{config['name']}'")


if __name__ == "__main__":
    main()
