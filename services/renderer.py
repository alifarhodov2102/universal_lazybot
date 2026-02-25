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
    """Alice makes the address readable so dispatchers don't get a headache."""
    if not addr:
        return ""
    addr = addr.strip()
    
    # Split single-line addresses into two lines for better visual structure
    if "\n" not in addr and addr.count(",") >= 2:
        parts = addr.split(",", 1)
        return f"{parts[0].strip()},\n{parts[1].strip()}"
    
    return addr

def _build_stop_string(stop_list: list) -> str:
    """Combines all details into one 'info' string for the new skeleton style 🧠"""
    if not stop_list:
        return "N/A"
    
    stop = stop_list[0] # Focus on first stop for simple templates
    facility = stop.get("facility", "").strip()
    address = _format_address(stop.get("address", ""))
    time = stop.get("time", "").strip()
    
    # Build the block: Facility, Address, and Time on separate lines
    parts = []
    if facility: parts.append(facility)
    if address: parts.append(address)
    if time: parts.append(f"TIME: {time}")
    
    return "\n".join(parts)

def render_result(data: dict, user_template: str = None) -> str:
    """Converts raw JSON into a beautifully formatted Telegram message 🥱."""
    
    # 1. Create the unified info blocks for the user's custom skeleton
    pickup_info = _build_stop_string(data.get("pickups", []))
    delivery_info = _build_stop_string(data.get("deliveries", []))

    # 2. Clean up the basic data
    clean_data = {
        "broker": (data.get("broker") or "Rate Confirmation").strip(),
        "load_number": (data.get("load_number") or "N/A").strip(),
        "rate": (data.get("rate") or "N/A").strip(),
        "total_miles": (data.get("total_miles") or "N/A"),
        "pickup_info": pickup_info,
        "delivery_info": delivery_info,
        # Keep lists as fallback for users with advanced loop templates
        "pickups": data.get("pickups", []),
        "deliveries": data.get("deliveries", [])
    }

    # 3. Pick the winning template
    tmpl_str = user_template if user_template else DEFAULT_TEMPLATE

    try:
        # 4. Let Alice work her magic 💅
        template = Template(tmpl_str)
        rendered_text = template.render(**clean_data)
        
        # Remove triple-newlines to keep it clean
        return re.sub(r'\n{3,}', '\n\n', rendered_text).strip()
    
    except exceptions.TemplateError as e:
        return f"⚠️ <b>Template Error:</b> {str(e)}\n\nDon't break my code, honey. 💅"