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
        "0.0",
        "0.00",
    }


def _safe_text(
    value: Any,
    default: str = "N/A",
) -> str:
    """
    Escape extracted PDF text before inserting it into Telegram HTML.
    """
    if _is_missing(value):
        return default

    return html.escape(
        str(value).strip(),
        quote=False,
    )


def _parse_number(value: Any) -> Optional[float]:
    """
    Convert values such as '$2,500.00', '1,858 mi',
    or '70,000 lbs' into a float.
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


def _format_weight(value: Any) -> str:
    """
    Format an explicitly extracted shipment weight.

    The extraction pipeline is expected to return pounds. If another
    unit is explicitly present, preserve the original value rather
    than converting or guessing.
    """
    if _is_missing(value):
        return "N/A"

    raw_value = str(value).strip()
    normalized = raw_value.lower()

    if re.search(
        r"\b(?:kg|kgs|kilogram|kilograms)\b",
        normalized,
    ):
        return _safe_text(raw_value)

    weight_float = _parse_number(raw_value)

    if weight_float is None or weight_float <= 0:
        return _safe_text(raw_value)

    if weight_float.is_integer():
        number_display = f"{weight_float:,.0f}"
    else:
        number_display = f"{weight_float:,.1f}"

    return f"{number_display} lbs"


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
        "<code>"
        f"{html.escape(display_address, quote=False)}"
        "</code>"
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
# Custom-template normalization
# ============================================================

def _normalize_template(
    template_string: str,
) -> str:
    """
    Repair common mistakes produced by learned custom templates.

    Examples:

    RATE: ${{ rate }}
    becomes:
    RATE: {{ rate }}

    TOTAL MILES: {{ total_miles }} mi
    becomes:
    TOTAL MILES: {{ total_miles }}

    WEIGHT: {{ weight }} lbs
    becomes:
    WEIGHT: {{ weight }}

    WEIGHT:
    becomes:
    WEIGHT: {{ weight }}
    """
    normalized = str(template_string)

    # The renderer already formats the rate with "$".
    normalized = re.sub(
        r"\$\s*\{\{\s*rate\s*\}\}",
        "{{ rate }}",
        normalized,
        flags=re.IGNORECASE,
    )

    # The renderer already adds mileage units.
    normalized = re.sub(
        r"\{\{\s*total_miles\s*\}\}\s*"
        r"(?:mi|mile|miles)\b",
        "{{ total_miles }}",
        normalized,
        flags=re.IGNORECASE,
    )

    # The renderer already adds weight units.
    normalized = re.sub(
        r"\{\{\s*weight\s*\}\}\s*"
        r"(?:lb|lbs|pound|pounds)\b",
        "{{ weight }}",
        normalized,
        flags=re.IGNORECASE,
    )

    # The renderer already formats per-mile as "$X.XX/mi".
    normalized = re.sub(
        r"\{\{\s*per_mile\s*\}\}\s*/?\s*mi\b",
        "{{ per_mile }}",
        normalized,
        flags=re.IGNORECASE,
    )

    # Repair blank WEIGHT lines.
    normalized = re.sub(
        r"(?im)^("
        r"\s*(?:<b>)?\s*"
        r"WEIGHT"
        r"\s*(?:</b>)?\s*:"
        r")\s*$",
        r"\1 {{ weight }}",
        normalized,
    )

    # Repair blank TOTAL MILE / TOTAL MILES lines.
    normalized = re.sub(
        r"(?im)^("
        r"\s*(?:<b>)?\s*"
        r"TOTAL\s+MILES?"
        r"\s*(?:</b>)?\s*:"
        r")\s*$",
        r"\1 {{ total_miles }}",
        normalized,
    )

    # Repair blank RATE lines.
    normalized = re.sub(
        r"(?im)^("
        r"\s*(?:<b>)?\s*"
        r"RATE"
        r"\s*(?:</b>)?\s*:"
        r")\s*$",
        r"\1 {{ rate }}",
        normalized,
    )

    # Repair blank PER MILE lines.
    normalized = re.sub(
        r"(?im)^("
        r"\s*(?:<b>)?\s*"
        r"PER\s+MILE"
        r"\s*(?:</b>)?\s*:"
        r")\s*$",
        r"\1 {{ per_mile }}",
        normalized,
    )

    # Repair blank DURATION lines.
    normalized = re.sub(
        r"(?im)^("
        r"\s*(?:<b>)?\s*"
        r"DURATION"
        r"\s*(?:</b>)?\s*:"
        r")\s*$",
        r"\1 {{ duration }}",
        normalized,
    )

    return normalized


def _remove_empty_temperature_block(
    rendered_text: str,
) -> str:
    """
    Remove old custom-template temperature blocks when the load has
    no actual temperature information.

    This cleans templates previously saved in the database that
    unconditionally print N/A reefer fields.
    """
    lines = rendered_text.splitlines()
    cleaned_lines = []
    index = 0

    heading_pattern = re.compile(
        r"^\s*(?:🌡\s*)?"
        r"(?:reefer|temp(?:erature)?)"
        r"(?:\s+info(?:rmation)?)?"
        r"\s*:\s*$",
        re.IGNORECASE,
    )

    field_pattern = re.compile(
        r"^\s*"
        r"(?:temperature\s*)?"
        r"(?:set|mode|notes?|info)"
        r"\s*:\s*"
        r"(?:N/A|NA|NONE|NULL)?"
        r"\s*$",
        re.IGNORECASE,
    )

    while index < len(lines):
        current_line = lines[index]

        if not heading_pattern.match(current_line):
            cleaned_lines.append(current_line)
            index += 1
            continue

        block_end = index + 1
        field_count = 0

        while (
            block_end < len(lines)
            and field_pattern.match(lines[block_end])
        ):
            field_count += 1
            block_end += 1

        if field_count > 0:
            # Skip the empty heading and all empty N/A fields.
            index = block_end
            continue

        cleaned_lines.append(current_line)
        index += 1

    return "\n".join(cleaned_lines)


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
    # Weight
    # --------------------------------------------------------

    weight_display = _format_weight(
        data.get("weight")
    )

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
        "weight": weight_display,
        "rate": rate_display,
        "total_miles": miles_display,
        "per_mile": per_mile,
        "duration": duration,
        "stops_info": stops_info,

        # Reefer variables for default and custom templates.
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

    template_string = _normalize_template(
        template_string
    )

    try:
        template = Template(
            template_string
        )

        rendered_text = template.render(
            **clean_data
        )

        # Remove empty reefer sections from old custom templates.
        if not has_temperature_info:
            rendered_text = _remove_empty_temperature_block(
                rendered_text
            )

        # Remove lines containing only spaces or tabs.
        rendered_text = re.sub(
            r"\n[ \t]+\n",
            "\n\n",
            rendered_text,
        )

        # Remove excessive blank lines created by Jinja conditions.
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
