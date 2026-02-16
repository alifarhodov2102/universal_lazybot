from jinja2 import Template, exceptions
import re

# Alice's minimalist template: No more Google Maps, just clean data 💅
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
    Alice formats the address so it's not a giant mess.
    """
    if not addr:
        return ""
    addr = addr.strip()
    
    # Splitting long one-liners into readable parts for better UX
    if "\n" not in addr and addr.count(",") >= 2:
        parts = addr.split(",", 1)
        return f"{parts[0].strip()},\n{parts[1].strip()}"
    
    return addr

def render_result(data: dict, user_template: str = None) -> str:
    """
    Converts JSON data from the extractor into a sassy yet clean text format 🥱.
    """
    # 1. Clean and prepare the data
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

    # 2. Select the template
    tmpl_str = user_template if user_template else DEFAULT_TEMPLATE

    try:
        # 3. Render with HTML support for Telegram 💅
        template = Template(tmpl_str)
        rendered_text = template.render(**clean_data)
        
        # Clean up triple newlines and return the clean result
        return re.sub(r'\n{3,}', '\n\n', rendered_text).strip()
    
    except exceptions.TemplateError as e:
        return f"⚠️ <b>Template Error:</b> {str(e)}\n\nPlease check your settings, honey."