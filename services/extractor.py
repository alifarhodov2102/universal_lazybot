import asyncio
import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

import httpx

from config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_URL,
    MAX_CONCURRENT_AI,
)


logger = logging.getLogger("LazyAlice.Extractor")


# Current DeepSeek API model.
DEEPSEEK_MODEL = "deepseek-v4-flash"

# DeepSeek can occasionally take longer to finish sending a response,
# especially when the API is under load. Thinking mode is disabled in
# both requests because extraction and template generation do not need
# long reasoning.
DEEPSEEK_TEMPLATE_TIMEOUT = httpx.Timeout(
    connect=10.0,
    read=120.0,
    write=30.0,
    pool=10.0,
)

DEEPSEEK_EXTRACTION_TIMEOUT = httpx.Timeout(
    connect=10.0,
    read=120.0,
    write=30.0,
    pool=10.0,
)


# ============================================================
# Concurrency
# ============================================================

# Several PDFs can use DeepSeek simultaneously, but the number
# is limited to prevent API overload and Railway resource spikes.
AI_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_AI)

# Prevent several simultaneous PDF jobs from sending too many
# geocoding requests at exactly the same time.
GEOCODING_SEMAPHORE = asyncio.Semaphore(1)


# ============================================================
# Fallback regex patterns
# ============================================================

LOAD_RE = re.compile(
    r"(?:"
    r"Load\s*(?:Number|No\.?|#)|"
    r"PRO\s*#|"
    r"Order\s*#|"
    r"Reference\s*#|"
    r"Shipment\s*#|"
    r"Trip\s*#"
    r")"
    r"[:\s]*"
    r"([0-9A-Z][0-9A-Z\-]{3,29})",
    re.IGNORECASE,
)

RATE_RE = re.compile(
    r"(?:"
    r"Carrier\s+Freight\s+Pay|"
    r"Total\s+Carrier\s+Pay|"
    r"Total\s*(?:Carrier\s*)?Rate|"
    r"Total\s*Pay|"
    r"Rate\s*Total|"
    r"Base\s*Rate|"
    r"Linehaul|"
    r"Rate"
    r")"
    r"[:\s]*\$?\s*"
    r"([\d,]+(?:\.\d{1,2})?)",
    re.IGNORECASE,
)

MILES_RE = re.compile(
    r"(?:"
    r"Total\s*Miles|"
    r"Loaded\s*Miles|"
    r"Trip\s*Miles|"
    r"Distance|"
    r"Miles"
    r")"
    r"[:\s]*"
    r"([\d,]+(?:\.\d+)?)",
    re.IGNORECASE,
)


TOTAL_WEIGHT_RE = re.compile(
    r"(?:"
    r"Total\s*Weight|"
    r"Gross\s*Weight|"
    r"Shipment\s*Weight"
    r")"
    r"[:\s]*"
    r"([\d,]+(?:\.\d+)?)"
    r"\s*(?:lbs?|pounds?)?",
    re.IGNORECASE,
)

WEIGHT_RE = re.compile(
    r"\bWeight\b"
    r"[:\s]*"
    r"([\d,]+(?:\.\d+)?)"
    r"\s*(?:lbs?|pounds?)?",
    re.IGNORECASE,
)


# ============================================================
# Reefer temperature patterns
# ============================================================

TEMP_LABEL = (
    r"(?:"
    r"temp(?:erature)?"
    r"(?:\s*(?:set(?:ting)?|set[\s-]*point|required|requirement))?"
    r"|set[\s-]*point"
    r"|maintain(?:ed)?\s+at"
    r"|keep(?:\s+product)?\s+at"
    r"|reefer\s*temp(?:erature)?"
    r")"
)

TEMP_RANGE_RE = re.compile(
    rf"\b{TEMP_LABEL}\b"
    r"\s*(?:(?:[:=])\s*|-\s+)?"
    r"(-?\d{1,3}(?:\.\d+)?)"
    r"\s*(?:°|degrees?\s*)?([FC])?"
    r"\s*(?:to|through|thru|[-–—])\s*"
    r"(-?\d{1,3}(?:\.\d+)?)"
    r"\s*(?:°|degrees?\s*)?([FC])?\b",
    re.IGNORECASE,
)

TEMP_SINGLE_RE = re.compile(
    rf"\b{TEMP_LABEL}\b"
    r"\s*(?:(?:[:=])\s*|-\s+)?"
    r"(?:at\s*)?"
    r"(-?\d{1,3}(?:\.\d+)?)"
    r"\s*(?:°|degrees?\s*)?([FC])?\b",
    re.IGNORECASE,
)

TEMP_MODE_RE = re.compile(
    r"\b("
    r"continuous(?:\s+run)?"
    r"|start\s*[/\-]\s*stop"
    r"|cycle\s+sentry"
    r"|cycle"
    r")\b",
    re.IGNORECASE,
)

