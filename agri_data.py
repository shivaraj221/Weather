import random, datetime, hashlib, requests, os, json, math
from groq import Groq

def _seed(text): return int(hashlib.md5(str(text).lower().encode()).hexdigest(), 16)
def _is_india(cc): return (cc or "").upper() == "IN"

def get_current_season(cc="IN", lat=None):
    m = datetime.datetime.now().month
    if _is_india(cc):
        if 6 <= m <= 10: return "Kharif"
        elif m >= 11 or m <= 3: return "Rabi"
        else: return "Zaid"
    if lat is not None and lat < 0: # Southern Hemisphere
        return ["Summer","Summer","Autumn","Autumn","Autumn","Winter (Dry/Cool)","Winter (Dry/Cool)","Winter (Dry/Cool)","Spring","Spring","Spring","Summer"][m-1]
    return ["Winter","Winter","Spring","Spring","Spring","Summer","Summer","Summer","Autumn","Autumn","Autumn","Winter"][m-1]

# ─── LIVE SOIL DNA ANALYZER (ISRIC SoilGrids REST API) ───────────────────────
def get_live_soil_dna(lat, lon, location, master_ai_data=None):
    """
    Fetches real soil data from ISRIC SoilGrids REST API (https://rest.isric.org)
    - Uses lat/lon for precise global soil data
    - Falls back to Groq AI (already in master_ai_data) if API unavailable
    """
    try:
        if lat and lon:
            url = (
                f"https://rest.isric.org/soilgrids/v2.0/properties/query"
                f"?lon={lon}&lat={lat}"
                f"&property=phh2o&property=nitrogen&property=soc&property=clay&property=sand&property=silt"
                f"&depth=0-30cm&value=mean"
            )
            resp = requests.get(url, timeout=3, headers={"Accept": "application/json"})
            if resp.status_code == 200:
                d = resp.json()
                props = {p["name"]: p["depths"][0]["values"]["mean"] for p in d.get("properties", {}).get("layers", []) if p.get("depths")}
                
                # SoilGrids raw values need unit conversion
                ph_raw   = props.get("phh2o", 70)   # pH × 10
                n_raw    = props.get("nitrogen", 1900)  # cg/kg → mg/kg
                soc_raw  = props.get("soc", 15)     # dg/kg (organic carbon)
                clay_pct = (props.get("clay", 200) or 200) / 10   # g/kg → %
                sand_pct = (props.get("sand", 400) or 400) / 10
                silt_pct = (props.get("silt", 400) or 400) / 10

                ph      = round((ph_raw or 70) / 10, 1)
                n_ppm   = int((n_raw or 1900) / 10)          # cg/kg → mg/kg
                organic = round((soc_raw or 15) / 10 * 1.724, 2)  # SOC → OM
                k_ppm   = max(150, int(clay_pct * 4.5))       # estimated from clay
                p_ppm   = max(10, int(organic * 12))           # estimated from OM

                # Determine texture class
                if clay_pct > 40: texture = "Heavy Clay"
                elif clay_pct > 25: texture = "Clay Loam"
                elif sand_pct > 70: texture = "Sandy Loam"
                elif silt_pct > 50: texture = "Silty Loam"
                else: texture = "Loam"

                drainage = "Poor" if clay_pct > 40 else ("Good" if sand_pct > 50 else "Moderate")
                score = int(min(100, 40 + organic * 15 + (1 if 6.0 <= ph <= 7.5 else 0) * 15 + min(n_ppm / 10, 20)))
                grade = "A" if score > 80 else "B" if score > 65 else "C"

                recs = []
                recs.append({"type": "critical", "text": f"Nitrogen critically low ({n_ppm} ppm) — apply Urea @ 50 kg/ha immediately"} if n_ppm < 100 else
                            {"type": "warn",     "text": f"Nitrogen moderate ({n_ppm} ppm) — apply DAP @ 25 kg/ha"} if n_ppm < 180 else
                            {"type": "good",     "text": f"Nitrogen adequate ({n_ppm} ppm) — no top-dress needed"})
                recs.append({"type": "critical", "text": f"Phosphorus deficient ({p_ppm} ppm) — apply SSP @ 40 kg/ha"} if p_ppm < 15 else
                            {"type": "good",     "text": f"Phosphorus healthy ({p_ppm} ppm) — root development supported"})
                recs.append({"type": "warn",  "text": f"Potassium low ({k_ppm} ppm) — boost with MOP @ 60 kg/ha"} if k_ppm < 200 else
                            {"type": "good", "text": f"Potassium adequate ({k_ppm} ppm) — overall crop health good"})
                recs.append({"type": "critical", "text": f"pH {ph} acidic — apply agricultural lime @ 2 t/ha"} if ph < 6.0 else
                            {"type": "warn",     "text": f"pH {ph} alkaline — apply gypsum @ 500 kg/ha"} if ph > 8.0 else
                            {"type": "good",     "text": f"pH {ph} — optimal range for most crops"})
                recs.append({"type": "critical", "text": f"Organic matter very low ({organic}%) — apply FYM @ 5 t/ha"} if organic < 1.0 else
                            {"type": "warn",     "text": f"Organic matter low ({organic}%) — apply compost @ 2 t/ha"} if organic < 1.5 else
                            {"type": "good",     "text": f"Organic matter healthy ({organic}%) — soil biology active"})

                # Crop suitability based on texture/pH/drainage
                suitable = []
                if texture in ("Loam", "Clay Loam") and 6.0 <= ph <= 7.5:
                    suitable = ["Cotton", "Soybean", "Sorghum", "Wheat"]
                elif sand_pct > 60:
                    suitable = ["Groundnut", "Millets", "Sunflower", "Cassava"]
                elif clay_pct > 35:
                    suitable = ["Paddy/Rice", "Sugarcane", "Jute"]
                else:
                    suitable = ["Maize", "Vegetables", "Pulses", "Oilseeds"]

                return {
                    "source": "ISRIC SoilGrids API",
                    "type": master_ai_data.get("soil_type", "Regional Soil") if master_ai_data else "Regional Soil",
                    "ph": ph, "n": n_ppm, "p": p_ppm, "k": k_ppm,
                    "organic": organic, "texture": texture, "drainage": drainage,
                    "score": score, "grade": grade,
                    "clay_pct": round(clay_pct, 1), "sand_pct": round(sand_pct, 1), "silt_pct": round(silt_pct, 1),
                    "zn": round(0.8 + organic * 0.3, 1),
                    "fe": round(4.5 + n_ppm * 0.01, 1),
                    "ca": round(800 + k_ppm * 0.5, 0),
                    "mg": round(120 + p_ppm * 1.5, 0),
                    "crop_suitability": suitable,
                    "recommendations": recs
                }
    except Exception as e:
        print(f"[SoilGrids] API error: {str(e).encode('ascii', 'ignore').decode()}")

    # Fallback: Use Groq AI master data if available
    if master_ai_data and "soil_dna" in master_ai_data:
        ai = master_ai_data["soil_dna"]
        ph = ai.get("ph", 7.0)
        organic = ai.get("organic", 1.5)
        n_ppm = ai.get("n", 190)
        p_ppm = ai.get("p", 22)
        k_ppm = ai.get("k", 320)
        score = int(min(100, 40 + organic * 15 + (15 if 6.0 <= ph <= 7.5 else 0) + min(n_ppm / 10, 20)))
        grade = "A" if score > 80 else "B" if score > 65 else "C"
        recs = []
        recs.append({"type": "critical", "text": f"Nitrogen critically low ({n_ppm} ppm) — apply Urea @ 50 kg/ha"} if n_ppm < 100 else
                    {"type": "warn",     "text": f"Nitrogen moderate ({n_ppm} ppm) — apply DAP @ 25 kg/ha"} if n_ppm < 180 else
                    {"type": "good",     "text": f"Nitrogen adequate ({n_ppm} ppm) — no top-dress needed"})
        recs.append({"type": "critical", "text": f"Phosphorus deficient ({p_ppm} ppm) — apply SSP @ 40 kg/ha"} if p_ppm < 15 else
                    {"type": "good",     "text": f"Phosphorus healthy ({p_ppm} ppm) — root development supported"})
        recs.append({"type": "warn",  "text": f"Potassium low ({k_ppm} ppm) — boost with MOP @ 30 kg/ha"} if k_ppm < 200 else
                    {"type": "good", "text": f"Potassium adequate ({k_ppm} ppm) — overall crop health good"})
        recs.append({"type": "critical", "text": f"pH {ph} acidic — apply agricultural lime @ 2 t/ha"} if ph < 6.0 else
                    {"type": "warn",     "text": f"pH {ph} alkaline — apply gypsum @ 500 kg/ha"} if ph > 8.0 else
                    {"type": "good",     "text": f"pH {ph} — optimal range for most crops"})
        recs.append({"type": "critical", "text": f"Organic matter very low ({organic}%) — apply FYM @ 5 t/ha"} if organic < 1.0 else
                    {"type": "warn",     "text": f"Organic matter low ({organic}%) — apply compost @ 2 t/ha"} if organic < 1.5 else
                    {"type": "good",     "text": f"Organic matter healthy ({organic}%) — soil biology active"})
        return {
            "source": "Groq AI (SoilGrids unavailable)",
            "type": master_ai_data.get("soil_type", "Regional Soil"),
            "ph": ph, "n": n_ppm, "p": p_ppm, "k": k_ppm,
            "organic": organic, "texture": "Loam", "drainage": "Moderate",
            "score": score, "grade": grade,
            "zn": round(0.8 + organic * 0.3, 1),
            "fe": round(4.5 + n_ppm * 0.01, 1),
            "ca": round(800 + k_ppm * 0.5, 0),
            "mg": round(120 + p_ppm * 1.5, 0),
            "crop_suitability": master_ai_data.get("major_crops", "Wheat, Soybean").split(","),
            "recommendations": recs
        }

    # Final fallback: sensible defaults
    return {
        "source": "Default",
        "type": "Mixed Alluvial", "ph": 7.0, "n": 190, "p": 22, "k": 320,
        "organic": 1.5, "texture": "Loam", "drainage": "Moderate",
        "score": 70, "grade": "B",
        "zn": 1.2, "fe": 6.4, "ca": 960, "mg": 153,
        "crop_suitability": ["Wheat", "Soybean", "Cotton"],
        "recommendations": ["Add organic compost", "pH is optimal", "Potassium levels adequate"]
    }

