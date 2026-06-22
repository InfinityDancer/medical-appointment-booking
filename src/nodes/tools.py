import json
import requests
from langchain.tools import tool
import os
from dotenv import load_dotenv
from redis_config import get_redis_client
from src.utils.supabase_utils import supabase, semantic_search, get_organisation_id
load_dotenv()

token = os.getenv("BEARER_TOKEN") 

# print(token)

redis_client = get_redis_client()

@tool
def check_doctor_availability(doctor_name:str,start_date:str,start_time:str,end_time:str,end_date:str):
    """This tool is to check the availablilty of doctor, so a user can book appointment """
    print("doctor avalaibility",doctor_name,start_date,start_time,end_time,end_date)
    url = "https://medisync-60328.bubbleapps.io/version-test/api/1.1/wf/getAllSlots_copy"

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    payload = {
        "doctor_name":doctor_name,
        "slot_start_time":start_time,
        "slot_end_time":end_time,
        "slot_start_date":start_date,
        "slot_end_date":end_date
    }

    try:
        response = requests.post(url=url,headers=headers,data=json.dumps(payload))

        if response.status_code in [200, 201, 101]:
            print("✅ appointment fetched succesfully:")
            data = response.json()
            # print("doctor availability: ",data)
            return {"status": "success", "data": data}
    except Exception as e:
        print("something went wrong while fetch doctor availablity",e)
        
@tool
def get_appointment_list(phone_number):
    """ return appointment list """
    print("get:",phone_number)
    url = "https://medisync-60328.bubbleapps.io/version-test/api/1.1/wf/fetch_all_appointments"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    # Extract phone number and remove leading '91' if present
    phone_number = phone_number
    if phone_number.startswith("91") and len(phone_number) > 10:
        phone_number = phone_number[-10:]
    # print(phone_number)
    payload = {
        "phone_number": phone_number
    }

    try:
        response = requests.post(url, headers=headers, data=json.dumps(payload))
        if response.status_code in [200, 201, 101]:
            print("✅ appointment fetched succesfully:")
            # state["graph_state"]["appointment_list"] = response.json()
            data = response.json()
            return {"status": "success", "data": data}
        else:
            print(f"❌ API call failed with status code {response.status_code}")
            return {"status": "error", "message": response.text}
    except Exception as e:
        print("❌ Something went wrong:", e)
        return {"status": "error", "message": str(e)}


# getting data from the sheet
@tool
def symptom_mapping(symptom_name):
    """ return symtomps """
    from src.services.langgraph_service import fetch_sheet
    rows = fetch_sheet("1ZgxSH4eiCpbA68RfOC0gdB6thRve_xmcfq0IF-Ad5nw")
    if rows and len(rows) > 1:
        headers = rows[0]
        mapped_data = [dict(zip(headers, row)) for row in rows[1:]]
        return mapped_data
    return []

@tool
def get_appointments(phone_number: str) -> dict:
    """Retrieves appointment mappings for the user from Redis."""
    cache_key = f"appointments_{phone_number}"

    try:
        cached = redis_client.get(cache_key)
        if cached:
            # Decode bytes → str → JSON
            # decoded = json.loads(cached.decode("utf-8"))
            print(f"✅ Retrieved appointments from Redis for {phone_number}")
            return cached
        print(f"ℹ️ No appointments found in Redis for {phone_number}")
        return {"appointments": None}``
    except Exception as e:
        print(f"❌ Error retrieving appointments from Redis: {e}")
        return {"error": str(e)}

@tool
def set_appointments(phone_number: str, appointment_data: dict | None = None, ttl: int = 300):
    """Stores user appointment mappings in Redis under their phone number, only if not already present."""
    print("set: ",phone_number)
    cache_key = f"appointments_{phone_number}"
    
    if not appointment_data:
        print("⚠️ No appointment data provided to set_appointments.")
        return {"success": False, "message": "No appointment data provided"}

    try:
        if redis_client.exists(cache_key):
            print(f"ℹ️ Appointments already exist in Redis for {phone_number}. Skipping set.")
            return {"success": True, "message": "Already exists", "cache_key": cache_key}
        redis_client.set(cache_key, json.dumps(appointment_data), ex=ttl)
        print(f"✅ Saved appointments to Redis for {phone_number}: {appointment_data}")
        return {"success": True, "cache_key": cache_key}
    except Exception as e:
        print(f"❌ Error saving appointments to Redis: {e}")
        return {"success": False, "error": str(e)}
    

