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
    """
    Check if a ticket should be raised based on intent_agent output.
    Returns 'yes' if ticket is suggested, 'no' otherwise.
    """
    try:
        graph_state = state.get("graph_state", {})
        if not graph_state:
            print("⚠️ graph_state not found, defaulting to 'no'")
            return "no"
        
        # Get the content string and parse it as JSON
        agent_output = graph_state.get("agent_output", {})
        if not agent_output:
            print("⚠️ agent_output not found, defaulting to 'no'")
            return "no"
        
        intent_agent = agent_output.get("intent_agent", {})
        if not intent_agent:
            print("⚠️ intent_agent not found, defaulting to 'no'")
            return "no"
        
        content_str = intent_agent.get("content", "")
        
        # Validate content_str is not empty
        if not content_str or not content_str.strip():
            print("⚠️ intent_agent content is empty, defaulting to 'no'")
            return "no"
        
        try:
            # Parse the JSON string to get the actual dictionary
            content_dict = json.loads(content_str)
            ticket_status = content_dict.get("suggest_ticket", False)
            
            if ticket_status == True or ticket_status == "true":
                print("✅ Ticket suggested: True")
                return "yes"
        except json.JSONDecodeError as e:
            print(f"❌ Error parsing JSON in if_ticket_is_raised: {e}")
            print(f"   Content preview: {content_str[:200] if content_str else 'None'}")
            return "no"
    
    except Exception as e:
        print(f"❌ Unexpected error in if_ticket_is_raised: {e}")
        return "no"
    
    print("✅ Ticket not suggested: No")
    return "no"


def route_by_intent_node(state):
    """
    Route the request to the appropriate node based on intent from intent_agent.
    """
    try:
        graph_state = state.get("graph_state", {})
        if not graph_state:
            print("⚠️ graph_state not found, defaulting to general_inquiry")
            state["graph_state"] = {"next_node": "general_inquiry"}
            return state
        
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
        
        # Validate intent_agent_output is not empty
        if isinstance(intent_agent_output, str) and not intent_agent_output.strip():
            print("⚠️ intent_agent output is empty string, defaulting to general_inquiry.")
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
            print(f"✅ Intent routing: {routing_status}")

        except json.JSONDecodeError as je:
            print(f"❌ JSON decode error in route_by_intent_node: {je}")
            print(f"   Output preview: {str(intent_agent_output)[:200]}")
            graph_state["next_node"] = "general_inquiry"
        except Exception as e:
            print(f"❌ Error parsing intent in route_by_intent_node: {e}")
            graph_state["next_node"] = "general_inquiry"

        state["graph_state"] = graph_state
        return state
    
    except Exception as outer_e:
        print(f"❌ Unexpected error in route_by_intent_node: {outer_e}")
        state["graph_state"] = {"next_node": "general_inquiry"}
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
