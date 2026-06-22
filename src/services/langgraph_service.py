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
from src.utils.utils import get_agent_response,extract_time_data_message

redis_client = get_redis_client()

class graphState(TypedDict):
    graph_state:dict
    messages: Annotated[list,add_messages]
    memory:dict
    doctor_agent_response:dict
    patient_details:dict


checkpointer = InMemorySaver()

# ---------- Fetch Clinic Info from Supabase ----------
def get_clinic_info(state):
    """
    Fetches organisation / clinic details from the Supabase
    'organisation_details' table, filtered by the agent_phonenumber
    (Meta phone_number_id), and stores them in graph_state["clinic_data"]
    using the same keys the downstream agents expect.
    """
    mapped_data = {}
    phone_number_id = state["graph_state"].get("phone_number_id", "")

    if supabase and phone_number_id:
        try:
            cache_key = f"clinic_info:{phone_number_id}"
            cached = redis_client.get(cache_key)
            if cached:
                print("✅ Using cached clinic data from Redis")
                mapped_data = json.loads(cached)
            else:
                response = (
                    supabase.table("organisation_details")
                    .select("*")
                    .eq("agent_phonenumber", phone_number_id)
                    .limit(1)
                    .execute()
                )

                if response.data and len(response.data) > 0:
                    row = response.data[0]
                    # Map Supabase columns → keys expected by agent prompts
                    mapped_data = {
                        "OrganisationId": row.get("organisation_id", ""),
                        "ClinicName": row.get("organisation_name", ""),
                        "Address": row.get("organisation_address", ""),
                        "PhoneNumber": row.get("organisation_phonenumber", ""),
                        "Hours": row.get("clinic_hours", ""),
                        "CancellationPolicy": row.get("cancellation_policy", ""),
                    }
                    redis_client.set(cache_key, json.dumps(mapped_data))
                    print(f"ℹ Clinic data fetched from Supabase for phone_number_id={phone_number_id} and cached")
                else:
                    print(f"⚠️ No organisation found for phone_number_id={phone_number_id}")
        except Exception as e:
            print(f"❌ Error fetching clinic info from Supabase: {e}")
    else:
        if not phone_number_id:
            print("⚠️ phone_number_id not provided — clinic_data will be empty.")
        if not supabase:
            print("⚠️ Supabase client not initialized — clinic_data will be empty.")

    state["graph_state"]["clinic_data"] = mapped_data
    return state


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
    booking_done = graph_state.get("booking_confirmation", "")

    if appointment_time and booking_done:
        time_data = extract_time_data_message(appointment_time)
        slot_start_time = time_data["start_time"]
        print("slot_start_time in reply node:",slot_start_time)
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
            data = f"Hey, your appointment has been rescheduled successfully on {date} at {time} 😊"
            should_clear_session = True
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
        booking_done = graph_state.get("booking_confirmation", False)

        if booking_done:
            reply_type = agent_output.get("booking_agent", {}).get("type")
            if reply_type == "backend_reply":
                print("i am booking confirmation")
                data = f"Hey {patient_name} Your appointment has been booked successfully on {appointment_date} at {slot_start_time} 😊."
                print(data)
                should_clear_session = True  # Set flag to clear session after booking

        else:
            if "doctor_agent" in agent_output:
                print("i am doctor agent")
                data = get_agent_response(agent_output,"doctor_agent")
            elif "date_time_agent" in agent_output:
                print("i am date agent")

                data = get_agent_response(agent_output,"date_time_agent")
            elif "patient_agent" in agent_output:
                print("i am patient agent")

                data = get_agent_response(agent_output,"patient_agent")
            else:
                print("i am not booking confirmation")
                print(state)
                try:
                    data = json.loads(agent_output.get("booking_agent", {}).get("content", "{}")).get("error_message", "cannot book appointment")
                except Exception:
                    data = "cannot book appointment"
                graph_state["booking_confirmation"] = False
                should_clear_session = True
    else:
        data = "something went wrong"
    
    # print(f"📤 Sending message: {data}")
    send_whatsapp_message(sender, data)
    
    # Clear the session if booking/update/cancellation was completed
    if should_clear_session:
        clear_state_memory(state)
    
    return state


def clear_state_memory(state):
    message_array = state["messages"]
    message_array.clear()
    state["graph_state"] = {}
    state["memory"] = {}
    state["patient_details"] = {}
    state["doctor_agent_response"] = {}
    # print("🧹 Session memory cleared.")

def ticket_wp_message(state:graphState):
    graph_state = state.get("graph_state")
    sender = graph_state.get("sender")
    message = "There seems to be an issue. I have raised a ticket for you and our team will reach out to you shorty"
    send_whatsapp_message(sender, message)
    return state


# ----------- Graph Setup ------------
graph = StateGraph(graphState)
graph.add_node("whatsapp_trigger",whatsapp_trigger_node)
graph.add_node("fetch_clinic_info",get_clinic_info)
graph.add_node("send_reply",reply_node)
graph.add_node("intent_agent",lambda state:intent_agent_node(state,INTENT_AGENT_PROMPT))
graph.add_node("mail_node",send_mail_node)
graph.add_node("ticket_wp_message",ticket_wp_message)
graph.add_node("create_ticket",create_ticket)

graph.add_node("route_by_intent", route_by_intent_node)
graph.add_node("booking_node", booking_node)
graph.add_node("update_node", update_node)
graph.add_node("cancellation_node", cancellation_node)
graph.add_node("general_inquiry_node", general_inquiry_node)


graph.add_edge(START,"whatsapp_trigger")

# if whatsapp message is empty, then end the workflow
graph.add_conditional_edges("whatsapp_trigger", condition_func, {
    "yes": "fetch_clinic_info",
    "no": END  # end if no message
})

graph.add_edge("fetch_clinic_info","intent_agent")


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
    
@traceable(name="workflow")  # 👈 trace the whole run
def get_langgraph_response(message: str, sender: str, phone_number_id: str = ""):
    state = {
        "graph_state": {"whatsapp_message": message, "sender": sender, "phone_number_id": phone_number_id},
        "messages": []  # start fresh if no history
    }
    app.invoke(state,{"configurable": {"thread_id": sender}})
    return "executed"