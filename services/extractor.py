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
    enhanced_prompt = system_prompt + """
    CRITICAL INSTRUCTION: If the example contains multiple stops (PU1, DEL1, DEL2, etc.), 
    replace that entire section with a single {{ stops_info }} tag. 
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
    """Alice calculates distance with triple-fallback logic 🗺️"""
    if not origin or not destination: return "N/A"
    
    def clean_addr(addr, level=0):
        # Strip common facility noise and duplicate lines
        addr = re.sub(r'^(?:FMC|JASPER|ARMSTRONG|PLANT \d+|DC|RESUPPLY|FPDC|WAREHOUSE|LOGISTICS|NAME:|ADDRESS:)\s+', '', addr, flags=re.I)
        parts = [p.strip() for p in addr.replace('\n', ',').split(",") if p.strip()]
        unique_parts = []
        for p in parts:
            if p not in unique_parts: unique_parts.append(p)
            
        if level == 1 and len(unique_parts) > 2:
            return ", ".join(unique_parts[-3:]) # Street, City, State
        if level == 2 and len(unique_parts) >= 2:
            return ", ".join(unique_parts[-2:]) # City, State only
        return ", ".join(unique_parts)

    async def fetch_coords(addr, client):
        # FIXED: Pure URL with query params for safety
        url = "[https://nominatim.openstreetmap.org/search](https://nominatim.openstreetmap.org/search)"
        params = {"q": addr, "format": "json", "limit": 1}
        try:
            r = await client.get(url, params=params, headers={"User-Agent": "LazyBot_Logistics/2.0"}, timeout=15)
            if r.status_code == 200 and r.json():
                return r.json()[0]["lat"], r.json()[0]["lon"]
        except Exception as e:
            logger.error(f"Geocoding error for {addr}: {e}")
        return None

    async with httpx.AsyncClient() as client:
        o_coords = d_coords = None
        for level in range(3):
            if not o_coords: o_coords = await fetch_coords(clean_addr(origin, level), client)
            if not d_coords: d_coords = await fetch_coords(clean_addr(destination, level), client)
            if o_coords and d_coords: break

        if o_coords and d_coords:
            try:
                # FIXED: Clean OSRM Protocol string
                osrm_url = f"[http://router.project-osrm.org/route/v1/driving/](http://router.project-osrm.org/route/v1/driving/){o_coords[1]},{o_coords[0]};{d_coords[1]},{d_coords[0]}?overview=false"
                res = await client.get(osrm_url, timeout=10)
                if res.status_code == 200:
                    meters = res.json()["routes"][0]["distance"]
                    return str(round(meters * 0.000621371, 1))
            except Exception as e:
                logger.error(f"OSRM Route Error: {e}")
                
    return "N/A"

async def deepseek_ai_extract(text: str) -> dict:
    """AI handles the Broker Name and ALL stops with high precision 🧠"""
    if not DEEPSEEK_API_KEY: return None
    
    prompt = f"""
Analyze this US Logistics Rate Confirmation. RETURN ONLY VALID JSON.
CRITICAL GUIDELINES:
1. BROKER: Identify the actual COMPANY NAME. Ignore names like 'Adrian Santos'.
2. ALL STOPS: Capture EVERY pickup (PU) and EVERY delivery (SO/Stop) in the document. Do not skip stops.
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
    logger.info("Starting Multi-Stop Cumulative Extraction Pipeline... 💅")
    
    data = await deepseek_ai_extract(text)
    
    if not data:
        data = {"broker": "N/A", "load_number": "", "rate": "", "total_miles": "N/A"}

    # 1. Regex Overlays (Safety fallback for IDs and Rates)
    if load_match := LOAD_RE.search(text):
        if not data.get("load_number") or data["load_number"] == "ID":
            data["load_number"] = load_match.group(1)

    if rate_match := RATE_RE.search(text):
        if not data.get("rate") or data["rate"] == "0.00":
            data["rate"] = rate_match.group(1)

    # 2. Cumulative Mileage Logic: PU1 -> DEL1 -> DEL2
    if not data.get("total_miles") or str(data["total_miles"]) in ["", "N/A", "0"]:
        if data.get("pickups") and data.get("deliveries"):
            all_stops = data["pickups"] + data["deliveries"]
            total_cumulative_miles = 0.0
            valid_segments = 0
            
            logger.info(f"⚙️ Calculating cumulative mileage for {len(all_stops)} stops...")
            
            for i in range(len(all_stops) - 1):
                seg_origin = all_stops[i]["address"]
                seg_dest = all_stops[i+1]["address"]
                
                logger.info(f"🛣️ Leg {i+1}: Calculating {seg_origin} TO {seg_dest}")
                
                seg_miles = await get_miles_free(seg_origin, seg_dest)
                
                if seg_miles != "N/A":
                    total_cumulative_miles += float(seg_miles)
                    valid_segments += 1
                    logger.info(f"✅ Leg {i+1} Distance: {seg_miles} mi")
            
            if valid_segments > 0:
                data["total_miles"] = str(round(total_cumulative_miles, 1))
                logger.info(f"🏁 Total Trip Miles: {data['total_miles']} mi")
            else:
                data["total_miles"] = "N/A"
            
    return data