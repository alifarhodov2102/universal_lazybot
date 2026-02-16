from jinja2 import Template, exceptions
import re

# Alice fixed the template to ensure every bold tag is closed properly 💅
DEFAULT_TEMPLATE = """
<b>{{ broker }}</b>

<b>Load#</b> {{ load_number }}

{% for p in pickups -%}
<b>PU{{ loop.index }}:</b> {{ p.facility }}
{{ p.address }}
{% if p.time %}<b>TIME:</b> {{ p.time }}{% endif %}
—————————————
{% endfor -%}

{% for d in deliveries -%}
<b>DEL{{ loop.index }}:</b> {{ d.facility }}
{{ d.address }}
{% if d.time %}<b>TIME:</b> {{ d.time }}{% endif %}
{% if not loop.last %}—————————————{% endif %}
{% endfor %}

<b>TOTAL MILES:</b> {{ total_miles }}
<b>RATE:</b> {{ rate }}
"""

def _format_address(addr: str) -> str:
    """
    Alice makes the address readable so dispatchers don't get a headache.
    """
    if not addr:
        return ""
    addr = addr.strip()
    
    # Split single-line addresses into two lines for better visual structure
    if "\n" not in addr and addr.count(",") >= 2:
        parts = addr.split(",", 1)
        return f"{parts[0].strip()},\n{parts[1].strip()}"
    
    return addr

def render_result(data: dict, user_template: str = None) -> str:
    """
    Converts raw JSON into a beautifully formatted Telegram message 🥱.
    """
    # 1. Clean up the data before Alice touches it
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

    # 2. Pick the winning template
    tmpl_str = user_template if user_template else DEFAULT_TEMPLATE

    try:
        # 3. Let Alice work her magic 💅
        # We use a strict template to ensure no broken HTML is generated
        template = Template(tmpl_str)
        rendered_text = template.render(**clean_data)
        
        # Remove any triple-newline "accidents" to keep it tight
        return re.sub(r'\n{3,}', '\n\n', rendered_text).strip()
    
    except exceptions.TemplateError as e:
        # Even the error message is bolded for you 🥱
        return f"⚠️ <b>Template Error:</b> {str(e)}\n\nDon't break my code, honey. 💅"