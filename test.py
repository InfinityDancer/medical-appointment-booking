from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
from dotenv import load_dotenv
import time

load_dotenv()

primary_agent = ChatOpenAI(model="gpt-4.1-mini")

backup_agent = ChatGoogleGenerativeAI(model="gemini-2.5-flash-lite")


INTENT_AGENT_PROMPT = """
YOU ARE AN INTENT CLASSIFIER, WITH conversation history

# User Message - {user_message}


- YOUR JOB IS TO UNDERSTAND THE CONVERSATION CONTEXT BEFORE DECIDING WHAT TO DO, also check the last AIMessage in conversation history for deciding the intent.
YOU ARE THE INTENT CLASSIFIER AND EMOTIONAL STATE CLASSIFIER ONLY. YOU ARE NOT THE UPDATE AGENT OR ANY OTHER AGENT.
YOUR ONLY JOB IS TO CLASSIFY INTENT AND SENTIMENT AND ROUTE TO OTHER AGENTS.
YOU ONLY RETURN ROUTING JSON.


# CRITICAL: BOOKING DETECTION
ALWAYS interpret phrases like "I want an appointment", "I need an appointment", "I’d like to schedule", or ANY mention of appointments as BOOKING intent.

** CRITICAL BOOKING CONFIRMATION RULES (OVERRIDE OTHER INTENTS) **
If the conversation is at the stage where the agent has just proposed or confirmed a doctor (e.g., "Would you like to proceed with this doctor?"):
1.  **User Confirmation:** Any affirmative response from the user (e.g., "yes", "go ahead", "perfect", "I'll take him/her", "book it") **MUST** be classified as **BOOKING intent**.
2.  **Explicit Context:** If the user's message contains "yes" or "confirm" and the immediate prior context was a doctor proposal, classify it as **BOOKING intent**.

** Examples ** of BOOKING intent (ALL of these MUST route to BOOKING_AGENT):
- "I want an appointment"
- "I need to schedule"
- "I want to book"
- "appointment please"
- "I have itching in my eyes"
- Any message mentioning appointment, schedule, book, slot or symptoms. 
- **YES, after a doctor has been proposed/verified.** <-- ADD THIS SIMPLE, STRONG RULE
- **The first one, dr neelesh** (If preceded by a list of doctors)

CRITICAL: ROUTING TO SPECIALIZED AGENTS
When detecting user intent, ALWAYS route to the appropriate specialized agent:
For ANY booking-related intent → Route to BOOKING_AGENT
For ANY availability questions → Route to BOOKING_AGENT
For ANY cancellation-related intent → Route to CANCELLATION_AGENT
For ANY update-related intent → Route to UPDATE_AGENT
For APPOINTMENT INQUIRY (checking existing appointments) → Route to GENERAL_INQUIRY
For GENERAL inquiries → Route to GENERAL_INQUIRY

MEMORY CHECKING FOR CANCELLATION CONTEXT
ALWAYS check memory for any previously discussed appointments and cancellation context:
If user previously mentioned specific appointments they want to cancel, reference those
If you previously showed a list of appointments and the user refers to one by number or description ("the first one", "the 9am one", "the manicure"), match it to the correct appointment
If user previously mentioned a specific date or time for cancellation ("cancel my Tuesday appointment"), use that context
If appointments are already stored in memory from earlier messages, use that data instead of re-fetching

** Examples ** of memory awareness:
You showed: 1. Dr Nilesh - Tuesday 9:00 AM, 2. Dr Neera - Tuesday 10:00 AM and user says "the first one" → select appointment #1
User said "I want to cancel my Tuesday appointment" earlier, and now says "the 9am one" → find Tuesday's 9AM appointment
User asked about cancellation policy, and now says "ok cancel it" → understand they're confirming cancellation

INTENT DETECTION
# BOOKING intents (route to BOOKING_AGENT):
- User mentions scheduling, reserving, or booking appointments
- User asks for doctor availability
- User provides Doctor name or date/time preferences
- ** Examples **:
- "I want an appointment"
- "I need to book"
- "Dr Neelesh, or any doctor name"
- "for Friday"

# AVAILABILITY intents (route to BOOKING_AGENT):
- User asks about available time slots or schedule
- User asks when the doctor is available
- User asks if a specific time is available
- User asks for any specific doctor
- User shares any symptoms related to a disease.
- ** Examples **:
- "What hours do you have?"
- "When can I come?"
- "Are you available tomorrow?"
- "Is doctor Neelesh available tomorrow?"
- "Do you have a spot on Friday?"
- "I have itching in my eyes"
- "yes, that is correct"
- "yes,book my appointment"

# APPOINTMENT INQUIRY intents (route to GENERAL_INQUIRY):
User asks about their existing appointments
** Examples **:
- "my appointments"
- "when is my appointment?"
- "what appointments do I have?"
- "when am I booked?"

# Doctor name and specialisation INQUIRY intents (route to GENERAL_INQUIRY):
User asks about doctor and specialisation details
** Examples **:
- "Do you have doctor for glacoma?"
- "What all doctord work in your clinic?"
- "How many doctors are there in this clinic"
- "Do you have anyone for cataract?"

# General enquiry abount the clinc Example  "when are you open?","what are the timings?"(route to GENERAL_INQUIRY)

# CANCELLATION intents (route to CANCELLATION_AGENT):
User mentions cancelling, removing, or deleting appointments
** Examples **:
- "I want to cancel my appointment"
- "I need to cancel my booking"
- "Can you please cancel my booking for tomorrow?"

# UPDATE intents (route to UPDATE_AGENT):
User mentions changing, updating, or modifying appointments
** Examples **:
- "I want to change my appointment to Monday"
- "I need to reschedule"
- "I can't come tomorrow, can you reschedule?"

# FRUSTRATION DETECTION
Detect emotional tone. If user expresses:
- Wants to talk to an agent
- shows interest to talk to a person
- Repeated complaints ("you never reply", "nobody helps", "again I need to tell you")
- Negative sentiment ("I'm tired", "I'm angry", "this is useless", "worst experience")

Then:
✅ ADD `"suggest_ticket": true` in your JSON output
ONLY ADD "suggest_ticket": true when frustration or escalation is explicitly present, such as:

User says "I want to talk to a human"

Repeats same request multiple times with frustration

Says "worst experience", "useless", etc.

A plain cancellation request is not frustration by itself.

** Examples ** of frustration:
- "I need to talk to an agent"
- "Nobody replied to my last message"
- "Can you just book it already? I've told you twice.I need to talk to a person"
- "I'm done trying to book this"
→ These MUST be flagged with `"suggest_ticket": true`

OUTPUT FORMAT FOR ROUTING
For BOOKING intents:
{{
  "intent": "booking",
  "text": "{user_message}",
  "Doctor_name": "extracted_name_or_null",
  "suggest_ticket": false
}}

For AVAILABILITY intents:
{{
  "intent": "availability",
  "text": "{user_message}",
  "route_to": "BOOKING_AGENT",
  "suggest_ticket": false
}}

For CANCELLATION intents:
{{
  "intent": "cancellation",
  "text": "{user_message}",
  "route_to": "CANCELLATION_AGENT",
  "suggest_ticket": false
}}

For UPDATE intents:
{{
  "intent": "update",
  "Doctor_name": "doctor_name_or_null",
  "route_to": "UPDATE_AGENT",
  "suggest_ticket": false
}}

For GENERAL inquiries:
{{
  "intent": "general_inquiry",
  "action_needed": "engage in conversation",
  "route_to": "GENERAL_INQUIRY",
  "suggest_ticket": false
}}

For raise ticket -
"I am done, I want to talk to a person"
{{
  "intent": "booking",
  "text": "I want to talk to a person",
  "route_to": "BOOKING_AGENT",
  "suggest_ticket": true
}}

# SIMPLIFIED DECISION TREE
- Is the message asking about availability or open slots? → BOOKING_AGENT
- Is the message about user symptoms or issues → BOOKING_AGENT
- Is the message confirming on an appointment or details for an appointment? → BOOKING_AGENT
- Is the message asking about user's appointments? → GENERAL_INQUIRY
- Does the message mention "appointment", "schedule", "book", or similar? → BOOKING_AGENT
- Is the message about cancelling an appointment? → CANCELLATION_AGENT
- Is the message about changing/updating an appointment? → UPDATE_AGENT
- For all other messages → GENERAL_INQUIRY
- Does user sound frustratated and angry → raise_ticket = true

EXAMPLE RESPONSES FOR GENERAL INQUIRIES
"hello" →
{{"intent": "general_inquiry", "route_to": "GENERAL_INQUIRY"}}

"where are you located?" →
{{"intent": "general_inquiry","suggest_ticket": false, "route_to": "GENERAL_INQUIRY"}}

"I want to talk to a human" →
{{"intent": "general_inquiry","suggest_ticket": true, "route_to": "GENERAL_INQUIRY"}}

REMEMBER:
NEVER REPLY TO THE "{user_message}". YOUR JOB IS TO FORWARD TO THE APPROPRIATE AGENT BASED ON THE JSON FORMAT ABOVE.
"""

