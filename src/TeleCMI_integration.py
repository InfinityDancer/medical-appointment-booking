import asyncio
import os
import json
import time
import re as _re
from dotenv import load_dotenv

load_dotenv()

from piopiy.agent import Agent
from piopiy.voice_agent import VoiceAgent
from piopiy.services.sarvam.stt import SarvamSTTService
from piopiy.services.sarvam.tts import SarvamTTSService
from piopiy.services.openai.llm import OpenAILLMService
from piopiy.adapters.schemas.function_schema import FunctionSchema
from loguru import logger

from piopiy.observers.base_observer import BaseObserver, FramePushed
from piopiy.frames.frames import (
    MetricsFrame,
    UserStoppedSpeakingFrame,
    BotStartedSpeakingFrame,
    UserStartedSpeakingFrame,
    ErrorFrame,
    LLMMessagesAppendFrame,
)
from piopiy.metrics.metrics import LLMUsageMetricsData
from piopiy.audio.interruptions.min_words_interruption_strategy import MinWordsInterruptionStrategy

# Increase VAD audio frame timeout from 0.5s to 1.0s to tolerate brief audio gaps
import piopiy.transports.base_input as _base_input
_base_input.AUDIO_INPUT_TIMEOUT_SECS = 1.0

from src.utils.metrics import ConversationMetricsTracker
from datetime import datetime, timedelta
from src.utils.prompts import VOICE_AGENT_PROMPT
from src.services.medical_service import (
    get_doctor_availability,
    get_all_doctors,
    get_patient_details,
    book_appointment,
    get_appointments,
    initiate_cancel_appointment,
    confirm_cancel_appointment,
    initiate_reschedule_appointment,
    confirm_reschedule_appointment,
    handle_general_inquiry,
    fetch_clinic_info,
    create_ticket,
    fuzzy_match_doctor_name_with_score,
    warm_doctors_cache
)

# Minimum seconds between reconnection attempts
RECONNECT_COOLDOWN_SECS = 2.0
# Maximum consecutive reconnect failures before we stop trying
MAX_CONSECUTIVE_FAILURES = 3

