from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
from .tools import get_appointment_list,symptom_mapping,get_appointments,set_appointments,check_doctor_availability,get_all_doctors,get_patient_details,search_services
from langchain.callbacks.base import BaseCallbackHandler
from langchain.schema import SystemMessage, HumanMessage, AIMessage
from langgraph.prebuilt import create_react_agent
from src.utils.utils import current_time,extract_ai_reply
from src.services.flow_service import send_patient_onboarding_flow
import json

time_data = current_time()

# Primary Agent (openai)
primary_agent = ChatOpenAI(model="gpt-4.1-mini")

backup_agent = ChatGoogleGenerativeAI(model="gemini-2.5-flash-lite")

# Backup Agent (OpenAI)

class StateSyncHandler(BaseCallbackHandler):
    def __init__(self, state):
        self.state = state

    def on_tool_end(self, output, **kwargs):
        try:
            data = json.loads(output) if isinstance(output, str) else output
            if isinstance(data, dict) and "Appointment" in data:
                self.state["graph_state"]["appointment_list"] = data
        except Exception as e:
            print("⚠️ Failed to sync tool output:", e)

intent_openai_executor = create_react_agent(model=primary_agent, tools=[])
intent_gemini_executor = create_react_agent(model=backup_agent, tools=[])

def deduplicate_consecutive_messages(messages: list):
    """
    Deduplicate consecutive identical messages from a list.
    """
    if not messages:
        return []

    deduped = [messages[0]]
    for msg in messages[1:]:
        if msg != deduped[-1]:
            deduped.append(msg)

    return deduped

def get_last_intent(conversation_data):
    """
    Extract the last 'intent' value from AIMessage JSONs in conversation history.
    """
    last_intent = None
    for msg in conversation_data:
        if isinstance(msg, AIMessage):
            try:
                data = json.loads(msg.content)
                if "intent" in data:
                    last_intent = data["intent"]
            except json.JSONDecodeError:
                continue
    return last_intent

def intent_agent_node(state: dict, base_prompt: str):
    """
    Reusable Agent Node with Backup + JSON enforcement.
    - state: workflow state dict
    - base_prompt: your big structured JSON/system prompt (must have {user_message} placeholder)
    """
    user_message = state["graph_state"].get("whatsapp_message", "")
    followup_message = state["graph_state"].get("followup_message", "none")

    # ==================== FLOW RESPONSE SHORT-CIRCUIT ====================
    # If this is a flow response, skip LLM classification and route directly to booking
    if state["graph_state"].get("is_flow_response"):
        print("📋 Flow response detected — bypassing intent classification, routing to booking")
        flow_intent = json.dumps({
            "intent": "booking",
            "text": user_message,
            "suggest_ticket": False
        })
        state["graph_state"].setdefault("agent_output", {})
        state["graph_state"]["agent_output"]["intent_agent"] = {
            "provider": "flow_bypass",
            "content": flow_intent
        }
        return {"messages": [{"role": "user", "content": user_message}, {"role": "assistant", "content": flow_intent}], "graph_state": state["graph_state"]}

    # conversation history array
    conversation_data = state.get("messages", [])[-10:]
    last_intent = get_last_intent(conversation_data)
    # print(last_intent)
    intent_input_prompt = base_prompt.format(
        user_message=user_message,
        conversation_history=conversation_data,
        last_intent=last_intent or "none",
        followup_message=followup_message
    )
    # Append user msg
    new_messages = [
        {"role": "user", "content": user_message}
    ]
    #print(state["messages"])
    
    try:
        # Try primary agent (OpenAI)
        reply = intent_openai_executor.invoke({
            'messages': [
                SystemMessage(content=intent_input_prompt),
                HumanMessage(content=user_message)
            ]
        })

        openai_reply = extract_ai_reply(reply)

        new_messages.append({"role": "assistant", "content": openai_reply})
        # print(new_messages)
        state["graph_state"].setdefault("agent_output", {})
        state["graph_state"]["agent_output"]["intent_agent"] = {
            "provider": "openai",
            "content": openai_reply
        }
        return {"messages": new_messages, "graph_state": state["graph_state"]}

    except Exception as e1:
        try:
            # Backup agent (Gemini)
            reply = intent_gemini_executor.invoke({
                'messages': [
                    SystemMessage(content=intent_input_prompt),
                    HumanMessage(content=user_message)
                ]
            })

            gemini_reply = extract_ai_reply(reply)


            # print("gemini: ", gemini_reply)

            new_messages.append({"role": "assistant", "content": gemini_reply})
            # print(state["messages"])
            state["graph_state"].setdefault("agent_output", {})
            state["graph_state"]["agent_output"]["intent_agent"] = {
                "provider": "gemini",
                "content": gemini_reply
            }
            return {"messages": new_messages, "graph_state": state["graph_state"]}

        except Exception as e2:
            state["graph_state"].setdefault("agent_output", {})
            state["graph_state"]["agent_output"]["error"] = {
                "error": f"Both agents failed: {str(e1)} | {str(e2)}"
            }
            return {"messages": new_messages, "graph_state": state["graph_state"]}



general_inquiry_tools = [get_appointment_list,symptom_mapping,search_services,get_all_doctors]

general_inquiry_openai_executor = create_react_agent(model= primary_agent,tools=general_inquiry_tools)
general_inquiry_gemini_executor = create_react_agent(model=backup_agent,tools=general_inquiry_tools)


def general_inquiry_agent(state: dict, base_prompt: str):
    graph_state = state["graph_state"]
    user_message = graph_state.get("whatsapp_message", "")
    followup_message = graph_state.get("followup_message", "none")
    user_number = graph_state["sender"]
    organisation_details = graph_state.get("organisation_details")
    organisation_name = organisation_details.get("organisation_name","")
    cancellation_policy = organisation_details.get("cancellation_policy","")
    organisation_id = organisation_details.get("organisation_id","")
    new_messages = state.get("messages", [])

    clinic_hours= organisation_details.get("clinic_hours")
    clinic_number = organisation_details.get("agent_phonenumber")
    
    # Construct clinic_location string from the new dynamic locations list
    locations = organisation_details.get("locations", [])
    if locations:
        clinic_location = " | ".join([f"{loc.get('location_name')}: {loc.get('address_line1')}, {loc.get('city')}" for loc in locations])
    else:
        clinic_location = organisation_details.get("clinic_address", "Contact clinic for details")
        
    time_data = current_time()
    # Format base/system prompt (only injected once)
    general_input_prompt = base_prompt.format(
        user_message=user_message,
        clinic_name=organisation_name,
        clinic_location=clinic_location,
        clinic_number=clinic_number,
        clinic_hours=clinic_hours,
        clinic_cancellation_policy=cancellation_policy,
        current_time=time_data['current_time'],
        user_phone_number=user_number,
        organisation_id=organisation_id,
        followup_message=followup_message
    )


    try:
        # ✅ Try Openai first
        reply = general_inquiry_openai_executor.invoke({
            "messages": [
                SystemMessage(content=general_input_prompt),  # your base prompt with clinic info
                HumanMessage(content=user_message)
            ]
        })
        reply_messages = reply["messages"]
        last_message = reply_messages[-1]   # AIMessage
        reply_text = last_message.content
        new_messages.append({"role": "assistant", "content": reply_text})
        state["graph_state"]["agent_output"]["general_inquiry_agent"] = {
            "provider": "openai",
            "content": reply_text
        }
        state["messages"] = deduplicate_consecutive_messages(new_messages)
        return state

    except Exception as e1:
        try:
            # ✅ Fallback: Gemini
            reply = general_inquiry_gemini_executor.invoke({
                "messages": [
                    SystemMessage(content=general_input_prompt),  # your base prompt with clinic info
                    HumanMessage(content=user_message)
                ]
            })
            reply_messages = reply["messages"]
            last_message = reply_messages[-1]   # AIMessage
            reply_text = last_message.content
            new_messages.append({"role": "assistant", "content": reply_text})
            state["graph_state"]["agent_output"]["general_inquiry_agent"] = {
                "provider": "gemini",
                "content": reply_text
            }
            state["messages"] = deduplicate_consecutive_messages(new_messages)
            return state

        except Exception as e2:
            # Both failed
            state["graph_state"]["agent_output"]["general_inquiry_agent"] = {
                "error": f"Both agents failed: {str(e1)} | {str(e2)}"
            }
            state["messages"] = deduplicate_consecutive_messages(new_messages)
            return state
        

