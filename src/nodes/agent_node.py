from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
from .tools import get_appointment_list,symptom_mapping,get_appointments,set_appointments,check_doctor_availability,get_all_doctors,get_patient_details,search_services
from langchain.callbacks.base import BaseCallbackHandler
from langchain.schema import SystemMessage, HumanMessage, AIMessage
from langgraph.prebuilt import create_react_agent
from src.utils.utils import current_time,extract_ai_reply
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

    # conversation history array
    conversation_data = state.get("messages", [])[-10:]
    last_intent = get_last_intent(conversation_data)
    # print(last_intent)
    intent_input_prompt = base_prompt.format(
        user_message=user_message,
        conversation_history=conversation_data,
        last_intent=last_intent or "none"
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
    clinic_data = graph_state["clinic_data"]
    user_number = graph_state["sender"]

    new_messages = state.get("messages", [])

    print(f"\n{'#'*60}")
    print(f"🤖 [GENERAL INQUIRY] Agent invoked")
    print(f"🤖 [GENERAL INQUIRY] User message: {user_message}")
    print(f"🤖 [GENERAL INQUIRY] Clinic: {clinic_data.get('ClinicName', 'N/A')}")
    print(f"{'#'*60}")

    # Format base/system prompt (only injected once)
    general_input_prompt = base_prompt.format(
        user_message=user_message,
        clinic_name=clinic_data["ClinicName"],
        clinic_location=clinic_data["Address"],
        clinic_number=clinic_data["PhoneNumber"],
        clinic_hours=clinic_data["Hours"],
        clinic_cancellation_policy=clinic_data["CancellationPolicy"],
        current_time=time_data['current_time'],
        user_phone_number=user_number
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

        # --- DEBUG: Log all intermediate messages from the ReAct agent ---
        print(f"\n{'─'*60}")
        print(f"🔎 [GENERAL INQUIRY] ReAct agent returned {len(reply_messages)} messages (OpenAI):")
        tool_was_called = False
        for idx, msg in enumerate(reply_messages):
            msg_type = type(msg).__name__
            print(f"   [{idx}] {msg_type}: {str(msg.content)[:200]}")
            if msg_type == "AIMessage" and hasattr(msg, 'tool_calls') and msg.tool_calls:
                tool_was_called = True
                for tc in msg.tool_calls:
                    print(f"        🛠️  TOOL CALL: {tc['name']}(args={tc['args']})")
            if msg_type == "ToolMessage":
                tool_was_called = True
                print(f"        📦 TOOL RESULT (preview): {str(msg.content)[:300]}")
        if tool_was_called:
            print(f"✅ [GENERAL INQUIRY] Tools WERE called (RAG likely used)")
        else:
            print(f"⚠️ [GENERAL INQUIRY] No tools were called (RAG NOT used)")
        print(f"{'─'*60}\n")
        # --- END DEBUG ---

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
        print(f"⚠️ [GENERAL INQUIRY] OpenAI failed: {e1}. Trying Gemini fallback...")
        try:
            # ✅ Fallback: Gemini
            reply = general_inquiry_gemini_executor.invoke({
                "messages": [
                    SystemMessage(content=general_input_prompt),  # your base prompt with clinic info
                    HumanMessage(content=user_message)
                ]
            })
            reply_messages = reply["messages"]

            # --- DEBUG: Log all intermediate messages from the ReAct agent ---
            print(f"\n{'─'*60}")
            print(f"🔎 [GENERAL INQUIRY] ReAct agent returned {len(reply_messages)} messages (Gemini fallback):")
            tool_was_called = False
            for idx, msg in enumerate(reply_messages):
                msg_type = type(msg).__name__
                print(f"   [{idx}] {msg_type}: {str(msg.content)[:200]}")
                if msg_type == "AIMessage" and hasattr(msg, 'tool_calls') and msg.tool_calls:
                    tool_was_called = True
                    for tc in msg.tool_calls:
                        print(f"        🛠️  TOOL CALL: {tc['name']}(args={tc['args']})")
                if msg_type == "ToolMessage":
                    tool_was_called = True
                    print(f"        📦 TOOL RESULT (preview): {str(msg.content)[:300]}")
            if tool_was_called:
                print(f"✅ [GENERAL INQUIRY] Tools WERE called (RAG likely used)")
            else:
                print(f"⚠️ [GENERAL INQUIRY] No tools were called (RAG NOT used)")
            print(f"{'─'*60}\n")
            # --- END DEBUG ---

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
            print(f"❌ [GENERAL INQUIRY] Both agents failed!")
            print(f"   OpenAI error: {e1}")
            print(f"   Gemini error: {e2}")
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
    clinic_data = graph_state["clinic_data"]
    user_number = graph_state["sender"]
    conversation_data = state.get("messages", [])[-20:]
    new_messages = state.get("messages", [])

    cancellation_input_prompt = base_prompt.format(
        user_message=user_message,
        clinic_name=clinic_data["ClinicName"],
        current_time=time_data['current_time'],
        current_date_reference = time_data['current_date_reference'],
        user_phone_number = user_number,
        conversation_context = conversation_data
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
        


update_tools = [get_appointments,set_appointments,get_appointment_list,check_doctor_availability]
update_gemini_executor = create_react_agent(backup_agent, update_tools)
update_openai_executor = create_react_agent(primary_agent, update_tools)

def update_agent(state:dict,base_prompt:str):

    graph_state = state["graph_state"]
    user_message = graph_state.get("whatsapp_message", "")
    clinic_data = graph_state["clinic_data"]
    user_number = graph_state["sender"]
    conversation_data = state.get("messages", [])[-10:]
    new_messages = state.get("messages", [])
    update_input_prompt = base_prompt.format(
      user_message= user_message,
      clinic_name=clinic_data["ClinicName"],
      user_phone_number=user_number,
      current_date_reference = time_data['current_date_reference'],
      conversation_context = conversation_data
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
    # print(doctor_name)
    # print(state.get("memory", {}))
    new_messages = state.get("messages", [])
    memory = state.get("memory", {})
    requested_time = state.get("memory", {}).get("requested_appointment_time", "")

    conversation_data = state.get("messages", [])[-10:]
    appointment_date_confirm = state.get("memory", {}).get("appointment_date_confirm",False)
    print("this is appointment date confirm",appointment_date_confirm)

    booking_classifier_input = base_prompt.format(
        user_message = user_message,
        doctor_name=doctor_name,
        requested_time=requested_time,
        appointment_date_confirm = appointment_date_confirm,
        conversation_context = conversation_data,
        memory = memory
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
doctor_agent_tools = [get_all_doctors]
doctor_agent_openai_executor = create_react_agent(primary_agent,doctor_agent_tools)
doctor_agent_gemini_executor = create_react_agent(backup_agent,doctor_agent_tools)

def doctor_name_agent(state:dict,base_prompt:str):
    graph_state = state["graph_state"]
    user_message = graph_state.get("whatsapp_message", "")
    doctor_name = state.get("memory", {}).get("doctor_name", "")
    doctor_agent_response = state.get("doctor_agent_response", {})
    conversation_data = state.get("messages", [])[-10:]
    new_messages = state.get("messages", [])

    doctor_agent_prompt = base_prompt.format(
        user_message = user_message,
        current_time=time_data['current_time'],
        doctor_name = doctor_name,
        conversation_context = conversation_data
    )
        
    # state["messages"].append(HumanMessage(content=user_message))

    try:
        # ✅ Try Openai first
        reply = doctor_agent_openai_executor.invoke({
            "messages": [
                SystemMessage(content=doctor_agent_prompt),  # your base prompt with clinic info
                HumanMessage(content=user_message)
            ]
        })
        reply_messages = reply["messages"]
        last_message = reply_messages[-1]   # AIMessage
        reply_text = last_message.content

        try:
            agent_response = json.loads(reply_text)
            # Update memory when doctor is confirmed
            if (agent_response.get("status") in ["doctor_found", "doctor_confirmed","time_mentioned"] or 
                        agent_response.get("next_action") == "doctor_confirmed") or doctor_agent_response.get("requested_time") is not None:

                        if "memory" not in state:
                            state["memory"] = {}

                        # ✅ Always store requested time if present
                        if agent_response.get("requested_time"):
                            state["memory"]["requested_appointment_time"] = agent_response["requested_time"]
                        
                        if agent_response.get("official_doctor_name"):
                            
                            state["memory"]["doctor_name"] = agent_response["official_doctor_name"]
                            state["memory"]["doctor_id"] = agent_response.get("doctor_id", "")
                            state["memory"]["doctor_specialty"] = agent_response.get("doctor_specialty", "")
                    
        except json.JSONDecodeError:
            # If response isn't JSON, proceed without state update
            print("failed to parse json in datetime_agent response")

            pass

        new_messages.append({"role": "assistant", "content": reply_text})
        print("this is doctor new message",new_messages)
        state["graph_state"]["agent_output"]["doctor_agent"] = {
            "content": reply_text,
            "type": "agent_reply"
        }
        state["doctor_agent_response"] = reply_text
        state["messages"] = deduplicate_consecutive_messages(new_messages)
        # print(state["messages"])
        return state

    except Exception as e1:
            try:
                # ✅ Fallback: Gemini
                reply = doctor_agent_gemini_executor.invoke({
                    "messages": [
                        SystemMessage(content=doctor_agent_prompt),  # your base prompt with clinic info
                        HumanMessage(content=user_message)
                    ]
                })
                reply_messages = reply["messages"]
                last_message = reply_messages[-1]   # AIMessage
                reply_text = last_message.content

                try:
                    agent_response = json.loads(reply_text)
                    # Update memory when doctor is confirmed

                    # handling the case when user use vague time term, ex: tomorrow,etc
                    if agent_response.get("status") == "doctor_list_shown" and agent_response.get("requested_time") is not None:
                        state["memory"]["requested_appointment_time"] = agent_response["requested_time"]

                    if (agent_response.get("status") in ["doctor_found", "doctor_confirmed","time_mentioned"] or 
                        agent_response.get("next_action") == "doctor_confirmed"):

                        if "memory" not in state:
                            state["memory"] = {}

                        # ✅ Always store requested time if present
                        if agent_response.get("requested_time"):
                            state["memory"]["requested_appointment_time"] = agent_response["requested_time"]
                        
                        if agent_response.get("official_doctor_name"):
                            
                            state["memory"]["doctor_name"] = agent_response["official_doctor_name"]
                            state["memory"]["doctor_id"] = agent_response.get("doctor_id", "")
                            state["memory"]["doctor_specialty"] = agent_response.get("doctor_specialty", "")
                           
                except json.JSONDecodeError:
                # If response isn't JSON, proceed without state update
                    print("failed to parse json in datetime_agent response")

                    pass    

                new_messages.append({"role": "assistant", "content": reply_text})
                # print(new_messages)
                state["graph_state"]["agent_output"]["doctor_agent"] = {
                    "content": reply_text,
                    "type": "agent_reply"
                }
                state["doctor_agent_response"] = reply_text
                state["messages"] = deduplicate_consecutive_messages(new_messages)
                # print(state["messages"])
                return state

            except Exception as e2:
                # Both failed
                state["graph_state"]["agent_output"]["doctor_agent"] = {
                    "error": f"Both agents failed: {str(e1)} | {str(e2)}"
                }
                state["messages"] = deduplicate_consecutive_messages(new_messages)
                # print(state["messages"])
                return state

datetime_agent_tools = [check_doctor_availability]
datetime_agent_openai_executor = create_react_agent(primary_agent,doctor_agent_tools)
datetime_agent_gemini_executor = create_react_agent(backup_agent,doctor_agent_tools)

def datetime_agent(state:dict,base_prompt:str):
    graph_state = state["graph_state"]
    user_message = graph_state.get("whatsapp_message", "")
    doctor_agent_response = state.get("doctor_agent_response")
    memory = state.get("memory",{})
    doctor_name = memory.get("doctor_name", "")
    print("doctor_name",doctor_name)
    doctor_id = memory.get("doctor_id", "")
    requested_time = memory.get("requested_appointment_time", "")
    conversation_data = state.get("messages", [])[-10:]
    time_mentioned = memory.get("requested_appointment_time","") 
    clinic_data = graph_state["clinic_data"]
    clinic_hours = clinic_data["Hours"]   
    new_messages = state.get("messages", [])                    

    datetime_agent_input_prompt = base_prompt.format(
        user_message = user_message,
        doctor_agent_response = doctor_agent_response,
        memory = memory,
        doctor_name = doctor_name,
        doctor_id = doctor_id,
        current_time=time_data['current_time'],
        requested_time=requested_time,
        conversation_context = conversation_data,
        time_mentioned = time_mentioned,
        clinic_hours = clinic_hours
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

            if agent_response.get("next_action") == "PatientDetails" and len(agent_response.get("available_slots", [])) == 1:
                state["memory"]["requested_appointment_time"] = agent_response.get("requested_time")
                state["memory"]["time_phrase"] = agent_response.get("time_phrase")
                state["memory"]["appointment_date_confirm"] = True
                state["memory"]["first_time_user"] = True
                state["memory"]["doctor_id"] = agent_response.get("Doctor_id")
                # print("setted requsted time and time phrase succesfully")
            
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
                    state["memory"]["first_time_user"] = True
                    state["memory"]["doctor_id"] = agent_response.get("Doctor_id")
                    # print("setted requsted time and time phrase succesfully")
                elif agent_response.get("status") == "ask_for_another_time":
                    state["memory"]["appointment_date_confirm"] = False

            
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

            if agent_response["status"] == "patient_confirmation_complete":
                state["memory"]["patient_name"] = agent_response.get("patient_name")
                state["memory"]["first_time_user"] = True
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