REEFER_SIGNAL_RE = re.compile(
    r"\b("
    r"reefer|"
    r"refrigerated|"
    r"temperature|"
    r"temp\s*set|"
    r"set[\s-]*point|"
    r"frozen|"
    r"chilled|"
    r"pre[\s-]*cool|"
    r"do\s+not\s+freeze|"
    r"continuous\s+run|"
    r"start\s*[/\-]\s*stop|"
    r"cycle\s+sentry"
    r")\b",
    re.IGNORECASE,
)

TEMPERATURE_NOTE_PATTERNS = (
    (
        re.compile(
            r"\bpre[\s-]*cool(?:ed|ing)?\s+required\b",
            re.IGNORECASE,
        ),
        "Pre-cool required",
    ),
    (
        re.compile(
            r"\bpre[\s-]*cool(?:ed|ing)?\b",
            re.IGNORECASE,
        ),
        "Pre-cool",
    ),
    (
        re.compile(
            r"\bdo\s+not\s+freeze\b",
            re.IGNORECASE,
        ),
        "Do not freeze",
    ),
    (
        re.compile(
            r"\bprotect\s+from\s+freez(?:e|ing)\b",
            re.IGNORECASE,
        ),
        "Protect from freezing",
    ),
    (
        re.compile(
            r"\bkeep\s+frozen\b",
            re.IGNORECASE,
        ),
        "Keep frozen",
    ),
    (
        re.compile(
            r"\bkeep\s+refrigerated\b",
            re.IGNORECASE,
        ),
        "Keep refrigerated",
    ),
)


EXPLICIT_TEMPERATURE_INSTRUCTION_RE = re.compile(
    r"(?:"
    r"\bdo\s+not\s+freeze\b|"
    r"\bprotect\s+from\s+freez(?:e|ing)\b|"
    r"\bkeep\s+(?:product\s+)?(?:frozen|refrigerated|chilled)\b|"
    r"\bpre[\s-]*cool(?:ed|ing)?(?:\s+required)?\b|"
    r"\breefer\b.{0,80}\b(?:continuous|start\s*[/\-]\s*stop|cycle\s+sentry)\b|"
    r"\b(?:continuous|start\s*[/\-]\s*stop|cycle\s+sentry)\b.{0,80}\breefer\b"
    r")",
    re.IGNORECASE | re.DOTALL,
)


# ============================================================
# General helpers
# ============================================================

def is_missing(value: Any) -> bool:
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


def clean_ai_json(content: str) -> Dict[str, Any]:
    """
    Parse DeepSeek JSON even when it includes Markdown fences.
    """
    cleaned = (
        content
        .replace("```json", "")
        .replace("```jinja2", "")
        .replace("```", "")
        .strip()
    )

    try:
        result = json.loads(cleaned)

    except json.JSONDecodeError:
        first_brace = cleaned.find("{")
        last_brace = cleaned.rfind("}")

        if first_brace == -1 or last_brace == -1:
            raise

        result = json.loads(
            cleaned[first_brace:last_brace + 1]
        )

    if not isinstance(result, dict):
        raise ValueError(
            "DeepSeek response was not a JSON object."
        )

    return result


