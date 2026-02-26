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

# 1. High-Performance Regex Patterns (Alphanumeric for PRO/Load IDs)
LOAD_RE = re.compile(r"(?:PRO\s*#|Load\s*#|Order\s*#|Reference\s*#)[:\s]*([0-9A-Z-]{5,15})", re.I)
RATE_RE = re.compile(r"(?:Total\s*Rate|Total\s*Carrier\s*Pay|Total\s*Pay|Rate)[:\s]*\$?\s*([\d,]+\.\d{2})", re.I)
MILES_RE = re.compile(r"(?:Total\s*Miles|Distance|Miles)[:\s]*([\d.,]+)", re.I)

async def extract_template_structure(system_prompt: str, user_example: str) -> str:
    """Alice turns a real load message into a clean skeleton/template 🧠💅"""
    if not DEEPSEEK_API_KEY:
        logger.error("DEEPSEEK_API_KEY missing!")
        return user_example

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
            return skeleton.replace("```jinja2", "").replace("```json", "").replace("```", "").strip()
        except Exception as e:
            logger.error(f"Template Extraction Error: {e}")
            return user_example

def regex_extract(text: str) -> dict:
    """Fast extraction to save tokens on basic fields 💰"""
    data = {"broker": "", "load_number": "", "rate": "", "total_miles": ""}
    
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    
    # Smarter Broker Logic: Skip lines that look like dates or PRO numbers
    # This prevents the "Broker: PRO# 62055 Rate Confirmation..." bug
    for line in lines[:5]:
        if not re.search(r'\d{2}/\d{2}/\d{2}', line) and not re.search(r'PRO\s*#', line, re.I):
            data["broker"] = line[:100]
            break

    if load_match := LOAD_RE.search(text):
        data["load_number"] = load_match.group(1)

    if rate_match := RATE_RE.search(text):
        data["rate"] = rate_match.group(1)

    if miles_match := MILES_RE.search(text):
        data["total_miles"] = miles_match.group(1)
        
    return data

async def get_miles_free(origin: str, destination: str) -> str:
    """Alice calculates distance by stripping facility noise for OSRM 🗺️"""
    if not origin or not destination: return ""
    
    # Clean facility names so geocoding only sees the address
    clean_regex = r'^(?:FMC|JASPER|ARMSTRONG|PLANT \d+|DC|RESUPPLY|FPDC|WAREHOUSE|LOGISTICS)\s+'
    o_addr = re.sub(clean_regex, '', origin, flags=re.I).strip()
    d_addr = re.sub(clean_regex, '', destination, flags=re.I).strip()

    try:
        async with httpx.AsyncClient() as client:
            async def get_coords(addr):
                url = f"[https://nominatim.openstreetmap.org/search?q=](https://nominatim.openstreetmap.org/search?q=){addr}&format=json&limit=1"
                r = await client.get(url, headers={"User-Agent": "LazyBot_Logistics/2.0"}, timeout=15)
                if r.status_code == 200 and r.json():
                    return r.json()[0]["lat"], r.json()[0]["lon"]
                return None

            o_coords = await get_coords(o_addr)
            d_coords = await get_coords(d_addr)

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
    """AI handles the complex address blocks and stop verification 🧠"""
    if not DEEPSEEK_API_KEY: return None
    
    prompt = f"""
Analyze this US Logistics Rate Confirmation. RETURN ONLY VALID JSON.
CRITICAL: Prioritize 'PRO #' as the main Load ID.

Return JSON:
{{
  "broker": "Full Legal Company Name",
  "load_number": "ID",
  "pickups": [{{ "facility": "Name", "address": "Full Address", "time": "Date/Time" }}],
  "deliveries": [{{ "facility": "Name", "address": "Full Address", "time": "Date/Time" }}],
  "rate": "0.00",
  "total_miles": "0"
}}

TEXT:
{text[:12000]}
"""
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                DEEPSEEK_URL, 
                headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": "deepseek-chat",
                    "messages": [
                        {"role": "system", "content": "You are a US Logistics Specialist. Prioritize PRO # for Load ID."},
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
    logger.info("Starting Universal Extraction Pipeline... 💅")
    
    # 1. Regex First (Cost: $0)
    data = regex_extract(text)
    
    # 2. AI Second for Stops and Verification
    ai_data = await deepseek_ai_extract(text)
    
    if ai_data:
        # Merge AI stops and missing fields into regex data
        data["pickups"] = ai_data.get("pickups", [])
        data["deliveries"] = ai_data.get("deliveries", [])
        
        # Priority: Trust Regex for ID/Broker if AI gets confused by Solvera headers
        if not data["load_number"]: data["load_number"] = ai_data.get("load_number")
        if not data["rate"] or data["rate"] == "0.00": data["rate"] = ai_data.get("rate")
        
        # CRITICAL: Trust PDF mileage (Regex) before OSRM map calculations
        if not data["total_miles"] or data["total_miles"] == "0": 
            data["total_miles"] = ai_data.get("total_miles")
        
        if not data["broker"]: data["broker"] = ai_data.get("broker")

    # 3. Final Mileage Check (ONLY if both PDF and AI completely failed)
    if not data.get("total_miles") or str(data["total_miles"]) in ["", "N/A", "0"]:
        if data.get("pickups") and data.get("deliveries"):
            miles = await get_miles_free(data["pickups"][0]["address"], data["deliveries"][-1]["address"])
            data["total_miles"] = miles
            
    return data