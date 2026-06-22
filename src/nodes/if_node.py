import json
import re

def if_message_exists_node(state):
    """
    IF node: checks if a message exists.
    Returns 'yes' if message is found, else 'no'.
    """
    graph_state = state.get("graph_state", "")
    # print("graph state",graph_state)
    msg = graph_state.get("whatsapp_message","")
    # print(msg)
    if msg and len(msg.strip()) > 0:
        return "yes"
    return "no"



def if_ticket_is_raised(state):
    graph_state = state.get("graph_state")
    
    # Get the content — may be a JSON string or already a parsed dict
    content_raw = graph_state.get("agent_output", {}).get("intent_agent", {}).get("content", {})
    try:
        if isinstance(content_raw, dict):
            content_dict = content_raw
        else:
            content_dict = json.loads(content_raw)
        ticket_status = content_dict.get("suggest_ticket", False)
        
        if ticket_status == True or ticket_status == "true":
            return "yes"
    except (json.JSONDecodeError, TypeError) as e:
        print(f"Error parsing JSON, ticket raised: {e}")
        return "no"
    
    return "no"


def route_by_intent_node(state):
    graph_state = state["graph_state"]
    agent_output = graph_state.get("agent_output", {})
    
    intent_agent_output = (
        agent_output.get("intent_agent", {}).get("content") 
        if agent_output.get("intent_agent") 
        else None
    )

    # default
    graph_state["next_node"] = "general_inquiry"

    if not intent_agent_output:
        print("⚠️ No intent_agent output found, defaulting to general_inquiry.")
        state["graph_state"] = graph_state
        return state

    try:
        # Case 1: if the model already returned a dict (e.g., OpenAI JSON mode)
        if isinstance(intent_agent_output, dict):
            output_dict = intent_agent_output

        # Case 2: clean + extract JSON from string
        else:
            text = intent_agent_output.strip()
            
            # Try direct JSON load first
            try:
                output_dict = json.loads(text)
            except json.JSONDecodeError:
                # Extract only the JSON object part if model wrapped with text
                match = re.search(r"\{.*\}", text, re.DOTALL)
                if match:
                    json_str = match.group(0)
                    output_dict = json.loads(json_str)
                else:
                    raise ValueError("No valid JSON object found in model output.")
        
        # ✅ Extract intent key safely
        routing_status = output_dict.get("intent", "general_inquiry")
        graph_state["next_node"] = routing_status

    except Exception as e:
        print(f"❌ Error parsing JSON intent safely: {e}")
        graph_state["next_node"] = "general_inquiry"

    state["graph_state"] = graph_state
    return state



# Separate conditional function
def route_by_next_node(state):
    graph_state = state.get("graph_state", {})
    next_node = graph_state.get("next_node", "general_inquiry")
    
    if next_node == "general_inquiry":
        return "general_inquiry_node"
    elif next_node == "update":
        return "update_node" 
    elif next_node == "cancellation":
        return "cancellation_node"
    elif next_node == "booking" or next_node == "availability":
        return "booking_node"
    else:
        return "general_inquiry_node"
