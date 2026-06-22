from typing import Dict, List, Any, Optional
from src.services.supabase_service import supabase_service
from src.utils.location_resolver import resolve_location_id
from src.services.api_service import create_ticket, create_ticket_API
import traceback

def minutes_to_time(minutes: int) -> str:
    h = minutes // 60
    m = minutes % 60
    period = "AM" if h < 12 else "PM"
    hour = 12 if h % 12 == 0 else h % 12
    # Ensure two digits for minutes
    return f"{hour}:{m:02d} {period}"

def transform_roster_data(raw_data: List[Dict[str, Any]], doctor_name_map: Dict[str, str], doctor_specialization_map: Dict[str, str]) -> List[Dict[str, Any]]:
    doctor_map = {}

    for entry in raw_data:
        doctor_id = entry.get("doctor")
        weekday = entry.get("weekday")
        availability = entry.get("availability", {})
        loc = entry.get("location")
        org_id = entry.get("organisation_id")
        print(f"[DEBUG] Processing roster entry: doctor_id={doctor_id}, weekday={weekday}, location={loc}, org_id={org_id}, availability={availability}")
        
        timing = availability.get("timing", [])
        if len(timing) >= 2:
            start_min, end_min = timing[0], timing[1]
        else:
            continue

        if doctor_id not in doctor_map:
            doctor_map[doctor_id] = {
                "doctor_id": doctor_id,
                "doctor_name": doctor_name_map.get(doctor_id, None),
                "doctor_specialty": doctor_specialization_map.get(doctor_id, "General Medicine") or "General Medicine",
                "organisation_id": org_id,
                "schedule": {},
            }

        schedule = doctor_map[doctor_id]["schedule"]
        print(f"[DEBUG] Current schedule for doctor {doctor_id}: {schedule}")
        
        if weekday not in schedule:
            schedule[weekday] = {}

        if loc not in schedule[weekday]:
            schedule[weekday][loc] = []

        schedule[weekday][loc].append({
            "start": minutes_to_time(start_min),
            "end": minutes_to_time(end_min),
        })
    print(f"[DEBUG] Final doctor map after transformation: {doctor_map}")
    return list(doctor_map.values())

def fetch_all_doctors_data(organisation_id: str, location: Optional[str] = None) -> Dict[str, Any]:
    if not organisation_id:
        return {"result_code": 400, "error": "organisation_id is required"}

    # Resolve location text to UUID if provided
    location_id = None
    if location:
        location_id = resolve_location_id(organisation_id, location)
        print(f"[DEBUG] Resolved location '{location}' to ID: {location_id}")
        if not location_id:
            return {"result_code": 404, "error": f"Location '{location}' not found for this organization"}

    client = supabase_service.client

    try:
        # Step 1: Fetch roster data
        query = client.table("new_doctor_roster_config_duplicate").select("*").eq("organisation_id", organisation_id)
        
        if location_id:
            query = query.eq("location", location_id)

        roster_res = query.execute()
        roster_data = roster_res.data
        
        if roster_data is None:
            return {"result_code": 404, "error": "Failed to fetch doctor roster config"}

        # Step 2: Extract unique doctor IDs from roster
        doctor_ids = list(set([r.get("doctor") for r in roster_data if r.get("doctor")]))
        
        # Step 3: Fetch names from users_profile using user_id
        if not doctor_ids:
            # If no doctors found, we just return empty list
            return {
                "result_code": 101,
                "message": "Successfully fetched doctor roster config",
                "doctors": []
            }
            
        profiles_res = client.table("users_profile").select("user_id, user_name, user_specialization").in_("user_id", doctor_ids).execute()
        profiles = profiles_res.data or []

        # Step 4: Build a lookup map { doctor_uuid -> user_name } and { doctor_uuid -> user_specialization }
        doctor_name_map = {}
        doctor_specialization_map = {}
        for profile in profiles:
            doctor_name_map[profile.get("user_id")] = profile.get("user_name")
            doctor_specialization_map[profile.get("user_id")] = profile.get("user_specialization")

        # Step 5: Transform data
        doctors = transform_roster_data(roster_data, doctor_name_map, doctor_specialization_map)

        return {
            "result_code": 101,
            "message": "Successfully fetched doctor roster config",
            "doctors": doctors
        }
    except Exception as e:
        traceback.print_exc()
        error_description = (
                f"[API ERROR] Fetch all doctors data API failed.\n"
                f"error: {str(e)}\n"
                )
        create_ticket_API(
            ticket_description=error_description
            )
        return {"result_code": 500, "error": str(e) or "Cannot fetch doctor data"}