def apply_defaults(
    data: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    result = data.copy() if isinstance(data, dict) else {}

    defaults = {
        "broker": "N/A",
        "load_number": "N/A",
        "weight": "N/A",
        "pu_number": "N/A",
        "del_number": "N/A",
        "bol_number": "N/A",
        "ref_number": "N/A",
        "pickups": [],
        "deliveries": [],
        "rate": "N/A",
        "total_miles": "N/A",
        "temperature_set": "N/A",
        "temperature_mode": "N/A",
        "temperature_notes": "N/A",
    }

    for key, default_value in defaults.items():
        if key not in result or result[key] is None:
            result[key] = default_value

    if not isinstance(result["pickups"], list):
        result["pickups"] = []

    if not isinstance(result["deliveries"], list):
        result["deliveries"] = []

    return result


def format_weight_value(raw_value: Any) -> str:
    """
    Normalize a numeric weight to a readable pounds value.
    """
    try:
        numeric = float(
            str(raw_value)
            .replace(",", "")
            .strip()
        )
    except (TypeError, ValueError):
        return "N/A"

    if numeric <= 0:
        return "N/A"

    if numeric.is_integer():
        return f"{int(numeric):,} lbs"

    return f"{numeric:,.1f} lbs"


def extract_weight_fallback(
    text: str,
) -> Tuple[str, bool]:
    """
    Return an explicit total weight when available.

    The boolean indicates whether the value came from a Total/Gross/
    Shipment Weight label and may safely override a smaller AI value.
    """
    total_match = TOTAL_WEIGHT_RE.search(text)

    if total_match:
        return (
            format_weight_value(total_match.group(1)),
            True,
        )

    regular_match = WEIGHT_RE.search(text)

    if regular_match:
        return (
            format_weight_value(regular_match.group(1)),
            False,
        )

    return "N/A", False


# ============================================================
# Temperature extraction
# ============================================================

def normalize_temperature_value(
    number: str,
    unit: Optional[str],
) -> str:
    number = number.strip()

    if unit:
        return f"{number}°{unit.upper()}"

    return f"{number} (unit not stated)"


def extract_temperature_fallback(
    text: str,
) -> Dict[str, str]:
    """
    Extract temperature details directly from labeled PDF text.

    The function does not assume Fahrenheit or Celsius when the
    document does not state the unit.
    """
    result = {
        "temperature_set": "N/A",
        "temperature_mode": "N/A",
        "temperature_notes": "N/A",
    }

    range_match = TEMP_RANGE_RE.search(text)

    if range_match:
        (
            low_value,
            low_unit,
            high_value,
            high_unit,
        ) = range_match.groups()

        resolved_low_unit = low_unit or high_unit
        resolved_high_unit = high_unit or low_unit

        if not resolved_low_unit and not resolved_high_unit:
            result["temperature_set"] = (
                f"{low_value} to {high_value} "
                f"(unit not stated)"
            )

        else:
            low = normalize_temperature_value(
                low_value,
                resolved_low_unit,
            )

            high = normalize_temperature_value(
                high_value,
                resolved_high_unit,
            )

            result["temperature_set"] = (
                f"{low} to {high}"
            )

    else:
        single_match = TEMP_SINGLE_RE.search(text)

        if single_match:
            number, unit = single_match.groups()

            result["temperature_set"] = (
                normalize_temperature_value(
                    number,
                    unit,
                )
            )

    has_reefer_signal = bool(
        REEFER_SIGNAL_RE.search(text)
        or result["temperature_set"] != "N/A"
    )

    if not has_reefer_signal:
        return result

    mode_match = TEMP_MODE_RE.search(text)

    if mode_match:
        raw_mode = mode_match.group(1).lower()

        if "continuous" in raw_mode:
            result["temperature_mode"] = "Continuous"

        elif "start" in raw_mode:
            result["temperature_mode"] = "Start/Stop"

        elif "sentry" in raw_mode:
            result["temperature_mode"] = "Cycle Sentry"

        else:
            result["temperature_mode"] = "Cycle"

    notes = []

    for pattern, normalized_note in TEMPERATURE_NOTE_PATTERNS:
        if (
            pattern.search(text)
            and normalized_note not in notes
        ):
            notes.append(normalized_note)

    if notes:
        result["temperature_notes"] = "; ".join(notes)

    return result


def build_temperature_info(
    data: Dict[str, Any],
) -> str:
    """
    Create a reusable block for custom templates.
    """
    lines = []

    temperature_set = data.get("temperature_set")
    temperature_mode = data.get("temperature_mode")
    temperature_notes = data.get("temperature_notes")

    if not is_missing(temperature_set):
        lines.append(
            f"SET: {temperature_set}"
        )

    if not is_missing(temperature_mode):
        lines.append(
            f"MODE: {temperature_mode}"
        )

    if not is_missing(temperature_notes):
        lines.append(
            f"NOTES: {temperature_notes}"
        )

    return "\n".join(lines)


def has_explicit_temperature_requirement(
    text: str,
    fallback: Dict[str, str],
) -> bool:
    """
    Accept temperature data only when the load contains an actual
    setpoint/range or a direct operational temperature instruction.

    Generic terms-and-conditions text mentioning "reefer temperature"
    does not count.
    """
    if not is_missing(
        fallback.get("temperature_set")
    ):
        return True

    return bool(
        EXPLICIT_TEMPERATURE_INSTRUCTION_RE.search(text)
    )


# ============================================================
# Custom template learning
# ============================================================

TEMPLATE_TEMPERATURE_RE = re.compile(
    r"\b(?:temperature|temp(?:erature)?\s*set|reefer)\b",
    re.IGNORECASE,
)


def _strip_unrequested_temperature_blocks(
    template_text: str,
) -> str:
    """
    Remove temperature-related Jinja blocks and labels when the user
    did not include a temperature section in their example.
    """
    lines = template_text.splitlines()
    kept_lines = []
    skip_depth = 0

    for line in lines:
        lowered = line.lower()

        if skip_depth > 0:
            if "{% if" in lowered:
                skip_depth += 1

            if "{% endif" in lowered:
                skip_depth -= 1

            continue

        if (
            "{% if" in lowered
            and (
                "temperature" in lowered
                or "has_temperature_info" in lowered
            )
        ):
            skip_depth = 1
            continue

        if any(
            token in lowered
            for token in (
                "temperature_set",
                "temperature_mode",
                "temperature_notes",
                "temperature_info",
                "has_temperature_info",
            )
        ):
            continue

        if re.search(
            r"\b(?:reefer\s+info|temp(?:erature)?\s+info|"
            r"temperature\s+set|temperature\s+mode|"
            r"temperature\s+notes)\b",
            line,
            re.IGNORECASE,
        ):
            continue

        kept_lines.append(line)

    return "\n".join(kept_lines)


def _fill_blank_template_labels(
    template_text: str,
) -> str:
    """
    Repair common blank labels generated from user examples.
    """
    replacements = (
        (
            r"(?im)^(\s*(?:<b>)?WEIGHT(?:</b>)?\s*:)\s*$",
            r"\1 {{ weight }}",
        ),
        (
            r"(?im)^(\s*(?:<b>)?TOTAL\s+MILES?(?:</b>)?\s*:)\s*$",
            r"\1 {{ total_miles }}",
        ),
        (
            r"(?im)^(\s*(?:<b>)?TOTAL\s+MILE(?:</b>)?\s*:)\s*$",
            r"\1 {{ total_miles }}",
        ),
        (
            r"(?im)^(\s*(?:<b>)?RATE(?:</b>)?\s*:)\s*$",
            r"\1 {{ rate }}",
        ),
    )

    for pattern, replacement in replacements:
        template_text = re.sub(
            pattern,
            replacement,
            template_text,
        )

    return template_text


def normalize_learned_template(
    template_text: str,
    user_example: str,
) -> str:
    """
    Keep the learned template faithful to the user's example.

    The renderer already supplies currency, mileage units, and weight
    units, so duplicated symbols around placeholders are removed.
    """
    normalized = (
        template_text
        .replace("```jinja2", "")
        .replace("```json", "")
        .replace("```", "")
        .strip()
    )

    normalized = re.sub(
        r"\$\s*\{\{\s*rate\s*\}\}",
        "{{ rate }}",
        normalized,
        flags=re.IGNORECASE,
    )

    normalized = re.sub(
        r"\{\{\s*total_miles\s*\}\}\s*(?:mi|miles?)\b",
        "{{ total_miles }}",
        normalized,
        flags=re.IGNORECASE,
    )

    normalized = re.sub(
        r"\{\{\s*weight\s*\}\}\s*(?:lbs?|pounds?)\b",
        "{{ weight }}",
        normalized,
        flags=re.IGNORECASE,
    )

    normalized = _fill_blank_template_labels(
        normalized
    )

    user_requested_temperature = bool(
        TEMPLATE_TEMPERATURE_RE.search(
            user_example
        )
    )

    if not user_requested_temperature:
        normalized = _strip_unrequested_temperature_blocks(
            normalized
        )

    normalized = re.sub(
        r"\n{3,}",
        "\n\n",
        normalized,
    )

    return normalized.strip()


async def extract_template_structure(
    system_prompt: str,
    user_example: str,
) -> str:
    """
    Learn the user's custom format or append simple notes to the
    default output template.
    """
    is_only_notes = not any(
        keyword in user_example.upper()
        for keyword in (
            "BROKER",
            "LOAD",
            "ID",
            "PU#",
        )
    )

    if is_only_notes:
        logger.info(
            "User sent notes only. Appending notes to default template."
        )

        default_skeleton = """
<b>{{ broker }}</b>

<b>Load#</b> <code>{{ load_number }}</code>
{% if ref_number and ref_number != 'N/A' %}<b>Ref#</b> <code>{{ ref_number }}</code>{% endif %}

{% if bol_number and bol_number != 'N/A' %}<b>BOL#</b> <code>{{ bol_number }}</code>{% endif %}
{% if pu_number and pu_number != 'N/A' %}<b>PU#</b> <code>{{ pu_number }}</code>{% endif %}
{% if del_number and del_number != 'N/A' %}<b>DEL#</b> <code>{{ del_number }}</code>{% endif %}

{{ stops_info }}
—————————————
<b>PER MILE:</b> {{ per_mile }}
<b>DURATION:</b> {{ duration }}

<b>WEIGHT:</b> {{ weight }}
<b>TOTAL MILES:</b> {{ total_miles }}
<b>RATE:</b> {{ rate }}

{% if temperature_set and temperature_set != 'N/A' %}
🌡 <b>TEMP INFO:</b>
<b>SET:</b> {{ temperature_set }}
{% if temperature_mode and temperature_mode != 'N/A' %}<b>MODE:</b> {{ temperature_mode }}{% endif %}
{% if temperature_notes and temperature_notes != 'N/A' %}<b>NOTES:</b> {{ temperature_notes }}{% endif %}
{% endif %}
"""

        return (
            f"{default_skeleton.strip()}\n\n"
            f"{user_example.strip()}"
        )

    if not DEEPSEEK_API_KEY:
        logger.warning(
            "DEEPSEEK_API_KEY is missing. "
            "Custom template learning was skipped."
        )
        return user_example

    instructions = (
        f"{system_prompt}\n\n"
        "Create a Jinja template that follows the user's example "
        "exactly.\n"
        "STRICT TEMPLATE RULES:\n"
        "1. Keep all fixed notes, warnings, emojis, separators, "
        "wording, and line order exactly as provided.\n"
        "2. Replace all pickup and delivery blocks with "
        "{{ stops_info }}.\n"
        "3. Use only fields and sections that are visibly present "
        "in the user's example. Do not add any new section.\n"
        "4. If the example has a blank WEIGHT label, use "
        "{{ weight }} after it.\n"
        "5. Use {{ rate }} without adding a dollar sign because "
        "the renderer already includes $.\n"
        "6. Use {{ total_miles }} without adding mi or miles "
        "because the renderer already includes the unit.\n"
        "7. Use {{ weight }} without adding lbs because the "
        "renderer already includes the unit.\n"
        "8. Never add Reefer Info, TEMP INFO, temperature fields, "
        "or temperature variables unless the user's example itself "
        "contains a temperature or reefer section.\n"
        "9. Do not append a list of available variables.\n"
        "Return only the Jinja template without Markdown fences."
    )

    async with AI_SEMAPHORE:
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    DEEPSEEK_URL,
                    headers={
                        "Authorization": (
                            f"Bearer {DEEPSEEK_API_KEY}"
                        ),
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": DEEPSEEK_MODEL,
                        "messages": [
                            {
                                "role": "system",
                                "content": instructions,
                            },
                            {
                                "role": "user",
                                "content": user_example,
                            },
                        ],
                        "thinking": {
                            "type": "disabled",
                        },
                        "temperature": 0.1,
                        "max_tokens": 2000,
                    },
                    timeout=DEEPSEEK_TEMPLATE_TIMEOUT,
                )

                response.raise_for_status()

                payload = response.json()

                skeleton = (
                    payload["choices"][0]["message"]["content"]
                    .replace("```jinja2", "")
                    .replace("```json", "")
                    .replace("```", "")
                    .strip()
                )

                if not skeleton:
                    return user_example

                return normalize_learned_template(
                    skeleton,
                    user_example,
                )

            except httpx.TimeoutException:
                logger.error(
                    "Template extraction timed out after 120 seconds."
                )

                return user_example

            except httpx.HTTPStatusError as exc:
                logger.error(
                    "DeepSeek template HTTP error %s: %s",
                    exc.response.status_code,
                    exc.response.text[:500],
                )

                return user_example

            except Exception as exc:
                logger.exception(
                    "Template extraction failed: %s",
                    exc,
                )

                return user_example


