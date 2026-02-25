import os
import re
import json
import httpx
import logging
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Extractor")

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"

# 1. Regex Patterns for US Logistics Fallback
LOAD_RE = re.compile(r"(?:Load\s*#|Order\s*#|Reference\s*#|PRO\s*#)[:\s]*([0-9]{5,10})", re.I)
RATE_RE = re.compile(r"(?:Total\s*Carrier\s*Pay|Total\s*Pay|Flat\s*Rate|Rate)[:\s]*\$?\s*([\d,]+\.\d{2})", re.I)
MILES_RE = re.compile(r"(?:Total\s*Miles|Distance|Miles)[:\s]*([\d.,]+)", re.I)

async def extract_template_structure(system_prompt: str, user_example: str) -> str:
    """
    Alice uses her AI brain to turn a driver's real load message into a 
    clean skeleton/template for future use. 🧠💅
    """
    if not DEEPSEEK_API_KEY:
        logger.error("DEEPSEEK_API_KEY missing!")
        return user_example

    logger.info("DeepSeek: Cleaning example text into a skeleton structure... 💅")
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                DEEPSEEK_URL,
                headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": "deepseek-chat",
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_example}
                    ],
                    "temperature": 0.1
                },
                timeout=30.0
            )
            result = response.json()
            skeleton = result['choices'][0]['message']['content'].strip()
            # Remove any markdown code blocks the AI might add
            return skeleton.replace("```jinja2", "").replace("```json", "").replace("```", "").strip()
        except Exception as e:
            logger.error(f"Template Extraction Error: {e}")
            return user_example

def regex_extract(text: str) -> dict:
    data = {
        "broker": "",
        "load_number": "",
        "pickups": [],
        "deliveries": [],
        "rate": "",
        "total_miles": ""
    }
    
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if lines:
        data["broker"] = " ".join(lines[:3])[:100]

    if load_match := LOAD_RE.search(text):
        data["load_number"] = load_match.group(1)

    if rate_match := RATE_RE.search(text):
        data["rate"] = rate_match.group(1)

    if miles_match := MILES_RE.search(text):
        data["total_miles"] = miles_match.group(1)
        
    return data

async def get_miles_free(origin: str, destination: str) -> str:
    if not origin or not destination: return ""
    logger.info(f"OSRM: Calculating miles... my poor brain 🧠")
    try:
        async with httpx.AsyncClient() as client:
            async def get_coords(addr):
                url = f"[https://nominatim.openstreetmap.org/search?q=](https://nominatim.openstreetmap.org/search?q=){addr}&format=json&limit=1"
                r = await client.get(url, headers={"User-Agent": "LazyBot_Logistics/2.0"}, timeout=10)
                if r.status_code == 200 and r.json():
                    return r.json()[0]["lat"], r.json()[0]["lon"]
                return None

            o_coords = await get_coords(origin)
            d_coords = await get_coords(destination)

            if o_coords and d_coords:
                osrm_url = f"[http://router.project-osrm.org/route/v1/driving/](http://router.project-osrm.org/route/v1/driving/){o_coords[1]},{o_coords[0]};{d_coords[1]},{d_coords[0]}?overview=false"
                res = await client.get(osrm_url, timeout=10)
                if res.status_code == 200:
                    meters = res.json()["routes"][0]["distance"]
                    return str(round(meters * 0.000621371, 1))
    except Exception as e:
        logger.error(f"OSRM Error: {e}")
    return ""

async def deepseek_ai_extract(text: str) -> dict:
    if not DEEPSEEK_API_KEY: return None
    full_text = text[:12000]

    prompt = f"""
Analyze this US Logistics Rate Confirmation. 
Extract data with high precision. RETURN ONLY VALID JSON.

Guidelines:
1. BROKER: Company name at the top (e.g., RYAN TRANSPORTATION, ECHO).
2. LOAD_NUMBER: 'Load #', 'Order #', or 'Reference #'.
3. RATE: 'Total Carrier Pay' or 'Total:'.
4. STOPS: 
   - PU (Pickups): 'PU 1', 'Shipper', or 'Origin'.
   - DEL (Deliveries): 'SO 1', 'Consignee', or 'Destination'.
   - Extract: Facility Name, Full Address, and Appointment Time.

Return this JSON structure:
{{
  "broker": "Full Legal Company Name",
  "load_number": "Numeric ID only",
  "pickups": [{{ "facility": "Name", "address": "Full Address", "time": "Date/Time" }}],
  "deliveries": [{{ "facility": "Name", "address": "Full Address", "time": "Date/Time" }}],
  "rate": "Total amount",
  "total_miles": "Distance"
}}

TEXT TO ANALYZE:
{full_text}
"""
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                DEEPSEEK_URL, 
                headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": "deepseek-chat",
                    "messages": [
                        {"role": "system", "content": "You are a US Logistics Specialist. Extract only business data into JSON."},
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": 0
                },
                timeout=60.0
            )
            content = response.json()['choices'][0]['message']['content']
            clean_json = content.replace("```json", "").replace("```", "").strip()
            return json.loads(clean_json)
        except Exception as e:
            logger.error(f"DeepSeek AI Error: {e}")
            return None

async def smart_extract(text: str) -> dict:
    logger.info("Starting Extraction Pipeline... 💅")
    data = await deepseek_ai_extract(text)
    
    if not data:
        data = regex_extract(text)
    
    if not data.get("total_miles") or data["total_miles"] in ["", "N/A", "0"]:
        if data.get("pickups") and data.get("deliveries"):
            origin = data["pickups"][0]["address"]
            dest = data["deliveries"][-1]["address"]
            miles = await get_miles_free(origin, dest)
            data["total_miles"] = miles
            
    return data