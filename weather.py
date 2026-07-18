from dotenv import load_dotenv
from pprint import pprint
import requests
import os
import re

load_dotenv()


def get_current_weather(city=None, lat=None, lon=None):
    api_key = os.getenv("API_KEY")
    if not api_key:
        return {"cod": 0, "error": "No API key configured"}

    try:
        if lat and lon:
            # Step 1a: Use Reverse Geocoding API to resolve lat/lon to city name
            geo_url = f'http://api.openweathermap.org/geo/1.0/reverse?lat={lat}&lon={lon}&limit=1&appid={api_key}'
            geo_response = requests.get(geo_url, timeout=5)
            
            if geo_response.status_code == 200 and geo_response.json():
                geo_data = geo_response.json()[0]
                resolved_name = geo_data.get('name', f"{lat}, {lon}")
                country = geo_data.get('country', 'IN')
                state = geo_data.get('state', '')
            else:
                resolved_name = f"{lat}, {lon}"
                country = "IN"
                state = ""
        else:
            if not city: city = "Hyderabad"
            # Step 1b: Use Geocoding API to resolve location to lat/lon
            # Detect if input is a Pincode (starts with numbers)
            is_zip = bool(re.match(r'^\d+', city))
            
            if is_zip:
                zip_query = city if ',' in city else f"{city},IN"
                geo_url = f'http://api.openweathermap.org/geo/1.0/zip?zip={zip_query}&appid={api_key}'
            else:
                # Normalize city name to remove administrative terms that confuse OWM
                search_city = re.sub(r'(?i)\b(mandal|district|village|city|town)\b', '', city).strip()
                geo_url = f'http://api.openweathermap.org/geo/1.0/direct?q={search_city}&limit=1&appid={api_key}'
                
            geo_response = requests.get(geo_url, timeout=5)

            if geo_response.status_code != 200 or not geo_response.json():
                return {"cod": 404, "message": f"Location '{city}' not found"}

            geo_data = geo_response.json()
            # The direct API returns a list, the zip API returns a dict
            if isinstance(geo_data, list):
                if len(geo_data) == 0:
                    return {"cod": 404, "message": f"Location '{city}' not found"}
                geo_data = geo_data[0]

            lat = geo_data['lat']
            lon = geo_data['lon']
            country = geo_data.get('country', 'IN')
            state = geo_data.get('state', '')
            resolved_name = geo_data.get('name', city)

        # Step 2: Fetch current weather using lat/lon (metric units = Celsius)
        weather_url = (
            f'http://api.openweathermap.org/data/2.5/weather'
            f'?lat={lat}&lon={lon}&appid={api_key}&units=metric'
        )
        weather_response = requests.get(weather_url, timeout=5)

        if weather_response.status_code != 200:
            return {"cod": weather_response.status_code, "message": "Weather fetch failed"}

        data = weather_response.json()
        # Ensure cod is always an int
        data['cod'] = int(data.get('cod', 200))
        # Attach extra geo metadata
        # Attach extra geo metadata
        data['name'] = resolved_name
        data['country'] = country
        data['state'] = state
        data['lat'] = float(lat)
        data['lon'] = float(lon)
        
        # Step 3: Fetch 5-day forecast (every 3 hours)
        forecast_url = (
            f'http://api.openweathermap.org/data/2.5/forecast'
            f'?lat={lat}&lon={lon}&appid={api_key}&units=metric'
        )
        try:
            fc_res = requests.get(forecast_url, timeout=8)
            if fc_res.status_code == 200:
                # Group by day to make a simple daily forecast
                daily_forecast = {}
                for item in fc_res.json().get('list', []):
                    date = item['dt_txt'].split(' ')[0]
                    if date not in daily_forecast:
                        daily_forecast[date] = {
                            "temp_min": item['main']['temp_min'],
                            "temp_max": item['main']['temp_max'],
                            "desc": item['weather'][0]['description']
                        }
                    else:
                        daily_forecast[date]['temp_min'] = min(daily_forecast[date]['temp_min'], item['main']['temp_min'])
                        daily_forecast[date]['temp_max'] = max(daily_forecast[date]['temp_max'], item['main']['temp_max'])
                
                # Take first 5 days
                data['forecast'] = [{"date": k, **v} for k, v in list(daily_forecast.items())[:5]]
            else:
                data['forecast'] = []
        except Exception:
            data['forecast'] = []

        return data

    except requests.exceptions.Timeout:
        return {"cod": 0, "error": "Weather API timed out"}
    except requests.exceptions.ConnectionError:
        return {"cod": 0, "error": "Could not connect to weather service"}
    except Exception as e:
        return {"cod": 0, "error": str(e)}


if __name__ == "__main__":
    print('\n*** Get Current Weather Conditions ***\n')
    city = input("\nPlease enter a city name: ")
    if not bool(city.strip()):
        city = "Hyderabad"
    weather_data = get_current_weather(city)
    print("\n")
    pprint(weather_data)
