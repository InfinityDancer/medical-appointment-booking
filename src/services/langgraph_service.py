from datetime import datetime, timedelta
import os
import json
from langgraph.graph import StateGraph, END,START
from src.services.whatsapp_service import send_whatsapp_message
from src.nodes.if_node import if_message_exists_node,if_ticket_is_raised,route_by_intent_node,route_by_next_node
from src.nodes.whatsapp_node import whatsapp_trigger_node
from langsmith import traceable
from langsmith.run_helpers import traceable
from src.utils.supabase_utils import supabase
from src.nodes.agent_node import intent_agent_node
from prompts import INTENT_AGENT_PROMPT
from src.nodes.mail_node import send_mail_node
from langgraph.checkpoint.memory import InMemorySaver
from typing import TypedDict,Annotated
from langgraph.graph import add_messages
from src.nodes.mail_node import send_mail_node
from src.services.api_service import create_ticket
from redis_config import get_redis_client
from src.nodes.booking_node import booking_node
from src.nodes.cancellation_node import cancellation_node
from src.nodes.general_inquiry import general_inquiry_node
from src.nodes.update_node import update_node
from src.nodes.fetch_organisation_details_node import fetch_organisation_details
from src.utils.utils import get_agent_response,extract_time_data_message
from src.services.message_logging_service import log_message

redis_client = get_redis_client()

class graphState(TypedDict):
    graph_state:dict
    messages: Annotated[list,add_messages]
    memory:dict
    doctor_agent_response:dict
    patient_details:dict
    organisation_id:str
    organisation_details:dict
    organisation_locations:list


checkpointer = InMemorySaver()

# ---------- Node Error Wrapper ----------
def wrap_node_with_error_tracking(node_fn, node_name):
    """
    Wraps a node function to track errors and include node_name in the state.
    Also checks for forced errors (for testing).
    """
    def wrapped_node(state):
        try:
            # Store current node being executed
            if "graph_state" not in state:
                state["graph_state"] = {}
            state["graph_state"]["current_node"] = node_name
            
            # Execute the node
            result = node_fn(state)
            
            # Clear current_node if successful
            if result and "graph_state" in result:
                result["graph_state"].pop("current_node", None)
            
            return result
        except Exception as e:
            # Store error info with node name
            error_msg = str(e)
            if "graph_state" not in state:
                state["graph_state"] = {}
            state["graph_state"]["last_error_node"] = node_name
            state["graph_state"]["last_error_msg"] = error_msg
            print(f"❌ Error in node '{node_name}': {error_msg}")
            error_description = (
                f"[API ERROR] '{node_name}' API failed.\n"
                f"error: {str(error_msg)}\n"
                ) 
            create_ticket(
                state=state,
                ticket_description=error_description
            )

            # Re-raise the exception so it propagates
            raise
    
    return wrapped_node


def condition_func(state):
    # print(if_message_exists_node(state))
    return if_message_exists_node(state)


