# Medical Appointment Booking - WhatsApp Workflows

This branch (`whatsapp_workflows`) contains specialized **WhatsApp Workflows** for the Medical Appointment Booking system, originally developed as `medisync_whatsapp_workflows` at Prognos Labs.

## Overview

While the core `medisync` application handles foundational scheduling, this branch extends the WhatsApp (text) agent with complex, multi-step workflows. It is designed to guide patients through intricate scheduling logic, intake forms, post-appointment follow-ups, and targeted medical inquiries directly within WhatsApp.

## Key Features

- **Advanced Conversational Flows:** Adds structured workflow support to the WhatsApp agent, ensuring patients are guided through necessary steps without getting stuck.
- **Appointment Lifecycle Management:** Automates workflows for not just booking, but also rescheduling, cancellations, and pre-appointment preparation checks.
- **Interactive Messaging:** Leverages WhatsApp's interactive capabilities (like buttons and lists) where applicable to streamline the user experience.
- **Integration with Core Services:** Works in tandem with the primary backend logic to check doctor schedules, book slots, and update patient records in real time.

## Getting Started

1. Ensure your `.env` file is configured with the necessary WhatsApp Business API credentials, database URLs, and LLM API keys.
2. Install the required dependencies (refer to `requirements.txt`).
3. Run the application to initialize the webhook listener for WhatsApp messages.
4. Interact with the bot via WhatsApp to test the automated workflow capabilities.

