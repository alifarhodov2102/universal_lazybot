import asyncio
import json
import logging
import re
from typing import Any, Dict, Optional, Tuple

import httpx

from config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_URL,
    MAX_CONCURRENT_AI,
)


logger = logging.getLogger("LazyAlice.Extractor")


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


# ============================================================
# Custom template learning
# ============================================================

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
        "Replace all pickup and delivery stops with "
        "{{ stops_info }}.\n"
        "For reefer information, use these variables:\n"
        "{{ temperature_set }}\n"
        "{{ temperature_mode }}\n"
        "{{ temperature_notes }}\n"
        "{{ temperature_info }}\n"
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
                        "model": "deepseek-chat",
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
                        "temperature": 0.1,
                    },
                    timeout=30.0,
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

                return skeleton or user_example

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
                        "model": "deepseek-chat",
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
                        "temperature": 0,
                    },
                    timeout=60.0,
                )

                response.raise_for_status()

                payload = response.json()

                content = (
                    payload["choices"][0]
                    ["message"]["content"]
                )

                return clean_ai_json(content)

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

    # Prevent AI from inventing reefer information for dry loads.
    if not REEFER_SIGNAL_RE.search(text):
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