def reply_node(state: graphState):
    memory = state.get("memory",{})
    patient_details = state.get("patient_details",{})
    patient_name = patient_details.get("Patient_name","")
    appointment_time = memory.get("requested_appointment_time","")
    sender = state["graph_state"]["sender"]
    graph_state = state.get("graph_state")
    organisation_id = state.get("organisation_id") or state.get("organisation_details", {}).get("organisation_id", "")
    booking_done = graph_state.get("booking_confirmation", "")
    location = memory.get("location",'')
    if appointment_time and booking_done:
        time_data = extract_time_data_message(appointment_time)
        slot_start_time = time_data["start_time"]
        # print("slot_start_time in reply node:",slot_start_time)
        appointment_date = time_data["appointment_date"]
   
    sender = graph_state.get("sender")
    data = None
    #reschedule time data
    time = graph_state.get("time","")
    date = graph_state.get("date","")
    
    should_clear_session = False  # Flag to track if we should clear session
    if graph_state['next_node'] == "general_inquiry":
        data = graph_state.get("agent_output").get("general_inquiry_agent").get("content","")
    elif graph_state['next_node'] == "update":
        reply_type = graph_state.get("agent_output").get("update_agent").get("type")
        if reply_type == "backend_reply":
            # Check if there's a reschedule conflict
            reschedule_conflict = graph_state.get("reschedule_conflict", False)
            if reschedule_conflict:
                # Get error message from update response
                update_response_str = graph_state.get("agent_output").get("update_agent").get("content", "{}")
                try:
                    update_response = json.loads(update_response_str)
                    error_msg = update_response.get("error_message", "You already have an appointment at this time.")
                    data = f"Hey {patient_name}, {error_msg}. Please choose a different time slot. 😊"
                except:
                    data = f"Hey {patient_name}, you already have an appointment at this time. Please choose a different time slot. 😊"
                graph_state["reschedule_conflict"] = False
            else:
                data = f"Hey, your appointment has been rescheduled successfully on {date} at {time} 😊"
            should_clear_session = True
        elif reply_type == "agent_reply":
            # Doctor not available - send agent message without clearing session
            # User may want to change location, so keep session alive
            data = graph_state.get("agent_output").get("update_agent").get("content", "")
            should_clear_session = False
        else:
            data = graph_state.get("agent_output").get("update_agent").get("content")
    elif graph_state['next_node'] == "cancellation":
        reply_type = graph_state.get("agent_output").get("cancellation_agent").get("type")
        if reply_type == "backend_reply":
            data = "Your appointment has been cancelled successfully."
            should_clear_session = True
        else:
            data = graph_state.get("agent_output").get("cancellation_agent").get("content")
            print("cancel agent data: ",data)
    elif graph_state['next_node'] == "booking" or graph_state['next_node'] == "availability":
        agent_output = graph_state.get("agent_output", {})
        # print("agent_output",agent_output)
        booking_done = graph_state.get("booking_confirmation", False)

        if booking_done:
            reply_type = agent_output.get("booking_agent", {}).get("type")
            if reply_type == "backend_reply":
                print("i am booking confirmation")
                # Get the booking response to check if it's a new booking or existing appointment
                booking_response = agent_output.get("booking_agent", {}).get("booking_agent_result", {})
                response_message = booking_response.get("message", "")
                
                # Check if appointment already exists
                if "already have an appointment" in response_message.lower():
                    # Existing appointment - send different message
                    existing_appointment = booking_response.get("appointment", {})
                    existing_time = existing_appointment.get("appointment_start", appointment_date)
                    existing_location = existing_appointment.get("location", location)
                    data = f"Hey {patient_name}, you already have an appointment scheduled at {existing_time} at {existing_location} 😊."
                else:
                    # New booking - send success message
                    data = f"Hey {patient_name} Your appointment has been booked successfully on {appointment_date} at {slot_start_time},{location} 😊."
                # print(data)
                should_clear_session = True  # Set flag to clear session after booking

        else:
            print(f"\n📋 reply_node: Booking flow — agent_output keys: {list(agent_output.keys())}")
            for key in agent_output:
                val = agent_output[key]
                if isinstance(val, dict):
                    print(f"   agent_output['{key}'] keys: {list(val.keys())}")
                    if "error" in val:
                        print(f"   ⚠️ agent_output['{key}'] has ERROR: {val['error']}")
                else:
                    print(f"   agent_output['{key}'] = {str(val)[:200]}")

            if "doctor_agent" in agent_output:
                print("i am doctor agent")
                data = get_agent_response(agent_output,"doctor_agent")
            elif "date_time_agent" in agent_output:
                print("i am date agent")

                data = get_agent_response(agent_output,"date_time_agent")
            elif "patient_agent" in agent_output:
                print("i am patient agent")

                data = get_agent_response(agent_output,"patient_agent")

            elif "location_agent" in agent_output:
                print("i am location agent")
                data = get_agent_response(agent_output,"location_agent")
            else:
                print("i am not booking confirmation")
                # print(state)
                try:
                    data = json.loads(agent_output.get("booking_agent", {}).get("content", "{}")).get("error_message", "cannot book appointment")
                except Exception:
                    data = "cannot book appointment"
                graph_state["booking_confirmation"] = False
                should_clear_session = True
    else:
        data = "something went wrong"
    
    # print(f"📤 Sending message: {data}")
    # Log agent response before sending with node name
    if organisation_id and sender:
        node_name = graph_state.get('next_node', 'unknown')
        log_message(sender, organisation_id, "agent", data, agent_name=node_name)
    
    send_whatsapp_message(sender, data,state)
    
    # Clear the session if booking/update/cancellation was completed
    if should_clear_session:
        clear_state_memory(state)
    
    return state


def clear_state_memory(state):
    # Remove conversation_id from Redis before clearing state
    try:
        sender = state.get("graph_state", {}).get("sender")
        phone_number_id = state.get("graph_state", {}).get("phone_number_id", "")
        composite_id = f"{sender}:{phone_number_id}" if sender else None
        if composite_id:
            # Mark session as just-ended for 5 min to block webhook retries of the terminal message
            redis_client.setex(f"session_ended:{composite_id}", 300, 1)
            print(f"🧹 Session ended marker set for {composite_id}")
    except Exception as e:
        print(f"⚠️ Error clearing Redis conversation: {str(e)}")
    
    # Clear state
    message_array = state["messages"]
    message_array.clear()
    state["graph_state"] = {}
    state["memory"] = {}
    state["patient_details"] = {}
    state["doctor_agent_response"] = {}
    state["organisation_id"] = None
    state["organisation_details"] = {}
    # print("🧹 Session memory cleared.")

