import asyncio
import json
import os
import time
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, Request, File, UploadFile, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from dotenv import load_dotenv
load_dotenv()

from src.utils.prompts import VOICE_AGENT_PROMPT
from src.TeleCMI_integration import format_prompt, FUNCTION_MAP, fuzzy_match_doctor_name_with_score
from src.utils.metrics import ConversationMetricsTracker
from src.services.medical_service import warm_doctors_cache

import openai
from openai import AsyncOpenAI

# Initialize OpenAI client
client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

app = FastAPI(title="Medisync Voice Agent Web Demo")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory session store (for demo purposes)
sessions = {}

# Load tools from config
with open("config/tools.json", "r") as f:
    tools_config = json.load(f)
    
openai_tools = []
for tool in tools_config.get("functions", []):
    openai_tools.append({
        "type": "function",
        "function": tool
    })

# Add switch_language tool (which was added dynamically in TeleCMI)
openai_tools.append({
    "type": "function",
    "function": {
        "name": "switch_language",
        "description": "Switch the language of the voice agent. Call this when the user confirms their preferred language during the initial language check, or if the user explicitly requests to speak in a different language later. Supported language codes: hi-IN, bn-IN, en-IN, gu-IN, kn-IN, ml-IN, mr-IN, od-IN, pa-IN, ta-IN, te-IN.",
        "parameters": {
            "type": "object",
            "properties": {
                "language_code": {
                    "type": "string",
                    "description": "The exact language code to switch to, e.g., 'hi-IN'"
                }
            },
            "required": ["language_code"]
        }
    }
})


class ChatRequest(BaseModel):
    session_id: str
    text: str
    phone_number: Optional[str] = "1234567890"

def get_or_create_session(session_id: str, phone_number: str = "1234567890"):
    if session_id not in sessions:
        formatted_prompt, clinic_name = format_prompt(caller_phone=phone_number)
        greeting = f"Hello, this is {clinic_name}, how may I help you?"
        
        sessions[session_id] = {
            "messages": [
                {"role": "system", "content": formatted_prompt},
                {"role": "assistant", "content": greeting}
            ],
            "phone_number": phone_number,
            "clinic_name": clinic_name,
            "language": "en-IN"
        }
        print(f"Created new session {session_id} for phone {phone_number}")
    return sessions[session_id]

async def execute_tool_call(tool_call, session):
    function_name = tool_call.function.name
    try:
        arguments = json.loads(tool_call.function.arguments)
    except Exception as e:
        arguments = {}
        
    print(f"Executing tool: {function_name} with args {arguments}")
    
    # Auto-inject phone number for certain functions (matching TeleCMI logic)
    if function_name in [
        "get_patient_details", "get_appointments",
        "initiate_cancel_appointment", "confirm_cancel_appointment",
        "initiate_reschedule_appointment", "confirm_reschedule_appointment"
    ]:
        arguments["phone_number"] = session.get("phone_number")
        
    if function_name == "book_appointment":
        arguments["patient_phone"] = session.get("phone_number")

    if function_name == "switch_language":
        lang_code = arguments.get("language_code", "en-IN")
        session["language"] = lang_code
        return {
            "status": "success",
            "message": f"Successfully switched to {lang_code}. You must now reply strictly in this language."
        }
        
    if function_name not in FUNCTION_MAP:
        return {"error": f"Unknown function: {function_name}"}
        
    func = FUNCTION_MAP[function_name]
    
    # Fuzzy match logic for get_doctor_availability
    if function_name == "get_doctor_availability" and "doctor_name" in arguments:
        original_name = arguments["doctor_name"]
        matched_name, _ = fuzzy_match_doctor_name_with_score(original_name)
        if matched_name and matched_name != original_name:
            arguments["doctor_name"] = matched_name
        arguments.setdefault("start_time", "00:00")
        arguments.setdefault("end_time", "23:59")
        
    try:
        # Run synchronous function in thread pool
        loop = asyncio.get_event_loop()
        result_str = await loop.run_in_executor(None, lambda: func(**arguments))
        result = json.loads(result_str) if isinstance(result_str, str) else result_str
        return result
    except Exception as e:
        print(f"Tool execution error: {e}")
        return {"status": "error", "message": str(e)}

@app.on_event("startup")
async def startup_event():
    print("Warming up doctors cache...")
    warm_doctors_cache()

@app.post("/api/chat")
async def chat(request: ChatRequest):
    session = get_or_create_session(request.session_id, request.phone_number)
    
    # Append user message
    session["messages"].append({"role": "user", "content": request.text})
    
    # Call LLM
    response_data = {"type": "text", "logs": []}
    
    # Simple retry loop for tool calling
    for _ in range(5):
        try:
            completion = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=session["messages"],
                tools=openai_tools,
                tool_choice="auto"
            )
            
            message = completion.choices[0].message
            session["messages"].append(message)
            
            if message.tool_calls:
                for tool_call in message.tool_calls:
                    # Execute tool
                    result = await execute_tool_call(tool_call, session)
                    response_data["logs"].append({
                        "tool": tool_call.function.name,
                        "args": tool_call.function.arguments,
                        "result": result
                    })
                    
                    # Append tool result to messages
                    session["messages"].append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps(result)
                    })
                # Loop continues to get the next LLM response after tool execution
            else:
                # We have a final text response
                response_data["content"] = message.content
                break
                
        except Exception as e:
            print(f"LLM Error: {e}")
            raise HTTPException(status_code=500, detail=str(e))
            
    return response_data

@app.get("/api/session/{session_id}")
async def get_session(session_id: str):
    if session_id not in sessions:
        get_or_create_session(session_id)
    
    # Filter messages to only show system, user, assistant (for the UI transcript)
    filtered = []
    for m in sessions[session_id]["messages"]:
        if isinstance(m, dict) and m["role"] in ["user", "assistant"] and m.get("content"):
            filtered.append({"role": m["role"], "content": m["content"]})
        elif hasattr(m, 'role') and m.role in ["user", "assistant"] and m.content:
            filtered.append({"role": m.role, "content": m.content})
            
    return {"messages": filtered, "language": sessions[session_id].get("language", "en-IN")}

# Mount static files for the frontend
os.makedirs("src/web_app", exist_ok=True)
app.mount("/", StaticFiles(directory="src/web_app", html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