# ============================================================
# Address and mileage calculation
# ============================================================

async def fetch_coords(
    address: str,
    client: httpx.AsyncClient,
) -> Optional[Tuple[str, str]]:
    url = "https://nominatim.openstreetmap.org/search"

    params = {
        "q": address,
        "format": "json",
        "limit": 1,
    }

    try:
        async with GEOCODING_SEMAPHORE:
            response = await client.get(
                url,
                params=params,
                headers={
                    "User-Agent": "LazyBot_Logistics/2.0",
                },
                timeout=15.0,
            )

        response.raise_for_status()

        results = response.json()

        if results:
            return (
                results[0]["lat"],
                results[0]["lon"],
            )

    except Exception as exc:
        logger.warning(
            "Geocoding failed for %r: %s",
            address,
            exc,
        )

    return None


def clean_address(
    address: str,
    level: int = 0,
) -> str:
    cleaned = re.sub(
        r"^(?:"
        r"FMC|"
        r"JASPER|"
        r"ARMSTRONG|"
        r"PLANT\s+\d+|"
        r"DC|"
        r"RESUPPLY|"
        r"FPDC|"
        r"WAREHOUSE|"
        r"LOGISTICS|"
        r"NAME:|"
        r"ADDRESS:"
        r")\s+",
        "",
        str(address),
        flags=re.IGNORECASE,
    )

    parts = [
        part.strip()
        for part in cleaned.replace("\n", ",").split(",")
        if part.strip()
    ]

    unique_parts = []

    for part in parts:
        if part not in unique_parts:
            unique_parts.append(part)

    if level == 1 and len(unique_parts) > 2:
        return ", ".join(unique_parts[-3:])

    if level == 2 and len(unique_parts) >= 2:
        return ", ".join(unique_parts[-2:])

    return ", ".join(unique_parts)