cancellation_tools = [get_appointments,set_appointments,get_appointment_list]
cancellation_gemini_executor = create_react_agent(backup_agent, cancellation_tools)
cancellation_openai_executor = create_react_agent(primary_agent, cancellation_tools)

def cancellation_agent(state:dict,base_prompt:str):
    graph_state = state["graph_state"]
    user_message = graph_state.get("whatsapp_message", "")
    user_number = graph_state["sender"]
    conversation_data = state.get("messages", [])[-20:]
    new_messages = state.get("messages", [])
    organisation_details = graph_state.get("organisation_details","")
    organisation_id = organisation_details.get("organisation_id","")
    clinic_name = organisation_details.get("clinic_name","")
    time_data = current_time()
    cancellation_input_prompt = base_prompt.format(
        user_message=user_message,
        current_time=time_data['current_time'],
        current_date_reference = time_data['current_date_reference'],
        user_phone_number = user_number,
        conversation_context = conversation_data,
        organisation_id=organisation_id,
        clinic_name=clinic_name
    )
    
    try:
        # ✅ Try Openai first
        reply = cancellation_openai_executor.invoke({
            "messages": [
                SystemMessage(content=cancellation_input_prompt),  # your base prompt with clinic info
                HumanMessage(content=user_message)
            ]
        })
        reply_messages = reply["messages"]
        last_message = reply_messages[-1]   # AIMessage
        reply_text = last_message.content

        new_messages.append({"role": "assistant", "content": reply_text})
        # print(new_messages)
        state["graph_state"]["agent_output"]["cancellation_agent"] = {
            "content": reply_text,
            "type": "agent_reply"
        }
        state["messages"] = deduplicate_consecutive_messages(new_messages)
        # print(state["messages"])
        return state

    except Exception as e1:
        try:
            # ✅ Fallback: Gemini
            reply = cancellation_gemini_executor.invoke({
                "messages": [
                    SystemMessage(content=cancellation_input_prompt),  # your base prompt with clinic info
                    HumanMessage(content=user_message)
                ]
            })
            reply_messages = reply["messages"]
            last_message = reply_messages[-1]   # AIMessage
            reply_text = last_message.content

            new_messages.append({"role": "assistant", "content": reply_text})
            # print(new_messages)
            state["graph_state"]["agent_output"]["cancellation_agent"] = {
                "content": reply_text,
                "type": "agent_reply"
            }
            state["messages"] = deduplicate_consecutive_messages(new_messages)
            # print(state["messages"])
            return state

        except Exception as e2:
            # Both failed
            state["graph_state"]["agent_output"]["cancellation_agent"] = {
                "error": f"Both agents failed: {str(e1)} | {str(e2)}"
            }
            state["messages"] = deduplicate_consecutive_messages(new_messages)
            # print(state["messages"])
            return state
        


update_tools = [get_appointments,set_appointments,get_appointment_list,check_doctor_availability,get_all_doctors]
update_gemini_executor = create_react_agent(backup_agent, update_tools)
update_openai_executor = create_react_agent(primary_agent, update_tools)

def update_agent(state:dict,base_prompt:str):

    graph_state = state["graph_state"]
    user_message = graph_state.get("whatsapp_message", "")
    user_number = graph_state["sender"]
    conversation_data = state.get("messages", [])[-10:]
    new_messages = state.get("messages", [])
    organisation_details = graph_state.get("organisation_details","")
    organisation_id = organisation_details.get("organisation_id","")
    clinic_name = organisation_details.get("clinic_name","")
    memory = state.get("memory", {})
    saved_appointment_id = memory.get("saved_event_id",'')
    new_start_time = memory.get("new_start_time",'')
    saved_requested_time= memory.get('saved_requested_time','')


    time_data = current_time()
    update_input_prompt = base_prompt.format(
      user_message= user_message,
      clinic_name=clinic_name,
      user_phone_number=user_number,
      current_date_reference = time_data['current_date_reference'],
      conversation_context = conversation_data,
      organisation_id=organisation_id,
      saved_appointment_id=saved_appointment_id,
      new_start_time= new_start_time,
      saved_requested_time = saved_requested_time
    )

    # state["messages"].append(HumanMessage(content=user_message))

    try:
        # ✅ Try Openai first
        reply = update_openai_executor.invoke({
            "messages": [
                SystemMessage(content=update_input_prompt),  # your base prompt with clinic info
                HumanMessage(content=user_message)
            ]
        })
        reply_messages = reply["messages"]
        last_message = reply_messages[-1]   # AIMessage
        reply_text = last_message.content

        new_messages.append({"role": "assistant", "content": reply_text})
        # print(new_messages)
    
        state["graph_state"]["agent_output"]["update_agent"] = {
            "content": reply_text,
            "type": "agent_reply"
        }
        print("update agent reply text",reply_text)
        state["messages"] = deduplicate_consecutive_messages(new_messages)
        # print(state["messages"])
        return state

    except Exception as e1:
        try:
            # ✅ Fallback: Gemini
            reply = update_gemini_executor.invoke({
                "messages": [
                    SystemMessage(content=update_input_prompt),  # your base prompt with clinic info
                    HumanMessage(content=user_message)
                ]
            })
            reply_messages = reply["messages"]
            last_message = reply_messages[-1]   # AIMessage
            reply_text = last_message.content

            new_messages.append({"role": "assistant", "content": reply_text})
            # print(new_messages)
            state["graph_state"]["agent_output"]["update_agent"] = {
                "content": reply_text,
                "type": "agent_reply"
            }
            print("update agent reply text",reply_text)
            state["messages"] = deduplicate_consecutive_messages(new_messages)
            # print(state["messages"])
            return state

        except Exception as e2:
            # Both failed
            state["graph_state"]["agent_output"]["update_agent"] = {
                "error": f"Both agents failed: {str(e1)} | {str(e2)}"
            }
            state["messages"] = deduplicate_consecutive_messages(new_messages)
            # print(state["messages"])
            return state


booking_tools = [symptom_mapping]
booking_gemini_executor = create_react_agent(backup_agent, booking_tools)
booking_openai_executor = create_react_agent(primary_agent, booking_tools)

