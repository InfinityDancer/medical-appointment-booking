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
        # only cancel if agent confirms appointment identification
        if parsed.get("update_ready") == True or parsed.get("update_ready") == "true" or parsed.get("update_ready") == true:
            event_id = parsed["event_id"]
            new_time = parsed["new_start_time"]
            new_date = parsed["new_start_date"]
            response = reschedule_appointment(event_id, new_date, new_time)
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
                print(f"reschedule API failed: {error_msg}")
                state["graph_state"]["agent_output"]["update_agent"] = {
                    "content": json.dumps({"status": "booking_failed", "error_message": error_msg}),
                    "type": "backend_reply"
                }
            
    except json.JSONDecodeError:
        # Not a structured JSON yet — just a normal AI message
        print("Update agent output is not valid JSON. Proceeding without API call.")
        return state


def format_datetime_simple(date_str, time_str):
    """One-liner version of the datetime formatter"""
    date_obj = datetime.strptime(date_str, '%Y-%m-%d')
    time_obj = datetime.strptime(time_str, '%H:%M')
    
    formatted_date = date_obj.strftime('%-d %B')  # Note: '-' works on Linux/Mac
    formatted_time = time_obj.strftime('%-I%p').lower()
    
    return formatted_date, formatted_time
