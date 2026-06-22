import os
from supabase_config import get_supabase_client
from redis_config import get_redis_client

# Time-To-Live for cache (e.g. 24 hours)
TIMEZONE_CACHE_TTL = 86400 

def get_location_timezone(organisation_id: str, location_name: str) -> str:
    """
    Resolves the exact IANA Timezone string for a given clinic location.
    Checks Redis cache first, falls back to Supabase DB, and if missing, defaults to 'Asia/Kolkata'.
    """
    default_tz = "Asia/Kolkata"
    
    if not organisation_id or not location_name:
        print("Warning: Missing org_id or location_name. Defaulting to", default_tz)
        return default_tz
        
    location_name = location_name.lower()
    cache_key = f"tz:{organisation_id}:{location_name}"
    
    # 1. Check Redis Cache
    try:
        redis_client = get_redis_client()
        if redis_client:
            cached_tz = redis_client.get(cache_key)
            if cached_tz:
                return cached_tz
    except Exception as e:
        print(f"Redis cache miss/error for timezone: {e}")
        redis_client = None

    # 2. Query Supabase
    try:
        supabase = get_supabase_client()
        # ILIKE is not natively supported in Python SDK's .eq(), but .ilike() is.
        response = (
            supabase.table("organisation_locations")
            .select("organisation_timezone")
            .eq("organisation_id", organisation_id)
            .ilike("location_name", f"%{location_name}%")
            .limit(1)
            .execute()
        )
        
        data = response.data
        if data and len(data) > 0:
            db_tz = data[0].get("organisation_timezone")
            if db_tz:
                # 3. Save to Redis Cache
                if redis_client:
                    try:
                        redis_client.setex(cache_key, TIMEZONE_CACHE_TTL, db_tz)
                    except Exception as e:
                        print(f"Failed to cache timezone to Redis: {e}")
                
                return db_tz
                
    except Exception as e:
        print(f"Error querying Supabase for timezone: {e}")
        
    # 4. Fallback Default
    return default_tz
