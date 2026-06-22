from .agent_node import update_agent
from prompts import UPDATE_AGENT_PROMPT
import json
from src.services.api_service import reschedule_appointment
from src.services.flow_service import (
    get_flow_response,
    send_reschedule_flow,
    send_location_flow,
)
from datetime import datetime


def _is_flow_response_message(state) -> bool:
    """Check if the current message is a synthetic flow response marker."""
    msg = state.get("graph_state", {}).get("whatsapp_message", "")
    return msg.startswith("[FLOW_RESPONSE:")


def _handle_reschedule_flow_response(state) -> dict | None:
    """
    Check if there is a reschedule flow response in Redis.
    If found, directly call reschedule API and return updated state.
    """
    sender = state.get("graph_state", {}).get("sender", "")
    flow_data = get_flow_response(sender, "reschedule")

    if not flow_data:
        return None

    if not flow_data.get("confirmed"):
        return None

    # Retrieve the full context from the flow_context stored during send
    from src.services.flow_service import get_flow_context
    context = get_flow_context(sender, "reschedule") or {}

    selected_appt = context.get("selected_appointment", {})
    event_id = selected_appt.get("appointment_id", "")
    location = selected_appt.get("location", "")
    org_id = context.get("organisation_id", "")
    new_date = context.get("new_date", "")
    new_slot = context.get("new_slot", "")

    if not event_id or not new_date or not new_slot:
        print(f"⚠️ Incomplete reschedule flow data: event_id={event_id}, date={new_date}, slot={new_slot}")
        return None

    # Call reschedule API directly
    response = reschedule_appointment(event_id, new_date, new_slot, location, org_id)
    date_display, time_display = format_datetime_simple(new_date, new_slot)
    print(f"📅 Reschedule via flow: {event_id} → {new_date} {new_slot} — response: {response}")

    if response and response.get("status") == "success":
        state["graph_state"]["agent_output"]["update_agent"] = {
            "update_agent_result": response,
            "type": "backend_reply",
            "content": json.dumps(response),
        }
        state["graph_state"]["time"] = time_display
        state["graph_state"]["date"] = date_display
    else:
        error_msg = response.get("message", "cannot reschedule appointment")
        state["graph_state"]["agent_output"]["update_agent"] = {
            "content": json.dumps({"status": "booking_failed", "error_message": error_msg}),
            "type": "backend_reply",
        }

    return state


def _try_send_reschedule_flow(state, update_output: str):
    """
    After the update agent lists appointments for selection, also send
    the reschedule flow as an interactive alternative.
    """
    try:
        # If output is not JSON or doesn't have update_ready, it's likely listing appointments
        try:
            parsed = json.loads(update_output)
            # If it's structured JSON with update_ready, the agent is further along
            if "update_ready" in parsed:
                return
        except json.JSONDecodeError:
            pass  # Non-JSON output = agent is asking for something

        # Check if the agent's message mentions appointments (listing them)
        if not (update_output and ("appointment" in update_output.lower() or "scheduled" in update_output.lower())):
            return

        sender = state.get("graph_state", {}).get("sender", "")
        org_details = state.get("graph_state", {}).get("organisation_details", {}) or {}
        org_id = org_details.get("organisation_id", "")

        if not sender or not org_id:
            return

        # Fetch current appointments to populate the flow
        from src.services.fetch_all_appointments_service import fetch_all_appointments
        result = fetch_all_appointments(sender, org_id, "Asia/Kolkata")
        appointments_data = result.get("appointments", [])

        if not appointments_data:
            return

        # Filter to only booked/rescheduled appointments
        flow_appointments = []
        for appt in appointments_data:
            status = (appt.get("status") or "").lower()
            if status in ("booked", "rescheduled"):
                flow_appointments.append({
                    "appointment_id": appt.get("appointment_id", ""),
                    "doctor_name": appt.get("doctor_name", ""),
                    "doctor_id": appt.get("doctor_id", ""),
                    "date_display": appt.get("appointment_start", ""),
                    "time_display": appt.get("appointment_start", "").split(" ")[-2] if appt.get("appointment_start") else "",
                    "location": appt.get("location", ""),
                })

        if flow_appointments:
            send_reschedule_flow(sender, flow_appointments, org_id, state)
            print("📤 Reschedule flow sent alongside text response")

    except Exception as e:
        print(f"⚠️ Could not send reschedule flow: {e}")