# Backward-compatibility alias for internal functions (fertilizer, supply chain, etc.)
def get_soil_dna(location):
    return get_live_soil_dna(None, None, location)


# ─── RISK ENGINE ─────────────────────────────────────────────────────────────
def calculate_farm_risk(weather_data, location):
    temp = float((weather_data or {}).get('main',{}).get('temp', 28))
    humidity = float((weather_data or {}).get('main',{}).get('humidity', 65))
    wind = float((weather_data or {}).get('wind',{}).get('speed', 10)) * 3.6 # m/s to km/h
    
    # Deterministic environmental calculations
    flood_risk = min(100, max(5, int((humidity - 60) * 1.5))) if humidity > 70 else 5
    water_stress = min(100, max(5, int((temp - 30) * 4))) if temp > 30 else 5
    disease_risk = min(100, max(5, int((humidity - 50) * 1.2 + (temp - 20) * 1.5))) if (humidity > 65 and 20 < temp < 35) else 15
    frost_risk = min(100, max(5, int((10 - temp) * 8))) if temp < 10 else 5
    wind_risk = min(100, max(5, int(wind * 1.5)))
    
    # Basic market risk based on season day (simulating harvest pressure)
    day_of_year = datetime.datetime.now().timetuple().tm_yday
    market_risk = 30 + int(math.sin(day_of_year / 365.0 * math.pi * 4) * 20)
    
    metrics = {
        "Disease": disease_risk,
        "Market": market_risk,
        "Flood": flood_risk,
        "Water Stress": water_stress,
        "Frost": frost_risk,
        "Wind": wind_risk
    }
    
    # Overall score should precisely match the highest imminent threat, making it intuitive for the user
    overall = max(metrics.values())
    
    actions = []
    if disease_risk > 65: actions.append({"action":"Apply preventive fungicide immediately.","impact":"Critical","icon":"🦠"})
    if water_stress > 65: actions.append({"action":"Schedule deep irrigation cycle. Evapotranspiration is severe.","impact":"Critical","icon":"💧"})
    if flood_risk > 65:   actions.append({"action":"Clear field drainage channels. Waterlogging imminent.","impact":"High","icon":"🌊"})
    if frost_risk > 65:   actions.append({"action":"Deploy frost protection (covers/heaters). Freezing imminent.","impact":"Critical","icon":"❄️"})
    
    return {
        "overall_score": overall,
        "metrics": {k: f"{v}%" for k, v in metrics.items()},
        "recommended_actions": actions
    }