def booking_classifier_agent(state:dict,base_prompt:str):
    graph_state = state["graph_state"]
    user_message = graph_state.get("whatsapp_message", "")
    doctor_name = state.get("memory", {}).get("doctor_name", "")
    unverified_doctor_name = state.get("memory", {}).get("unverified_doctor_name", "")
    # print(doctor_name)
    # print(state.get("memory", {}))
    new_messages = state.get("messages", [])
    memory = state.get("memory", {})

    requested_time = state.get("memory", {}).get("requested_appointment_time", "")
    location = state.get("memory", {}).get("location", "")
    print("this is location in booking agent",location)

    conversation_data = state.get("messages", [])[-10:]
    appointment_date_confirm = state.get("memory", {}).get("appointment_date_confirm",False)
    print("this is appointment date confirm",appointment_date_confirm)

    organisation_details = graph_state.get("organisation_details", {}) or {}
    org_locations = graph_state.get("organisation_locations", [])
    location_names = [loc.get("location_name") for loc in org_locations] if isinstance(org_locations, list) else []
    print(f"org_locations: {org_locations}, location_names: {location_names}")

    if isinstance(org_locations, list):
         clinic_locations = ", ".join(location_names) if location_names else "Not available"
    else:
        clinic_locations = str(location_names) if location_names else "Not available"

    clinic_location_count = len(location_names) if isinstance(location_names, list) else (1 if location_names else 0)


    booking_classifier_input = base_prompt.format(
        user_message = user_message,
        doctor_name=doctor_name,
        unverified_doctor_name=unverified_doctor_name,
        requested_time=requested_time,
        appointment_date_confirm = appointment_date_confirm,
        conversation_context = conversation_data,
        memory = memory,
        location=location,
        clinic_location_count=clinic_location_count
    )
    
    # state["messages"].append(HumanMessage(content=user_message))

    try:
        # ✅ Try Openai first
        reply = update_openai_executor.invoke({
            "messages": [
                SystemMessage(content=booking_classifier_input),  # your base prompt with clinic info
                HumanMessage(content=user_message)
            ]
        })
        reply_messages = reply["messages"]
        last_message = reply_messages[-1]   # AIMessage
        reply_text = last_message.content

        state["graph_state"]["agent_output"]["booking_agent"] = {
            "content": reply_text,
            "type": "agent_reply"
        }
        state["messages"] = deduplicate_consecutive_messages(new_messages)
        # print(state["messages"])
        return state
    except Exception as e1:
        try:
            # ✅ Fallback: Gemini
            reply = update_gemini_executor.invoke({
                "messages": [
                    SystemMessage(content=booking_classifier_input),  # your base prompt with clinic info
                    HumanMessage(content=user_message)
                ]
            })
            reply_messages = reply["messages"]
            last_message = reply_messages[-1]   # AIMessage
            reply_text = last_message.content

            new_messages.append({"role": "assistant", "content": reply_text})
            # print(new_messages)
            state["graph_state"]["agent_output"]["booking_agent"] = {
                "content": reply_text,
                "type": "agent_reply"
            }
            state["messages"] = deduplicate_consecutive_messages(new_messages)
            # print(state["messages"])
            return state
        except Exception as e2:
            # Both failed
            state["graph_state"]["agent_output"]["booking_agent"] = {
                "error": f"Both agents failed: {str(e1)} | {str(e2)}"
            }
            state["messages"] = deduplicate_consecutive_messages(new_messages)
            # print(state["messages"])
            return state

location_agent_openai_executor = create_react_agent(primary_agent,tools=[])
location_agent_gemini_executor = create_react_agent(backup_agent,tools=[])

