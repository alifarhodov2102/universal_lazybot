from jinja2 import Template, exceptions
import re

# Alice's default style remains a backup 💅
DEFAULT_TEMPLATE = """
<b>{{ broker }}</b>

<b>Load#</b> {{ load_number }}

<b>PU1:</b> {{ pickup_info }}

<b>DEL1:</b> {{ delivery_info }}

<b>TOTAL MILES:</b> {{ total_miles }}
<b>RATE:</b> {{ rate }}
<b>PER MILE:</b> {{ per_mile }}
<b>DURATION:</b> {{ duration }}
"""

def _format_address(addr: str) -> str:
    """
    Alice makes the address readable by forcing City, ST Zip to a new line. 💅
    """
    if not addr:
        return ""
    
    addr = addr.strip()
    
    # Split logic: look for the last comma which usually separates Street from City
    if "," in addr:
        # We split from the right to separate the "City, ST Zip" part
        parts = addr.rsplit(",", 1)
        street = parts[0].strip()
        location = parts[1].strip() # This is the City, State Zip part
        return f"{street},\n{location}"
        
    return addr

def _build_stop_string(stop_list: list) -> str:
    """Combines all details into one 'info' block for the template. 🧠"""
    if not stop_list:
        return "N/A"
    
    stop = stop_list[0]
    facility = stop.get("facility", "").strip()
    address = _format_address(stop.get("address", ""))
    time = stop.get("time", "").strip()
    
    parts = []
    if facility: parts.append(facility)
    if address: parts.append(address)
    if time: parts.append(f"TIME: {time}")
    
    return "\n".join(parts)

def _calculate_drive_duration(miles_float: float) -> str:
    """
    Alice calculates duration based on a 700-mile driving day (~14-15 hours).
    Includes a 2-hour buffer for loading/unloading. 🚛
    """
    if miles_float <= 0:
        return "N/A"
    
    # Logic: 700 miles per day rule
    # 700 miles in a 14-hour duty day = ~50mph average speed.
    # We add 2 hours total for the PU and DEL stops.
    total_hours = (miles_float / 50) + 2
    
    hours = int(total_hours)
    minutes = int((total_hours - hours) * 60)
    
    return f"{hours}h {minutes}m"

def render_result(data: dict, user_template: str = None) -> str:
    """Converts raw JSON into a beautifully formatted Telegram message 🥱."""
    
    # 1. Handle Miles formatting (Remove 'mi' if already exists to avoid 'mi mi')
    raw_miles = str(data.get("total_miles") or "0").replace('mi', '').replace(',', '').strip()
    try:
        miles_float = float(raw_miles)
        miles_display = f"{miles_float} mi" if miles_float > 0 else "N/A"
    except:
        miles_float = 0
        miles_display = "N/A"

    # 2. Handle Rate and Per Mile calculation 💰
    raw_rate = data.get("rate") or "0"
    # Strip everything except numbers and decimals
    rate_clean = re.sub(r"[^\d.]", "", str(raw_rate))
    try:
        rate_float = float(rate_clean)
        rate_display = f"${rate_float:,.2f}"
        per_mile = f"${round(rate_float / miles_float, 2)}/mi" if miles_float > 0 else "N/A"
    except:
        rate_display = raw_rate if raw_rate != "0" else "N/A"
        per_mile = "N/A"

    # 3. Trip Duration based on mileage (700mi/day rule) 🕒
    duration = _calculate_drive_duration(miles_float)

    # 4. Create the unified info blocks
    pickup_info = _build_stop_string(data.get("pickups", []))
    delivery_info = _build_stop_string(data.get("deliveries", []))

    # 5. Clean up the data for rendering
    clean_data = {
        "broker": (data.get("broker") or "N/A").strip(),
        "load_number": (data.get("load_number") or "N/A").strip(),
        "rate": rate_display,
        "total_miles": miles_display,
        "per_mile": per_mile,
        "duration": duration,
        "pickup_info": pickup_info,
        "delivery_info": delivery_info,
        "pickups": data.get("pickups", []),
        "deliveries": data.get("deliveries", [])
    }

    # 6. Pick the winning template
    tmpl_str = user_template if user_template else DEFAULT_TEMPLATE

    try:
        # 7. Let Alice work her magic 💅
        template = Template(tmpl_str)
        rendered_text = template.render(**clean_data)
        
        # Remove triple-newlines to keep it tight
        return re.sub(r'\n{3,}', '\n\n', rendered_text).strip()
    
    except exceptions.TemplateError as e:
        return f"⚠️ <b>Template Error:</b> {str(e)}\n\nDon't break my code, honey. 💅"