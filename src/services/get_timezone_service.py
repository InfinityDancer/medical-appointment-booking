from datetime import datetime
try:
    from zoneinfo import ZoneInfo
except ImportError:
    pass # we know zoneinfo exists in python 3.9+ natively

def format_timezone(doctor_time: str, timezone_str: str = "Asia/Kolkata") -> dict:
    if not doctor_time:
        return {"error": "Missing doctor_time in request body"}

    try:
        # Standardize the format for the Date constructor identically to TS rules
        formatted_str = doctor_time.replace(" ", "T").replace("+00", "+00:00").replace("Z", "+00:00")
        
        # Parse timezone aware datetime natively
        dt = datetime.fromisoformat(formatted_str)
        
        # Shift timezone dynamically natively
        target_tz = ZoneInfo(timezone_str)
        tz_aware_dt = dt.astimezone(target_tz)
        
        # Emulate `toLocaleString('en-US')` which natively drops leading zeroes for days.
        # e.g., 'Dec 5, 2024, 05:30 PM' 
        month = tz_aware_dt.strftime('%b')
        day = tz_aware_dt.day  # dynamically scalar stripping '0' padding globally across OS deployments
        year = tz_aware_dt.strftime('%Y')
        time_str = tz_aware_dt.strftime('%I:%M %p') # Note formatting padding matches the '2-digit' minute expectation
        
        # Javascript formats hour: 'numeric' so 05:30 PM becomes 5:30 PM.
        if time_str.startswith("0"):
            time_str = time_str[1:]
        
        new_date_string = f"{month} {day}, {year}, {time_str}"
        
        return {"output": new_date_string}
    except Exception as e:
        return {"error": str(e) or "Invalid Date Format"}
