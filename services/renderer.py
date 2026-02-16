from jinja2 import Template, exceptions
import re

# Standart format: Google Maps va Mileage qo'shildi
DEFAULT_TEMPLATE = """
{{ broker }}

Load# {{ load_number }}

{% for p in pickups -%}
PU{{ loop.index }}: {{ p.facility }}
{{ p.address }}
📍 [Google Maps](https://www.google.com/maps/search/?api=1&query={{ p.address|replace(' ', '+')|replace('\n', '+') }})
{% if p.time %}TIME: {{ p.time }}{% endif %}
{% endfor %}

{% for d in deliveries -%}
DEL{{ loop.index }}: {{ d.facility }}
{{ d.address }}
📍 [Google Maps](https://www.google.com/maps/search/?api=1&query={{ d.address|replace(' ', '+')|replace('\n', '+') }})
{% if d.time %}TIME: {{ d.time }}{% endif %}
{% endfor %}

TOTAL MILES: {{ total_miles }}
RATE: {{ rate }}
"""

def _format_address(addr: str) -> str:
    """
    Manzilni chiroyli ko'rinishga keltirish.
    """
    if not addr:
        return ""
    addr = addr.strip()
    
    # Bir qatorli manzillarni o'qish qulay bo'lishi uchun bo'laklaymiz
    if "\n" not in addr and addr.count(",") >= 2:
        parts = addr.split(",", 1)
        return f"{parts[0].strip()},\n{parts[1].strip()}"
    
    return addr

def render_result(data: dict, user_template: str = None) -> str:
    """
    Extractor'dan kelgan JSON ma'lumotlarini Jinja2 shabloni orqali 
    tayyor matnga aylantiradi.
    """
    # 1. Ma'lumotlarni tozalash va tayyorlash
    clean_data = {
        "broker": (data.get("broker") or "N/A").strip(),
        "load_number": (data.get("load_number") or "N/A").strip(),
        "rate": (data.get("rate") or "N/A").strip(),
        "total_miles": (data.get("total_miles") or "N/A"), # Masofa qo'shildi
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

    # 2. Shablonni tanlash
    tmpl_str = user_template if user_template else DEFAULT_TEMPLATE

    try:
        # 3. Render qilish
        template = Template(tmpl_str)
        rendered_text = template.render(**clean_data)
        
        # Ortiqcha bo'sh qatorlarni tozalash
        return re.sub(r'\n{3,}', '\n\n', rendered_text).strip()
    
    except exceptions.TemplateError as e:
        return f"⚠️ Shablonda xatolik bor: {str(e)}\n\nIltimos, sozlamalarni tekshiring."