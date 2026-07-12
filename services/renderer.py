import html
import re
from typing import Any, Dict, List, Optional

from jinja2 import Template, exceptions


# ============================================================
# Default Telegram output template
# ============================================================

DEFAULT_TEMPLATE = """
<b>{{ broker }}</b>

<b>Load#</b> <code>{{ load_number }}</code>

{% if ref_number and ref_number != 'N/A' %}<b>Ref#</b> <code>{{ ref_number }}</code>{% endif %}
{% if bol_number and bol_number != 'N/A' %}<b>BOL#</b> <code>{{ bol_number }}</code>{% endif %}
{% if pu_number and pu_number != 'N/A' %}<b>PU#</b> <code>{{ pu_number }}</code>{% endif %}
{% if del_number and del_number != 'N/A' %}<b>DEL#</b> <code>{{ del_number }}</code>{% endif %}

{{ stops_info }}

{% if has_temperature_info %}
🌡 <b>TEMP INFO:</b>
{{ temperature_info }}

{% endif %}
—————————————
<b>PER MILE:</b> {{ per_mile }}
<b>DURATION:</b> {{ duration }}

<b>WEIGHT:</b> {{ weight }}
<b>TOTAL MILES:</b> {{ total_miles }}
<b>RATE:</b> {{ rate }}
"""


# ============================================================
# General helpers
# ============================================================

def _is_missing(value: Any) -> bool:
    if value is None:
        return True

    normalized = str(value).strip().lower()

    return normalized in {
        "",
        "n/a",
        "na",
        "none",
        "null",
        "unknown",
        "not stated",
        "0",
        "0.00",
    }


def _safe_text(
    value: Any,
    default: str = "N/A",
) -> str:
    """
    Escape extracted PDF text before inserting it into Telegram HTML.

    This prevents names and addresses containing characters such as
    <, >, and & from breaking the Telegram message.
    """
    if _is_missing(value):
        return default

    return html.escape(
        str(value).strip(),
        quote=False,
    )


def _parse_number(value: Any) -> Optional[float]:
    """
    Convert values such as '$2,500.00', '450 mi', or '40,000 lbs'
    into a float.
    """
    if value is None:
        return None

    cleaned = str(value).replace(",", "")

    match = re.search(
        r"-?\d+(?:\.\d+)?",
        cleaned,
    )

    if not match:
        return None

    try:
        return float(match.group(0))

    except (TypeError, ValueError):
        return None


# ============================================================
# Address and stop rendering
# ============================================================

def _format_address(address: Any) -> str:
    """
    Make an address readable and copyable in Telegram.
    """
    if _is_missing(address):
        return ""

    raw_address = str(address).strip()

    parts = [
        part.strip()
        for part in raw_address.replace("\n", ",").split(",")
        if part.strip()
    ]

    unique_parts = []

    for part in parts:
        normalized_part = part.lower()

        if not any(
            existing.lower() == normalized_part
            for existing in unique_parts
        ):
            unique_parts.append(part)

    if not unique_parts:
        return ""

    if len(unique_parts) >= 2:
        street = unique_parts[0]
        location = ", ".join(unique_parts[1:])

        display_address = (
            f"{street},\n"
            f"{location}"
        )

    else:
        display_address = unique_parts[0]

    return (
        f"<code>"
        f"{html.escape(display_address, quote=False)}"
        f"</code>"
    )


def _normalize_stops(
    stops: Any,
) -> List[Dict[str, Any]]:
    if not isinstance(stops, list):
        return []

    return [
        stop
        for stop in stops
        if isinstance(stop, dict)
    ]


def _build_multi_stop_string(
    pickups: Any,
    deliveries: Any,
) -> str:
    """
    Render every pickup and delivery stop.
    """
    pickup_list = _normalize_stops(pickups)
    delivery_list = _normalize_stops(deliveries)

    stop_lines = []

    for index, stop in enumerate(
        pickup_list,
        start=1,
    ):
        facility = _safe_text(
            stop.get("facility"),
            default="",
        )

        address = _format_address(
            stop.get("address")
        )

        appointment_time = _safe_text(
            stop.get("time"),
            default="",
        )

        stop_lines.append(
            f"<b>📍PU{index}:</b>"
        )

        if facility:
            stop_lines.append(facility)

        if address:
            stop_lines.append(address)

        if appointment_time:
            stop_lines.append(
                f"<b>TIME:</b> {appointment_time}"
            )

        stop_lines.append(
            "—————————————"
        )

    for index, stop in enumerate(
        delivery_list,
        start=1,
    ):
        facility = _safe_text(
            stop.get("facility"),
            default="",
        )

        address = _format_address(
            stop.get("address")
        )

        appointment_time = _safe_text(
            stop.get("time"),
            default="",
        )

        stop_lines.append(
            f"<b>📍DEL{index}:</b>"
        )

        if facility:
            stop_lines.append(facility)

        if address:
            stop_lines.append(address)

        if appointment_time:
            stop_lines.append(
                f"<b>TIME:</b> {appointment_time}"
            )

        if index < len(delivery_list):
            stop_lines.append(
                "—————————————"
            )

    if not stop_lines:
        return "<i>No pickup or delivery stops found.</i>"

    return "\n".join(stop_lines)


# ============================================================
# Reefer temperature rendering
# ============================================================