def location_agent(state: dict, base_prompt: str):
    """Location agent with improved error handling and debugging."""
    reply_text = None
    agent_response = None
    new_messages = []
    location_agent_prompt = ""
    
    try:
        # ============ SETUP ============
        graph_state = state.get("graph_state", {})
        if not graph_state:
            print("⚠️  graph_state not found in state")
            graph_state = {}
            
        user_message = graph_state.get("whatsapp_message", "")
        
        # ==================== FLOW RESPONSE HANDLING ====================
        if user_message == "__FLOW_RESPONSE__location_selection":
            flow_data = graph_state.get("flow_response_data")
            if flow_data:
                print(f"📋 Processing location selection flow response: {flow_data}")
                selected_location = flow_data.get("location_id") or flow_data.get("location_name")
                
                if "memory" not in state: state["memory"] = {}
                state["memory"]["location"] = selected_location
                
                synthetic_output = {
                    "status": "location_confirmed",
                    "location": selected_location,
                    "next_action": "DoctorNameAgent"
                }
                state["graph_state"]["agent_output"]["location_agent"] = {
                    "content": json.dumps(synthetic_output),
                    "type": "agent_reply"
                }
                new_messages = state.get("messages", [])
                new_messages.append({"role": "assistant", "content": json.dumps(synthetic_output)})
                state["messages"] = deduplicate_consecutive_messages(new_messages)
                print(f"✅ Location selected from flow: {selected_location}")
                return state
        location = state.get("memory", {}).get("location", "")
        conversation_data = state.get("messages", [])[-10:] if state.get("messages") else []
        new_messages = state.get("messages", [])
        time_data = current_time()

        # Extract available clinic locations from the new dynamic locations list
        organisation_details = graph_state.get("organisation_details", {}) or {}
        locations = organisation_details.get("locations", [])
        
        if locations:
            clinic_locations = ", ".join([loc.get("location_name") for loc in locations])
        else:
            # Fallback to legacy field if list is empty
            org_locations = organisation_details.get("organisation_locations", [])
            if isinstance(org_locations, list):
                clinic_locations = ", ".join(org_locations) if org_locations else "Not available"
            else:
                clinic_locations = str(org_locations) if org_locations else "Not available"

        print(f"📍 location_agent prep - user_msg: {bool(user_message)}, location: {bool(location)}, conv_data: {len(conversation_data)}, clinic_locations: {clinic_locations}")

        # ============ PROMPT FORMATTING ============
        try:
            location_agent_prompt = base_prompt.format(
                current_time=time_data['current_time'],
                user_message=user_message,
                location=location,
                conversation_context=conversation_data,
                clinic_locations=clinic_locations
            )
            print("✅ Prompt formatting successful")
        except KeyError as ke:
            print(f"❌ Prompt format error - Missing key: {ke}")
            raise ValueError(f"Missing format parameter: {ke}") from ke
        except Exception as fe:
            print(f"❌ Prompt format error: {fe}")
            raise

        # ============ OPENAI AGENT ============
        try:
            print("🔄 Invoking OpenAI location_agent executor...")
            reply = location_agent_openai_executor.invoke({
                "messages": [
                    SystemMessage(content=location_agent_prompt),
                    HumanMessage(content=user_message)
                ]
            })
            print("✅ OpenAI invoke successful")
            reply_messages = reply["messages"]
            last_message = reply_messages[-1]
            reply_text = last_message.content

            # Validate that reply_text is not empty
            if not reply_text or not reply_text.strip():
                print("⚠️  OpenAI location_agent returned empty response")
                raise ValueError("Empty response from OpenAI")

            # Parse JSON response
            try:
                agent_response = json.loads(reply_text)
                print("location agent_response",agent_response)
                if agent_response.get("status") == "location_confirmed":
                    if "memory" not in state or state["memory"] is None:
                        state["memory"] = {}
                    
                    if agent_response.get("location"):
                        state["memory"]["location"] = agent_response["location"]
                        print(f"Location confirmed: {agent_response['location']}")
                    
                    if agent_response.get("unverified_doctor_name"):
                        state["memory"]["unverified_doctor_name"] = agent_response["unverified_doctor_name"]

                    if agent_response.get("requested_time"):
                        state["memory"]["requested_appointment_time"] = agent_response["requested_time"]
                    
                elif agent_response.get("status") == 'awaiting_location':
                    if "memory" not in state:
                        state["memory"] = {}
                    if agent_response.get("requested_time"):
                        state["memory"]["requested_appointment_time"] = agent_response["requested_time"]
                    if agent_response.get("unverified_doctor_name"):
                        state["memory"]["unverified_doctor_name"] = agent_response["unverified_doctor_name"]
                        
            except json.JSONDecodeError as parse_e:
                print(f"⚠️  Failed to parse JSON in location_agent response: {parse_e}")
                print(f"   Response preview: {reply_text[:200] if reply_text else 'None'}")
                raise

            new_messages.append({"role": "assistant", "content": reply_text})
            state["graph_state"]["agent_output"]["location_agent"] = {
                "content": reply_text,
                "type": "agent_reply"
            }
            state["messages"] = deduplicate_consecutive_messages(new_messages)
            print("✅ OpenAI location_agent completed successfully")
            return state

        # ============ GEMINI FALLBACK ============
        except Exception as e1:
            import traceback
            print(f"❌ OpenAI location_agent failed: {e1}")
            print(f"   Traceback: {traceback.format_exc()}")
            try:
                print("🔄 Invoking Gemini location_agent executor as fallback...")
                reply = location_agent_gemini_executor.invoke({
                    "messages": [
                        SystemMessage(content=location_agent_prompt),
                        HumanMessage(content=user_message)
                    ]
                })
                print("✅ Gemini invoke successful")
                reply_messages = reply["messages"]
                last_message = reply_messages[-1]
                reply_text = last_message.content

                # Validate that reply_text is not empty
                if not reply_text or not reply_text.strip():
                    print("⚠️  Gemini location_agent returned empty response")
                    raise ValueError("Empty response from Gemini")

                try:
                    agent_response = json.loads(reply_text)
                    print("location agent_response",agent_response)
                    
                    if agent_response.get("status") == "location_confirmed":
                        if "memory" not in state:
                            state["memory"] = {}
                        state["memory"]["location"] = agent_response["location"]
                        print(f"Location confirmed: {agent_response['location']}")
                    else:
                        # Location not confirmed. Trigger the WhatsApp Location Selection Flow.
                        try:
                            from src.services.flow_service import send_location_selection_flow
                            sender = graph_state.get("sender")
                            org_id = organisation_details.get("organisation_id", "")
                            doctor_name = state.get("memory", {}).get("doctor_name", "the doctor")
                            
                            # Only trigger if we have multiple locations or if LLM explicitly says we need selection
                            if len(locations) > 1 or agent_response.get("status") == "awaiting_location":
                                print(f"📋 Location not confirmed — sending location selection flow to {sender}")
                                response = send_location_selection_flow(sender, locations, doctor_name, org_id, state)
                                
                                flow_sent_response = {
                                    "status": "flow_sent",
                                    "agent_response": "Please choose your preferred clinic location. 📋",
                                    "next_action": "awaiting_flow_response"
                                }
                                state["graph_state"]["agent_output"]["location_agent"] = {
                                    "content": json.dumps(flow_sent_response),
                                    "type": "flow_sent"
                                }
                                new_messages.append({"role": "assistant", "content": json.dumps(flow_sent_response)})
                                state["messages"] = deduplicate_consecutive_messages(new_messages)
                                return state
                        except Exception as flow_err:
                            print(f"❌ Failed to send location selection flow, falling back to text: {flow_err}")

                except json.JSONDecodeError as parse_e:
                    print(f"⚠️  Failed to parse JSON in location_agent Gemini response: {parse_e}")
                    print(f"   Response preview: {reply_text[:200] if reply_text else 'None'}")
                    raise

                new_messages.append({"role": "assistant", "content": reply_text})
                state["graph_state"]["agent_output"]["location_agent"] = {
                    "content": reply_text,
                    "type": "agent_reply"
                }
                state["messages"] = deduplicate_consecutive_messages(new_messages)
                print("✅ Gemini location_agent completed successfully (fallback)")
                return state

            except Exception as e2:
                import traceback
                print(f"❌ Gemini location_agent also failed: {e2}")
                print(f"   Traceback: {traceback.format_exc()}")
                # Return a valid JSON response to avoid downstream parsing errors
                fallback_response = json.dumps({
                    "status": "awaiting_location",
                    "location": None,
                    "agent_response": "Please confirm your preferred clinic location for the appointment.",
                    "next_action": "capture_location"
                })
                print(f"⚠️  Using hardcoded fallback response due to both agents failing")
                if "memory" not in state:
                    state["memory"] = {}
                state["graph_state"]["agent_output"]["location_agent"] = {
                    "content": fallback_response,
                    "type": "agent_reply",
                    "error_openai": str(e1),
                    "error_gemini": str(e2)
                }
                state["messages"] = deduplicate_consecutive_messages(new_messages)
                return state

    except Exception as outer_e:
        import traceback
        print(f"❌ location_agent unexpected error: {outer_e}")
        print(f"   Traceback: {traceback.format_exc()}")
        # Final fallback
        fallback_response = json.dumps({
            "status": "awaiting_location",
            "location": None,
            "agent_response": "Please confirm your preferred clinic location for the appointment.",
            "next_action": "capture_location"
        })
        if "graph_state" not in state:
            state["graph_state"] = {}
        if "agent_output" not in state["graph_state"]:
            state["graph_state"]["agent_output"] = {}
        state["graph_state"]["agent_output"]["location_agent"] = {
            "content": fallback_response,
            "type": "agent_reply",
            "error": str(outer_e)
        }
        state["messages"] = deduplicate_consecutive_messages(new_messages)
        return state

doctor_agent_tools = [get_all_doctors]
doctor_agent_openai_executor = create_react_agent(primary_agent,doctor_agent_tools)
doctor_agent_gemini_executor = create_react_agent(backup_agent,doctor_agent_tools)

