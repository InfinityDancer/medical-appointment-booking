from .agent_node import cancellation_agent
from prompts import CANCELLATION_AGENT_PROMPT
import json
from src.services.api_service import cancel_appointment

def cancellation_node(state):
    
    state = cancellation_agent(state, CANCELLATION_AGENT_PROMPT)
    cancel_agent_output = (
        state['graph_state']
        .get("agent_output", {})
        .get("cancellation_agent", {})
        .get("content", "")
    )

    # try parsing JSON — only proceed if it's valid structured data
    try:
        parsed = json.loads(cancel_agent_output)
        # only cancel if agent confirms appointment identification
        if parsed.get("appointment_identified") and parsed.get("event_id"):
            event_id = parsed["event_id"]
            cancel_response = cancel_appointment(event_id)

            state["graph_state"]["agent_output"]["cancellation_agent"]["cancel_agent_result"] = cancel_response
            state["graph_state"]["agent_output"]["cancellation_agent"]["type"] = "backend_reply"

            return state  # stop here after actual cancellation
        else:
            # JSON is valid but not a cancel trigger (e.g. listing appointments)
            return state

    except json.JSONDecodeError:
        # Not a structured JSON yet — just a normal AI message
        return state
