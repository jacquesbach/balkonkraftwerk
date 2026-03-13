import math
import requests
from config import LATITUDE, LONGITUDE, GAP_THRESHOLD

def trapezoid_wh(prev_w, w, dt):
    """Manuelle Trapez-Berechnung in Python (falls benötigt)"""
    if prev_w is None or dt is None:
        return 0.0
    if 0 < dt <= GAP_THRESHOLD:
        return ((prev_w + w) / 2.0) * (dt / 3600.0)
    return 0.0

def calculate_eur(kwh, date_str, prices):
    """
    Einheitliche Euro-Berechnung mit hoher Präzision.
    Rundung immer auf 6 Nachkommastellen.
    """
    for p in prices:
        if date_str >= p["date"]:
            return round(kwh * p["price"], 6)
    return round(kwh * 0.329, 6)

def calculate_sun_elevation(date):
    """Berechnet den ungefähren Sonnenstand basierend auf Breitengrad und Tag im Jahr."""
    day_of_year = date.timetuple().tm_yday
    # vereinfachtes astronomisches Modell
    decl = -23.44 * math.cos(math.radians((360/365) * (day_of_year + 10)))
    elevation = 90 - abs(LATITUDE - decl)
    return max(elevation, 0)

def get_historical_avg_temp(day):
    """
    Holt Tagesmitteltemperatur von OpenMeteo Historical API.
    """
    try:
        url = (
            f"https://archive-api.open-meteo.com/v1/archive"
            f"?latitude={LATITUDE}"
            f"&longitude={LONGITUDE}"
            f"&start_date={day}"
            f"&end_date={day}"
            f"&daily=temperature_2m_mean"
            f"&timezone=Europe/Berlin"
        )
        r = requests.get(url, timeout=5)
        data = r.json().get("daily", {})
        temps = data.get("temperature_2m_mean", [])
        if temps:
            return float(temps[0])
    except Exception as e:
        print("Historical Temp Error:", e)
    return 0.0

def get_weather_forecast(days=7):
    """Holt die Wettervorhersage für die nächsten Tage."""
    try:
        url = (
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={LATITUDE}"
            f"&longitude={LONGITUDE}"
            f"&daily=cloud_cover_mean,temperature_2m_mean"
            f"&timezone=Europe/Berlin"
        )
        r = requests.get(url, timeout=5)
        data = r.json().get("daily", {})
        dates = data.get("time", [])
        clouds = data.get("cloud_cover_mean", [])
        temps = data.get("temperature_2m_mean", [])
        return list(zip(dates[:days], clouds[:days], temps[:days]))
    except Exception as e:
        print("Forecast Weather Error:", e)
        return []