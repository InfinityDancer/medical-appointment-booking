import json
import requests
import os
from dotenv import load_dotenv

load_dotenv()

token = os.getenv("BEARER_TOKEN") 

def cancel_appointment(id):
    url = 'https://medisync-60328.bubbleapps.io/version-test/api/1.1/wf/cancel_appointmement'
    headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json" 
    }

    payload = {
        "appointment_id":id
    }

    try:
        response = requests.post(url,headers=headers,data=json.dumps(payload))
        if response.status_code in [200, 201,101]:
            print("✅ cancel appointment successfully:", response.json())
            # state["graph_state"]["ticket_response"] = response.json()
        else:
            print(f"❌ API call failed with status code {response.status_code}")
            print("Response:", response.text)
            # state["graph_state"]["ticket_response"] = {"error": response.text}
    except Exception as e:
        print("❌ Something went wrong:", e)
        return e
    


def create_ticket(state):
    url= "https://medisync-60328.bubbleapps.io/version-test/api/1.1/wf/create_ticket"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json" 
    }

    payload={
        "patient_phone_number": state["graph_state"]["sender"],
        "ticket_description":"Bot related issue - intent mapping"
    }
    try:
        response = requests.post(url,headers=headers,data=json.dumps(payload))
        if response.status_code in [200, 201,101]:
            print("✅ Ticket created successfully:", response.json())
            # state["graph_state"]["ticket_response"] = response.json()
        else:
            print(f"❌ API call failed with status code {response.status_code}")
            print("Response:", response.text)
            # state["graph_state"]["ticket_response"] = {"error": response.text}
    except Exception as e:
        print("❌ Something went wrong:", e)
        # state["graph_state"]["ticket_response"] = {"error": str(e)}

    return state



def reschedule_appointment(id:str,new_date:str,new_time:str):
    print("reschedule_appointment",id,new_date,new_time)
    """"""
    url = "https://medisync-60328.bubbleapps.io/version-test/api/1.1/wf/reschedule_appointment"

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    payload ={
        "appointment_id":id,
        "new_date":new_date,
        "new_time":new_time
    }

    try:
        response = requests.post(url=url,headers=headers,data=json.dumps(payload))
        data = response.json()
        print("Raw Reschedule API response:", data)

        # Extract the real response part
        api_response = data.get("response", {})
        result_code = str(api_response.get("result_code", ""))
        result_message = api_response.get("result_response", "Unknown error")

         # Handle result codes properly
        if result_code == "101":
            print("✅ Appointment successfully booked")
            return {
                "status": "success",
                "message": "Appointment successfully booked",
                "api_response": api_response
            }
        elif result_code == "102":
            print("⚠️ Slot not available")
            return {
                "status": "error",
                "message": "Slot not available",
                "api_response": api_response
            }
        elif result_code == "103":
            print("⚠️ Incorrect Appointment Id")
            return {
                "status": "error",
                "message": "Incorrect Appointment Id",
                "api_response": api_response
            }
        elif result_code == "104":
            print("⚠️ Invalid Appointment")
            return {
                "status": "error",
                "message": "Invalid Appointment",
                "api_response": api_response
            }
        else:
            print("❌ Unexpected response")
            return {
                "status": "error",
                "message": result_message,
                "api_response": api_response
            }
        
    except Exception as e:
        print("❌ Exception while Rescheduling:", e)
        return {
            "status": "error",
            "message": f"Exception occurred: {e}"
        }


def book_appointment(name: str, email: str, dob: str, number: str, gender: str, id: str, slot_start_time: str, slot_start_date: str):
    """Book appointment for the patient"""
    url = "https://medisync-60328.bubbleapps.io/version-test/api/1.1/wf/bookappointment"

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    payload = {
        "slot_start_time": slot_start_time,
        "slot_start_date": slot_start_date,
        "doctorId": id,
        "patient_name": name,
        "patient_phone_number": number,
        "patient_dob_date": dob,
        "patient_gender": gender,
        "patient_email": email
    }

    try:
        response = requests.post(url, headers=headers, data=json.dumps(payload))
        data = response.json()
        print("Raw API response:", data)

        # Extract the real response part
        api_response = data.get("response", {})
        result_code = str(api_response.get("result_code", ""))
        result_message = api_response.get("result_response", "Unknown error")

        # Handle result codes properly
        if result_code == "101":
            print("✅ Appointment successfully booked")
            return {
                "status": "success",
                "message": "Appointment successfully booked",
                "api_response": api_response
            }

        elif result_code == "103":
            print("⚠️ Slot not available")
            return {
                "status": "error",
                "message": "Slot not available",
                "api_response": api_response
            }

        elif result_code == "105":
            print("⚠️ Invalid doctor or patient data")
            return {
                "status": "error",
                "message": "Invalid Phone Number",
                "api_response": api_response
            }

        else:
            print("❌ Unexpected response")
            return {
                "status": "error",
                "message": result_message,
                "api_response": api_response
            }

    except Exception as e:
        print("❌ Exception while booking:", e)
        return {
            "status": "error",
            "message": f"Exception occurred: {e}"
        }