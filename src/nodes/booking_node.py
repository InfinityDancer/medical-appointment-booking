from src.nodes.agent_node import booking_classifier_agent,patient_details_agent,doctor_name_agent,datetime_agent,location_agent
from prompts import BOOKING_AGENT_PROMPT,DATE_TIME_AGENT_PROMPT,PATIENT_CONFIRMATION_AGENT_PROMPT,DOCTOR_AGENT_PROMPT,LOCATION_AGENT_PROMPT
import json
from src.utils.utils import extract_time_data
from src.services.api_service import book_appointment

def booking_node(state):
    # 1. Run the initial booking classifier
    state = booking_classifier_agent(state, BOOKING_AGENT_PROMPT)
    
    # 2. Route to the next appropriate agent (e.g., DoctorNameAgent, DateTimeAgent, or PatientDetails)
    # The result of this call is the updated state after the next agent has run.
    state = route_to_next_agent(state)
    # print("next_agent: ", state.get("next_node", "UNKNOWN"))
    
    
    # 3. Check for patient confirmation completion to trigger the final booking API call
    patient_details = state.get("patient_details", {})
    
    if patient_details.get("status") == "patient_confirmation_complete":
        print("Patient confirmation complete. Attempting to book appointment...")
        
        # Extract data needed for the booking API
        graph_state = state["graph_state"]
        memory = state["memory"]

        organisation_details = graph_state.get("organisation_details","")
        organisation_id = organisation_details.get("organisation_id","")
        # Extract required fields (using keys like 'Patient_name', 'DOB', etc. as seen in your example)
        name = patient_details.get("Patient_name")
        dob = patient_details.get("DOB")
        email = patient_details.get("Email")
        gender = patient_details.get("Gender")
        number = graph_state.get("sender") # The user's phone number
        doctor_id = memory.get("doctor_id")
        requested_time = memory.get("requested_appointment_time")
        location = memory.get("location")
        # Format the time components
        try:
            time_data = extract_time_data(requested_time)
            slot_start_date = time_data["slot_start_date"]
            slot_start_time = time_data["slot_start_time"]
            
        except Exception as e:
            print(f"Error: Failed to extract/format time data from '{requested_time}'. {e}")
            # If time formatting fails, update state with an internal error and stop.
            state["graph_state"]["agent_output"]["booking_agent"] = {
                "content": json.dumps({"status": "error", "error_message": "Internal error formatting appointment time."}),
                "type": "backend_reply"
            }
            return state

        # Call the booking API
        try:
            print("booking data",name, email, dob, number, gender, doctor_id, slot_start_time, slot_start_date,location)
            response = book_appointment(
                organisation_id=organisation_id,
                patient_name=name,
                patient_email=email,
                patient_dob=dob,
                patient_phone_number=number,
                patient_gender=gender,
                doctor_id=doctor_id,
                slot_start_time=slot_start_time,
                slot_start_date=slot_start_date,
                location=location
            )
            
            print("booking response:",response)

            # Assuming book_appointment returns a dictionary-like object with a 'status' key
            if response and response.get("status") == "success":
                agent_output = graph_state.get("agent_output", {})
                # remove the patient_agent entry if present
                if "patient_agent" in agent_output:
                    agent_output.pop("patient_agent", None)
                print("Booking API call successful.")
                state["graph_state"]["agent_output"]["booking_agent"]["booking_agent_result"] = response
                state["graph_state"]["agent_output"]["booking_agent"]["type"] = "backend_reply"
                state["graph_state"]["booking_confirmation"] = True
            else:
                # Handle API failure response
                agent_output = graph_state.get("agent_output", {})
                # remove the patient_agent entry if present
                if "patient_agent" in agent_output:
                    agent_output.pop("patient_agent", None)
                error_msg = response.get("message","cannot book appointment")
                print(f"Booking API failed: {error_msg}")
                state["graph_state"]["agent_output"]["booking_agent"] = {
                    "content": json.dumps({"status": "booking_failed", "error_message": error_msg}),
                    "type": "backend_reply"
                }

        except Exception as e:
            # Handle connection/network error to the external booking service
            print(f"Critical Error: Failed to call booking API. {e}")
            state["graph_state"]["agent_output"]["booking_agent"] = {
                "content": json.dumps({"status": "booking_failed", "error_message": f"System error: Cannot connect to the booking service. ({str(e)})"}),
                "type": "backend_reply"
            }

    # If status is not 'patient_confirmation_complete', the function simply returns the state, 
    # allowing the conversational flow to continue in the pipeline.
    return state


# find next agent route after booking classifier agent
def route_to_next_agent(state):
    next_route = state["graph_state"]["agent_output"]["booking_agent"].get("content","")

    parsed = json.loads(next_route)
    if parsed.get("routing") == 'LocationAgent':
        return location_agent(state,LOCATION_AGENT_PROMPT)
    elif parsed.get("routing") == "DoctorNameAgent":
        return doctor_name_agent(state,DOCTOR_AGENT_PROMPT)
    elif parsed.get("routing") == "DateTimeAgent":
        return datetime_agent(state,DATE_TIME_AGENT_PROMPT)
    elif parsed.get("routing") == "PatientDetails":
        return patient_details_agent(state,PATIENT_CONFIRMATION_AGENT_PROMPT)
    else:
        raise ValueError(f"Unknown routing: {next_route}")



