from jinja2 import Template, exceptions
import re

# Alice's updated default style now supports multiple stops! 💅
DEFAULT_TEMPLATE = """
<b>{{ broker }}</b>

<b>Load#</b> {{ load_number }}

{{ stops_info }}

<b>TOTAL MILES:</b> {{ total_miles }}
<b>RATE:</b> {{ rate }}
<b>PER MILE:</b> {{ per_mile }}
<b>DURATION:</b> {{ duration }}
"""

def _format_address(addr: str) -> str:
    """Alice makes the address readable by forcing City, State Zip to a new line. 💅"""
    if not addr:
        return ""
    
    addr = addr.strip()
    parts = [p.strip() for p in addr.split(",")]
    
    if len(parts) >= 3:
        street = ", ".join(parts[:-3]) if len(parts) > 3 else parts[0]
        location = ", ".join(parts[-3:])
        return f"{street},\n{location}"
    
    if "," in addr:
        parts = addr.rsplit(",", 1)
        return f"{parts[0].strip()},\n{parts[1].strip()}"
        
    return addr

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
        rate_display = raw_rate if raw_rate != "0" else "N/A"
        per_mile = "N/A"

    # 3. Trip Duration 🕒
    duration = _calculate_drive_duration(miles_float)

    # 4. Multi-Stop Info Block 📍
    stops_info = _build_multi_stop_string(data.get("pickups", []), data.get("deliveries", []))

    # 5. Clean up the data
    clean_data = {
        "broker": (data.get("broker") or "N/A").strip(),
        "load_number": (data.get("load_number") or "N/A").strip(),
        "rate": rate_display,
        "total_miles": miles_display,
        "per_mile": per_mile,
        "duration": duration,
        "stops_info": stops_info,
        "pickup_info": _build_multi_stop_string(data.get("pickups", []), [])[:200], # Fallback for old templates
        "delivery_info": _build_multi_stop_string([], data.get("deliveries", []))[:200], # Fallback for old templates
    }

    tmpl_str = user_template if user_template else DEFAULT_TEMPLATE
    tmpl_str = tmpl_str.replace('{{ total_miles }} mi', '{{ total_miles }}')

    try:
        template = Template(tmpl_str)
        rendered_text = template.render(**clean_data)
        return re.sub(r'\n{3,}', '\n\n', rendered_text).strip()
    except exceptions.TemplateError as e:
        return f"⚠️ <b>Template Error:</b> {str(e)}\n\nDon't break my code, honey. 💅"