def ticket_wp_message(state:graphState):
    graph_state = state.get("graph_state")
    sender = graph_state.get("sender")
    message = "There seems to be an issue. I have raised a ticket for you and our team will reach out to you shorty"
    send_whatsapp_message(sender, message,state)
    return state


# ----------- Graph Setup ------------
graph = StateGraph(graphState)
graph.add_node("whatsapp_trigger", wrap_node_with_error_tracking(whatsapp_trigger_node, "whatsapp_trigger"))
graph.add_node("fetch_organisation", wrap_node_with_error_tracking(fetch_organisation_details, "fetch_organisation"))
graph.add_node("send_reply", wrap_node_with_error_tracking(reply_node, "send_reply"))
graph.add_node("intent_agent", wrap_node_with_error_tracking(lambda state: intent_agent_node(state, INTENT_AGENT_PROMPT), "intent_agent"))
graph.add_node("mail_node", wrap_node_with_error_tracking(send_mail_node, "mail_node"))
graph.add_node("ticket_wp_message", wrap_node_with_error_tracking(ticket_wp_message, "ticket_wp_message"))
graph.add_node("create_ticket", wrap_node_with_error_tracking(create_ticket, "create_ticket"))

graph.add_node("route_by_intent", wrap_node_with_error_tracking(route_by_intent_node, "route_by_intent"))
graph.add_node("booking_node", wrap_node_with_error_tracking(booking_node, "booking_node"))
graph.add_node("update_node", wrap_node_with_error_tracking(update_node, "update_node"))
graph.add_node("cancellation_node", wrap_node_with_error_tracking(cancellation_node, "cancellation_node"))
graph.add_node("general_inquiry_node", wrap_node_with_error_tracking(general_inquiry_node, "general_inquiry_node"))


graph.add_edge(START,"whatsapp_trigger")

# if whatsapp message is empty, then end the workflow
graph.add_conditional_edges("whatsapp_trigger", condition_func, {
    "yes": "fetch_organisation",
    "no": END  # end if no message
})

# Fetch organisation details based on agent phone number
graph.add_edge("fetch_organisation","intent_agent")


# branching based on ticket condition
graph.add_conditional_edges("intent_agent",if_ticket_is_raised,{
    "yes": "mail_node",
    "no": "route_by_intent"
})

# if the ticket raised, then move to mail node
graph.add_edge("mail_node", "ticket_wp_message")
graph.add_edge("ticket_wp_message", "create_ticket")
graph.add_edge("create_ticket", END)


# Router → 4 sections
graph.add_conditional_edges(
    "route_by_intent",
    route_by_next_node,
    {
        "booking_node": "booking_node",
        "update_node": "update_node",
        "cancellation_node": "cancellation_node",
        "general_inquiry_node": "general_inquiry_node",
    },
)

# Each section → reply → END
graph.add_edge("booking_node", "send_reply")
graph.add_edge("update_node", "send_reply")
graph.add_edge("cancellation_node", "send_reply")
graph.add_edge("general_inquiry_node", "send_reply")


graph.add_edge("send_reply",END)

app = graph.compile(checkpointer=checkpointer)
 
def resolve_db_agent_phonenumber(phone_number_id: str) -> str:
    """
    Resolves the raw phone_number_id from Meta to the correctly formatted 
    agent_phonenumber stored in organisation_details.
    Uses Redis caching to avoid frequent Supabase lookups.
    """
    if not phone_number_id:
        return None
        
    cache_key = f"phone_id_to_number:{phone_number_id}"
    try:
        cached = redis_client.get(cache_key)
        if cached:
            return cached.decode('utf-8') if isinstance(cached, bytes) else cached
    except Exception as e:
        print(f"⚠️ Redis cache error (resolving phone): {e}")

    try:
        # First find the organisation_id for this phone_number_id
        res_integration = supabase.table("organisation_whatsapp_integration") \
            .select("phone_number") \
            .eq("phone_number_id", phone_number_id) \
            .execute()
            
        if res_integration.data:
            formatted_number = res_integration.data[0].get("phone_number")
            # Cache it
            try:
                redis_client.set(cache_key, formatted_number, ex=86400) # 24 hours
            except Exception:
                pass
            return formatted_number
            
        print(f"⚠️ Could not resolve phone_number_id {phone_number_id} to a formatted number")
        return None
    except Exception as e:
        print(f"❌ Error resolving db agent phonenumber: {e}")
        return None