# ─── VILLAGE INTELLIGENCE NETWORK ─────────────────────────────────────────────
def get_village_network(location, temp, weather_desc, season):
    api_key = os.getenv("GROQ_API_KEY","")
    mock_active = random.randint(18,94)
    
    def _fallback():
        random.seed(_seed(location + str(datetime.datetime.now().date())))
        desc = weather_desc.lower()
        rain = "rain" in desc or "drizzle" in desc
        hot  = temp > 35
        pool = [
            {"user": f"Farmer 3km East of {location}", "report": f"Brown planthopper infestation spotted on paddy fields. Recommend scouting immediately.", "time": "2h ago", "type": "danger"},
            {"user": f"Local Mandi ({location})", "report": f"Procurement price for soybean dropped ₹120/qtl today. Consider holding stock.", "time": "4h ago", "type": "warning"},
            {"user": "KVK Extension Office", "report": f"Free soil health card testing camp at KVK {location} this Friday. Register online.", "time": "6h ago", "type": "info"},
            {"user": f"Farmer 1km North", "report": ("Waterlogging in low-lying fields after last night's rain. Clear drainage channels." if rain else f"Heat stress visible on cotton leaves. Started drip irrigation."), "time": "1d ago", "type": "warning"},
            {"user": "FPO Alert", "report": "Group procurement of Urea at ₹240 less than retail price. Contact FPO office.", "time": "1d ago", "type": "info"},
        ]
        active = random.sample(pool, k=min(4, len(pool)))
        danger_reports = [r for r in active if r["type"] == "danger"]
        pred = f"🚨 AI Analysis: {danger_reports[0]['report'].split('.')[0]} — High spread probability within 72 hrs. Scout fields." if danger_reports else ""
        random.seed()
        return {"active_farmers": mock_active, "reports": active, "ai_prediction": pred}

    if not api_key: return _fallback()
    
    try:
        client = Groq(api_key=api_key)
        prompt = f"""You are a hyper-local AgriNet AI. Generate 4 REALISTIC community alerts from farmers and institutions near {location} during {season} season with {temp}°C and {weather_desc} weather.

Return ONLY valid JSON with this exact schema:
{{
  "reports": [
    {{
      "user": "Farmer 2km North / Local Mandi / KVK Extension / FPO Alert",
      "report": "Specific, realistic single-sentence alert. Include crop names, prices, distances, or chemicals relevant to {location}.",
      "time": "2h ago",
      "type": "info or warning or danger"
    }}
  ],
  "ai_prediction": "If any danger reports exist, write one specific 1-sentence outbreak prediction mentioning the exact pest/disease and action. Otherwise return empty string."
}}

Rules:
- Mix types: 1 danger, 1-2 warning, 1-2 info
- Use real local crop names for {location}
- Use correct local currency for {location}
- Use correct local agricultural extension names (e.g. FARI for Mauritius, KVK for India)
- danger types must name a specific pest or disease
- CRITICAL: If {temp}°C is below 0°C or above 50°C, or {location} is unarable (like Antarctica), DO NOT hallucinate crops. Simply output a single info report stating that farming is impossible here due to extreme climate."""
        
        res = client.chat.completions.create(
            messages=[{"role":"user","content":prompt}], 
            model="llama-3.1-8b-instant", 
            temperature=0.6,
            response_format={"type": "json_object"}
        )
        data = json.loads(res.choices[0].message.content.strip())
        reports = data.get("reports", [])
        
        # Validate types
        for r in reports:
            if r.get("type") not in ["info", "warning", "danger"]:
                r["type"] = "info"
        
        if len(reports) < 2: return _fallback()
        
        pred = data.get("ai_prediction", "")
        return {"active_farmers": mock_active, "reports": reports[:4], "ai_prediction": pred}
    except Exception as e:
        print(f"Village Network AI Error: {str(e).encode('ascii', 'ignore').decode()}")
        return _fallback()


