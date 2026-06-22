from typing import Optional
from supabase_config import get_supabase_client
from redis_config import get_redis_client

# Time-To-Live for cache (24 hours)
LOCATION_CACHE_TTL = 86400


def resolve_location_id(organisation_id: str, location_name: str) -> Optional[str]:
    """
    Resolves a human-readable location name (e.g. 'mumbai') to its UUID
    from the organisation_locations table.

    Checks Redis cache first, falls back to Supabase DB.
    Returns the UUID string, or None if no match is found.
    """
    if not organisation_id or not location_name:
        print("Warning: Missing org_id or location_name for location resolution.")
        return None

    location_name_lower = location_name.strip().lower()
    cache_key = f"loc_id:{organisation_id}:{location_name_lower}"
    redis_client = None

    # 1. Check Redis Cache
    try:
        redis_client = get_redis_client()
        if redis_client:
            cached_id = redis_client.get(cache_key)
            if cached_id:
                print(f"Location ID cache hit: {location_name} -> {cached_id}")
                return cached_id
    except Exception as e:
        print(f"Redis cache miss/error for location resolution: {e}")
        redis_client = None

    # 2. Query Supabase
    try:
        supabase = get_supabase_client()
        response = (
            supabase.table("organisation_locations")
            .select("id")
            .eq("organisation_id", organisation_id)
            .ilike("location_name", f"%{location_name_lower}%")
            .limit(1)
            .execute()
        )

        data = response.data
        
        if data and len(data) > 0:
            location_id = data[0].get("id")
            if location_id:
                print(f"Location resolved from DB: {location_name} -> {location_id}")
                location_id_str = str(location_id)
                # 3. Save to Redis Cache
                if redis_client:
                    try:
                        redis_client.setex(cache_key, LOCATION_CACHE_TTL, location_id_str)
                    except Exception as e:
                        print(f"Failed to cache location ID to Redis: {e}")

                print(f"Location resolved: {location_name} -> {location_id_str}")
                return location_id_str

    except Exception as e:
        print(f"Error querying Supabase for location ID: {e}")

    print(f"Location '{location_name}' not found for org {organisation_id}")
    return None


def resolve_location_name(organisation_id: str, location_id: str) -> Optional[str]:
    """
    Reverse-resolves a location UUID back to its human-readable name.
    Useful for displaying location to the user/agent when data comes back
    from UUID-based tables.

    Returns the location_name string, or None if not found.
    """
    if not organisation_id or not location_id:
        return None

    cache_key = f"loc_name:{organisation_id}:{location_id}"
    redis_client = None

    # 1. Check Redis Cache
    try:
        redis_client = get_redis_client()
        if redis_client:
            cached_name = redis_client.get(cache_key)
            if cached_name:
                return cached_name
    except Exception as e:
        print(f"Redis cache miss/error for location name resolution: {e}")
        redis_client = None

    # 2. Query Supabase
    try:
        supabase = get_supabase_client()
        response = (
            supabase.table("organisation_locations")
            .select("location_name")
            .eq("organisation_id", organisation_id)
            .eq("id", location_id)
            .limit(1)
            .execute()
        )

        data = response.data
        if data and len(data) > 0:
            name = data[0].get("location_name")
            if name:
                if redis_client:
                    try:
                        redis_client.setex(cache_key, LOCATION_CACHE_TTL, name)
                    except Exception as e:
                        print(f"Failed to cache location name to Redis: {e}")
                return name

    except Exception as e:
        print(f"Error querying Supabase for location name: {e}")

    return None
