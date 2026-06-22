import asyncio
import inspect
import json
import logging
import os
import signal
from contextvars import ContextVar
from typing import Awaitable, Callable, Dict, Optional

import socketio
from dotenv import load_dotenv
load_dotenv()

from piopiy.voice_agent import VoiceAgent
from piopiy.services.deepgram.stt import DeepgramSTTService
from piopiy.services.openai.llm import OpenAILLMService
from piopiy.services.cartesia.tts import CartesiaTTSService

SIGNALING_URL = "https://signaling.piopiy.com"

URL_CTX: ContextVar[str] = ContextVar("telecmi_url")
TOKEN_CTX: ContextVar[str] = ContextVar("telecmi_token")
ROOM_CTX: ContextVar[str] = ContextVar("telecmi_room")

class Agent:
    def __init__(
        self,
        agent_id: str,
        agent_token: str,
        create_session: Callable[..., Awaitable[None]],
        signaling_url: Optional[str] = None,
        debug: bool = False,
    ):
        self.agent_id = agent_id
        self.agent_token = agent_token
        self.create_session = create_session
        self.signaling_url = signaling_url or SIGNALING_URL
        self.debug = debug

        level = logging.DEBUG if debug else logging.INFO
        logging.basicConfig(level=level)
        self._logger = logging.getLogger(__name__)

        self.sio = socketio.AsyncClient(logger=False, engineio_logger=False)
        self.active_sessions: Dict[str, asyncio.Task] = {}

        self._setup_events()

    def _setup_events(self):
        @self.sio.event
        async def connect():
            self._logger.info("Connected to signaling as agent %s", self.agent_id)

        @self.sio.on("join_room")
        async def handle_join_session(invite: dict):
            room = invite.get("room_name")
            token = invite.get("token")
            url = invite.get("url") or self.signaling_url

            if not room or not token:
                self._logger.warning("join_room missing room_name or token: %s", invite)
                return

            raw_meta = invite.get("metadata")
            parsed_meta = None
            if raw_meta:
                try:
                    if isinstance(raw_meta, str):
                        if r"\}" in raw_meta:
                            raw_meta = raw_meta.replace(r"\}", "}")
                        parsed_meta = json.loads(raw_meta)
                        if isinstance(parsed_meta, str):
                            parsed_meta = json.loads(parsed_meta)
                    else:
                        parsed_meta = raw_meta
                except Exception as exc:
                    self._logger.warning("Failed to parse metadata: %s", exc)

            async def session_runner():
                tok_url = URL_CTX.set(url)
                tok_token = TOKEN_CTX.set(token)
                tok_room = ROOM_CTX.set(room)
                try:
                    sig = inspect.signature(self.create_session)
                    kwargs = {}
                    if "call_id" in sig.parameters:
                        kwargs["call_id"] = invite.get("call_id")
                    if "agent_id" in sig.parameters:
                        kwargs["agent_id"] = invite.get("agent_id")
                    if "from_number" in sig.parameters:
                        kwargs["from_number"] = invite.get("from_number")
                    if "to_number" in sig.parameters:
                        kwargs["to_number"] = invite.get("to_number")
                    if "metadata" in sig.parameters:
                        kwargs["metadata"] = parsed_meta
                    await self.create_session(**kwargs)
                finally:
                    ROOM_CTX.reset(tok_room)
                    TOKEN_CTX.reset(tok_token)
                    URL_CTX.reset(tok_url)

            task = asyncio.create_task(session_runner(), name=f"session:{room}")
            self.active_sessions[room] = task
            task.add_done_callback(lambda _: self.active_sessions.pop(room, None))

        @self.sio.on("cancel_room")
        async def handle_cancel_session(data: dict):
            room = data.get("room_name")
            task = self.active_sessions.pop(room, None)
            if task and not task.done():
                task.cancel()

    async def connect(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(self.shutdown()))

        await self.sio.connect(
            self.signaling_url,
            auth={"agent_id": self.agent_id, "token": self.agent_token},
        )
        try:
            await self.sio.wait()
        except asyncio.CancelledError:
            pass

    async def shutdown(self) -> None:
        self._logger.info("Shutting down agent...")
        try:
            await self.sio.disconnect()
        except Exception:
            pass
        tasks = list(self.active_sessions.values())
        self.active_sessions.clear()
        for t in tasks:
            if not t.done():
                t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._logger.info("Agent shutdown complete.")


async def create_session(agent_id, call_id, from_number, to_number, metadata=None):
    print(f"Incoming call {call_id} from {from_number} to {to_number}")
    if metadata:
        print(f"Call Metadata: {metadata}")

    voice_agent = VoiceAgent(
        instructions="You are an advanced voice AI.",
        greeting="Hello! How can I help you today?",
    )

    stt = DeepgramSTTService(api_key=os.getenv("DEEPGRAM_API_KEY"))
    llm = OpenAILLMService(api_key=os.getenv("OPENAI_API_KEY"))
    tts = CartesiaTTSService(api_key=os.getenv("CARTESIA_API_KEY"))

    await voice_agent.Action(stt=stt, llm=llm, tts=tts)
    await voice_agent.start()


async def main():
    agent = Agent(
        agent_id=os.getenv("AGENT_ID"),
        agent_token=os.getenv("AGENT_TOKEN"),
        create_session=create_session,
        debug=True,
    )
    await agent.connect()


if __name__ == "__main__":
    asyncio.run(main())