def _try_send_location_flow_for_update(state, parsed_output: dict):
    """
    When the update agent suggests checking another location, send the
    location selection flow.
    """
    try:
        agent_reply = parsed_output.get("agent_reply", "")
        # Check if the agent is offering to check different locations
        if "different location" not in agent_reply.lower() and "other location" not in agent_reply.lower():
            return

        sender = state.get("graph_state", {}).get("sender", "")
        org_details = state.get("graph_state", {}).get("organisation_details", {}) or {}
        org_id = org_details.get("organisation_id", "")
        memory = state.get("memory", {})
        doctor_name = memory.get("saved_doctor_name", "")

        if not sender or not org_id:
            return

        # Get all locations for this doctor
        from src.services.doctor_service import fetch_all_doctors_data
        result = fetch_all_doctors_data(org_id)  # No location filter — get all
        doctors = result.get("doctors", [])

        current_location = memory.get("location", "")
        locations = set()
        for doc in doctors:
            schedule = doc.get("schedule", {})
            for weekday, locs in schedule.items():
                for loc in locs:
                    if loc.lower() != current_location.lower():
                        locations.add(loc)

        if locations:
            loc_list = [{"location_name": loc} for loc in locations]
            send_location_flow(sender, loc_list, doctor_name, org_id, state)
            print("📤 Location flow sent for update path")

    except Exception as e:
        print(f"⚠️ Could not send location flow in update path: {e}")


def _handle_location_flow_for_update(state) -> dict | None:
    """
    Check if there is a location selection flow response in Redis
    for the update/reschedule path. If found, extract the location
    and let the update agent re-check availability.
    """
    sender = state.get("graph_state", {}).get("sender", "")
    flow_data = get_flow_response(sender, "location_selection")

    if not flow_data:
        return None

    selected_location = flow_data.get("selected_location", "")
    if not selected_location or selected_location == "none":
        return None

    # Store selected location in state so the update agent can use it
    if "memory" not in state or state["memory"] is None:
        state["memory"] = {}
    state["memory"]["new_location_from_flow"] = selected_location

    # Override the user message so the update agent processes the location change
    state["graph_state"]["whatsapp_message"] = f"Yes, check availability at {selected_location}"

    print(f"✅ Location selected via flow for reschedule: {selected_location}")
    # Let the normal update_agent handle it with the updated message
    return None  # Return None to continue to normal agent flow


