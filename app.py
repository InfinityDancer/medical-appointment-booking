import socket
import requests
import ssl
import sys

from fastapi import FastAPI
from src.routes import whatsapp
from src.routes import webhook_routes
from src.routes import flow_routes

app = FastAPI()

# Register routes
app.include_router(whatsapp.router)
app.include_router(webhook_routes.router)
app.include_router(flow_routes.router)

@app.get("/")   
def root():
    return {"message": "WhatsApp Chatbot Running 🚀"}

@app.get("/debug-supabase")
async def debug_supabase():
    results = {}
    
    # Test 1: DNS resolution
    try:
        infos = socket.getaddrinfo("vdllmuxwkqenluqvezzn.supabase.co", 443)
        results["dns"] = [str(i[4]) for i in infos]
    except Exception as e:
        results["dns_error"] = str(e)
    
    # Test 2: REST API (known to work)
    try:
        r = requests.get(
            "https://vdllmuxwkqenluqvezzn.supabase.co/rest/v1/doctors",
            headers={"apikey": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InZkbGxtdXh3a3Flbmx1cXZlenpuIiwicm9sZSI6ImFub24iLCJpYXQiOjE3Njk5ODcwODgsImV4cCI6MjA4NTU2MzA4OH0.cX8fJRq5YLQp4cxpHtIUzdw1e9as81bKdib4bbw6Ok4"},
            timeout=10
        )
        results["rest_api"] = r.status_code
    except Exception as e:
        results["rest_api_error"] = str(e)
    
    # Test 3: Edge Function
    try:
        r = requests.post(
            "https://vdllmuxwkqenluqvezzn.supabase.co/functions/v1/get_all_doctors",
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InZkbGxtdXh3a3Flbmx1cXZlenpuIiwicm9sZSI6ImFub24iLCJpYXQiOjE3Njk5ODcwODgsImV4cCI6MjA4NTU2MzA4OH0.cX8fJRq5YLQp4cxpHtIUzdw1e9as81bKdib4bbw6Ok4"
            },
            json={"organisation_id": "06288cea-dd10-430f-9f24-dd19af31bc6b"},
            timeout=30
        )
        results["edge_function_status"] = r.status_code
        results["edge_function_response"] = r.text[:200]
    except Exception as e:
        results["edge_function_error"] = str(e)

    # Test 4: Environment info
    results["openssl_version"] = ssl.OPENSSL_VERSION
    results["python_version"] = sys.version

    return results

@app.get("/test-tools")
async def test_tools():
    import httpx
    results = {}
    
    url = "https://vdllmuxwkqenluqvezzn.supabase.co/functions/v1/get_all_doctors"
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InZkbGxtdXh3a3Flbmx1cXZlenpuIiwicm9sZSI6ImFub24iLCJpYXQiOjE3Njk5ODcwODgsImV4cCI6MjA4NTU2MzA4OH0.cX8fJRq5YLQp4cxpHtIUzdw1e9as81bKdib4bbw6Ok4",
        "apiKey": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InZkbGxtdXh3a3Flbmx1cXZlenpuIiwicm9sZSI6ImFub24iLCJpYXQiOjE3Njk5ODcwODgsImV4cCI6MjA4NTU2MzA4OH0.cX8fJRq5YLQp4cxpHtIUzdw1e9as81bKdib4bbw6Ok4"
    }
    payload = {"organisation_id": "06288cea-dd10-430f-9f24-dd19af31bc6b"}

    # Test 1: requests library
    try:
        import requests
        r = requests.post(url, headers=headers, json=payload, timeout=30)
        results["requests_lib"] = {
            "status": r.status_code,
            "response": r.text[:200]
        }
    except Exception as e:
        results["requests_lib"] = {"error": str(e)}

    # Test 2: httpx library
    try:
        with httpx.Client(timeout=30.0) as client:
            r = client.post(url, headers=headers, json=payload)
        results["httpx_lib"] = {
            "status": r.status_code,
            "response": r.text[:200]
        }
    except Exception as e:
        results["httpx_lib"] = {"error": str(e)}

    # Test 3: call the actual tool function directly (bypassing @tool decorator)
    try:
        import json as jsonlib
        import requests as req
        r = req.post(url, headers=headers, data=jsonlib.dumps(payload), timeout=30)
        results["direct_call"] = {
            "status": r.status_code,
            "response": r.text[:200]
        }
    except Exception as e:
        results["direct_call"] = {"error": str(e)}

    # Test 4: call the actual @tool function
    try:
        from src.nodes.tools import get_all_doctors
        result = get_all_doctors.invoke({
            "organisation_id": "06288cea-dd10-430f-9f24-dd19af31bc6b",
            "location": "bangalore"
        })
        results["tool_invoke"] = result
    except Exception as e:
        results["tool_invoke"] = {"error": str(e)}

    return results
