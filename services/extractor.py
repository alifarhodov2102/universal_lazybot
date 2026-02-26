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

# 1. High-Performance Regex Patterns
LOAD_RE = re.compile(r"(?:Load\s*Number|PRO\s*#|Load\s*#|Order\s*#|Reference\s*#)[:\s]*([0-9A-Z-]{5,15})", re.I)
RATE_RE = re.compile(r"(?:Total\s*Rate|Total\s*Pay|Base\s*Rate|Rate)[:\s]*\$?\s*([\d,]+\.\d{2})", re.I)
MILES_RE = re.compile(r"(?:Total\s*Miles|Distance|Miles)[:\s]*([\d.,]+)", re.I)

async def extract_template_structure(system_prompt: str, user_example: str) -> str:
    """Alice turns a real load message into a clean skeleton 🧠💅"""
    # CRITICAL: We instruct the AI to consolidate all stops into one tag
    enhanced_prompt = system_prompt + """
    CRITICAL INSTRUCTION: If the example contains multiple stops (PU1, DEL1, DEL2, etc.), 
    replace that entire section with a single {{ stops_info }} tag. 
    Do not keep manual labels like '1#: PICK UP' if they are part of the stops list.
    Ensure the resulting skeleton is clean and scannable.
    """
    
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
                        {"role": "system", "content": enhanced_prompt},
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

async def get_miles_free(origin: str, destination: str) -> str:
    if not origin or not destination: return ""
    
    def clean_for_map(addr):
        # 1. Remove Facility Prefixes 
        addr = re.sub(r'^(?:FMC|JASPER|ARMSTRONG|PLANT \d+|DC|RESUPPLY|FPDC|WAREHOUSE|LOGISTICS|NAME:)\s+', '', addr, flags=re.I)
        # 2. Fix Duplicate Address Lines (e.g., 109 Poland Spring Dr, 109 Poland Spring Dr) 
        parts = [p.strip() for p in addr.split(",")]
        unique_parts = []
        for p in parts:
            if p not in unique_parts: unique_parts.append(p)
        return ", ".join(unique_parts)

    o_addr = clean_for_map(origin)
    d_addr = clean_for_map(destination)

    try:
        async with httpx.AsyncClient() as client:
            async def get_coords(addr):
                # We use a proper URL to avoid bracket noise 
                url = f"https://nominatim.openstreetmap.org/search?q={addr}&format=json&limit=1"
                r = await client.get(url, headers={"User-Agent": "LazyBot_Logistics/2.0"}, timeout=15)
                if r.status_code == 200 and r.json():
                    return r.json()[0]["lat"], r.json()[0]["lon"]
                return None

            o_coords = await get_coords(o_addr)
            d_coords = await get_coords(d_addr)

            if o_coords and d_coords:
                osrm_url = f"http://router.project-osrm.org/route/v1/driving/{o_coords[1]},{o_coords[0]};{d_coords[1]},{d_coords[0]}?overview=false"
                res = await client.get(osrm_url, timeout=10)
                if res.status_code == 200:
                    meters = res.json()["routes"][0]["distance"]
                    return str(round(meters * 0.000621371, 1))
    except Exception as e:
        logger.error(f"OSRM Error: {e}")
    return ""

async def deepseek_ai_extract(text: str) -> dict:
    """AI handles the Broker Name and ALL stops with high precision 🧠"""
    if not DEEPSEEK_API_KEY: return None
    
    prompt = f"""
Analyze this US Logistics Rate Confirmation. RETURN ONLY VALID JSON.
CRITICAL GUIDELINES:
1. BROKER: Identify the actual COMPANY NAME (e.g., WORLDWIDE EXPRESS, GLOBALTRANZ). Ignore individual names like 'Adrian Santos'.
2. ALL STOPS: Capture EVERY pickup (PU) and EVERY delivery (SO/Stop) in the document. Do not skip any destinations.
3. ADRESSES: Extract the full address (Street, City, ST, Zip).
4. LOAD ID: Use the 'Load Number' or 'PRO #'.

Return JSON:
{{
  "broker": "Company Name Only",
  "load_number": "ID",
  "pickups": [{{ "facility": "Name", "address": "Full Address", "time": "Time" }}],
  "deliveries": [{{ "facility": "Name", "address": "Full Address", "time": "Time" }}],
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
                        {"role": "system", "content": "You are a US Logistics Specialist. You capture every single stop on a multi-stop load confirmation without exception."},
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
    logger.info("Starting Multi-Stop Extraction Pipeline... 💅")
    
    # 1. AI Primary Extraction (Captured Broker and all Stops)
    data = await deepseek_ai_extract(text)
    
    if not data:
        data = {"broker": "N/A", "load_number": "", "rate": "", "total_miles": ""}

    # 2. Regex Check for IDs and Rates (Safety fallback)
    if load_match := LOAD_RE.search(text):
        if not data.get("load_number") or data["load_number"] == "ID":
            data["load_number"] = load_match.group(1)

    if rate_match := RATE_RE.search(text):
        if not data.get("rate") or data["rate"] == "0.00":
            data["rate"] = rate_match.group(1)

    if miles_match := MILES_RE.search(text):
        if not data.get("total_miles") or data["total_miles"] == "0":
            data["total_miles"] = miles_match.group(1)
            
    # 3. Multi-Stop Distance Check
    # We calculate distance from the first pickup to the absolute LAST delivery in the list
    if not data.get("total_miles") or str(data["total_miles"]) in ["", "N/A", "0"]:
        if data.get("pickups") and data.get("deliveries"):
            origin = data["pickups"][0]["address"]
            destination = data["deliveries"][-1]["address"] 
            miles = await get_miles_free(origin, destination)
            data["total_miles"] = miles
            
    return data