async def get_miles_free(
    origin: str,
    destination: str,
) -> str:
    if not origin or not destination:
        return "N/A"

    async with httpx.AsyncClient() as client:
        origin_coords = None
        destination_coords = None

        for level in range(3):
            if not origin_coords:
                origin_coords = await fetch_coords(
                    clean_address(origin, level),
                    client,
                )

            if not destination_coords:
                destination_coords = await fetch_coords(
                    clean_address(destination, level),
                    client,
                )

            if origin_coords and destination_coords:
                break

        if not origin_coords or not destination_coords:
            return "N/A"

        try:
            url = (
                "https://router.project-osrm.org/"
                "route/v1/driving/"
                f"{origin_coords[1]},{origin_coords[0]};"
                f"{destination_coords[1]},{destination_coords[0]}"
            )

            response = await client.get(
                url,
                params={
                    "overview": "false",
                },
                timeout=15.0,
            )

            response.raise_for_status()

            routes = response.json().get(
                "routes",
                [],
            )

            if not routes:
                return "N/A"

            meters = float(
                routes[0]["distance"]
            )

            miles = meters * 0.000621371

            return str(
                round(miles, 1)
            )

        except Exception as exc:
            logger.warning(
                "OSRM route calculation failed: %s",
                exc,
            )

    return "N/A"