def resolve_organisation_id(phone_number_id: str) -> str:
    """
    Resolves the raw phone_number_id from Meta to the organisation_id.
    """
    if not phone_number_id:
        return None
        
    cache_key = f"phone_id_to_org_id:{phone_number_id}"
    try:
        cached = redis_client.get(cache_key)
        if cached:
            return cached.decode('utf-8') if isinstance(cached, bytes) else cached
    except Exception as e:
        print(f"⚠️ Redis cache error (resolving org id): {e}")

    try:
        res_integration = supabase.table("organisation_whatsapp_integration") \
            .select("organisation_id") \
            .eq("phone_number_id", phone_number_id) \
            .execute()
            
        if res_integration.data:
            org_id = res_integration.data[0].get("organisation_id")
            try:
                redis_client.set(cache_key, org_id, ex=86400)
            except Exception:
                pass
            return org_id
            
        return None
    except Exception as e:
        print(f"❌ Error resolving db organisation id: {e}")
        return None

@traceable(name="workflow")  # 👈 trace the whole run
def get_langgraph_response(message: str, sender: str, agent_phonenumber: str = None,phone_number_id: str = ""):
    # Guard: skip entirely if there is no message content
    if not message or not message.strip():
        print("⚠️ get_langgraph_response called with empty message — skipping")
        return "skipped"

    # Composite ID isolates sessions per user + clinic (phone_number_id)
    composite_id = f"{sender}:{phone_number_id}"
    
    organisation_id = resolve_organisation_id(phone_number_id)
    if not organisation_id:
        print(f"⚠️ get_langgraph_response: Could not resolve organisation_id for phone_number_id {phone_number_id}")
        # Proceed anyway as the workflow might still function, but logging won't work
    
    # Log incoming user message
    if sender and organisation_id:
        # check human escalation flag, pause agent if a human is handling this user
        try:
            # Check if human escalation flag is active for this user
            escalation_check = supabase.table("user_message_logs") \
                .select("human_escalation") \
                .eq("user_phonenumber", sender) \
                .eq("organisation_id", organisation_id) \
                .eq("human_escalation", True) \
                .execute()

            if escalation_check.data:
                # Human is actively handling this user --> log the message but don't let agent respond
                print(f"Human escalation active for {sender}. Agent paused.")
                log_message(sender, organisation_id, "user", message)
                return "paused"
        except Exception as e:
            print(f"Error checking human escalation: {e}")

        log_message(sender, organisation_id, "user", message)
    
    # Fetch follow-up message context if available
    followup_message = "none"
    try:
        if supabase:
            followup_row = supabase.table("followup_logs").select("messages").eq("user_phonenumber", sender).execute()
            if followup_row.data:
                messages_data = followup_row.data[0].get("messages", [])
                if isinstance(messages_data, str):
                    import json
                    try:
                        messages_array = json.loads(messages_data)
                    except Exception:
                        messages_array = []
                else:
                    messages_array = messages_data
                    
                # Find the last followup message
                for msg_obj in reversed(messages_array):
                    if msg_obj.get("agent_name") == "followup":
                        followup_message = msg_obj.get("text", "none")
                        break
    except Exception as e:
        print(f"⚠️ Error fetching followup_logs: {e}")

    # Retrieve existing state from checkpointer to ensure persistent fields
    # (like organisation_details) are available even in the exception handler.
    existing_state_snapshot = app.get_state({"configurable": {"thread_id": composite_id}})
    existing_org_details = existing_state_snapshot.values.get("organisation_details") if existing_state_snapshot.values else None
    existing_org_locations = existing_state_snapshot.values.get("organisation_locations") if existing_state_snapshot.values else None

    state = {
        "graph_state": {
            "whatsapp_message": message, 
            "sender": sender, 
            "agent_phonenumber": agent_phonenumber,
            "phone_number_id": phone_number_id, 
            "followup_message": followup_message
        },
        "messages": [],  # start fresh if no history
        "organisation_id": organisation_id,
        "organisation_details": existing_org_details,
        "organisation_locations": existing_org_locations
    }
    
    try:
        app.invoke(state, {"configurable": {"thread_id": composite_id}})
        
        # Clean up if followup was consumed
        if state.get("graph_state", {}).get("followup_message", "none") != "none":
            try:
                if supabase:
                    supabase.table("followup_logs").delete().eq("user_phonenumber", sender).execute()
                    print(f"🧹 Cleared followup_logs for {sender}")
            except Exception as e:
                print(f"⚠️ Error clearing followup_logs: {e}")
                
    except Exception as e:
        error_msg = str(e)
        # Check which node the error occurred in
        error_node = state.get("graph_state", {}).get("last_error_node", "unknown")
        error_detail = state.get("graph_state", {}).get("last_error_msg", error_msg)
        
        print(f"❌ Workflow error in node '{error_node}': {error_detail}")
        
        # Log error to conversation with node information
        if sender and organisation_id:
            detailed_error = f"Error in {error_node}: {error_detail}"
            log_message(sender, organisation_id, "agent", "An error occurred during processing", error=detailed_error)
        
        # Optionally send error message to user
        user_message = f"Sorry, an error occurred. Please try again."
        send_whatsapp_message(sender, user_message,state)
    
    return "executed"