def chat_with_agronomist(message, location):
    api_key = os.getenv("GROQ_API_KEY", "")
    if not api_key:
        return "I am operating in offline mode. Please add a Groq API key to enable live AI Chat!"
        
    try:
        client = Groq(api_key=api_key)
        prompt = f"You are a helpful, expert AI Agronomist assisting a farmer in {location}. Keep your response very short, friendly, and highly actionable (max 3 sentences). If {location} is a non-agricultural frozen/extreme region like Antarctica, humorously remind them that nothing grows there. The farmer asks: {message}"
        res = client.chat.completions.create(
            messages=[{"role":"user","content":prompt}], 
            model="llama-3.1-8b-instant", 
            temperature=0.5,
            max_tokens=150
        )
        return res.choices[0].message.content.strip()
    except Exception as e:
        return "I'm sorry, my connection to the AI network is currently unstable. Please try again later."

def get_master_ai_data(location, weather_data, season):
    api_key = os.getenv("GROQ_API_KEY", "")
    temp = float((weather_data or {}).get('main',{}).get('temp', 28))
    humidity = float((weather_data or {}).get('main',{}).get('humidity', 65))
    weather_desc = "Clear"
    if weather_data and "weather" in weather_data and len(weather_data["weather"]) > 0:
        weather_desc = weather_data["weather"][0]["description"]
        
    mock = {
        "strategy_headline": "Optimize Moisture & Monitor for Disease Pressure",
        "strategy_detail": "With current weather conditions, maintaining optimal soil moisture is critical for root development. High humidity creates ideal conditions for fungal diseases — scout your fields every 48 hours. Focus on drainage infrastructure to prevent waterlogging in low-lying plots. Apply a balanced NPK fertilizer this week to support crop immunity.",
        "major_crops": "Rice, Wheat, Cotton",
        "crop_problems": "Rice blast disease due to humidity, Cotton bollworm infestation, Wheat rust in cool-damp conditions",
        "soil_type": "Mixed Alluvial",
        "soil_dna": {"ph": 7.0, "organic": 1.5, "n": 190, "p": 22, "k": 320},
        "disease_name": "Fungal Leaf Blight",
        "disease_cause": "High humidity above 70% combined with warm temperatures creates ideal conditions for fungal spore germination.",
        "nutrient_deficiency": "Potassium (K) deficiency weakens cell walls, making the crop highly susceptible. Apply Potassium Sulfate @ 60 kg/ha to restore immunity.",
        "disease_cure": "Spray Mancozeb 75% WP @ 2.5 g/litre of water. Ensure proper field drainage and avoid overhead irrigation.",
        "omni_crop_recommendation": {
            "crop": "High-Yield Sorghum",
            "reasoning": "Sorghum is highly resilient to the current 28°C temp and 65% humidity. Its deep root system thrives in Mixed Alluvial soil (pH 7.0), effectively utilizing the available 320ppm Potassium while tolerating the moderate drainage."
        },
        "immediate_actions": [
            {"priority": "Critical Priority", "icon": "🦠", "action": "Apply preventive fungicide. Risk elevated due to high humidity."},
            {"priority": "Low Priority", "icon": "📈", "action": "Hold harvest in storage — prices trending upward this week."}
        ],
        "weather_explanation": "Expect mostly clear skies, allowing for uninterrupted field operations over the next few days.",
        "is_mock": True
    }
    
    if not api_key: return mock
    
    prompt = f"""You are an elite Agronomic AI with deep knowledge of regional agriculture worldwide.
Analyze this farm: Location: {location}, Season: {season}, Temp: {temp}C, Humidity: {humidity}%, Weather: {weather_desc}.

CRITICAL RULES:
1. If {temp}C is extreme (below 0°C or above 50°C) or {location} is unarable (like Antarctica), set major_crops to "None (Extreme Climate)", warn about the impossibility of farming in strategy_detail, and adjust all fields to reflect barren reality.
2. If location is in the Southern Hemisphere (e.g. Mauritius), July is Winter. Temperatures around 18°C-24°C are mild and ideal for agriculture, NEVER label them as 'Extreme Climate'.
3. Ensure data consistency: If a disease alert is active, Spore Count and Disease Risk metrics MUST reflect 'High' or 'Elevated', never '0' or 'Low'.

Respond ONLY with a valid JSON object (no markdown, no backticks, just raw JSON) with EXACTLY these keys:
{{
  "strategy_headline": "A short, punchy 8-word action headline for today's farm strategy",
  "strategy_detail": "A rich, detailed 4-5 sentence farming strategy SPECIFIC to {location}. Mention exact season, weather, and 3 distinct actionable steps. Be specific and actionable, not generic.",
  "major_crops": "Top 3 crops grown specifically in {location} region (use local names if applicable), comma separated",
  "crop_problems": "The 2-3 most common problems/diseases these crops face in {location}, comma separated",
  "soil_type": "The exact native soil type of {location} (e.g. Laterite, Black Cotton, Alluvial, Volcanic, Ice/Snow, Sand)",
  "soil_dna": {{
    "ph": [accurate float pH 5.0 to 8.5 for {location}],
    "organic": [accurate float organic matter % 0.5 to 3.5 for {location}],
    "n": [integer Nitrogen ppm realistic for {location}],
    "p": [integer Phosphorus ppm realistic for {location}],
    "k": [integer Potassium ppm realistic for {location}]
  }},
  "disease_name": "The MOST LIKELY disease to attack crops in {location} given {humidity}% humidity and {temp}C",
  "disease_cause": "2 sentences explaining precisely WHY this disease occurs under these exact weather conditions",
  "nutrient_deficiency": "Which specific nutrient (N/P/K/Ca/Mg/S/Fe/Zn/etc) makes the crop susceptible and how to correct it with exact dosage",
  "disease_cure": "Exact cure: chemical name, concentration, application rate, and timing to prevent hallucination",
  "omni_crop_recommendation": {{
    "crop": "The single BEST specific crop variety to plant RIGHT NOW considering ALL environmental factors",
    "reasoning": "A 4-5 sentence deep analysis explaining WHY this crop is perfect. You MUST explicitly state that your decision was calculated by evaluating: 1) Weather (Temp {temp}C, Humidity {humidity}%, {weather_desc}), 2) Soil (native type, pH, NPK), 3) Water availability, 4) Biological pest risks, 5) Market demand, and 6) Environmental/Extreme weather resilience for {location} in the {season} season."
  }},
  "immediate_actions": [
    {{
      "priority": "Critical Priority",
      "icon": "🦠",
      "action": "One sentence critical action based on weather and disease risk"
    }},
    {{
      "priority": "High Priority",
      "icon": "🌱",
      "action": "One sentence high priority farming action for today"
    }},
    {{
      "priority": "Medium Priority",
      "icon": "💧",
      "action": "One sentence irrigation or soil management action"
    }}
    }}
  ],
  "weather_explanation": "2 sentences explaining what {weather_desc} at {temp}C means specifically for {location} farmers and their crops.",
  "weather_analysis": {{
    "summary": "A rich 3-4 sentence deep analysis of what {weather_desc} at {temp}C and {humidity}% humidity means for farmers in {location}. Be specific about crops, diseases, and opportunities.",
    "disease_risks": [
      {{"disease": "Most likely disease name", "risk_level": "High/Medium/Low", "reason": "1 sentence cause based on weather"}},
      {{"disease": "Second likely disease", "risk_level": "High/Medium/Low", "reason": "1 sentence cause based on weather"}}
    ],
    "crop_stress": [
      {{"factor": "Heat/Moisture/Wind/etc stress factor", "impact": "1 sentence specific crop impact", "icon": "emoji"}},
      {{"factor": "Second stress factor", "impact": "1 sentence specific crop impact", "icon": "emoji"}}
    ],
    "action_timeline": [
      {{"when": "Today", "action": "Most urgent action for today based on live weather"}},
      {{"when": "Next 48 hrs", "action": "What to monitor or prepare"}},
      {{"when": "This week", "action": "Longer-term farm management task"}}
    ],
    "opportunity": "1 sentence about what the current weather is good for — a positive farming opportunity."
  }},
  "regional_water_status": {{
    "reservoir": "Name of the local reservoir or water source for {location}",
    "level": "Percentage (e.g. 78%)",
    "advice": "1 sentence on irrigation restrictions or optimal watering times"
  }},
  "smart_irrigation": {{
    "soil_moisture": "Percentage %",
    "forecasted_etc": "Value in mm/day",
    "ai_action": "Prescriptive action (e.g. 'No irrigation needed today. Schedule next cycle for July 20 at 06:30 AM (15mm)')"
  }},
  "phenological_stage": {{
    "crop": "Scientific and common name of top local crop",
    "current_phase": "Specific growth stage (e.g. Tuber Bulking, Flowering) and timeframe",
    "critical_need": "Exact nutrient or care requirement for this specific stage"
  }},
  "omni_telemetry": [
    // CRITICAL: Ensure values respect actual physics and biology for {location} at {temp}C.
    // Every label's value MUST contain a trend arrow (📉, ➡️, or 📈) at the end, e.g., '60% 📉'.
    {{"label": "UV Index", "value": "Number 1-11 + Trend", "color": "purple", "icon": "☀️"}},
    {{"label": "Solar Radiation", "value": "Value W/m²", "color": "yellow", "icon": "🌞"}},
    {{"label": "Cloud Cover", "value": "Percentage %", "color": "info", "icon": "🌫️"}},
    {{"label": "Wind Speed", "value": "Value km/h", "color": "info", "icon": "🌬️"}},
    {{"label": "Frost Risk", "value": "Low/Medium/High", "color": "info", "icon": "❄️"}},
    {{"label": "Heat Stress", "value": "Index 1-10", "color": "danger", "icon": "🌡️"}},
    {{"label": "Soil Moisture", "value": "Percentage %", "color": "info", "icon": "💧"}},
    {{"label": "Soil Temp", "value": "Temp in C", "color": "warning", "icon": "🌡️"}},
    {{"label": "Soil pH Level", "value": "Float 5-8", "color": "accent", "icon": "⚗️"}},
    {{"label": "Salinity (EC)", "value": "Value dS/m", "color": "warning", "icon": "🧂"}},
    {{"label": "Organic Matter", "value": "Percentage %", "color": "green", "icon": "🍂"}},
    {{"label": "Water Quality", "value": "Safe/Warning", "color": "info", "icon": "💦"}},
    {{"label": "Groundwater Depth", "value": "Value ft", "color": "blue", "icon": "🕳️"}},
    {{"label": "Evapotranspiration", "value": "Value mm/day", "color": "blue", "icon": "♨️"}},
    {{"label": "Air Quality (AQI)", "value": "Number 0-500", "color": "green", "icon": "💨"}},
    {{"label": "CO2 Concentration", "value": "Value ppm", "color": "purple", "icon": "🌍"}},
    {{"label": "Canopy Temp", "value": "Temp in C", "color": "warning", "icon": "🌳"}},
    {{"label": "Vegetation Health", "value": "Index 0-100", "color": "green", "icon": "🌿"}},
    {{"label": "Plant Height Est", "value": "Value cm", "color": "accent", "icon": "📏"}},
    {{"label": "Yield Prediction", "value": "Tonnes/ha", "color": "green", "icon": "📈"}},
    {{"label": "Spore Count", "value": "Spores/m³", "color": "danger", "icon": "🦠"}},
    {{"label": "Pest Index", "value": "Value 1-10", "color": "danger", "icon": "🦗"}},
    {{"label": "Pollinator Activity", "value": "Low/Med/High", "color": "yellow", "icon": "🐝"}},
    {{"label": "Disease Risk", "value": "Low/Med/High", "color": "danger", "icon": "⚠️"}}
  ]
}}"""
    try:
        client = Groq(api_key=api_key)
        res = client.chat.completions.create(
            messages=[{"role":"user","content":prompt}], 
            model="llama-3.1-8b-instant", 
            temperature=0.3,
            response_format={"type": "json_object"}
        )
        content = res.choices[0].message.content.strip()
        data = json.loads(content)
        data["is_mock"] = False
        return data
    except Exception as e:
        print(f"Groq API Error: {str(e).encode('ascii', 'ignore').decode()}")
        return mock


