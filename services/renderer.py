from jinja2 import Template, exceptions
import re
from datetime import datetime

# Alice's default style remains a backup 💅
DEFAULT_TEMPLATE = """
<b>{{ broker }}</b>

<b>Load#</b> {{ load_number }}

<b>PU1:</b> {{ pickup_info }}

<b>DEL1:</b> {{ delivery_info }}

<b>TOTAL MILES:</b> {{ total_miles }}
<b>RATE:</b> {{ rate }}
<b>PER MILE:</b> {{ per_mile }}
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

def _calculate_duration(pickups: list, deliveries: list) -> str:
    """Alice calculates how long the trip takes. 🕒"""
    try:
        if not pickups or not deliveries:
            return "N/A"
        
        # Extract first PU and last DEL times
        pu_str = pickups[0].get("time", "")
        del_str = deliveries[-1].get("time", "")
        
        # Regex to find dates in common formats (MM/DD/YYYY or MM/DD/YY)
        date_pattern = re.compile(r"(\d{1,2}/\d{1,2}/\d{2,4})")
        pu_match = date_pattern.search(pu_str)
        del_match = date_pattern.search(del_str)
        
        if pu_match and del_match:
            # Simple day difference calculation
            fmt = "%m/%d/%y" if len(pu_match.group(1).split('/')[-1]) == 2 else "%m/%d/%Y"
            d1 = datetime.strptime(pu_match.group(1), fmt)
            d2 = datetime.strptime(del_match.group(1), fmt)
            diff = d2 - d1
            
            days = diff.days
            # Logic: assume at least 14h for any overnight, or simplify to days/hours
            return f"{days}d 14h" if days >= 0 else "N/A"
    except:
        pass
    return "0d 14h"

def render_result(data: dict, user_template: str = None) -> str:
    """Converts raw JSON into a beautifully formatted Telegram message 🥱."""
    
    # 1. Handle Miles formatting
    raw_miles = data.get("total_miles")
    miles_str = str(raw_miles).replace(',', '')
    if miles_str and re.match(r"^\d*\.?\d+$", miles_str):
        miles_float = float(miles_str)
        miles_display = f"{miles_float} mi"
    else:
        miles_float = 0
        miles_display = "N/A"

    # 2. Handle Rate and Per Mile calculation 💰
    raw_rate = data.get("rate") or "0"
    rate_clean = re.sub(r"[^\d.]", "", str(raw_rate))
    try:
        rate_float = float(rate_clean)
        per_mile = f"${round(rate_float / miles_float, 2)}/mi" if miles_float > 0 else "N/A"
    except:
        per_mile = "N/A"

    # 3. Trip Duration 🕒
    duration = _calculate_duration(data.get("pickups", []), data.get("deliveries", []))

    # 4. Create the unified info blocks
    pickup_info = _build_stop_string(data.get("pickups", []))
    delivery_info = _build_stop_string(data.get("deliveries", []))

    # 5. Clean up the data for rendering
    clean_data = {
        "broker": (data.get("broker") or "Rate Confirmation").strip(),
        "load_number": (data.get("load_number") or "N/A").strip(),
        "rate": (data.get("rate") or "N/A").strip(),
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