def doctor_name_agent(state:dict,base_prompt:str):
    import traceback
    print("\n" + "="*60)
    print("🔵 ENTERED doctor_name_agent()")
    print("="*60)

    graph_state = state["graph_state"]
    user_message = graph_state.get("whatsapp_message", "")
    
    # ==================== FLOW RESPONSE HANDLING ====================
    if user_message == "__FLOW_RESPONSE__doctor_selection":
        flow_data = graph_state.get("flow_response_data")
        if flow_data:
            print(f"📋 Processing doctor selection flow response: {flow_data}")
            doctor_id = flow_data.get("doctor_id")
            doctor_name = flow_data.get("doctor_name", "Unknown Doctor")
            
            if "memory" not in state: state["memory"] = {}
            state["memory"]["doctor_id"] = doctor_id
            state["memory"]["doctor_name"] = doctor_name
            state["memory"]["unverified_doctor_name"] = "" # Clear any pending verification
            
            synthetic_output = {
                "status": "doctor_confirmed",
                "next_action": "DateTimeAgent"
            }
            state["graph_state"]["agent_output"]["doctor_agent"] = {
                "content": json.dumps(synthetic_output),
                "type": "agent_reply"
            }
            new_messages = state.get("messages", [])
            new_messages.append({"role": "assistant", "content": json.dumps(synthetic_output)})
            state["messages"] = deduplicate_consecutive_messages(new_messages)
            print(f"✅ Doctor selected from flow: {doctor_name} ({doctor_id})")
            return state

    doctor_name = state.get("memory", {}).get("doctor_name", "")
    unverified_doctor_name = state.get("memory", {}).get("unverified_doctor_name", "")
    doctor_agent_response = state.get("doctor_agent_response", {})
    conversation_data = state.get("messages", [])[-10:]
    new_messages = state.get("messages", [])
    organisation_details = graph_state.get("organisation_details", {}) or {}
    org_locations = graph_state.get("organisation_locations", [])
    location_names = [loc.get("location_name") for loc in org_locations] if isinstance(org_locations, list) else []
    print(f"org_locations: {org_locations}, location_names: {location_names}")

    if isinstance(org_locations, list):
         clinic_locations = ", ".join(location_names) if location_names else "Not available"
    else:
        clinic_locations = str(location_names) if location_names else "Not available"

    clinic_location_count = len(location_names) if isinstance(location_names, list) else (1 if location_names else 0)

    if isinstance(location_names, list) and len(location_names) == 1:
        location = location_names[0]
    else:
        location = state.get("memory", {}).get("location", "")
    print(f"clinic_locations: {clinic_locations}, location in memory: {location}")
    organisation_details = graph_state.get("organisation_details","")
    organisation_id = organisation_details.get("organisation_id","")
    time_data = current_time()

    print(f"📋 doctor_name_agent INPUTS:")
    print(f"   user_message: {user_message!r}")
    print(f"   doctor_name (from memory): {doctor_name!r}")
    print(f"   unverified_doctor_name: {unverified_doctor_name!r}")
    print(f"   location: {location!r}")
    print(f"   organisation_id: {organisation_id!r}")
    print(f"   stale doctor_agent_response (from state): {str(doctor_agent_response)[:200]}")
    print(f"   conversation_data length: {len(conversation_data)}")

    doctor_agent_prompt = base_prompt.format(
        user_message = user_message,
        current_time=time_data['current_time'],
        doctor_name = doctor_name,
        unverified_doctor_name = unverified_doctor_name,
        location = location,
        conversation_context = conversation_data,
        organisation_id=organisation_id
    )
        
    # state["messages"].append(HumanMessage(content=user_message))

    try:
        # ✅ Try Openai first
        print("\n🟢 PRIMARY AGENT (OpenAI) — ENTERING try block")
        print(f"   Using executor: doctor_agent_openai_executor (model: {primary_agent.model_name})")
        reply = doctor_agent_openai_executor.invoke({
            "messages": [
                SystemMessage(content=doctor_agent_prompt),  # your base prompt with clinic info
                HumanMessage(content=user_message)
            ]
        })
        print("🟢 PRIMARY AGENT (OpenAI) — invoke() returned successfully")
        reply_messages = reply["messages"]
        last_message = reply_messages[-1]   # AIMessage
        reply_text = last_message.content

        print(f"🟢 PRIMARY AGENT (OpenAI) — last_message type: {type(last_message).__name__}")
        print(f"🟢 PRIMARY AGENT (OpenAI) — reply_text (first 300 chars): {str(reply_text)[:300]!r}")
        print(f"🟢 PRIMARY AGENT (OpenAI) — reply_text empty? {not reply_text or not str(reply_text).strip()}")
        print(f"🟢 PRIMARY AGENT (OpenAI) — total messages in reply: {len(reply_messages)}")

        try:
            agent_response = json.loads(reply_text)
            print("🟢 PRIMARY AGENT — Parsed JSON agent_response:",agent_response)
            
            # Check if doctor is confirmed
            is_confirmed = (agent_response.get("status") in ["doctor_found", "doctor_confirmed","time_mentioned"] or 
                        agent_response.get("next_action") == "doctor_confirmed") or agent_response.get("requested_time") is not None
            
            if is_confirmed:
                        print(f"🟢 PRIMARY AGENT — Memory update triggered (status={agent_response.get('status')}, next_action={agent_response.get('next_action')}, requested_time={agent_response.get('requested_time')})")

                        if "memory" not in state:
                            state["memory"] = {}

                        # delete unverified doctor once it confirmed
                        state["memory"].pop("unverified_doctor_name", None)

                        # ✅ Always store requested time if present
                        if agent_response.get("requested_time"):
                            state["memory"]["requested_appointment_time"] = agent_response["requested_time"]
                        
                        if agent_response.get("official_doctor_name"):
                            state["memory"]["doctor_name"] = agent_response["official_doctor_name"]
                            state["memory"]["doctor_id"] = agent_response.get("doctor_id", "")
                            state["memory"]["doctor_specialty"] = agent_response.get("doctor_specialty", "")

                        if agent_response.get("location"):
                            state["memory"]["location"] = agent_response.get("location")
                        elif agent_response.get("location_name"): # common alternate key
                            state["memory"]["location"] = agent_response.get("location_name")
            else:
                # Not confirmed yet. Trigger the WhatsApp Doctor Selection Flow.
                try:
                    from src.services.flow_service import send_doctor_selection_flow
                    sender = graph_state.get("sender")
                    org_id = graph_state.get("organisation_details", {}).get("organisation_id", "")
                    # Fallback location resolution
                    location = state.get("memory", {}).get("location", "")
                    if not location:
                        org_locations = graph_state.get("organisation_locations", [])
                        location_names = [loc.get("location_name") for loc in org_locations] if isinstance(org_locations, list) else []
                        location = location_names[0] if location_names else ""
                    
                    print(f"📋 Doctor not confirmed — sending doctor selection flow to {sender}")
                    response = send_doctor_selection_flow(sender, org_id, location, state)
                    
                    flow_sent_response = {
                        "status": "flow_sent",
                        "agent_response": "Please choose a doctor from the menu. 📋",
                        "next_action": "awaiting_flow_response"
                    }
                    state["graph_state"]["agent_output"]["doctor_agent"] = {
                        "content": json.dumps(flow_sent_response),
                        "type": "flow_sent"
                    }
                    new_messages.append({"role": "assistant", "content": json.dumps(flow_sent_response)})
                    state["messages"] = deduplicate_consecutive_messages(new_messages)
                    return state
                except Exception as flow_err:
                    print(f"❌ Failed to send doctor selection flow, falling back to text: {flow_err}")
                
            if agent_response.get("status") == 'doctor_not_found' and agent_response.get("location"):
                state["memory"]["location"] = agent_response.get("location")

        except json.JSONDecodeError as jde:
            # If response isn't JSON, proceed without state update
            print(f"❌ PRIMARY AGENT — Failed to parse JSON: {jde}")
            print(f"   Raw reply_text: {str(reply_text)[:500]!r}")
            pass

        new_messages.append({"role": "assistant", "content": reply_text})
        print("🟢 PRIMARY AGENT — SUCCESS. Setting doctor_agent in agent_output.")
        state["graph_state"]["agent_output"]["doctor_agent"] = {
            "content": reply_text,
            "type": "agent_reply"
        }
        state["doctor_agent_response"] = reply_text
        state["messages"] = deduplicate_consecutive_messages(new_messages)
        # print(state["messages"])
        print("🟢 PRIMARY AGENT — doctor_name_agent DONE (returned from primary)\n")
        return state

    except Exception as e1:
            print(f"\n🔴 PRIMARY AGENT (OpenAI) — FAILED with exception: {type(e1).__name__}: {e1}")
            print(f"   Full traceback:\n{traceback.format_exc()}")
            try:
                # ✅ Fallback: Gemini
                print(f"\n🟡 BACKUP AGENT (Gemini) — ENTERING fallback try block")
                print(f"   Using executor: doctor_agent_gemini_executor (model: {backup_agent.model})")
                reply = doctor_agent_gemini_executor.invoke({
                    "messages": [
                        SystemMessage(content=doctor_agent_prompt),  # your base prompt with clinic info
                        HumanMessage(content=user_message)
                    ]
                })
                print("🟡 BACKUP AGENT (Gemini) — invoke() returned successfully")
                reply_messages = reply["messages"]
                last_message = reply_messages[-1]   # AIMessage
                reply_text = last_message.content

                print(f"🟡 BACKUP AGENT (Gemini) — last_message type: {type(last_message).__name__}")
                print(f"🟡 BACKUP AGENT (Gemini) — reply_text (first 300 chars): {str(reply_text)[:300]!r}")

                try:
                    agent_response = json.loads(reply_text)
                    print(f"🟡 BACKUP AGENT — Parsed JSON agent_response: {agent_response}")
                    
                    is_confirmed = (agent_response.get("status") in ["doctor_found", "doctor_confirmed","time_mentioned"] or 
                        agent_response.get("next_action") == "doctor_confirmed") or agent_response.get("requested_time") is not None
                    
                    if is_confirmed:
                        if "memory" not in state:
                            state["memory"] = {}

                        # ✅ Always store requested time if present
                        if agent_response.get("requested_time"):
                            state["memory"]["requested_appointment_time"] = agent_response["requested_time"]
                        
                        if agent_response.get("official_doctor_name"):
                            state["memory"]["doctor_name"] = agent_response["official_doctor_name"]
                            state["memory"]["doctor_id"] = agent_response.get("doctor_id", "")
                            state["memory"]["doctor_specialty"] = agent_response.get("doctor_specialty", "")
                        
                        if agent_response.get("location"):
                            state["memory"]["location"] = agent_response.get("location")
                        elif agent_response.get("location_name"):
                            state["memory"]["location"] = agent_response.get("location_name")
                    else:
                        try:
                            from src.services.flow_service import send_doctor_selection_flow
                            sender = graph_state.get("sender")
                            org_id = graph_state.get("organisation_details", {}).get("organisation_id", "")
                            location = state.get("memory", {}).get("location", "")
                            if not location:
                                org_locations = graph_state.get("organisation_locations", [])
                                location_names = [loc.get("location_name") for loc in org_locations] if isinstance(org_locations, list) else []
                                location = location_names[0] if location_names else ""
                            
                            print(f"📋 Doctor not confirmed (Gemini) — sending doctor selection flow to {sender}")
                            response = send_doctor_selection_flow(sender, org_id, location, state)
                            
                            flow_sent_response = {
                                "status": "flow_sent",
                                "agent_response": "Please choose a doctor from the menu. 📋",
                                "next_action": "awaiting_flow_response"
                            }
                            state["graph_state"]["agent_output"]["doctor_agent"] = {
                                "content": json.dumps(flow_sent_response),
                                "type": "flow_sent"
                            }
                            new_messages.append({"role": "assistant", "content": json.dumps(flow_sent_response)})
                            state["messages"] = deduplicate_consecutive_messages(new_messages)
                            return state
                        except Exception as flow_err:
                            print(f"❌ Failed to send doctor selection flow (Gemini fallback), falling back to text: {flow_err}")

                except json.JSONDecodeError as jde:
                # If response isn't JSON, proceed without state update
                    print(f"❌ BACKUP AGENT — Failed to parse JSON: {jde}")
                    print(f"   Raw reply_text: {str(reply_text)[:500]!r}")
                    pass    

                new_messages.append({"role": "assistant", "content": reply_text})
                print("🟡 BACKUP AGENT — SUCCESS. Setting doctor_agent in agent_output.")
                state["graph_state"]["agent_output"]["doctor_agent"] = {
                    "content": reply_text,
                    "type": "agent_reply"
                }
                state["doctor_agent_response"] = reply_text
                state["messages"] = deduplicate_consecutive_messages(new_messages)
                print("🟡 BACKUP AGENT — doctor_name_agent DONE (returned from backup)\n")
                return state

            except Exception as e2:
                # Both failed
                print(f"\n🔴🔴 BOTH AGENTS FAILED in doctor_name_agent!")
                print(f"   OpenAI error: {type(e1).__name__}: {e1}")
                print(f"   Gemini error: {type(e2).__name__}: {e2}")
                print(f"   Gemini traceback:\n{traceback.format_exc()}")
                error_msg = f"Both agents failed: {str(e1)} | {str(e2)}"
                fallback_content = json.dumps({
                    "status": "error",
                    "agent_response": "Something went wrong while finding doctors. Please try again.",
                    "error_detail": error_msg
                })
                state["graph_state"]["agent_output"]["doctor_agent"] = {
                    "content": fallback_content,
                    "type": "agent_reply",
                    "error": error_msg
                }
                state["messages"] = deduplicate_consecutive_messages(new_messages)
                print("🔴🔴 doctor_name_agent returning with error fallback\n")
                return state