# ─── FERTILIZER OPTIMIZER ────────────────────────────────────────────────────
def get_fertilizer_plan(location, season):
    soil = get_soil_dna(location)
    n_need = max(0, 240 - soil["n"])
    p_need = max(0, 40 - soil["p"])
    k_need = max(0, 400 - soil["k"])
    return {
        "N_kg_ha": n_need, "P_kg_ha": p_need, "K_kg_ha": k_need,
        "Urea_bags": round(n_need/46 * 2.17, 1),
        "DAP_bags": round(p_need/46 * 2.17, 1),
        "MOP_bags": round(k_need/60 * 2.17, 1),
        "micro_nutrients": "Zinc Sulphate 25 kg/ha" if soil["ph"] > 7.5 else "Boron 1 kg/ha",
        "cost_estimate": f"₹{int((n_need*6 + p_need*9 + k_need*4)):,}/ha",
        "split_schedule": ["Basal dose at sowing: 50% N + 100% P + 100% K","Top-dress at 30 DAP: 30% N","Top-dress at 60 DAP: 20% N"]
    }

# ─── PEST RADAR ──────────────────────────────────────────────────────────────
def get_pest_radar(location, season, weather_data):
    random.seed(_seed(location + season))
    temp = float((weather_data or {}).get('main',{}).get('temp', 28))
    humidity = float((weather_data or {}).get('main',{}).get('humidity', 65))
    pests = [
        {"name":"Brown Planthopper","risk": "Critical" if humidity>75 else "Low","direction":"3km East","eti":"48 hrs","control":"Buprofezin 25% SC @ 1.6 ml/L","organic":"Neem oil 3% spray"},
        {"name":"Fall Armyworm","risk": "High" if season=="Kharif" else "Low","direction":"7km North","eti":"5 days","control":"Spinetoram 11.7% SC @ 0.5 ml/L","organic":"Beauveria bassiana 5 g/L"},
        {"name":"Aphids","risk": "Medium" if temp<28 else "Low","direction":"Present in field","eti":"Now","control":"Imidacloprid 17.8% SL @ 0.3 ml/L","organic":"Soap water spray + yellow sticky traps"},
    ]
    random.seed()
    return {"pests": pests, "spray_window": "06:00–08:00 AM (wind speed <5 km/h)", "next_scouting": "3 days"}

