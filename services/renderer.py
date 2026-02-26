from jinja2 import Template, exceptions
import re

# Alice's final polished style 💅
# Layout: Broker -> Numbers -> Stops -> Per Mile/Duration -> Stats
DEFAULT_TEMPLATE = """
<b>{{ broker }}</b>

<b>Load#</b> {{ load_number }}

{% if ref_number and ref_number != 'N/A' %}<b>Ref#</b> {{ ref_number }}{% endif %}
{% if bol_number and bol_number != 'N/A' %}<b>BOL#</b> {{ bol_number }}{% endif %}
{% if pu_number and pu_number != 'N/A' %}<b>PU#</b> {{ pu_number }}{% endif %}
{% if del_number and del_number != 'N/A' %}<b>DEL#</b> {{ del_number }}{% endif %}

{{ stops_info }}
—————————————
<b>PER MILE:</b> {{ per_mile }}
<b>DURATION:</b> {{ duration }}

<b>WEIGHT:</b> {{ weight }}
<b>TOTAL MILES:</b> {{ total_miles }}
<b>RATE:</b> {{ rate }}
"""

def _format_address(addr: str) -> str:
    """Alice makes the address readable by forcing City, State Zip to a new line. 💅"""
    if not addr: return ""
    
    # Remove internal duplicates (e.g., "300 N GALLERIA DR, 300 N GALLERIA DR")
    parts = [p.strip() for p in addr.replace('\n', ',').split(",")]
    unique_parts = []
    for p in parts:
        if p and p not in unique_parts: unique_parts.append(p)
    
    # Format for display: Street on one line, City/ST/Zip on next
    if len(unique_parts) >= 2:
        street = unique_parts[0]
        location = ", ".join(unique_parts[1:])
        return f"{street},\n{location}"
        
    return ", ".join(unique_parts)

def _build_multi_stop_string(pickups: list, deliveries: list) -> str:
    """Loops through ALL pickups and ALL deliveries for multi-stop loads. 🧠"""
    stop_lines = []
    
    # Process all Pickups
    for i, stop in enumerate(pickups, 1):
        facility = stop.get("facility", "").strip()
        address = _format_address(stop.get("address", ""))
        time = stop.get("time", "").strip()
        
        stop_lines.append(f"<b>📍PU{i}:</b>")
        if facility: stop_lines.append(facility)
        if address: stop_lines.append(address)
        if time: stop_lines.append(f"TIME: {time}")
        stop_lines.append("—————————————")

    # Process all Deliveries
    for i, stop in enumerate(deliveries, 1):
        facility = stop.get("facility", "").strip()
        address = _format_address(stop.get("address", ""))
        time = stop.get("time", "").strip()
        
        stop_lines.append(f"<b>📍DEL{i}:</b>")
        if facility: stop_lines.append(facility)
        if address: stop_lines.append(address)
        if time: stop_lines.append(f"TIME: {time}")
        if i < len(deliveries): stop_lines.append("—————————————")
    
    return "\n".join(stop_lines)

def _calculate_drive_duration(miles_float: float) -> str:
    """Alice calculates duration based on 700-mile/day rule (~50mph + 2h buffer) 🚛"""
    if miles_float <= 0:
        return "N/A"
    
    # Average 50mph including minor breaks + 2 hours for stop handling
    total_hours = (miles_float / 50) + 2
    hours = int(total_hours)
    minutes = int((total_hours - hours) * 60)
    
    return f"{hours}h {minutes}m"

def render_result(data: dict, user_template: str = None) -> str:
    """Converts raw JSON into a beautifully formatted Telegram message 🥱."""
    
    # 1. Aggressive Miles cleaning
    raw_miles = str(data.get("total_miles") or "0").lower().replace('mi', '').replace(',', '').strip()
    try:
        miles_float = float(raw_miles)
        miles_display = f"{miles_float} mi" if miles_float > 0 else "N/A"
    except:
        miles_float = 0
        miles_display = "N/A"

    # 2. Handle Rate and Per Mile
    raw_rate = data.get("rate") or "0"
    rate_clean = re.sub(r"[^\d.]", "", str(raw_rate))
    try:
        rate_float = float(rate_clean)
        rate_display = f"${rate_float:,.2f}"
        per_mile = f"${round(rate_float / miles_float, 2)}/mi" if miles_float > 0 else "N/A"
    except:
        rate_display = raw_rate if raw_rate != "0" and raw_rate != "0.00" else "N/A"
        per_mile = "N/A"

    # 3. Trip Duration 🕒
    duration = _calculate_drive_duration(miles_float)

    # 4. Multi-Stop Info Block 📍
    stops_info = _build_multi_stop_string(data.get("pickups", []), data.get("deliveries", []))

    # 5. Clean up the data for rendering
    clean_data = {
        "broker": (data.get("broker") or "N/A").strip(),
        "load_number": (data.get("load_number") or "N/A").strip(),
        "weight": (data.get("weight") or "N/A").strip(),
        "pu_number": (data.get("pu_number") or "N/A").strip(),
        "del_number": (data.get("del_number") or "N/A").strip(),
        "bol_number": (data.get("bol_number") or "N/A").strip(),
        "ref_number": (data.get("ref_number") or "N/A").strip(),
        "rate": rate_display,
        "total_miles": miles_display,
        "per_mile": per_mile,
        "duration": duration,
        "stops_info": stops_info,
    }

    tmpl_str = user_template if user_template else DEFAULT_TEMPLATE
    
    # Clean template logic: remove manual 'mi' if it's already in our display
    tmpl_str = tmpl_str.replace('{{ total_miles }} mi', '{{ total_miles }}')

    try:
        template = Template(tmpl_str)
        rendered_text = template.render(**clean_data)
        
        # Remove triple-newlines to keep it tight
        return re.sub(r'\n{3,}', '\n\n', rendered_text).strip()
    
    except exceptions.TemplateError as e:
        return f"⚠️ <b>Template Error:</b> {str(e)}\n\nDon't break my code, honey. 💅"