class ReconnectingSarvamSTTService(SarvamSTTService):
    """SarvamSTTService that auto-reconnects on WebSocket failures."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._last_reconnect_ts: float = 0.0
        self._consecutive_failures: int = 0
        self._reconnecting: bool = False
        # Optional async callback: called with a Sarvam language code string (e.g. "hi-IN")
        # whenever a transcription is received. Set externally after construction.
        self.language_callback = None

    async def _handle_message(self, message):
        """Delegate to parent, then extract and broadcast the detected language."""
        await super()._handle_message(message)
        # After the parent has pushed the TranscriptionFrame, grab the language Sarvam detected.
        if message.type == "data" and message.data.language_code and self.language_callback:
            await self.language_callback(message.data.language_code)

    async def _try_reconnect(self) -> bool:
        """Attempt to reconnect to Sarvam.
        Returns True if reconnection succeeded, False otherwise.
        Respects a cooldown to avoid reconnect storms.
        """
        now = time.monotonic()
        elapsed = now - self._last_reconnect_ts

        if elapsed < RECONNECT_COOLDOWN_SECS:
            return False

        if self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            logger.error(
                f"Sarvam STT: giving up after {MAX_CONSECUTIVE_FAILURES} "
                "consecutive reconnect failures"
            )
            return False

        if self._reconnecting:
            return False

        self._reconnecting = True
        self._last_reconnect_ts = now

        try:
            logger.warning("Sarvam STT: WebSocket dropped - reconnecting…")
            await self._disconnect()
            await self._connect()

            if self._socket_client:
                logger.info("Sarvam STT: reconnected successfully")
                self._consecutive_failures = 0
                return True
            else:
                self._consecutive_failures += 1
                logger.error("Sarvam STT: reconnect returned no socket_client")
                return False

        except Exception as e:
            self._consecutive_failures += 1
            logger.error(f"Sarvam STT: reconnect failed — {e}")
            return False
        finally:
            self._reconnecting = False

    async def run_stt(self, audio: bytes):
        """Send audio to Sarvam, reconnecting once on failure."""
        if not self._socket_client:
            # Socket gone — try to bring it back
            if not await self._try_reconnect():
                yield None
                return

        try:
            # Normal path — delegate to parent
            async for frame in super().run_stt(audio):
                if isinstance(frame, ErrorFrame):
                    raise _SarvamSendError(str(frame.error))
                yield frame
            # Reset failure counter on success
            self._consecutive_failures = 0
            return

        except _SarvamSendError:
            # First failure — reconnect and retry once
            if await self._try_reconnect():
                try:
                    async for frame in super().run_stt(audio):
                        if isinstance(frame, ErrorFrame):
                            # Still failing after reconnect — log once, don't flood
                            logger.warning(
                                f"Sarvam STT: still failing after reconnect — {frame.error}"
                            )
                            yield None
                            return
                        yield frame
                    return
                except Exception:
                    pass

            # Could not recover — yield nothing (suppress error flood)
            yield None

class _SarvamSendError(Exception):
    """Internal sentinel to distinguish Sarvam send errors from other exceptions."""
    pass

import re as _re

class SanitizingSarvamTTSService(SarvamTTSService):
    """
    SarvamTTSService wrapper that scrubs LLM output before it reaches the
    Sarvam synthesis engine.

    Sarvam's bulbul TTS vocalises every character it receives, so stray
    markdown symbols, JSON punctuation, or formatting characters that leak
    through from the LLM produce audible gibberish at the end of utterances.
    This layer removes them reliably regardless of what the LLM outputs.
    """

    # Characters/patterns that produce audible noise in Sarvam TTS
    _MARKDOWN_RE = _re.compile(
        r'(\*{1,3}|_{1,3}|~{1,2}|`{1,3})'   # bold / italic / strikethrough / code
        r'|^#{1,6}\s*'                          # headings at line start
        r'|^\s*[-*•]\s+',                       # unordered list bullets at line start
        _re.MULTILINE,
    )
    # JSON structural characters that should never appear in spoken text
    _JSON_CHARS_RE = _re.compile(r'[{}\[\]]')
    # Sequences of non-alphanumeric, non-basic-punctuation chars (e.g. "::", "=>", "->")
    _SYMBOL_SEQ_RE = _re.compile(r'(?<!\d)[=\-<>|\\^]{2,}')
    # Trailing/leading whitespace per line, and multiple blank lines
    _EXTRA_WHITESPACE_RE = _re.compile(r'\n{3,}')

    @staticmethod
    def _sanitize(text: str) -> str:
        if not text:
            return text

        t = text

        # 1. Strip markdown formatting characters
        t = SanitizingSarvamTTSService._MARKDOWN_RE.sub('', t)

        # 2. Remove JSON structural brackets that LLM sometimes leaks
        t = SanitizingSarvamTTSService._JSON_CHARS_RE.sub('', t)

        # 3. Remove symbol sequences (arrows, pipes, etc.)
        t = SanitizingSarvamTTSService._SYMBOL_SEQ_RE.sub('', t)

        # 4. Collapse excess blank lines
        t = SanitizingSarvamTTSService._EXTRA_WHITESPACE_RE.sub('\n\n', t)

        # 5. Strip leading/trailing whitespace
        t = t.strip()

        if t != text:
            logger.debug(f"TTS sanitizer cleaned text | before={text!r:.80} | after={t!r:.80}")

        return t

    async def run_tts(self, text: str):
        """Sanitize text before forwarding to Sarvam synthesis."""
        clean = self._sanitize(text)
        async for frame in super().run_tts(clean):
            yield frame

def format_prompt(caller_phone: str = "") -> tuple[str, str]:
    """Return (formatted_prompt, clinic_name)."""
    print("Pre-fetching clinic data from Google Sheets")
    clinic_data = fetch_clinic_info()
    if clinic_data:
        print(f"Clinic data loaded: {clinic_data.get('ClinicName', 'Unknown')}")
    else:
        print("Failed to load clinic data")
        raise ValueError("Clinic data missing. Please ensure medisync-bot.json is present.")

    clinic_name = clinic_data.get("ClinicName", "Clinic")
    clinic_location = clinic_data.get("Address", "")
    clinic_hours = clinic_data.get("Hours", "")
    clinic_number = clinic_data.get("PhoneNumber", "")
    clinic_policy = clinic_data.get("CancellationPolicy", "")

    now = datetime.now()
    day_reference_lines = []
    # Relative-day aliases for the first three entries so the LLM never has
    # to do calendar arithmetic (which it gets wrong).
    relative_labels = {0: " (TODAY)", 1: " (TOMORROW)", 2: " (DAY AFTER TOMORROW)"}
    for i in range(14):
        d = now + timedelta(days=i)
        # Prevent 0-padded days (e.g. February 05) which confuses the LLM
        day_str = str(d.day)
        label = relative_labels.get(i, "")
        day_reference_lines.append(f"- {d.strftime('%A')}: {d.strftime('%b')} {day_str}{label}")
    day_reference_str = "\n".join(day_reference_lines)

    # Compute explicit last bookable date so LLM doesn't do its own date math
    last_bookable = now + timedelta(days=14)
    last_bookable_str = f"{last_bookable.strftime('%b')} {str(last_bookable.day)}"

    # Non-zero-padded current time
    current_time_str = f"{now.strftime('%A')}, {now.strftime('%b')} {now.day} {now.strftime('%I:%M %p')}"

    formatted_prompt = VOICE_AGENT_PROMPT.format(
        clinic_name=clinic_name,
        current_year=str(now.year),
        current_time=current_time_str,
        last_bookable_date=last_bookable_str,
        day_reference=day_reference_str,
        clinic_location=clinic_location,
        clinic_hours=clinic_hours,
        clinic_number=clinic_number,
        clinic_cancellation_policy=clinic_policy,
        caller_phone_number=caller_phone
    )
    
    print(f"Using voice agent prompt with clinic: {clinic_name}")
    return formatted_prompt, clinic_name

class TeleCMIMetricsObserver(BaseObserver):
    def __init__(self, tracker: ConversationMetricsTracker, task):
        super().__init__()
        self.tracker = tracker
        self.task = task
        self._frames_seen = set()
        self.last_user_stop_time = None
        self.inactivity_prompt_sent = False
        
        # Intent timeout tracking
        self.task._consecutive_no_intent_count = 0
        self.task._tool_called_this_turn = False

    async def on_push_frame(self, data: FramePushed):
        frame = data.frame
        
        from piopiy.frames.frames import TextFrame
        if isinstance(frame, TextFrame):
            print(f"DEBUG: TextFrame received: {frame.text}")
            
        if isinstance(frame, UserStoppedSpeakingFrame):
            self.last_user_stop_time = time.time()
            self.task._tool_called_this_turn = False
            return
            
        if isinstance(frame, BotStartedSpeakingFrame):
            if self.last_user_stop_time is not None:
                latency = time.time() - self.last_user_stop_time
                self.tracker.record_latency(latency)
                self.last_user_stop_time = None
            return

        # If user speaks, reset inactivity state
        if isinstance(frame, UserStartedSpeakingFrame):
            if self.inactivity_prompt_sent:
                print("User spoke after inactivity prompt. Resetting timeout to 30s.")
                self.inactivity_prompt_sent = False
                self.task._idle_timeout_secs = 30.0

        if not isinstance(frame, MetricsFrame):
            return

        if frame.id in self._frames_seen:
            return
        self._frames_seen.add(frame.id)

        for metrics_data in frame.data:
            if isinstance(metrics_data, LLMUsageMetricsData):
                usage = metrics_data.value
                self.tracker.prompt_tokens += usage.prompt_tokens
                self.tracker.completion_tokens += usage.completion_tokens
                self.tracker.total_tokens += usage.total_tokens
                if hasattr(usage, "cache_read_input_tokens") and usage.cache_read_input_tokens:
                    self.tracker.cache_read_tokens += usage.cache_read_input_tokens
                
                # Intent timeout logic
                if not getattr(self.task, "_tool_called_this_turn", False):
                    count = getattr(self.task, "_consecutive_no_intent_count", 0) + 1
                    setattr(self.task, "_consecutive_no_intent_count", count)
                    print(f"No intent detected this turn. Consecutive no-intent count: {count}")
                    
                    if count >= 5:
                        print("5 consecutive messages with no intent. Disconnecting the call.")
                        asyncio.create_task(self._disconnect_with_goodbye())
                else:
                    # Tool was called this turn (or previously in the same turn), reset the counter
                    setattr(self.task, "_consecutive_no_intent_count", 0)

    async def _disconnect_with_goodbye(self):
        from piopiy.frames.frames import TTSSpeakFrame, EndFrame
        await self.task.queue_frame(TTSSpeakFrame("I haven't detected a specific request, so I'll disconnect the call now. Goodbye!"))
        await asyncio.sleep(4)
        await self.task.queue_frame(EndFrame())

FUNCTION_MAP = {
    "get_doctor_availability": get_doctor_availability,
    "get_all_doctors": get_all_doctors,
    "get_patient_details": get_patient_details,
    "book_appointment": book_appointment,
    "get_appointments": get_appointments,
    "initiate_cancel_appointment": initiate_cancel_appointment,
    "confirm_cancel_appointment": confirm_cancel_appointment,
    "initiate_reschedule_appointment": initiate_reschedule_appointment,
    "confirm_reschedule_appointment": confirm_reschedule_appointment,
    "handle_general_inquiry": handle_general_inquiry,
    "create_ticket": create_ticket,
}

async def handle_function_call(params, tracker: ConversationMetricsTracker, voice_agent=None, caller_phone: str = ""):
    """Handle a piopiy function call via FunctionCallParams."""
    function_name = params.function_name
    arguments = dict(params.arguments)  # make mutable copy

    # Automatically inject the caller's phone number into arguments
    # so the LLM doesn't have to provide it.
    if function_name in [
        "get_patient_details", "get_appointments",
        "initiate_cancel_appointment", "confirm_cancel_appointment",
        "initiate_reschedule_appointment", "confirm_reschedule_appointment"
    ]:
        arguments["phone_number"] = caller_phone
        
    if function_name == "book_appointment":
        arguments["patient_phone"] = caller_phone

    print(f"Tool call: {function_name} | Args: {arguments}")

    if function_name not in FUNCTION_MAP:
        await params.result_callback({"error": f"Unknown function: {function_name}"})
        return

    func = FUNCTION_MAP[function_name]

    if function_name == "get_doctor_availability" and "doctor_name" in arguments:
        original_name = arguments["doctor_name"]
        matched_name, match_score = await asyncio.get_event_loop().run_in_executor(
            None, lambda: fuzzy_match_doctor_name_with_score(original_name)
        )
        if matched_name and matched_name != original_name:
            print(f"Fuzzy corrected doctor name: '{original_name}' -> '{matched_name}'")
            arguments["doctor_name"] = matched_name
        # Default time window to full day if LLM omitted optional fields
        arguments.setdefault("start_time", "00:00")
        arguments.setdefault("end_time", "23:59")

    loop = asyncio.get_event_loop()
    
    # Reset idle timeout monitor to avoid the agent disconnecting us while we wait for an API
    if voice_agent and voice_agent._task:
        if voice_agent._task._idle_monitor_task:
            voice_agent._task._idle_monitor_task.cancel()
            voice_agent._task._maybe_start_idle_task()
        
        # Mark that a tool was called this turn
        voice_agent._task._tool_called_this_turn = True

    result_str = await loop.run_in_executor(None, lambda: func(**arguments))

    try:
        result = json.loads(result_str) if isinstance(result_str, str) else result_str
        func_success = result.get("status") != "error"
    except Exception as e:
        print(f"Failed to parse result: {e}")
        result = {"status": "error", "message": "Failed to parse result"}
        func_success = False

    tracker.record_function_call(function_name, func_success)

    print(f"Tool result: {function_name} -> {json.dumps(result)[:200]}")
    await params.result_callback(result)

async def create_session(agent_id, call_id, from_number, to_number, metadata=None):
    print(f"Incoming call {call_id} from {from_number} to {to_number}")
    session_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{call_id}" if call_id else f"{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    tracker = ConversationMetricsTracker(session_id=session_id)

    # Load tool schemas
    with open("config/tools.json", "r") as f:
        tools = json.load(f)

    formatted_prompt, clinic_name = format_prompt(caller_phone=from_number)
    print(f"Set VOICE_AGENT_PROMPT as config prompt ({len(formatted_prompt)} chars)")

    greeting_text = f"Hello, this is {clinic_name}, how may I help you?"

    voice_agent = VoiceAgent(
        instructions=formatted_prompt,
        greeting=greeting_text,
        idle_timeout_secs=30.0,
        cancel_on_idle_timeout=False
    )

    for func_def in tools["functions"]:
        params = func_def.get("parameters", {})
        schema = FunctionSchema(
            name=func_def["name"],
            description=func_def.get("description", ""),
            properties=params.get("properties", {}),
            required=params.get("required", []),
        )
        voice_agent.add_tool(schema, lambda p: handle_function_call(p, tracker, voice_agent, caller_phone=from_number))
        print(f"Registered tool: {func_def['name']}")

    stt = ReconnectingSarvamSTTService(
        model="saaras:v3",
        api_key=os.getenv("SARVAM_API_KEY"),
    )

    llm = OpenAILLMService(
        api_key=os.getenv("OPENAI_API_KEY"),
        model="gpt-4o-mini",
    )

    tts = SanitizingSarvamTTSService(
        api_key=os.getenv("SARVAM_API_KEY"),
        model="bulbul:v3",
        voice_id="simran",
        # Available speakers for bulbul:v3 are: aditya, ritu, ashutosh, priya, neha, rahul,
        # pooja, rohan, simran, kavya, amit, dev, ishita, shreya, ratan, varun, manan, sumit,
        # roopa, kabir, aayan, shubh, advait, amelia, sophia, anand, tanya, tarun, sunny, mani,
        # gokul, vijay, shruti, suhani, mohit, kavitha, rehan, soham, rupali
        sample_rate=24000,
    )

    # --- Language detection with confirmation flow ---
    # Phase tracks the language negotiation state:
    #   "greeting"   – waiting for the caller's first utterance after the greeting
    #   "confirming" – agent asked the language-preference question, awaiting reply
    #   "active"     – language is locked in; mirror changes silently
    _lang_phase: list[str] = ["greeting"]
    _last_detected_lang: list[str | None] = [None]
    _offered_lang: list[str | None] = [None]  # language offered in the preference question

    # Languages supported by Sarvam TTS (bulbul). Assamese (as-IN) is detected
    # by STT but not synthesisable by TTS — fall back to English for those cases.
    _TTS_SUPPORTED_LANGS = {
        "bn-IN", "en-IN", "gu-IN", "hi-IN", "kn-IN",
        "ml-IN", "mr-IN", "od-IN", "pa-IN", "ta-IN", "te-IN",
    }
    _TTS_FALLBACK_LANG = "en-IN"

    # Human-readable names used in the LLM instruction
    _LANG_NAMES = {
        "bn-IN": "Bengali", "en-IN": "English", "gu-IN": "Gujarati",
        "hi-IN": "Hindi",   "kn-IN": "Kannada",  "ml-IN": "Malayalam",
        "mr-IN": "Marathi", "od-IN": "Odia",     "pa-IN": "Punjabi",
        "ta-IN": "Tamil",   "te-IN": "Telugu",
    }

    async def _switch_language(lang_code: str):
        """Actually switch TTS + instruct LLM to use the given language."""
        if lang_code not in _TTS_SUPPORTED_LANGS:
            logger.warning(
                f"Detected language '{lang_code}' is not supported by Sarvam TTS — "
                f"falling back to '{_TTS_FALLBACK_LANG}'"
            )
            lang_code = _TTS_FALLBACK_LANG
        if lang_code == _last_detected_lang[0]:
            return  # no change — skip the config round-trip
        _last_detected_lang[0] = lang_code

        # 1. Update TTS language so Sarvam speaks in the right language
        tts._settings["target_language_code"] = lang_code
        logger.info(f"Language confirmed: {lang_code} — updating TTS language")
        if tts._websocket:
            await tts._send_config()

        # 2. Instruct the LLM to reply in the same language.
        lang_name = _LANG_NAMES.get(lang_code, lang_code)
        lang_instruction = (
            f"IMPORTANT: The caller has confirmed they want to speak in {lang_name}. "
            f"You MUST respond ONLY in {lang_name} for the remainder of this call. "
            "Do NOT switch languages UNLESS the caller explicitly asks you to (e.g. 'please reply in English')."
        )
        await voice_agent._task.queue_frame(
            LLMMessagesAppendFrame(
                messages=[{"role": "system", "content": lang_instruction}],
                run_llm=False,
            )
        )

    async def _on_language_detected(lang_code: str):
        """Handle language detection with a greeting → confirm → active state machine."""
        if lang_code not in _TTS_SUPPORTED_LANGS:
            lang_code = _TTS_FALLBACK_LANG

        phase = _lang_phase[0]

        if phase == "greeting":
            # First utterance after the agent's greeting
            if lang_code == "en-IN":
                # Caller is speaking English — no need to ask, proceed normally
                _last_detected_lang[0] = lang_code
                _lang_phase[0] = "active"
                logger.info("Caller speaking English — skipping language preference question")
                return

            # Non-English detected — ask the caller's preference (in English)
            lang_name = _LANG_NAMES.get(lang_code, lang_code)
            _offered_lang[0] = lang_code
            _lang_phase[0] = "confirming"
            logger.info(f"Detected {lang_name} — asking caller for language preference")

            ask_pref = (
                f"The caller just spoke in {lang_name}. "
                f"You MUST immediately ask the caller (in English): "
                f"'Are you comfortable talking in {lang_name}?' "
                "Wait for their answer before proceeding with any other topic. "
                "Do NOT answer their original question yet. "
                "If they say yes, you MUST call the `switch_language` tool with the "
                f"language_code '{lang_code}'. "
                "If they say no, ask them which language they prefer, and call the "
                "`switch_language` tool once they specify it."
            )
            await voice_agent._task.queue_frame(
                LLMMessagesAppendFrame(
                    messages=[{"role": "system", "content": ask_pref}],
                    run_llm=False,
                )
            )
            return

        if phase == "confirming":
            # The caller is responding to the confirmation question. 
            # We no longer switch automatically based on the STT's short utterance language detection 
            # We wait for the LLM to process the user's intent and invoke the `switch_language` tool.
            return

        # phase == "active" — language is locked in. Do not auto-switch.
        # The LLM will handle explicit user requests to change language via the `switch_language` tool.
        pass

    stt.language_callback = _on_language_detected

    # Register the built-in switch_language tool
    switch_lang_schema = FunctionSchema(
        name="switch_language",
        description=(
            "Switch the language of the voice agent. Call this when the user confirms their preferred language "
            "during the initial language check, or if the user explicitly requests to speak in a different language later. "
            "Supported language codes: hi-IN, bn-IN, en-IN, gu-IN, kn-IN, ml-IN, mr-IN, od-IN, pa-IN, ta-IN, te-IN."
        ),
        properties={
            "language_code": {
                "type": "string",
                "description": "The exact language code to switch to, e.g., 'hi-IN'"
            }
        },
        required=["language_code"]
    )

    async def _handle_switch_lang(p):
        lang_code = p.arguments.get("language_code", "en-IN")
        print(f"Tool call: switch_language | Args: {p.arguments}")
        
        # Mark that a tool was called this turn
        if voice_agent and voice_agent._task:
            voice_agent._task._tool_called_this_turn = True
        
        _lang_phase[0] = "active"
        await _switch_language(lang_code)
        
        await p.result_callback({
            "status": "success",
            "message": f"Successfully switched TTS and agent to {lang_code}. You must now reply strictly in this language."
        })

    voice_agent.add_tool(switch_lang_schema, _handle_switch_lang)
    print("Registered tool: switch_language")

    await voice_agent.Action(
        stt=stt, llm=llm, tts=tts, vad=True,
        allow_interruptions=True,
        interruption_strategy=MinWordsInterruptionStrategy(min_words=3)
    )
    
    await voice_agent._build_task()
    
    def _make_handler(t, va):
        async def _handler(p):
            await handle_function_call(p, t, va, caller_phone=from_number)
        return _handler

    for func_def in tools["functions"]:
        llm.register_function(
            func_def["name"],
            _make_handler(tracker, voice_agent),
            cancel_on_interruption=False,
        )

    llm.register_function(
        "switch_language",
        _handle_switch_lang,
        cancel_on_interruption=False,
    )

    observer = TeleCMIMetricsObserver(tracker, voice_agent._task)
    voice_agent._task.add_observer(observer)
    
    @voice_agent._task.event_handler("on_idle_timeout")
    async def on_idle_timeout(task):
        if not observer.inactivity_prompt_sent:
            print("30s inactivity timeout reached. Sending prompt and reducing timeout to 10s.")
            observer.inactivity_prompt_sent = True
            task._idle_timeout_secs = 10.0
            from piopiy.frames.frames import TTSSpeakFrame
            await task.queue_frame(TTSSpeakFrame("Are you still there? I haven't heard from you in a while."))
            # Manually restart the idle monitor with the new 10s timeout
            if task._idle_monitor_task:
                task._idle_monitor_task.cancel()
            task._maybe_start_idle_task()
        else:
            print("10s inactivity timeout reached after prompt. Disconnecting call.")
            from piopiy.frames.frames import TTSSpeakFrame, EndFrame
            await task.queue_frame(TTSSpeakFrame("I haven't heard from you, so I'll disconnect the call now. Goodbye!"))
            await asyncio.sleep(3) # Wait for TTS to finish speaking
            await task.queue_frame(EndFrame())

    try:
        await voice_agent._runner.run(voice_agent._task)
    finally:
        tracker.save()

async def main():
    # start doctor cache so the first call doesn't have a cold lookup
    warm_doctors_cache()

    agent = Agent(
        agent_id=os.getenv("AGENT_ID"),
        agent_token=os.getenv("AGENT_TOKEN"),
        create_session=create_session,
        debug=True
    )
    await agent.connect()

if __name__ == "__main__":
    asyncio.run(main())