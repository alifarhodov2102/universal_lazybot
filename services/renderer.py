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

def render_result(data: dict, user_template: str = None) -> str:
    """Converts raw JSON into a beautifully formatted Telegram message 🥱."""
    
    # 1. Handle Miles formatting
    miles = data.get("total_miles")
    if miles and str(miles).replace('.', '', 1).isdigit():
        miles = f"{miles} mi"
    elif not miles or miles == "0" or miles == "N/A":
        miles = "N/A"

    # 2. Create the unified info blocks
    pickup_info = _build_stop_string(data.get("pickups", []))
    delivery_info = _build_stop_string(data.get("deliveries", []))

    # 3. Clean up the basic data
    clean_data = {
        "broker": (data.get("broker") or "Rate Confirmation").strip(),
        "load_number": (data.get("load_number") or "N/A").strip(),
        "rate": (data.get("rate") or "N/A").strip(),
        "total_miles": miles,
        "pickup_info": pickup_info,
        "delivery_info": delivery_info,
        "pickups": data.get("pickups", []),
        "deliveries": data.get("deliveries", [])
    }

    # 4. Pick the winning template
    tmpl_str = user_template if user_template else DEFAULT_TEMPLATE

    try:
        # 5. Let Alice work her magic 💅
        template = Template(tmpl_str)
        rendered_text = template.render(**clean_data)
        
        # Remove triple-newlines to keep it tight
        return re.sub(r'\n{3,}', '\n\n', rendered_text).strip()
    
    except exceptions.TemplateError as e:
        return f"⚠️ <b>Template Error:</b> {str(e)}\n\nDon't break my code, honey. 💅"