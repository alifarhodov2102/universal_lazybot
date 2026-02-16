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

# 1. Regex Patterns
LOAD_RE = re.compile(r"(?:PRO\s*#|Load\s*#|Order\s*#|Reference\s*#|#)[:\s]*([A-Z0-9-]+)", re.I)
RATE_RE = re.compile(r"(?:TOTAL\s*RATE|LINE\s*HAUL\s*RATE|Total\s*Pay|Rate)[:\s]*\$?\s*([\d,]+\.\d{2})", re.I)
MILES_RE = re.compile(r"(?:Miles|Distance|Total\s*Miles)[:\s]*(\d+)", re.I)

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
        data["broker"] = lines[0][:60]

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
                url = f"https://nominatim.openstreetmap.org/search?q={addr}&format=json&limit=1"
                r = await client.get(url, headers={"User-Agent": "LazyBot/1.0"}, timeout=10)
                if r.status_code == 200 and r.json():
                    return r.json()[0]["lat"], r.json()[0]["lon"]
                return None

            o_coords = await get_coords(origin)
            d_coords = await get_coords(destination)

            if o_coords and d_coords:
                osrm_url = f"http://router.project-osrm.org/route/v1/driving/{o_coords[1]},{o_coords[0]};{d_coords[1]},{d_coords[0]}?overview=false"
                res = await client.get(osrm_url, timeout=10)
                if res.status_code == 200:
                    meters = res.json()["routes"][0]["distance"]
                    return str(round(meters * 0.000621371, 1))
    except Exception as e:
        logger.error(f"OSRM Error: {e}")
        return ""
    return ""

async def deepseek_ai_extract(text: str) -> dict:
    if not DEEPSEEK_API_KEY: return None
    
    full_text = text[:15000]

    prompt = f"""
Analyze this logistics Rate Confirmation carefully. 
Extract ALL information and return ONLY valid JSON.

JSON structure:
{{
  "broker": "Full company name",
  "load_number": "Main Load# (look for #NUMBER at top)",
  "pickups": [{{ "facility": "", "address": "", "time": "" }}],
  "deliveries": [{{ "facility": "", "address": "", "time": "" }}],
  "rate": "Total money amount (Look for 'Line Haul - Flat Rate' or 'Total:', ignore 'Amount: 1')",
  "total_miles": "Total miles"
}}

Rules:
1. RATE: Values like 3000.00 are rates. 
2. STOPS: 'PICK 1' is pickup, 'STOP 1' is delivery. 
3. MILES: Look for 'Miles:' (e.g., 769).

TEXT:
{full_text} 
"""
    logger.info("DeepSeek request sent... hope it's not too long 🙄")
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                DEEPSEEK_URL, 
                headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": "deepseek-chat",
                    "messages": [{"role": "system", "content": "You are a logistics expert. Output ONLY valid JSON."},
                                 {"role": "user", "content": prompt}],
                    "temperature": 0
                },
                timeout=50.0
            )
            
            raw_content = response.json()['choices'][0]['message']['content']
            clean_json = raw_content.replace("```json", "").replace("```", "").strip()
            return json.loads(clean_json)
        except Exception as e:
            logger.error(f"DeepSeek Parsing Error: {e}")
            return None

async def smart_extract(text: str) -> dict:
    logger.info("Starting Smart Extraction... 💅")
    data = regex_extract(text)
    
    is_incomplete = not data["rate"] or not data["load_number"] or not data["pickups"]
    
    if is_incomplete:
        logger.info("Ugh, Regex failed. Waking up the AI... 🥱")
        ai_data = await deepseek_ai_extract(text)
        if ai_data:
            for key, value in ai_data.items():
                if value and (not data.get(key) or data[key] == "" or data[key] == []):
                    data[key] = value

    if not data.get("total_miles") or data["total_miles"] == "":
        if data["pickups"] and data["deliveries"]:
            origin = data["pickups"][0]["address"]
            dest = data["deliveries"][-1]["address"]
            miles = await get_miles_free(origin, dest)
            data["total_miles"] = miles
            
    return data