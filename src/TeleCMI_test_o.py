import asyncio
import os

from piopiy.agent import Agent
from piopiy.voice_agent import VoiceAgent
from piopiy.services.deepgram.stt import DeepgramSTTService
from piopiy.services.openai.llm import OpenAILLMService
from piopiy.services.deepgram.tts import DeepgramTTSService

from dotenv import load_dotenv
load_dotenv()

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
    tts = DeepgramTTSService(api_key=os.getenv("DEEPGRAM_API_KEY"))

    await voice_agent.Action(stt=stt, llm=llm, tts=tts)
    await voice_agent.start()


async def main():
    agent = Agent(
        create_session=create_session,
        agent_id=os.getenv("AGENT_ID"),
        agent_token=os.getenv("AGENT_TOKEN"),
        debug=True # Enable debug logging (optional, default: False)
    )
    await agent.connect()


if __name__ == "__main__":
    asyncio.run(main())