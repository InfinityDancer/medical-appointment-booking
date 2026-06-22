from .agent_node import update_agent
from prompts import UPDATE_AGENT_PROMPT
import json
from src.services.api_service import reschedule_appointment
from datetime import datetime

def update_node(state):
    state = update_agent(state, UPDATE_AGENT_PROMPT)
    update_agent_output = (
        state['graph_state']
        .get("agent_output", {})
        .get("update_agent", {})
        .get("content", "")
    )
    print("update_agent_output:", update_agent_output)  
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
        
            
            # Store agent reply in the state to be sent
            state["graph_state"]["agent_output"]["update_agent"] = {
                "content": agent_reply,
                "type": "agent_reply"
            }
            
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
