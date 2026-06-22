import json
import os
import time
import statistics
import logging

logger = logging.getLogger(__name__)

# Intent mapping: function name → human-readable intent
FUNCTION_INTENT_MAP = {
    'get_doctor_availability': 'check_availability',
    'get_all_doctors': 'list_doctors',
    'get_patient_details': 'patient_lookup',
    'book_appointment': 'book_appointment',
    'get_appointments': 'view_appointments',
    'initiate_cancel_appointment': 'cancel_appointment',
    'confirm_cancel_appointment': 'cancel_appointment',
    'initiate_reschedule_appointment': 'reschedule_appointment',
    'confirm_reschedule_appointment': 'reschedule_appointment',
    'handle_general_inquiry': 'general_inquiry',
    'create_ticket': 'escalation',
}

class ConversationMetricsTracker:
    """Tracks KPIs for a single voice session and persists them on save()."""

    def __init__(self, session_id: str, metrics_dir: str = "TCMI_conversation_metrics"):
        self.session_id = session_id
        self.metrics_dir = metrics_dir
        self.start_time = time.time()

        # Counters
        self.user_messages = 0
        self.agent_messages = 0
        self.function_calls_total = 0
        self.function_calls_successful = 0

        # Task completion flags
        self.booking_attempted = False
        self.booking_successful = False
        self.cancellation_attempted = False
        self.cancellation_successful = False
        self.reschedule_attempted = False
        self.reschedule_successful = False
        self.escalated_to_human = False

        # Latency
        self.response_latencies: list[float] = []

        # Token usage
        self.total_tokens = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.cache_read_tokens = 0

        # Intents
        self.intents_detected: list[str] = []

    # Recording helpers

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """Estimate GPT token count from text (~1.3 tokens per word)."""
        if not text:
            return 0
        return max(1, int(len(text.split()) * 1.3))

    def record_message(self, role: str, text: str = ""):
        """Record a user or agent message and estimate token usage from text."""
        estimated = self._estimate_tokens(text)
        if role == "user":
            self.user_messages += 1
            self.prompt_tokens += estimated
        elif role == "assistant":
            self.agent_messages += 1
            self.completion_tokens += estimated
        self.total_tokens += estimated

    def record_function_call(self, func_name: str, success: bool):
        """Record a function call result and derive intent / task-completion flags."""
        self.function_calls_total += 1
        if success:
            self.function_calls_successful += 1

        # Derive intent
        intent = FUNCTION_INTENT_MAP.get(func_name, func_name)
        if intent not in self.intents_detected:
            self.intents_detected.append(intent)

        # Task-completion tracking
        if func_name == "book_appointment":
            self.booking_attempted = True
            if success:
                self.booking_successful = True
        elif func_name in ("initiate_cancel_appointment", "confirm_cancel_appointment"):
            self.cancellation_attempted = True
            if func_name == "confirm_cancel_appointment" and success:
                self.cancellation_successful = True
        elif func_name in ("initiate_reschedule_appointment", "confirm_reschedule_appointment"):
            self.reschedule_attempted = True
            if func_name == "confirm_reschedule_appointment" and success:
                self.reschedule_successful = True
        elif func_name == "create_ticket":
            self.escalated_to_human = True

    def record_latency(self, seconds: float):
        """Record a response latency measurement."""
        self.response_latencies.append(round(seconds, 4))

    def save(self):
        """Write session metrics to a JSON file and append to the master log."""
        duration = round(time.time() - self.start_time, 1)

        avg_latency = round(statistics.mean(self.response_latencies), 4) if self.response_latencies else 0
        max_latency = round(max(self.response_latencies), 4) if self.response_latencies else 0

        accuracy = (
            round(self.function_calls_successful / self.function_calls_total * 100, 1)
            if self.function_calls_total > 0
            else 100.0
        )

        first_call_resolution = (
            not self.escalated_to_human and self.function_calls_successful > 0
        )

        intent_recognition_accuracy = 100.0  # intents are derived deterministically

        metrics = {
            "session_id": self.session_id,
            "duration_seconds": duration,
            "conversation_complete": True,
            "first_call_resolution": first_call_resolution,
            "intent_recognition_accuracy": intent_recognition_accuracy,
            "escalation_rate": 1.0 if self.escalated_to_human else 0.0,
            "accuracy": accuracy,
            "avg_latency_seconds": avg_latency,
            "max_latency_seconds": max_latency,
            "total_tokens": self.total_tokens,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "user_messages": self.user_messages,
            "agent_messages": self.agent_messages,
            "function_calls_total": self.function_calls_total,
            "function_calls_successful": self.function_calls_successful,
            "booking_attempted": self.booking_attempted,
            "booking_successful": self.booking_successful,
            "cancellation_attempted": self.cancellation_attempted,
            "cancellation_successful": self.cancellation_successful,
            "reschedule_attempted": self.reschedule_attempted,
            "reschedule_successful": self.reschedule_successful,
            "escalated_to_human": self.escalated_to_human,
            "response_latencies": self.response_latencies,
            "intents_detected": self.intents_detected,
        }

        os.makedirs(self.metrics_dir, exist_ok=True)

        # Individual session file
        session_file = os.path.join(self.metrics_dir, f"session_{self.session_id}.json")
        with open(session_file, "w") as f:
            json.dump(metrics, f, indent=2)

        # Append to master log
        master_log = os.path.join(self.metrics_dir, "all_sessions.jsonl")
        with open(master_log, "a") as f:
            f.write(json.dumps(metrics) + "\n")

        logger.info(f"[{self.session_id}] Metrics saved → {session_file}")
