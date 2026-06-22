from langsmith import traceable

@traceable
def whatsapp_trigger_node(state):
    """
    Trigger node. WhatsApp webhook payload will be passed here.
    """
    return state