datetime_agent_tools = [check_doctor_availability]
datetime_agent_openai_executor = create_react_agent(primary_agent,datetime_agent_tools)
datetime_agent_gemini_executor = create_react_agent(backup_agent,datetime_agent_tools)

def datetime_agent(state:dict,base_prompt:str):
    graph_state = state["graph_state"]
    user_message = graph_state.get("whatsapp_message", "")
    
    # ==================== FLOW RESPONSE HANDLING ====================
    if user_message == "__FLOW_RESPONSE__slot_picker":
        flow_data = graph_state.get("flow_response_data")
        if flow_data:
            print(f"📋 Processing slot picker flow response: {flow_data}")
            selected_date = flow_data.get("selected_date")
            selected_time = flow_data.get("selected_time")
            
            from datetime import datetime
            time_display = selected_time
            if selected_time:
                try:
                    time_display = datetime.strptime(selected_time, "%H:%M").strftime("%I:%M %p")
                except ValueError:
                    pass
                
            requested_time_str = f"{selected_date} at {time_display}"
            
            if "memory" not in state: state["memory"] = {}
            state["memory"]["requested_appointment_time"] = requested_time_str
            state["memory"]["time_phrase"] = requested_time_str
            state["memory"]["appointment_date_confirm"] = True
            
            synthetic_output = {
                "status": "time_confirmed",
                "requested_time": requested_time_str,
                "time_phrase": requested_time_str,
                "next_action": "PatientDetails"
            }
            state["graph_state"]["agent_output"]["date_time_agent"] = {
                "content": json.dumps(synthetic_output),
                "type": "agent_reply"
            }
            new_messages = state.get("messages", [])
            new_messages.append({"role": "assistant", "content": json.dumps(synthetic_output)})
            state["messages"] = deduplicate_consecutive_messages(new_messages)
            print(f"✅ Slot selected from flow: {requested_time_str}")
            return state
            
    doctor_agent_response = state.get("doctor_agent_response")
    memory = state.get("memory",{})
    doctor_name = memory.get("doctor_name", "")
    print("doctor_name",doctor_name)
    doctor_id = memory.get("doctor_id", "")
    requested_time = memory.get("requested_appointment_time", "")
    conversation_data = state.get("messages", [])[-10:]
    time_mentioned = memory.get("requested_appointment_time","") 
    location = memory.get("location", "")
    new_messages = state.get("messages", [])                    
    location = memory.get("location",'')
    organisation_details = graph_state.get("organisation_details","")
    organisation_id = organisation_details.get("organisation_id","")
    clinic_hours = organisation_details.get("clinic_hours","")
    time_data = current_time()

    datetime_agent_input_prompt = base_prompt.format(
        user_message = user_message,
        doctor_agent_response = doctor_agent_response,
        memory = memory,
        doctor_name = doctor_name,
        doctor_id = doctor_id,
        location = location,
        current_time=time_data['current_time'],
        requested_time=requested_time,
        conversation_context = conversation_data,
        time_mentioned = time_mentioned,
        clinic_hours = clinic_hours,
        organisation_id=organisation_id
    )
   
    # state["messages"].append(HumanMessage(content=user_message))

    try:
        # ✅ Try Openai first
        reply = update_openai_executor.invoke({
            "messages": [
                SystemMessage(content=datetime_agent_input_prompt),  # your base prompt with clinic info
                HumanMessage(content=user_message)
            ]
        })
        reply_messages = reply["messages"]
        last_message = reply_messages[-1]   # AIMessage
        reply_text = last_message.content

        try:
            agent_response = json.loads(reply_text)
            print("date response",agent_response)
            if agent_response.get("official_doctor_name"):
                if "memory" not in state:
                    state["memory"] = {}
                state["memory"]["doctor_name"] = agent_response["official_doctor_name"]
                print(state["memory"]["doctor_name"])

            if agent_response.get("next_action") == "PatientDetails":
                state["memory"]["requested_appointment_time"] = agent_response.get("requested_time")
                state["memory"]["time_phrase"] = agent_response.get("time_phrase")
                state["memory"]["appointment_date_confirm"] = True
                state["memory"]["doctor_id"] = agent_response.get("Doctor_id")
            elif agent_response.get("next_action") == 'ask_for_another_time':
                state["memory"]["requested_appointment_time"] = agent_response.get("requested_time")
                state["memory"]["appointment_date_confirm"] = False
                
                # Trigger Slot Picker Flow instead of chatting
                try:
                    from src.services.flow_service import send_slot_picker_flow
                    sender = graph_state.get("sender")
                    org_id = organisation_id
                    loc_to_use = location
                    
                    print(f"📋 Time not confirmed — sending slot picker flow to {sender}")
                    response = send_slot_picker_flow(sender, doctor_id, doctor_name, loc_to_use, org_id, state)
                    
                    flow_sent_response = {
                        "status": "flow_sent",
                        "agent_response": "Please pick an available date and time slot from the calendar. 📅",
                        "next_action": "awaiting_flow_response"
                    }
                    state["graph_state"]["agent_output"]["date_time_agent"] = {
                        "content": json.dumps(flow_sent_response),
                        "type": "flow_sent"
                    }
                    new_messages.append({"role": "assistant", "content": json.dumps(flow_sent_response)})
                    state["messages"] = deduplicate_consecutive_messages(new_messages)
                    return state
                except Exception as flow_err:
                    print(f"❌ Failed to send slot picker flow, falling back to text: {flow_err}")
            
            # Consistently capture location if provided
            if agent_response.get("location"):
                state["memory"]["location"] = agent_response.get("location")
            elif agent_response.get("location_name"):
                state["memory"]["location"] = agent_response.get("location_name")
        except json.JSONDecodeError:
            print("failed to parse json in datetime_agent response")
            pass
        
        new_messages.append({"role": "assistant", "content": reply_text})
        # print(new_messages)
        state["graph_state"]["agent_output"]["date_time_agent"] = {
            "content": reply_text,
            "type": "agent_reply"
        }
        state["messages"] = deduplicate_consecutive_messages(new_messages)
        # print(state["messages"])
        return state

    except Exception as e1:
        try:
            # ✅ Fallback: Gemini
            reply = update_gemini_executor.invoke({
                "messages": [
                    SystemMessage(content=datetime_agent_input_prompt),  # your base prompt with clinic info
                    HumanMessage(content=user_message)
                ]
            })
            reply_messages = reply["messages"]
            last_message = reply_messages[-1]   # AIMessage
            reply_text = last_message.content

            try:
                agent_response = json.loads(reply_text)
                print("date response",agent_response)
                if agent_response.get("official_doctor_name"):
                    if "memory" not in state:
                        state["memory"] = {}
                    state["memory"]["doctor_name"] = agent_response["official_doctor_name"]
                    print(state["memory"]["doctor_name"])

                if agent_response.get("next_action") == "PatientDetails":
                    state["memory"]["requested_appointment_time"] = agent_response.get("requested_time")
                    state["memory"]["time_phrase"] = agent_response.get("time_phrase")
                    state["memory"]["appointment_date_confirm"] = True
                    state["memory"]["doctor_id"] = agent_response.get("Doctor_id")
                elif agent_response.get("next_action") == "ask_for_another_time":
                    state["memory"]["appointment_date_confirm"] = False
                    
                    try:
                        from src.services.flow_service import send_slot_picker_flow
                        sender = graph_state.get("sender")
                        org_id = organisation_id
                        loc_to_use = location
                        
                        print(f"📋 Time not confirmed (Gemini) — sending slot picker flow to {sender}")
                        response = send_slot_picker_flow(sender, doctor_id, doctor_name, loc_to_use, org_id, state)
                        
                        flow_sent_response = {
                            "status": "flow_sent",
                            "agent_response": "Please pick an available date and time slot from the calendar. 📅",
                            "next_action": "awaiting_flow_response"
                        }
                        state["graph_state"]["agent_output"]["date_time_agent"] = {
                            "content": json.dumps(flow_sent_response),
                            "type": "flow_sent"
                        }
                        new_messages.append({"role": "assistant", "content": json.dumps(flow_sent_response)})
                        state["messages"] = deduplicate_consecutive_messages(new_messages)
                        return state
                    except Exception as flow_err:
                        print(f"❌ Failed to send slot picker flow, falling back to text: {flow_err}")
                
                # Consistently capture location if provided
                if agent_response.get("location"):
                    state["memory"]["location"] = agent_response.get("location")
                elif agent_response.get("location_name"):
                    state["memory"]["location"] = agent_response.get("location_name")

            
            except json.JSONDecodeError:
                print("failed to parse json in datetime_agent response")
                pass

            new_messages.append({"role": "assistant", "content": reply_text})
            # print(new_messages)
            state["graph_state"]["agent_output"]["date_time_agent"] = {
                "content": reply_text,
                "type": "agent_reply"
            }
            state["messages"] = deduplicate_consecutive_messages(new_messages)
            # print(state["messages"])
            return state

        except Exception as e2:
            # Both failed
            state["graph_state"]["agent_output"]["date_time_agent"] = {
                "error": f"Both agents failed: {str(e1)} | {str(e2)}"
            }
            state["messages"] = deduplicate_consecutive_messages(new_messages)
            # print(state["messages"])
            return state