# ─── MARKET ORACLE ───────────────────────────────────────────────────────────
COMMODITIES = {
    "Kharif": [("Cotton",6200),("Soybean",4100),("Rice",2200),("Tur Dal",7200),("Groundnut",5800)],
    "Rabi":   [("Wheat",2400),("Mustard",5200),("Chickpea",5600),("Barley",1900),("Potato",1800)],
    "Zaid":   [("Watermelon",1200),("Sunflower",5000),("Moong Dal",8200),("Vegetables",2500),("Maize",1900)],
}
def get_market_oracle(location, season):
    api_key = os.getenv("GROQ_API_KEY","")
    
    # Fallback to random if no API key or API fails
    def _fallback():
        random.seed(_seed(season + location + str(datetime.datetime.now().date())))
        commodities = COMMODITIES.get(season, COMMODITIES["Kharif"])
        result = []
        for name, base in commodities:
            chg = round(random.uniform(-5.5, 6.5), 1)
            price = int(base * (1 + chg/100))
            trend = "Up" if chg > 0.5 else "Down" if chg < -0.5 else "Stable"
            signal = "SELL" if chg < -2 else ("HOLD" if chg > 2 else "CONTRACT")
            result.append({"name":name,"price":price,"unit":"quintal","change":chg,"trend":trend,"signal":signal,"market_context":"Stable seasonal demand"})
        random.seed()
        return {"commodities":result, "msp_alert": "MSP for Paddy: ₹2,183/qtl | Wheat: ₹2,275/qtl"}

    if not api_key: return _fallback()

    try:
        client = Groq(api_key=api_key)
        prompt = f"""You are an elite Agri-Market AI. Generate realistic current market prices for the top 5 crops grown near {location} during the {season} season. Provide the exact local currency unit implicitly in the price if needed, but return ONLY integers for price.

CRITICAL RULES:
1. If {location} is fundamentally unarable (e.g. Antarctica), DO NOT hallucinate crops. Return a single commodity called "None" with price 0, and warn in msp_alert.
2. Use hyper-localized context: Currency MUST match the country (e.g. Rs or MUR for Mauritius, ₹ for India, etc.).
3. Use realistic local crop names (e.g. Pomme de Terre, Chataigne, Brède for Mauritius).

Respond ONLY with a valid JSON object matching this schema:
{{
  "commodities": [
    {{
      "name": "Crop Name",
      "price": [Integer price per quintal/ton/kg],
      "unit": "kg/bunch/ton/quintal",
      "change": [Float percentage change like 2.5 or -1.4],
      "trend": "Up" or "Down" or "Stable",
      "signal": "SELL" or "BUY" or "HOLD" or "CONTRACT",
      "market_context": "Short 3-5 word realistic market context (e.g. 'High demand, low supply')"
    }}
  ],
  "msp_alert": "A 1-sentence market/govt pricing alert specific to {location}"
}}
"""
        res = client.chat.completions.create(
            messages=[{"role":"user","content":prompt}], 
            model="llama-3.1-8b-instant", 
            temperature=0.4,
            response_format={"type": "json_object"}
        )
        content = res.choices[0].message.content.strip()
        data = json.loads(content)
        return data
    except Exception as e:
        print(f"Market Oracle API Error: {e}")
        return _fallback()

