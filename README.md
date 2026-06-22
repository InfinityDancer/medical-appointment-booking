# Medical Appointment Booking - Agentic RAG (WhatsApp)

This branch (`Agentic-RAG`) contains the **Agentic Retrieval-Augmented Generation (RAG)** feature for the Medical Appointment Booking system, originally developed as `medisync_ARAG` at Prognos Labs.

## Overview

The Agentic RAG integration enhances the core WhatsApp text agent by combining **Retrieval-Augmented Generation (RAG)** with autonomous agentic workflows. This allows the conversational agent to effectively query medical knowledge bases, clinical FAQs, and facility information, while autonomously taking actions like scheduling, rescheduling, or canceling appointments.

## Key Features

- **WhatsApp Integration:** Operates seamlessly over WhatsApp to assist patients with their inquiries and bookings.
- **Agentic Workflows:** Employs autonomous agent frameworks to evaluate user intent, deciding when to retrieve information and when to execute booking tools.
- **Retrieval-Augmented Generation (RAG):** Allows the agent to pull from external knowledge sources (like clinic policies, preparation instructions for procedures, or doctor specialties) to answer patient questions accurately before or during the booking process.
- **Stateful Conversations:** Maintains the context of the interaction, meaning the agent can remember retrieved information while navigating the complex logic of checking doctor availability and securing a time slot.

## Getting Started

1. Set up the `.env` file with the required environment variables (WhatsApp API credentials, Database URLs, LLM API keys, and Vector Database credentials).
2. Install the necessary dependencies (refer to `requirements.txt`).
3. Run the application to start the webhook listener for WhatsApp messages.
4. Interact with the agent through the configured WhatsApp business number.