patient_agent_tools = [get_patient_details]
patient_agent_openai_executor = create_react_agent(primary_agent,patient_agent_tools)
patient_agent_gemini_executor = create_react_agent(backup_agent,patient_agent_tools)

def patient_details_agent(state:dict,base_prompt:str):
    graph_state = state["graph_state"]
    user_message = graph_state.get("whatsapp_message", "")
    memory = state.get("memory",{})
    doctor_name = memory.get("doctor_name", "")
    requested_time = memory.get("requested_appointment_time", "")
    time_phrase = memory.get("time_phrase")
    user_number = graph_state["sender"]
    patient_name = memory.get("patient_name","")
    patient_details = state.get("patient_details",{})
    fist_time_user = memory.get("first_time_user","")
    conversation_data = state.get("messages", [])[-10:]
    need_patient_details = patient_details.get("status","")
    print(need_patient_details)
    new_messages = state.get("messages", [])

    # ==================== FLOW RESPONSE HANDLING ====================
    # If this is a flow response (from the patient onboarding WhatsApp flow),
    # extract patient details from graph state and skip the LLM entirely.
    if user_message == "__FLOW_RESPONSE__patient_onboarding":
        flow_data = graph_state.get("flow_response_data")
        
        if flow_data:
            print(f"📋 Processing patient onboarding flow response: {flow_data}")
            
            # Map flow fields to expected patient_details format
            patient_response = {
                "status": "patient_confirmation_complete",
                "agent_response": "Thanks for filling in your details! I'll proceed with your booking. ✅",
                "next_action": "create_booking",
                "Patient_name": flow_data.get("full_name", ""),
                "Email": flow_data.get("email", ""),
                "DOB": flow_data.get("date_of_birth"),
                "Gender": flow_data.get("gender", "").capitalize() if flow_data.get("gender") else None
            }
            
            state["patient_details"] = patient_response
            state["memory"]["patient_name"] = patient_response["Patient_name"]
            
            # Set agent output so reply_node can send confirmation
            state["graph_state"]["agent_output"]["patient_agent"] = {
                "content": json.dumps(patient_response),
                "type": "agent_reply"
            }
            
            print(f"✅ Patient details from flow: {patient_response}")
            
            new_messages.append({"role": "assistant", "content": json.dumps(patient_response)})
            state["messages"] = deduplicate_consecutive_messages(new_messages)
            return state
        else:
            print(f"⚠️ No flow response data found in graph state")
            # Fall through to normal LLM handling

    patient_details_input_prompt = base_prompt.format(
        doctor_name = doctor_name,
        requested_time = requested_time,
        time_phrase = time_phrase,
        user_message = user_message,
        user_phone_number = user_number,
        patient_name = patient_name,
        patient_details = patient_details,
        fist_time_user = fist_time_user,
        conversation_context = conversation_data,
        status = need_patient_details
    )

    try:
        # ✅ Try Openai first
        reply = patient_agent_openai_executor.invoke({
            "messages": [
                SystemMessage(content=patient_details_input_prompt),  # your base prompt with clinic info
                HumanMessage(content=user_message)
            ]
        })
        reply_messages = reply["messages"]
        last_message = reply_messages[-1]   # AIMessage
        reply_text = last_message.content
        print(reply_text)

        try:
            agent_response = json.loads(reply_text)

            # ==================== TRIGGER FLOW FOR NEW PATIENTS ====================
            # If the LLM determines this is a new patient needing details,
            # send the WhatsApp onboarding flow instead of text conversation.
            if agent_response.get("status") == "needs_patient_details":
                try:
                    sender = graph_state.get("sender")
                    print(f"📋 New patient detected — sending onboarding flow to {sender}")
                    response = send_patient_onboarding_flow(sender, state)
                    # output of workflow
                    
                    # Set agent output to indicate flow was sent
                    flow_sent_response = {
                        "status": "flow_sent",
                        "agent_response": "Please fill in your details using the form I just sent. 📋",
                        "next_action": "awaiting_flow_response"
                    }
                    state["patient_details"] = flow_sent_response # change
                    state["graph_state"]["agent_output"]["patient_agent"] = {
                        "content": json.dumps(flow_sent_response),
                        "type": "flow_sent"
                    }
                    new_messages.append({"role": "assistant", "content": json.dumps(flow_sent_response)})
                    state["messages"] = deduplicate_consecutive_messages(new_messages)
                    return state
                except Exception as flow_send_err:
                    print(f"❌ Failed to send onboarding flow, falling back to text: {flow_send_err}")
                    # Fall through to original text-based handling below

            if agent_response["status"] == "patient_confirmation_complete":
                state["memory"]["patient_name"] = agent_response.get("patient_name")
                # state["memory"]["first_time_user"] = True
            state["patient_details"] = agent_response
        except Exception as e:
            pass
        
        new_messages.append({"role": "assistant", "content": reply_text})
        # print(new_messages)
        state["graph_state"]["agent_output"]["patient_agent"] = {
            "content": reply_text,
            "type": "agent_reply"
        }
        state["messages"] = deduplicate_consecutive_messages(new_messages)
        # print(state["messages"])
        return state

    except Exception as e1:
        try:
            # ✅ Fallback: Gemini
            reply = patient_agent_gemini_executor.invoke({
                "messages": [
                    SystemMessage(content=patient_details_input_prompt),  # your base prompt with clinic info
                    HumanMessage(content=user_message)
                ]
            })
            reply_messages = reply["messages"]
            last_message = reply_messages[-1]   # AIMessage
            reply_text = last_message.content

            try:
                agent_response = json.loads(reply_text)

                # Also trigger flow for new patients in Gemini fallback
                if agent_response.get("status") == "needs_patient_details":
                    try:
                        sender = graph_state.get("sender")
                        print(f"📋 New patient detected (Gemini) — sending onboarding flow to {sender}")
                        response = send_patient_onboarding_flow(sender, state)
                        
                        flow_sent_response = {
                            "status": "flow_sent",
                            "agent_response": "Please fill in your details using the form I just sent. 📋",
                            "next_action": "awaiting_flow_response"
                        }
                        state["patient_details"] = flow_sent_response
                        state["graph_state"]["agent_output"]["patient_agent"] = {
                            "content": json.dumps(flow_sent_response),
                            "type": "flow_sent"
                        }
                        new_messages.append({"role": "assistant", "content": json.dumps(flow_sent_response)})
                        state["messages"] = deduplicate_consecutive_messages(new_messages)
                        return state
                    except Exception as flow_send_err:
                        print(f"❌ Failed to send onboarding flow (Gemini), falling back to text: {flow_send_err}")

                state["patient_details"] = agent_response
            except Exception as e:
                pass

            new_messages.append({"role": "assistant", "content": reply_text})
            # print(new_messages)
            state["graph_state"]["agent_output"]["patient_agent"] = {
                "content": reply_text,
                "type": "agent_reply"
            }
            state["messages"] = deduplicate_consecutive_messages(new_messages)
            # print(state["messages"])
            return state

        except Exception as e2:
            # Both failed
            state["graph_state"]["agent_output"]["patient_agent"] = {
                "error": f"Both agents failed: {str(e1)} | {str(e2)}"
            }
            state["messages"] = deduplicate_consecutive_messages(new_messages)
            # print(state["messages"])
            return state