# ─── PROFIT PLANNER ──────────────────────────────────────────────────────────
def get_profit_planner(season):
    crops = COMMODITIES.get(season, COMMODITIES["Kharif"])
    plans = []
    for name, msp in crops[:3]:
        yield_qtl = random.randint(20, 55)
        cost_ha = random.randint(28000, 65000)
        revenue = yield_qtl * msp
        profit = revenue - cost_ha
        roi = round(profit/cost_ha*100, 1)
        plans.append({"crop":name,"yield_qtl":yield_qtl,"revenue":f"₹{revenue:,}","cost":f"₹{cost_ha:,}","profit":f"₹{profit:,}","roi":f"{roi}%","viable": profit>0})
    return plans

# ─── IRRIGATION ENGINE ────────────────────────────────────────────────────────
def get_irrigation_plan(weather_data, location):
    temp = float((weather_data or {}).get('main',{}).get('temp', 28))
    humidity = float((weather_data or {}).get('main',{}).get('humidity', 65))
    et0 = round(0.0023*(temp+17.8)*(abs(100-humidity)**0.5)*7.5, 2)
    deficit = round(et0 * 1.2, 1)
    random.seed(_seed(location))
    return {
        "et0": f"{et0} mm/day",
        "soil_moisture": f"{random.randint(28,55)}%",
        "water_deficit": f"{deficit} mm",
        "schedule": "06:00 AM tomorrow" if deficit > 4 else "Not needed for 3 days",
        "drip_efficiency": "Save 42% water vs flood irrigation",
        "groundwater": f"{random.randint(90,180)} ft",
        "rain_forecast": "28mm expected in 48 hrs — skip next irrigation",
        "harvesting_potential": "Medium — Install 10,000L tank to capture roof runoff",
    }

# ─── GOVT SCHEMES ────────────────────────────────────────────────────────────
def get_govt_schemes():
    return [
        {"name":"PM-KISAN","benefit":"₹6,000/year direct income support to all farmers.","tag":"Income","link":"https://pmkisan.gov.in/"},
        {"name":"PMFBY Crop Insurance","benefit":"Crop loss insurance with premium as low as 1.5%.","tag":"Insurance","link":"https://pmfby.gov.in/"},
        {"name":"eNAM Platform","benefit":"Sell directly to buyers across India via online auction.","tag":"Market","link":"https://enam.gov.in/"},
        {"name":"Kisan Credit Card (KCC)","benefit":"Collateral-free crop loans up to ₹3 lakh @ 4% interest.","tag":"Credit","link":"#"},
        {"name":"Soil Health Card","benefit":"Free government soil testing and nutrient map.","tag":"Soil","link":"https://soilhealth.dac.gov.in/"},
        {"name":"PMKSY Water Scheme","benefit":"Drip/sprinkler irrigation subsidy up to 55%.","tag":"Water","link":"https://pmksy.gov.in/"},
        {"name":"Agri Infrastructure Fund","benefit":"₹1 crore loan at 3% interest for cold storage/warehousing.","tag":"Finance","link":"#"},
    ]

# ─── TRANSPORT & SUPPLY CHAIN ─────────────────────────────────────────────────
def get_supply_chain(location):
    random.seed(_seed(location))
    return {
        "nearest_mandi": f"APMC {location.title()} ({random.randint(8,35)}km)",
        "truck_cost": f"₹{random.randint(800,2200):,}",
        "fuel_status": random.choice(["Stable","Rising +2%","Falling -1%"]),
        "storage_available": f"{random.randint(200,2000)} MT (Cold Storage nearby)",
        "aggregators": ["ITC e-Choupal","Cargill Direct","State Cooperative"],
        "sell_signal": random.choice(["Sell NOW — prices peak in 3 days","Wait 7 days — upward trend projected","URGENT: Oversupply expected in 14 days"])
    }

