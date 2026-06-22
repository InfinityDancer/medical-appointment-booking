import json
import time
import os
import sys
from datetime import datetime, timezone

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from redis_config import get_redis_client
from supabase_config import get_supabase_client
from src.services.whatsapp_service import send_whatsapp_message, send_whatsapp_template, fetch_template_info_from_db


def get_wait_seconds(value: int, unit: str) -> int:
    unit = unit.lower()
    if "day" in unit:
        return value * 86400
    if "hour" in unit:
        return value * 3600
    if "min" in unit:
        return value * 60
    if "sec" in unit:
        return value
    raise ValueError(f"Unsupported time unit: '{unit}'. Expected day, hour, min, or sec.")


def extract_trigger_ids(trigger: dict):
    # Trigger format: {"match": {"and": [{"==": [{"var": "cohort_id"}, "..."]}, ...]}}
    cohort_id = None
    stage_id = None

    conditions = trigger.get("match", {}).get("and", [])
    for cond in conditions:
        eq = cond.get("==", [])
        if len(eq) == 2:
            var_block, value = eq[0], eq[1]
            if isinstance(var_block, dict):
                if var_block.get("var") == "cohort_id":
                    cohort_id = value
                elif var_block.get("var") == "stage_id":
                    stage_id = value

    return cohort_id, stage_id

