import os
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

def get_supabase_client():
    """
    Initialize and return Supabase client.
    Required environment variables:
    - SUPABASE_URL: Your Supabase project URL
    - SUPABASE_KEY: Your Supabase API key (anon key)
    """
    SUPABASE_URL = os.getenv("SUPABASE_URL")
    SUPABASE_KEY = os.getenv("SUPABASE_KEY")
    
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise ValueError(
            "Missing Supabase credentials. "
            "Please set SUPABASE_URL and SUPABASE_KEY environment variables."
        )
    
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    return supabase