# ─── DIGITAL TWIN ─────────────────────────────────────────────────────────────
def get_digital_twin(season):
    sowing_offset = random.randint(30, 90)
    total_days = {"Kharif": 120, "Rabi": 150, "Zaid": 90}.get(season, 120)
    elapsed = sowing_offset
    remaining = total_days - elapsed
    pct = round(elapsed/total_days*100)
    stages = ["Land Prep","Sowing","Germination","Vegetative","Flowering","Grain Fill","Maturity","Harvest"]
    stage_idx = min(int(pct/12.5), 7)
    harvest_date = (datetime.datetime.now() + datetime.timedelta(days=remaining)).strftime("%d %b %Y")
    return {
        "crop_age": f"{elapsed} days",
        "stage": stages[stage_idx],
        "total_days": total_days,
        "harvest_date": harvest_date,
        "remaining": remaining,
        "pct": pct,
        "carbon_credits": f"${random.randint(32,85)}/acre",
        "historical": f"{round(random.uniform(-0.5, 2.5), 1)}°C {'above' if random.random()>0.4 else 'below'} 10-yr average",
        "labor_peak": f"High labor demand in {random.randint(7,21)} days (Harvesting phase)",
        "timeline": [
            {"date":"Day 1","event":"Sowing completed","status":"done"},
            {"date":f"Day {elapsed-20}","event":"First fertilizer application","status":"done"},
            {"date":"Today","event":"Current stage","status":"active"},
            {"date":f"Day {total_days-20}","event":"Pre-harvest scouting","status":"upcoming"},
            {"date":f"Day {total_days}","event":"Estimated harvest","status":"upcoming"},
        ]
    }

# ─── NEGOTIATION ASSISTANT ────────────────────────────────────────────────────
def get_negotiation_advisor(location, season):
    market = get_market_oracle(location, season)
    if market.get("commodities"):
        top = market["commodities"][0]
        return {
            "current_offer": f"₹{int(top['price']*0.92):,}",
            "fair_value": f"₹{top['price']:,}",
            "next_week_est": f"₹{int(top['price']*1.03):,}",
            "advice": f"The buyer's offer is ~8% below fair market value. Counter-offer at ₹{top['price']:,}. If they refuse, wait 5 days — prices are trending upward.",
            "walk_away_price": f"₹{int(top['price']*0.88):,}"
        }
    return {}

# ─── MASTER AGGREGATOR ────────────────────────────────────────────────────────
def get_dashboard_data(city, weather_data):
    cc = (weather_data or {}).get('sys',{}).get('country','IN')
    lat = float((weather_data or {}).get('coord',{}).get('lat', None) or 0) or None
    season = get_current_season(cc, lat)
    temp = float((weather_data or {}).get('main',{}).get('temp', 28))
    humidity = float((weather_data or {}).get('main',{}).get('humidity', 65))
    weather_desc = "Clear"
    if weather_data and "weather" in weather_data and len(weather_data["weather"]) > 0:
        weather_desc = weather_data["weather"][0]["description"]

    lat = float((weather_data or {}).get('coord',{}).get('lat', None) or 0) or None
    lon = float((weather_data or {}).get('coord',{}).get('lon', None) or 0) or None

    master_ai = get_master_ai_data(city, weather_data, season)
    live_soil = get_live_soil_dna(lat, lon, city, master_ai)

    return {
        "location": city,
        "season": season,
        "risk_engine": calculate_farm_risk(weather_data, city),
        "village_network": get_village_network(city, temp, weather_desc, season),
        "master_ai": master_ai,
        "soil_dna": live_soil,
        "fertilizer": get_fertilizer_plan(city, season),
        "pest_radar": get_pest_radar(city, season, weather_data),
        "market_oracle": get_market_oracle(city, season),
        "profit_plans": get_profit_planner(season),
        "irrigation": get_irrigation_plan(weather_data, city),
        "schemes": get_govt_schemes(),
        "supply_chain": get_supply_chain(city),
        "digital_twin": get_digital_twin(season),
        "negotiation": get_negotiation_advisor(city, season),
        
        # Weather-Driven Deep Telemetry Algorithms
        "telemetry": {
            "soil_temp": f"{round(temp - (humidity * 0.05), 1)}°C",
            "leaf_wetness": f"{min(98, max(12, int(humidity * 1.1)))}%",
            "uv_index": f"{11 if weather_desc == 'Clear' else (4 if 'rain' in weather_desc else 7)}",
            "wind_dir": random.choice(["NE", "NW", "SE", "SW", "N", "S"]),
        },
        "daily_tasks": [
            {"task": "Prepare soil for " + ("planting" if temp < 30 else "heat mitigation"), "status": "pending", "type": "warn"},
            {"task": ("Ensure drainage channels are clear" if 'rain' in weather_desc else "Inspect main irrigation line for micro-leaks"), "status": "pending", "type": "info"},
            {"task": "Log today's crop growth stage photos to twin", "status": "done", "type": "good"},
        ],
        "equipment": {
            "tractor_fuel": f"{min(100, max(10, int(temp * 2.5)))}%",
            "pump_status": "Idle (Raining)" if 'rain' in weather_desc else "Active (Running)",
            "battery_array": f"{100 if weather_desc == 'Clear' else 65}%",
        }
    }