# ============================================================
# DeepSeek extraction
# ============================================================

async def deepseek_ai_extract(
    text: str,
) -> Optional[Dict[str, Any]]:
    if not DEEPSEEK_API_KEY:
        logger.error(
            "DEEPSEEK_API_KEY is missing. "
            "AI extraction was skipped."
        )

        return None

    prompt = f"""
Analyze this US Logistics Rate Confirmation.

RETURN ONLY ONE VALID JSON OBJECT.

STRICT RULES:

1. BROKER
Extract the actual broker or logistics company name.

2. LOAD ID
Extract the Load Number, Load ID, PRO #, Order #, Shipment #,
or Trip #. Do not use phone numbers, dates, invoice numbers,
or rates.

3. WEIGHT
Extract the total shipment weight in pounds only when explicitly
stated. Do not calculate or guess it.

4. REFERENCES
Extract PU#, DEL#, BOL#, PO#, and Ref# only when explicitly
stated. Do not use phone numbers, appointment times, ZIP codes,
or dates.

5. STOPS
Capture every pickup and every delivery in actual route order.
Include the facility name, complete address, date, and time.
Do not invent missing information.

6. RATE
Extract the total carrier payment. Do not use fuel surcharge,
detention, lumper, or per-mile amounts as the total rate.

7. MILES
Extract total miles or loaded miles only when explicitly stated.

8. REEFER TEMPERATURE
Extract the exact temperature setpoint or temperature range.
Preserve negative values.
Preserve Fahrenheit or Celsius exactly as stated.
When a temperature is stated without a unit, append:
(unit not stated)

Extract the operating mode only when explicitly stated:
Continuous, Start/Stop, Cycle, or Cycle Sentry.

Extract short notes such as:
Pre-cool required
Do not freeze
Keep frozen
Keep refrigerated

For a dry load or when temperature is absent, return N/A.
Never infer temperature from the commodity.

JSON SCHEMA:

{{
  "broker": "Company Name Only",
  "load_number": "N/A",
  "weight": "N/A",
  "pu_number": "N/A",
  "del_number": "N/A",
  "bol_number": "N/A",
  "ref_number": "N/A",
  "pickups": [
    {{
      "facility": "Name",
      "address": "Full Address",
      "time": "MM/DD/YYYY HH:MM"
    }}
  ],
  "deliveries": [
    {{
      "facility": "Name",
      "address": "Full Address",
      "time": "MM/DD/YYYY HH:MM"
    }}
  ],
  "rate": "N/A",
  "total_miles": "N/A",
  "temperature_set": "N/A",
  "temperature_mode": "N/A",
  "temperature_notes": "N/A"
}}

RATE CONFIRMATION TEXT:

{text[:12000]}
""".strip()

    logger.info(
        "Waiting for a DeepSeek processing slot."
    )

    async with AI_SEMAPHORE:
        logger.info(
            "DeepSeek processing slot acquired."
        )

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    DEEPSEEK_URL,
                    headers={
                        "Authorization": (
                            f"Bearer {DEEPSEEK_API_KEY}"
                        ),
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": DEEPSEEK_MODEL,
                        "messages": [
                            {
                                "role": "system",
                                "content": (
                                    "You are a US logistics rate "
                                    "confirmation specialist. "
                                    "Extract only information explicitly "
                                    "present in the document. Return only "
                                    "valid JSON."
                                ),
                            },
                            {
                                "role": "user",
                                "content": prompt,
                            },
                        ],
                        "thinking": {
                            "type": "disabled",
                        },
                        "response_format": {
                            "type": "json_object",
                        },
                        "temperature": 0,
                        "max_tokens": 3000,
                    },
                    timeout=DEEPSEEK_EXTRACTION_TIMEOUT,
                )

                response.raise_for_status()

                payload = response.json()

                content = (
                    payload["choices"][0]
                    ["message"]["content"]
                )

                return clean_ai_json(content)

            except httpx.TimeoutException:
                logger.error(
                    "DeepSeek extraction timed out after 120 seconds."
                )

            except httpx.HTTPStatusError as exc:
                logger.error(
                    "DeepSeek HTTP error %s: %s",
                    exc.response.status_code,
                    exc.response.text[:500],
                )

            except Exception as exc:
                logger.exception(
                    "DeepSeek extraction failed: %s",
                    exc,
                )

    return None


