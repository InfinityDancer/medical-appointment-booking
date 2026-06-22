import os
import requests
from typing import Optional

def fetch_timezone_csc_api(city_name: str, state_iso: str, country_iso: str) -> Optional[str]:
    """
    Fetches the Exact IANA Timezone string for a given city via the CountryStateCity API.
    
    Args:
        city_name (str): e.g., "Phoenix"
        state_iso (str): e.g., "AZ"
        country_iso (str): e.g., "US"
        
    Returns:
        str: IANA timezone string e.g., "America/Phoenix" or None if not found/failed.
    """
    api_key = os.getenv("CSC_API_KEY")
    if not api_key:
        print("Warning: CSC_API_KEY environment variable not set. Timezone lookup may fail.")
        return None
        
    url = f"https://api.countrystatecity.in/v1/countries/{country_iso}/states/{state_iso}/cities"
    headers = {"X-CSCAPI-KEY": api_key}
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        cities_data = response.json()
        
        # Search the returned list for the exact city name
        for city in cities_data:
            if city.get("name", "").lower() == city_name.lower():
                # If timezone is present in the list API response
                if "timezone" in city:
                    return city["timezone"]
                    
                # If the API requires getting specific city details:
                # We can call the specific city endpoint if timezone isn't in the list
                city_id = city.get("id")
                if city_id:
                    detail_url = f"https://api.countrystatecity.in/v1/countries/{country_iso}/states/{state_iso}/cities/{city_id}"
                    detail_response = requests.get(detail_url, headers=headers, timeout=10)
                    detail_response.raise_for_status()
                    detail_data = detail_response.json()
                    return detail_data.get("timezone")

        print(f"City '{city_name}' not found in CountryStateCity response.")
        return None
    except requests.RequestException as e:
        print(f"Error calling CountryStateCity API: {e}")
        return None