def process_automations_cron():

    redis_client = get_redis_client()
    supabase = get_supabase_client()
    now_ts = int(time.time())

    print(f"\n[Worker] Starting cycle at {datetime.now(timezone.utc).isoformat()}")

    # Fetch all active automation rules
    rules_resp = supabase.table("automation_table").select("*").eq('"is_active?"', True).execute()

    all_rules_resp = supabase.table("automation_table").select("*").execute()
    rules_data = [r for r in all_rules_resp.data if r.get("is_active?") == True]

    if not rules_data:
        print("[Worker] No active automation rules found.")
        return

    for rule in rules_data:
        auto_id   = rule["id"]
        auto_name = rule["automation_name"]
        raw_rules = rule.get("automation_rules")

        print(f"\n Evaluating Rule: '{auto_name}'")

        if not raw_rules:
            print("  No automation_rules JSON found. Skipping.")
            continue

        try:
            automation = json.loads(raw_rules) if isinstance(raw_rules, str) else raw_rules
        except json.JSONDecodeError:
            print("  Failed to parse automation_rules JSON. Skipping.")
            continue

        steps   = automation.get("steps", [])
        trigger = automation.get("trigger", {})

        if not steps or not trigger:
            print("  Missing steps or trigger in JSON. Skipping.")
            continue

        # Get cohort and stage from trigger JSON block
        cohort_id, stage_id = extract_trigger_ids(trigger)

        if not cohort_id or not stage_id:
            print("  Could not extract cohort/stage from trigger. Skipping.")
            continue

        steps_map     = {step["id"]: step for step in steps}
        first_step_id = steps[0]["id"]

        # Fetch all patients currently in this cohort + stage
        matches_resp = (
            supabase.table("matching_table")
            .select("patient_id, created_at, update_date, patient(*)")
            .eq("cohort_id", cohort_id)
            .eq("stage_id", stage_id)
            .execute()
        )

        if not matches_resp.data:
            print(f"  No patients found in Cohort:{cohort_id} and Stage:{stage_id}")
            continue

        # Group by patient_id to prevent duplicates if multiple rows exist for the same patient
        unique_patients = {}
        for m in matches_resp.data:
            pid = m["patient_id"]
            if pid not in unique_patients:
                unique_patients[pid] = m
            else:
                # Keep the oldest entry if duplicates are found for accurate timing
                if m["created_at"] < unique_patients[pid]["created_at"]:
                    unique_patients[pid] = m

        for patient_id, match in unique_patients.items():
            created_at_str  = match["created_at"]
            update_date_str = match.get("update_date")
            patient_data    = match.get("patient", {})

            # If patient already completed every step, skip them entirely
            done_key = f"auto:done:{auto_id}:{patient_id}"
            if redis_client.get(done_key):
                continue

            try:
                base_date_str = update_date_str if update_date_str else created_at_str
                entry_ts = int(
                    datetime.fromisoformat(base_date_str.replace("Z", "+00:00")).timestamp()
                )
            except Exception:
                print(f"  Error parsing date for patient {patient_id}. Skipping.")
                continue

            # Walk through the step chain from the very first step
            accumulated_wait = 0
            current_step_id  = first_step_id

            while current_step_id and current_step_id != "step_end":
                step = steps_map.get(current_step_id)
                if not step:
                    break

                step_type    = step.get("type")
                next_step_id = step.get("next")

                if step_type == "wait":
                    duration  = step.get("duration", {})
                    wait_secs = get_wait_seconds(
                        duration.get("value", 0),
                        duration.get("unit", "Minutes")
                    )
                    accumulated_wait += wait_secs
                    action_time = entry_ts + accumulated_wait

                    if action_time <= (now_ts + 300):
                        current_step_id = next_step_id
                    else:
                        mins_left = (action_time - now_ts) // 60
                        print(f"  [WAIT] Patient {patient_id} | Step '{current_step_id}' | {mins_left} mins remaining")
                        break 

                elif step_type == "send_whatsapp":
                    step_sent_key  = f"auto:sent:{auto_id}:{patient_id}:{current_step_id}"
                    step_retry_key = f"auto:retry:{auto_id}:{patient_id}:{current_step_id}"

                    # Already sent this step? Move to the next one.
                    if redis_client.get(step_sent_key):
                        current_step_id = next_step_id
                        continue

                    retries = int(redis_client.get(step_retry_key) or 0)
                    if retries >= 3:
                        print(f"  [SKIP] Patient {patient_id} | Step '{current_step_id}' | Max retries hit.")
                        break

                    phone_no     = patient_data.get("patient_phone_no")
                    patient_name = patient_data.get("patient_name", "Patient")
                    template_id  = step.get("message", {}).get("template_id", "")

                    if not phone_no:
                        print(f"  Patient {patient_id} has no phone number. Skipping step.")
                        break

                    # GLOBAL LOCK: Prevent sending the same template to the same number 
                    # multiple times in 24 hours.
                    global_lock_key = f"global:sent:{phone_no}:{template_id}"
                    if redis_client.get(global_lock_key):
                        print(f"  [GLOBAL SKIP] Phone {phone_no} recently received template '{template_id}'.")
                        current_step_id = next_step_id
                        continue

                    print(f"  Sending step '{current_step_id}' to {patient_name} ({phone_no})...")

                    try:
                        # Fetch org_id from the automation rule to satisfy whatsapp config
                        org_id = rule.get("organisation_id")
                        state = {"organisation_details": {"organisation_id": org_id}}

                        # build the 5 variables
                        name_parts = str(patient_name).strip().split(" ", 1)
                        first_name = name_parts[0] if name_parts else "Patient"
                        last_name = name_parts[1] if len(name_parts) > 1 else ""

                        org_resp = supabase.table("organisation_details").select("organisation_name").eq("organisation_id", org_id).execute()
                        hospital_name = org_resp.data[0]["organisation_name"] if org_resp.data else ""

                        # fetch the most recent appointment to get doctor and time
                        appt_resp = supabase.table("appointments").select("*").eq("patient", patient_id).order("created_at", desc=True).limit(1).execute()
                        doctor_name = "Doctor"
                        date_and_time = ""
                        if appt_resp.data:
                            appt = appt_resp.data[0]
                            
                            raw_date = str(appt.get("appointment_start_date", ""))
                            if raw_date:
                                try:
                                    dt = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
                                    date_and_time = dt.strftime("%d %b %Y, %I:%M %p")
                                except Exception:
                                    date_and_time = raw_date
                                    
                            # Fetch doctor name using the UUID
                            doc_id = appt.get("doctor")
                            if doc_id:
                                doc_resp = supabase.table("users_profile").select("user_name").eq("user_id", doc_id).execute()
                                if doc_resp.data:
                                    doctor_name = doc_resp.data[0].get("user_name", "Doctor")

                        var_mapping = {
                            "Patient_name": first_name,
                            "Last_name": last_name,
                            "Hospital_name": hospital_name,
                            "Doctor_name": doctor_name,
                            "Date_and_time": date_and_time
                        }

                        # DYNAMIC TEMPLATE MATCHING from our DB
                        cache_key = f"template_info:{template_id}"
                        cached_info = redis_client.get(cache_key)
                        
                        if cached_info:
                            template_info = json.loads(cached_info)
                        else:
                            print(f"  [FETCH] Fetching structure for '{template_id}' from DB...")
                            template_info = fetch_template_info_from_db(template_id, org_id)
                            if template_info:
                                redis_client.setex(cache_key, 3600, json.dumps(template_info)) 

                        if not template_info:
                            print(f"  Could not find template '{template_id}' in DB. Falling back to default.")
                            template_info = {"parameter_format": "NAMED", "params": ["patient_name", "hospital_name"]}

                        # All available variables mapping
                        available_vars = {
                            "patient_name": str(first_name).strip() or " ",
                            "last_name": str(last_name).strip() or " ",
                            "doctor_name": str(doctor_name).strip() or " ",
                            "hospital_name": str(hospital_name).strip() or " ",
                            "date_and_time": str(date_and_time).strip() or " ",
                            "appointment_date_time": str(date_and_time).strip() or " " 
                        }

                        # Build NAMED variables array from template params
                        expected_params = template_info.get("params", [])
                        variables = [
                            {"type": "text", "parameter_name": param, "text": available_vars.get(param, " ")}
                            for param in expected_params
                        ]


                        # Use the new template send function
                        send_resp = send_whatsapp_template(
                            to=phone_no, 
                            template_name=template_id, 
                            variables=variables, 
                            state=state
                        )

                        if send_resp.status_code in [200, 201]:
                            print(f"  Sent successfully to {phone_no}!")
                            redis_client.set(step_sent_key, "1")
                            redis_client.setex(global_lock_key, 86400, "1") # 24h lock
                            redis_client.delete(step_retry_key)
                            current_step_id = next_step_id 
                        else:
                            raise Exception(f"API Error {send_resp.status_code}: {send_resp.text}")

                    except Exception as e:
                        print(f"  Failed for {phone_no}: {e}. Retry {retries + 1}/3 on next run.")
                        redis_client.set(step_retry_key, retries + 1)
                        break 

                else:
                    current_step_id = next_step_id

            if current_step_id == "step_end":
                redis_client.set(done_key, "1")
                print(f"  Patient {patient_id} completed all steps!")

if __name__ == "__main__":
    process_automations_cron()