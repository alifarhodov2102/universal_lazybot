from jinja2 import Template, exceptions
import re

# Alice fixed the broken HTML tags because accuracy matters 💅
DEFAULT_TEMPLATE = """
<b>{{ broker }}</b>

<b>Load#</b> {{ load_number }}

{% for p in pickups -%}
<b>PU{{ loop.index }}:</b> {{ p.facility }}
{{ p.address }}
{% if p.time %}<b>TIME:</b> {{ p.time }}{% endif %}
{% endfor %}
—————————————
{% for d in deliveries -%}
<b>DEL{{ loop.index }}:</b> {{ d.facility }}
{{ d.address }}
{% if d.time %}<b>TIME:</b> {{ d.time }}{% endif %}
{% endfor %}

<b>TOTAL MILES:</b> {{ total_miles }}
<b>RATE:</b> {{ rate }}
"""

def _format_address(addr: str) -> str:
    """
    Alice cleans up the messy address formatting for dispatchers.
    """
    if not addr:
        return ""
    addr = addr.strip()
    
    # Split long single-line addresses for better readability
    if "\n" not in addr and addr.count(",") >= 2:
        parts = addr.split(",", 1)
        return f"{parts[0].strip()},\n{parts[1].strip()}"
    
    return addr

def render_result(data: dict, user_template: str = None) -> str:
    """
    Converts JSON data into a clean, bolded, and sassy Telegram message 🥱.
    """
    # 1. Sanitize and prepare data for rendering
    clean_data = {
        "broker": (data.get("broker") or "Rate Confirmation").strip(),
        "load_number": (data.get("load_number") or "N/A").strip(),
        "rate": (data.get("rate") or "N/A").strip(),
        "total_miles": (data.get("total_miles") or "N/A"),
        "pickups": [
            {
                "facility": (p.get("facility") or "").strip(),
                "address": _format_address(p.get("address")),
                "time": (p.get("time") or "").strip()
            } for p in data.get("pickups", [])
        ],
        "deliveries": [
            {
                "facility": (d.get("facility") or "").strip(),
                "address": _format_address(d.get("address")),
                "time": (d.get("time") or "").strip()
            } for d in data.get("deliveries", [])
        ]
    }

    # 2. Use custom or default template
    tmpl_str = user_template if user_template else DEFAULT_TEMPLATE

    try:
        # 3. Render and clean up white spaces for Telegram
        template = Template(tmpl_str)
        rendered_text = template.render(**clean_data)
        
        # Eliminate excessive newlines
        return re.sub(r'\n{3,}', '\n\n', rendered_text).strip()
    
    except exceptions.TemplateError as e:
        return f"⚠️ <b>Template Error:</b> {str(e)}\n\nCheck your logic, honey. 💅"