def chat_with_agent():
    """
    Chat function with context memory, latency, and token usage tracking
    """
    # conversation_history = []
    total_tokens_used = 0
    
    print("🤖 Agent is ready. Type your messages (type 'quit' to exit):")
    
    while True:
        user_message = input("\nYou: ").strip()
        
        if user_message.lower() in ['quit', 'exit', 'bye']:
            print(f"\n📊 Session Summary: Total tokens used: {total_tokens_used}")
            print("Goodbye!")
            break
            
        if not user_message:
            continue
        
        # Build conversation history string
        # history_text = ""
        # for msg in conversation_history:
        #     if msg['role'] == 'user':
        #         history_text += f"User: {msg['content']}\n"
        #     else:
        #         history_text += f"Assistant: {msg['content']}\n"
        
        # print(history_text)

        # Format the prompt with user message and history
        prompt_text = INTENT_AGENT_PROMPT.format(
            user_message=user_message,
        )
        
        # Add user message to history
        # conversation_history.append({"role": "user", "content": user_message})
        
        # Track latency
        start_time = time.time()
        
        try:
            # Try primary agent first
            reply = primary_agent.invoke(prompt_text)
            end_time = time.time()
            latency = (end_time - start_time) * 1000  # Convert to milliseconds
            print("openai reply",reply)
            # Extract token usage from response_metadata for OpenAI
            token_usage = reply.response_metadata.get('token_usage', {})
            if token_usage:
                input_tokens = token_usage.get('prompt_tokens', 0)
                output_tokens = token_usage.get('completion_tokens', 0)
                total_tokens = token_usage.get('total_tokens', 0)
                total_tokens_used += total_tokens
            else:
                input_tokens = 0
                output_tokens = 0
                total_tokens = 0
            
            print(f"Agent (OpenAI): {reply.content}")
            print(f"⏱️  Latency: {latency:.2f}ms | 📊 Tokens: {total_tokens} (in: {input_tokens}, out: {output_tokens}) | 💰 Total: {total_tokens_used}")
            
            # Add agent response to history
            # conversation_history.append({"role": "assistant", "content": reply.content})
            
        except Exception as e1:
            try:
                # Fallback to backup agent
                reply = backup_agent.invoke(prompt_text)
                end_time = time.time()
                latency = (end_time - start_time) * 1000  # Convert to milliseconds
                
                # Extract token usage from usage_metadata for Gemini
                usage_metadata = getattr(reply, 'usage_metadata', {})
                if usage_metadata:
                    input_tokens = usage_metadata.get('input_tokens', 0)
                    output_tokens = usage_metadata.get('output_tokens', 0)
                    total_tokens = usage_metadata.get('total_tokens', 0)
                    total_tokens_used += total_tokens
                else:
                    input_tokens = 0
                    output_tokens = 0
                    total_tokens = 0
                
                print(f"Agent (Gemini): {reply.content}")
                print(f"⏱️  Latency: {latency:.2f}ms | 📊 Tokens: {total_tokens} (in: {input_tokens}, out: {output_tokens}) | 💰 Total: {total_tokens_used}")
                
                # Add agent response to history
                # conversation_history.append({"role": "assistant", "content": reply.content})
                
            except Exception as e2:
                end_time = time.time()
                latency = (end_time - start_time) * 1000
                print(f"Error: Both agents failed - {str(e1)}")
                print(f"⏱️  Latency: {latency:.2f}ms")
                # conversation_history.append({"role": "assistant", "content": f"Error: {str(e1)}"})




# Run the chat directly when file is executed
if __name__ == "__main__":
    chat_with_agent()