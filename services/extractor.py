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

# 1. Improved Regex Patterns for US Logistics
LOAD_RE = re.compile(r"(?:Load\s*#|Order\s*#|Reference\s*#|PRO\s*#)[:\s]*([0-9]{5,10})", re.I)
RATE_RE = re.compile(r"(?:Total\s*Carrier\s*Pay|Total\s*Pay|Flat\s*Rate|Rate)[:\s]*\$?\s*([\d,]+\.\d{2})", re.I)
MILES_RE = re.compile(r"(?:Total\s*Miles|Distance|Miles)[:\s]*([\d.,]+)", re.I)

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
        # Broker is usually in the first 3 lines of a professional RC
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
                # Using Nominatim with proper headers
                url = f"https://nominatim.openstreetmap.org/search?q={addr}&format=json&limit=1"
                r = await client.get(url, headers={"User-Agent": "LazyBot_Logistics/2.0"}, timeout=10)
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
    
    # Send only the most relevant part of the RC to save tokens and focus AI
    full_text = text[:12000]

    prompt = f"""
Analyze this US Logistics Rate Confirmation. 
Extract data with high precision. RETURN ONLY VALID JSON.

Guidelines:
1. BROKER: Look for the company name at the very top (e.g., RYAN TRANSPORTATION, ECHO, TQL). DO NOT return MC numbers or Fax.
2. LOAD_NUMBER: Look for 'Load #', 'Order #', or 'Reference #'. It's usually 6-10 digits.
3. RATE: Look for 'Total Carrier Pay' or 'Total:'. Ignore 'Tracking Hold' or individual fees.
4. STOPS: 
   - PU (Pickups): Look for 'PU 1', 'Shipper', or 'Origin'.
   - DEL (Deliveries): Look for 'SO 1', 'Consignee', or 'Destination'.
   - Extract: Facility Name, Full Address (Street, City, ST, Zip), and Appointment Time.
5. MILES: Total distance between origin and destination.

Return this JSON:
{{
  "broker": "Full Legal Company Name",
  "load_number": "Numeric ID only",
  "pickups": [{{ "facility": "Name", "address": "Full Address", "time": "Date/Time" }}],
  "deliveries": [{{ "facility": "Name", "address": "Full Address", "time": "Date/Time" }}],
  "rate": "Total amount (e.g. 1500.00)",
  "total_miles": "Distance"
}}

TEXT TO ANALYZE:
{full_text}
"""
    logger.info("DeepSeek: Consulting the AI Oracle... 🥱")
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                DEEPSEEK_URL, 
                headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": "deepseek-chat",
                    "messages": [
                        {"role": "system", "content": "You are a US Logistics Specialist. You ignore garbage text and extract only business data into JSON."},
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
    
    # Start with AI for high accuracy on McLeod/Ryan layouts
    data = await deepseek_ai_extract(text)
    
    # Fallback to Regex if AI fails completely
    if not data:
        logger.warning("AI failed. Falling back to basic Regex... 🙄")
        data = regex_extract(text)
    
    # Verify and fix miles if missing
    if not data.get("total_miles") or data["total_miles"] in ["", "N/A", "0"]:
        if data.get("pickups") and data.get("deliveries"):
            origin = data["pickups"][0]["address"]
            dest = data["deliveries"][-1]["address"]
            miles = await get_miles_free(origin, dest)
            data["total_miles"] = miles
            
    return data