# ============================================================
# Missing-stop fallback for Hwy Haul-style PDFs
# ============================================================

STOP_SECTION_RE = re.compile(
    r"(?im)^\s*(Pickup|Dropoff)\s*$"
)

STOP_END_RE = re.compile(
    r"(?im)^\s*(?:Pickup|Dropoff|Policies\s*&\s*Agreement)\s*$"
)

ADDRESS_TWO_LINE_RE = re.compile(
    r"(?P<street>\d{1,6}\s+[^\n]+?)\s*,?\s*\n"
    r"(?P<city>[A-Za-z0-9 .'\-]+,\s*[A-Z]{2}\s+\d{5}(?:-\d{4})?)",
    re.IGNORECASE,
)

ADDRESS_ONE_LINE_RE = re.compile(
    r"(?P<full>"
    r"\d{1,6}\s+[^,\n]+,\s*"
    r"[A-Za-z0-9 .'\-]+,\s*"
    r"[A-Z]{2}\s+\d{5}(?:-\d{4})?"
    r")",
    re.IGNORECASE,
)

APPOINTMENT_RE = re.compile(
    r"Appointment\s+Date\s*&\s*Time\s*"
    r"(?:\n|\s)+"
    r"(?P<date>[A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})"
    r"\s+at\s+"
    r"(?P<time>\d{1,2}:\d{2})",
    re.IGNORECASE,
)


def _format_appointment_datetime(
    date_value: str,
    time_value: str,
) -> str:
    """
    Convert 'Oct 25, 2024' into '10/25/2024'.

    If parsing fails, preserve the text instead of guessing.
    """
    try:
        parsed_date = datetime.strptime(
            date_value.strip(),
            "%b %d, %Y",
        )

        return (
            f"{parsed_date.strftime('%m/%d/%Y')} "
            f"{time_value.strip()}"
        )

    except ValueError:
        return (
            f"{date_value.strip()} "
            f"{time_value.strip()}"
        )


def _extract_stop_from_section(
    section_text: str,
) -> Optional[Dict[str, str]]:
    """
    Extract one facility, address, and appointment from a labeled
    Pickup or Dropoff section.
    """
    lines = [
        line.strip()
        for line in section_text.splitlines()
        if line.strip()
    ]

    if not lines:
        return None

    # In Hwy Haul PDFs, the first non-empty line after the heading
    # is the facility name.
    facility = lines[0]

    address = ""

    two_line_address = ADDRESS_TWO_LINE_RE.search(
        section_text
    )

    if two_line_address:
        street = (
            two_line_address.group("street")
            .strip()
            .rstrip(",")
        )

        city = (
            two_line_address.group("city")
            .strip()
        )

        address = f"{street}, {city}"

    else:
        one_line_address = ADDRESS_ONE_LINE_RE.search(
            section_text
        )

        if one_line_address:
            address = (
                one_line_address.group("full")
                .strip()
            )

    appointment = ""

    appointment_match = APPOINTMENT_RE.search(
        section_text
    )

    if appointment_match:
        appointment = _format_appointment_datetime(
            appointment_match.group("date"),
            appointment_match.group("time"),
        )

    # Do not create a useless stop when the section did not
    # contain any actual location information.
    if not facility and not address:
        return None

    return {
        "facility": facility or "N/A",
        "address": address,
        "time": appointment,
    }


def extract_hwyhaul_stops_fallback(
    text: str,
) -> Dict[str, list]:
    """
    Recover pickup and dropoff data from Hwy Haul-style PDFs.

    This runs only as a backup. Valid AI stops are never overwritten.
    It supports multiple Pickup and Dropoff sections when present.
    """
    result = {
        "pickups": [],
        "deliveries": [],
    }

    normalized_text = (
        str(text)
        .replace("\r\n", "\n")
        .replace("\r", "\n")
    )

    section_matches = list(
        STOP_SECTION_RE.finditer(normalized_text)
    )

    for index, match in enumerate(section_matches):
        stop_type = match.group(1).lower()
        section_start = match.end()

        if index + 1 < len(section_matches):
            section_end = section_matches[index + 1].start()
        else:
            trailing_end = STOP_END_RE.search(
                normalized_text,
                section_start,
            )

            section_end = (
                trailing_end.start()
                if trailing_end
                else len(normalized_text)
            )

        section_text = normalized_text[
            section_start:section_end
        ].strip()

        stop = _extract_stop_from_section(
            section_text
        )

        if not stop:
            continue

        if stop_type == "pickup":
            result["pickups"].append(stop)
        else:
            result["deliveries"].append(stop)

    return result


