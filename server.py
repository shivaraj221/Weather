from flask import Flask, render_template, request, jsonify
from weather import get_current_weather
from agri_data import get_dashboard_data, chat_with_agronomist
from waitress import serve
import sqlite3, datetime, os

app = Flask(__name__)

# ─── SQLite Chat History Setup ───────────────────────────────────────────────
DB_PATH = os.path.join(os.path.dirname(__file__), "nexagri_chat.db")

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chat_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                location TEXT NOT NULL,
                role TEXT NOT NULL,
                message TEXT NOT NULL,
                timestamp TEXT NOT NULL
            )
        """)
init_db()

def save_message(location, role, message):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO chat_history (location, role, message, timestamp) VALUES (?, ?, ?, ?)",
            (location, role, message, datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )

def get_history(location, limit=50):
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT role, message, timestamp FROM chat_history WHERE location=? ORDER BY id DESC LIMIT ?",
            (location, limit)
        ).fetchall()
    return [{"role": r[0], "message": r[1], "timestamp": r[2]} for r in reversed(rows)]


@app.route('/')
@app.route('/index')
def index():
    return render_template('index.html')


@app.route('/weather')
def get_weather():
    city = request.args.get('city', '').strip() or "Hyderabad"
    weather_data = get_current_weather(city)

    if not weather_data.get('cod') == 200:
        return render_template('city-not-found.html')

    return render_template(
        "weather.html",
        title=weather_data["name"],
        status=weather_data["weather"][0]["description"].capitalize(),
        temp=f"{weather_data['main']['temp']:.1f}",
        feels_like=f"{weather_data['main']['feels_like']:.1f}"
    )


@app.route('/agri-dashboard')
def agri_dashboard():
    city = request.args.get('city', '').strip()
    lat = request.args.get('lat', '').strip()
    lon = request.args.get('lon', '').strip()

    # ── Weather ───────────────────────────────────────────────────────────────
    if lat and lon:
        weather_data = get_current_weather(lat=lat, lon=lon)
        city = weather_data.get('name', f"{float(lat):.2f}, {float(lon):.2f}")
    else:
        if not city: city = "Hyderabad"
        weather_data = get_current_weather(city)
        if weather_data and weather_data.get('cod') == 200:
            city = weather_data.get('name', city)

    if weather_data.get('cod') == 200:
        weather_info = {
            "city":     weather_data.get("name", city),
            "status":   weather_data["weather"][0]["description"].capitalize(),
            "temp":     f"{weather_data['main']['temp']:.1f}",
            "humidity": weather_data['main']['humidity'],
            "lat":      weather_data.get('lat'),
            "lon":      weather_data.get('lon'),
            "forecast": weather_data.get('forecast', [])
        }
    else:
        weather_info = None
        weather_data = None # Pass None to backend so it uses defaults

    # ── Agri Decision Engine Data ─────────────────────────────────────────────
    dashboard_data = get_dashboard_data(city, weather_data)

    return render_template(
        "dashboard.html",
        title=f"NexAgri Decision Engine – {city}",
        city=city,
        weather=weather_info,
        data=dashboard_data,
    )


@app.route('/api/chat', methods=['POST'])
def api_chat():
    data = request.json
    msg = data.get('message', '')
    loc = data.get('location', 'India')
    
    # Save user message
    save_message(loc, "user", msg)
    
    reply = chat_with_agronomist(msg, loc)
    
    # Save AI reply
    save_message(loc, "assistant", reply)
    
    return jsonify({"reply": reply})


@app.route('/api/chat/history')
def api_chat_history():
    loc = request.args.get('location', 'India')
    return jsonify(get_history(loc))


@app.route('/api/chat/clear', methods=['POST'])
def api_chat_clear():
    loc = request.json.get('location', 'India')
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM chat_history WHERE location=?", (loc,))
    return jsonify({"status": "cleared"})


if __name__ == "__main__":
    print("---------------------------------------------------------")
    print("NexAgri Decision Engine is running!")
    print("Open your browser and go to: http://localhost:8000/agri-dashboard")
    print("   (Press CTRL+C to quit)")
    print("---------------------------------------------------------")
    serve(app, host="0.0.0.0", port=8000)