def update_node(state):
    # ——— Check for flow responses BEFORE running the agent ———
    if _is_flow_response_message(state):
        msg = state.get("graph_state", {}).get("whatsapp_message", "")

        # Handle reschedule flow response
        if "reschedule" in msg:
            result = _handle_reschedule_flow_response(state)
            if result is not None:
                return result

        # Handle location selection flow response (for update path)
        if "location_selection" in msg:
            _handle_location_flow_for_update(state)  
            # Falls through to run update_agent with modified message
            
        # Handle slot picker flow response (for update path)
        if "slot_picker" in msg:
            flow_data = state.get("graph_state", {}).get("flow_response_data", {})
            if flow_data:
                selected_time = flow_data.get("selected_time")
                selected_date = flow_data.get("selected_date")
                
                if selected_date and selected_time:
                    try:
                        from datetime import datetime
                        time_display = datetime.strptime(selected_time, "%H:%M").strftime("%I:%M %p")
                    except ValueError:
                        time_display = selected_time
                        
                    requested_time_str = f"{selected_date} at {time_display}"
                    
                    # Override the user message so the update agent processes the new time
                    state["graph_state"]["whatsapp_message"] = requested_time_str
                    print(f"✅ Slot selected via flow for reschedule: {requested_time_str}")
            # Falls through to run update_agent with modified message

    # ——— Run the conversational update agent ———
    state = update_agent(state, UPDATE_AGENT_PROMPT)
    update_agent_output = (
        state['graph_state']
        .get("agent_output", {})
        .get("update_agent", {})
        .get("content", "")
    )
    print("update_agent_output:", update_agent_output)  

    # Try sending reschedule flow alongside text (when agent lists appointments)
    _try_send_reschedule_flow(state, update_agent_output)

    # try parsing JSON — only proceed if it's valid structured data
    try:
        parsed = json.loads(update_agent_output)
        print(parsed)
        
        # Handle case where doctor is not available (update_ready: false)
        # Save event_id, doctor_name, and requested_time in memory for later location change
        if parsed.get("update_ready") is False or parsed.get("update_ready") == "false":
            event_id = parsed.get("event_id")
            doctor_name = parsed.get("doctor_name_for_update")
            requested_time = parsed.get("requested_time")
            organisation_id = parsed.get("organisation_id")
            agent_reply = parsed.get("agent_reply", "")
            new_start_date = parsed.get("new_start_date",'')
            
            if "memory" not in state or state["memory"] is None:
                state["memory"] = {}

            # Save to memory for potential location change
            state["memory"]["saved_event_id"] = event_id
            state["memory"]["saved_doctor_name"] = doctor_name
            state["memory"]["saved_requested_time"] = requested_time
            state["memory"]["saved_organisation_id"] = organisation_id
            state["memory"]["new_start_date"] = new_start_date
        
            # Trigger Slot Picker if asking for a new time
            if event_id and not new_start_date and not requested_time:
                try:
                    from src.services.flow_service import send_slot_picker_flow
                    sender = state.get("graph_state", {}).get("sender", "")
                    
                    doctor_id = state.get("memory", {}).get("saved_doctor_id", "")
                    location = state.get("memory", {}).get("location", "")
                    
                    response = send_slot_picker_flow(sender, doctor_id, doctor_name, location, organisation_id, state)
                    
                    agent_reply = "Please pick a new date and time slot for your appointment from the calendar. 📅"
                    state["graph_state"]["agent_output"]["update_agent"] = {
                        "content": json.dumps({"status": "flow_sent", "agent_reply": agent_reply, "next_action": "awaiting_flow_response"}),
                        "type": "flow_sent"
                    }
                    print("📤 Slot Picker flow sent for update path")
                    return state
                except Exception as e:
                    print(f"⚠️ Could not send slot picker flow in update path: {e}")

            # Store agent reply in the state to be sent
            state["graph_state"]["agent_output"]["update_agent"] = {
                "content": agent_reply,
                "type": "agent_reply"
            }
            
            # Try sending location flow when agent suggests checking other locations
            _try_send_location_flow_for_update(state, parsed)
            
            print(f"Doctor not available. Saved state: event_id={event_id}, requested_time={requested_time}")
            return state
        
        # only proceed if agent confirms appointment identification (update_ready: true)
        if parsed.get("update_ready") == True or parsed.get("update_ready") == "true":
            event_id = parsed["event_id"]
            new_time = parsed["new_start_time"]
            new_date = parsed["new_start_date"]
            location = parsed["location"]
            organisation_id = parsed["organisation_id"]
            response = reschedule_appointment(event_id, new_date, new_time,location,organisation_id)
            date,time = format_datetime_simple(new_date,new_time)
            print("update response: ",response)
            print(date,time)
            if response and response.get("status") == "success":
                state["graph_state"]["agent_output"]["update_agent"]["update_agent_result"] = response
                state["graph_state"]["agent_output"]["update_agent"]["type"] = "backend_reply"
                state["graph_state"]["time"]=time
                state["graph_state"]["date"]=date
            else:
                error_msg = response.get("message","cannot reschedule appointment")
                result_code = response.get("result_code")
                print(f"reschedule API failed: {error_msg}")
                
                # Check if conflict (already has appointment in this interval)
                if result_code == 409 or "already have another appointment" in error_msg.lower():
                    state["graph_state"]["agent_output"]["update_agent"] = {
                        "content": json.dumps({"status": "reschedule_conflict", "error_message": error_msg}),
                        "type": "backend_reply"
                    }
                    state["graph_state"]["reschedule_conflict"] = True
                else:
                    state["graph_state"]["agent_output"]["update_agent"] = {
                        "content": json.dumps({"status": "booking_failed", "error_message": error_msg}),
                        "type": "backend_reply"
                    }
            return state
        
        # Fallback return in case no conditions matched
        return state    
    except json.JSONDecodeError:
        # Not a structured JSON yet — just a normal AI message
        print("Update agent output is not valid JSON. Proceeding without API call.")
        return state


def format_datetime_simple(date_str, time_str):
    """One-liner version of the datetime formatter"""
    date_obj = datetime.strptime(date_str, '%Y-%m-%d')
    time_obj = datetime.strptime(time_str, '%H:%M')
    
    formatted_date = f"{date_obj.day} {date_obj.strftime('%B')}"
    formatted_time = time_obj.strftime('%I%p').lstrip('0').lower()
    
    return formatted_date, formatted_time
