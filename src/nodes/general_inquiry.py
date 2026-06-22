from .agent_node import general_inquiry_agent
from prompts import GENERAL_INQUIRY_AGENT_PROMPT

def general_inquiry_node(state):
    state = general_inquiry_agent(state,GENERAL_INQUIRY_AGENT_PROMPT)
    
    return state