def _build_temperature_info(
    temperature_set: Any,
    temperature_mode: Any,
    temperature_notes: Any,
) -> str:
    """
    Build the reefer temperature section.

    All values are already escaped for Telegram HTML.
    """
    lines = []

    if not _is_missing(temperature_set):
        lines.append(
            f"<b>SET:</b> {_safe_text(temperature_set)}"
        )

    if not _is_missing(temperature_mode):
        lines.append(
            f"<b>MODE:</b> {_safe_text(temperature_mode)}"
        )

    if not _is_missing(temperature_notes):
        lines.append(
            f"<b>NOTES:</b> {_safe_text(temperature_notes)}"
        )

    return "\n".join(lines)


# ============================================================
# Calculations
# ============================================================

def _calculate_drive_duration(
    miles_float: float,
) -> str:
    """
    Estimate driving duration using the existing project rule:

    50 mph average speed plus a 2-hour operational buffer.
    """
    if miles_float <= 0:
        return "N/A"

    total_minutes = round(
        ((miles_float / 50) + 2) * 60
    )

    hours, minutes = divmod(
        total_minutes,
        60,
    )

    return f"{hours}h {minutes}m"


# ============================================================
# Main renderer
# ============================================================

def render_result(
    data: dict,
    user_template: Optional[str] = None,
) -> str:
    """
    Convert extracted load data into a Telegram HTML message.
    """
    if not isinstance(data, dict):
        data = {}

    # --------------------------------------------------------
    # Miles
    # --------------------------------------------------------

    miles_float = _parse_number(
        data.get("total_miles")
    )

    if miles_float is not None and miles_float > 0:
        miles_display = f"{miles_float:,.1f} mi"

    else:
        miles_float = 0.0
        miles_display = "N/A"

    # --------------------------------------------------------
    # Rate and rate per mile
    # --------------------------------------------------------

    rate_float = _parse_number(
        data.get("rate")
    )

    if rate_float is not None and rate_float > 0:
        rate_display = f"${rate_float:,.2f}"

        if miles_float > 0:
            per_mile = (
                f"${rate_float / miles_float:,.2f}/mi"
            )

        else:
            per_mile = "N/A"

    else:
        raw_rate = data.get("rate")

        if _is_missing(raw_rate):
            rate_display = "N/A"

        else:
            rate_display = _safe_text(raw_rate)

        per_mile = "N/A"

    # --------------------------------------------------------
    # Duration
    # --------------------------------------------------------

    duration = _calculate_drive_duration(
        miles_float
    )

    # --------------------------------------------------------
    # Stops
    # --------------------------------------------------------

    stops_info = _build_multi_stop_string(
        data.get("pickups"),
        data.get("deliveries"),
    )

    # --------------------------------------------------------
    # Reefer temperature
    # --------------------------------------------------------

    temperature_set_raw = data.get(
        "temperature_set"
    )

    temperature_mode_raw = data.get(
        "temperature_mode"
    )

    temperature_notes_raw = data.get(
        "temperature_notes"
    )

    has_temperature_info = any(
        not _is_missing(value)
        for value in (
            temperature_set_raw,
            temperature_mode_raw,
            temperature_notes_raw,
        )
    )

    temperature_info = _build_temperature_info(
        temperature_set_raw,
        temperature_mode_raw,
        temperature_notes_raw,
    )

    # --------------------------------------------------------
    # Rendering context
    # --------------------------------------------------------

    clean_data = {
        "broker": _safe_text(
            data.get("broker")
        ),
        "load_number": _safe_text(
            data.get("load_number")
        ),
        "pu_number": _safe_text(
            data.get("pu_number")
        ),
        "del_number": _safe_text(
            data.get("del_number")
        ),
        "bol_number": _safe_text(
            data.get("bol_number")
        ),
        "ref_number": _safe_text(
            data.get("ref_number")
        ),
        "weight": _safe_text(
            data.get("weight")
        ),
        "rate": rate_display,
        "total_miles": miles_display,
        "per_mile": per_mile,
        "duration": duration,
        "stops_info": stops_info,

        # Reefer variables for default and custom templates
        "has_temperature_info": has_temperature_info,
        "temperature_set": _safe_text(
            temperature_set_raw
        ),
        "temperature_mode": _safe_text(
            temperature_mode_raw
        ),
        "temperature_notes": _safe_text(
            temperature_notes_raw
        ),
        "temperature_info": temperature_info,
    }

    template_string = (
        user_template
        if user_template
        else DEFAULT_TEMPLATE
    )

    # Compatibility with templates that manually added "mi".
    template_string = template_string.replace(
        "{{ total_miles }} mi",
        "{{ total_miles }}",
    )

    try:
        template = Template(
            template_string
        )

        rendered_text = template.render(
            **clean_data
        )

        # Remove excessive blank lines created by Jinja conditions.
        rendered_text = re.sub(
            r"\n[ \t]+\n",
            "\n\n",
            rendered_text,
        )

        rendered_text = re.sub(
            r"\n{3,}",
            "\n\n",
            rendered_text,
        )

        return rendered_text.strip()

    except exceptions.TemplateError as exc:
        error_message = html.escape(
            str(exc),
            quote=False,
        )

        return (
            "⚠️ <b>Template Error</b>\n\n"
            f"<code>{error_message}</code>\n\n"
            "Use /reset_template to restore the default format."
        )
