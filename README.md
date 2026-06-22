# Medical Appointment Booking - Voice Agent

This branch (`voice-agent`) contains the **Live Telephonic Voice Agent** integration for the Medical Appointment Booking system, originally developed as `medisync-TELEcmi-integration` at Prognos Labs.

## Overview

The voice agent is designed to handle medical appointment bookings, general inquiries, and patient assistance over live voice calls. It integrates advanced speech-to-text (STT) and text-to-speech (TTS) pipelines with conversational AI to provide a seamless, human-like interaction for patients.

## Key Features

- **Telephony Integration:** Originally integrated with **TeleCMI** to handle live incoming and outgoing calls.
- **Speech-to-Text (STT):** Utilizes **Sarvam STT** (`reconnecting_sarvam_stt.py`) for accurate, low-latency transcription of patient audio.
- **Conversational AI:** Powered by LLMs (e.g., GPT-4o mini, Saaras) to process natural language and execute medical service business logic.
- **Text-to-Speech (TTS):** Uses **Murf TTS** (`murf_tts.py` / Bulbul) to generate natural, high-quality voice responses.
- **Medical Service Logic:** Core business logic managed via `medical_service.py` for tasks like booking, rescheduling, and fetching patient details.
- **Browser-based Demo:** Includes a web demo (`web_demo.py`) utilizing FastAPI and WebSockets to test the audio pipeline without requiring an active telephony connection.
- **Metrics & Logging:** Tracks conversation metrics and logs interactions via Redis (`redis_client.py`) and JSON-based session logging (`TCMI_conversation_metrics`).

## Directory Structure Overview

- `/src/` - Core application source code.
  - `TeleCMI_integration.py` - Main integration script for TeleCMI.
  - `web_demo.py` - Browser-based interface for testing the voice agent.
  - `/services/` - Business logic including `medical_service.py`, `murf_tts.py`, and `reconnecting_sarvam_stt.py`.
  - `/utils/` - Utility scripts including prompts, metrics, and Redis clients.
- `/config/` - Configuration files like `tools.json` for agent capabilities.
- `/TCMI_conversation_metrics/` - Session logs and conversation metrics data.

## Getting Started

1. Ensure all environment variables are set in your `.env` file (API keys for STT, TTS, LLM, and Redis credentials).
2. Install dependencies (refer to requirements).
3. To test locally without a phone number, run the web demo:
   ```bash
   python src/web_demo.py
   ```
4. Access the web interface in your browser to interact with the voice agent.