# ============================================================
# Main pipeline
# ============================================================

async def smart_extract(
    text: str,
) -> Dict[str, Any]:
    logger.info(
        "Starting multi-stop extraction pipeline."
    )

    data = apply_defaults(
        await deepseek_ai_extract(text)
    )

    # Deterministic stop fallback for Hwy Haul-style documents.
    # It only fills a missing side and never overwrites valid AI stops.
    fallback_stops = extract_hwyhaul_stops_fallback(text)

    if (
        not data.get("pickups")
        and fallback_stops["pickups"]
    ):
        data["pickups"] = fallback_stops["pickups"]

        logger.info(
            "Recovered %s pickup stop(s) with deterministic fallback.",
            len(fallback_stops["pickups"]),
        )

    if (
        not data.get("deliveries")
        and fallback_stops["deliveries"]
    ):
        data["deliveries"] = fallback_stops["deliveries"]

        logger.info(
            "Recovered %s delivery stop(s) with deterministic fallback.",
            len(fallback_stops["deliveries"]),
        )

    # Weight fallback
    fallback_weight, is_explicit_total_weight = (
        extract_weight_fallback(text)
    )

    if (
        is_explicit_total_weight
        and not is_missing(fallback_weight)
    ):
        # An explicitly labeled total is more reliable than a
        # single-order or single-stop weight selected by AI.
        data["weight"] = fallback_weight

    elif (
        is_missing(data.get("weight"))
        and not is_missing(fallback_weight)
    ):
        data["weight"] = fallback_weight

    # Load number fallback
    load_match = LOAD_RE.search(text)

    if (
        load_match
        and is_missing(data.get("load_number"))
    ):
        data["load_number"] = load_match.group(1)

    # Rate fallback
    rate_match = RATE_RE.search(text)

    if (
        rate_match
        and is_missing(data.get("rate"))
    ):
        data["rate"] = rate_match.group(1)

    # Miles fallback
    miles_match = MILES_RE.search(text)

    if (
        miles_match
        and is_missing(data.get("total_miles"))
    ):
        data["total_miles"] = (
            miles_match.group(1)
            .replace(",", "")
        )

    # Temperature fallback
    fallback_temperature = (
        extract_temperature_fallback(text)
    )

    for field_name in (
        "temperature_set",
        "temperature_mode",
        "temperature_notes",
    ):
        fallback_value = (
            fallback_temperature[field_name]
        )

        if not is_missing(fallback_value):
            # Direct labeled PDF extraction has priority over AI.
            data[field_name] = fallback_value

    # Prevent generic contracts and legal language from creating
    # a fake temperature section for a dry or unspecified load.
    if not has_explicit_temperature_requirement(
        text,
        fallback_temperature,
    ):
        data["temperature_set"] = "N/A"
        data["temperature_mode"] = "N/A"
        data["temperature_notes"] = "N/A"

    data["temperature_info"] = (
        build_temperature_info(data)
    )

    # Calculate cumulative mileage only when PDF did not state it.
    if is_missing(data.get("total_miles")):
        pickups = data.get(
            "pickups",
            [],
        )

        deliveries = data.get(
            "deliveries",
            [],
        )

        all_stops = pickups + deliveries

        if len(all_stops) >= 2:
            total_cumulative_miles = 0.0
            valid_segments = 0

            logger.info(
                "Calculating cumulative mileage for %s stops.",
                len(all_stops),
            )

            for index in range(
                len(all_stops) - 1
            ):
                origin = str(
                    all_stops[index].get(
                        "address",
                        "",
                    )
                ).strip()

                destination = str(
                    all_stops[index + 1].get(
                        "address",
                        "",
                    )
                ).strip()

                if not origin or not destination:
                    logger.warning(
                        "Skipping mileage leg %s because "
                        "an address is missing.",
                        index + 1,
                    )

                    continue

                logger.info(
                    "Calculating leg %s: %s -> %s",
                    index + 1,
                    origin,
                    destination,
                )

                segment_miles = await get_miles_free(
                    origin,
                    destination,
                )

                if segment_miles == "N/A":
                    continue

                try:
                    total_cumulative_miles += float(
                        segment_miles
                    )

                    valid_segments += 1

                except ValueError:
                    logger.warning(
                        "Invalid mileage returned for "
                        "leg %s: %r",
                        index + 1,
                        segment_miles,
                    )

            if valid_segments > 0:
                data["total_miles"] = str(
                    round(
                        total_cumulative_miles,
                        1,
                    )
                )

                logger.info(
                    "Calculated total mileage: %s",
                    data["total_miles"],
                )

    return data