@tool
def get_all_doctors():
    """Fetch all doctors, using Redis cache with 1 hour expiry."""
    cache_key = "all_doctors"
    ttl = 21600  # 6 hour in seconds

    try:
        cached = redis_client.get(cache_key)
        if cached:
            print("✅ Retrieved doctors from Redis cache.")
            return {"status": "success", "data": json.loads(cached)}
    except Exception as e:
        print(f"❌ Error retrieving doctors from Redis: {e}")

    url = "https://medisync-60328.bubbleapps.io/version-test/api/1.1/wf/get_all_doctors"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    try:
        response = requests.get(url, headers=headers)
        if response.status_code in [200, 201, 101]:
            print("✅ Doctor fetched successfully from API.")
            data = response.json()
            try:
                redis_client.set(cache_key, json.dumps(data), ex=ttl)
                print("✅ Saved doctors to Redis cache.")
            except Exception as e:
                print(f"❌ Error saving doctors to Redis: {e}")
            return {"status": "success", "data": data}
        else:
            print(f"❌ API call failed with status code {response.status_code}")
            return {"status": "error", "message": response.text}
    except Exception as e:
        print("❌ Something went wrong:", e)
        return {"status": "error", "message": str(e)}


@tool
def get_patient_details(phone_number: str) -> dict:
    """ retrieve patient details """
    url = "https://medisync-60328.bubbleapps.io/version-test/api/1.1/wf/get_patient_details"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    payload = {
        "phone_number": phone_number
    }

    try:
        response = requests.post(url, headers=headers, data=json.dumps(payload))
        data = response.json()
        if response.status_code in [200, 201, 101]:

            print("✅ Patient details fetched successfully from API.")
            return {"status": "success", "message": data}
        else:
            print(f"❌ API call failed with status code {response.status_code}")
            return {"status": "error", "message": response.text}
    except Exception as e:
        print("❌ Something went wrong: ", e)
        return {"status": "error", "message": str(e)}


@tool
def search_services(query: str, clinic_name: str) -> dict:
    """Search the clinic's knowledge base for information about services, treatments,
    procedures, and related documents. Use this when the user asks about specific
    medical services, treatments, procedures, pricing, or anything that might be
    documented in the clinic's service descriptions and related documents.
    IMPORTANT: Do NOT optimise, summarize, or rewrite the query. Let the query parameter be the user's exact original question.
    Requires the clinic_name to identify the correct organisation."""

    print(f"\n{'='*60}")
    print(f"🔍 [RAG] search_services TOOL CALLED")
    print(f"🔍 [RAG] Query: {query}")
    print(f"🔍 [RAG] Clinic Name: {clinic_name}")
    print(f"{'='*60}")

    org_id = get_organisation_id(clinic_name)
    if not org_id:
        print(f"❌ [RAG] Could not resolve organisation_id for clinic: {clinic_name}")
        return {"status": "error", "message": "Could not resolve organisation for this clinic"}
    print(f"✅ [RAG] Resolved organisation_id: {org_id}")

    results = semantic_search(query, org_id)
    if results is None:
        print(f"❌ [RAG] Semantic search returned None (error)")
        return {"status": "error", "message": "Semantic search failed"}
    if not results:
        print(f"⚠️ [RAG] Semantic search returned 0 results")
        return {"status": "success", "data": [], "message": "No relevant results found"}
    print(f"✅ [RAG] Semantic search returned {len(results)} results")
    for i, r in enumerate(results):
        print(f"   📄 Result {i+1}: similarity={r.get('similarity', 'N/A'):.3f}, content preview: {str(r.get('content', ''))[:100]}...")
    print(f"{'='*60}\n")
    return {"status": "success", "data": results}