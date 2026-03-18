import os
import sqlite3
import math
import numpy as np
import joblib
import datetime
from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
from sklearn.base import clone
from sklearn.metrics import mean_absolute_error
from sklearn.model_selection import train_test_split
from database import get_db_connection
from config import DB_FILE, MODEL_FILE
from utils import calculate_sun_elevation

def build_training_data():
    conn = get_db_connection()
    c = conn.cursor()
    # SQL erweitert um die neuen Spalten
    c.execute("""
        SELECT day, kwh, avg_clouds, avg_temp, daylight_duration, sunshine_duration 
        FROM daily_stats 
        WHERE kwh IS NOT NULL 
        ORDER BY day
    """)
    rows = c.fetchall()
    conn.close()

    X, y, kwh_history = [], [], []

    for day_str, kwh, clouds, temp, daylight, sunshine in rows:
        date = datetime.datetime.strptime(day_str, "%Y-%m-%d")
        day_of_year = date.timetuple().tm_yday
        
        # Zeitliche Features
        sin_day = math.sin(2 * math.pi * day_of_year / 365)
        cos_day = math.cos(2 * math.pi * day_of_year / 365)
        sun_elev = calculate_sun_elevation(date)
        
        # Lag-Features (was war gestern?)
        prev_kwh = kwh_history[-1] if kwh_history else 0
        rolling_avg = sum(kwh_history[-7:]) / 7 if len(kwh_history) >= 7 else prev_kwh

        # X-Vektor mit den neuen Werten (Daylight & Sunshine in Sekunden)
        X.append([
            sin_day, 
            cos_day, 
            clouds or 0, 
            temp or 0, 
            sun_elev, 
            prev_kwh, 
            rolling_avg,
            daylight or 0,
            sunshine or 0
        ])
        y.append(kwh)
        kwh_history.append(kwh)

    return np.array(X), np.array(y)

def train_model():
    X, y = build_training_data()
    # Da wir mehr Features haben, sollten wir mind. 10-15 Tage haben für ein erstes Training
    if len(X) < 15:
        print(f"⚠️ Nicht genug Trainingsdaten ({len(X)}/15).")
        return None

    # Feature-Liste erweitert
    feature_names = [
        "sin_day", "cos_day", "clouds", "temperature", 
        "sun_elevation", "prev_kwh", "rolling_avg", 
        "daylight", "sunshine"
    ]
    
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, shuffle=False)

    # 1. Hauptmodell
    model = RandomForestRegressor(n_estimators=300, random_state=42)
    model.fit(X_train, y_train)
    
    # 2. Unteres Quantil (Worst Case)
    model_low = GradientBoostingRegressor(loss='quantile', alpha=0.1, n_estimators=300, random_state=42)
    model_low.fit(X_train, y_train)

    # 3. Oberes Quantil (Best Case)
    model_high = GradientBoostingRegressor(loss='quantile', alpha=0.9, n_estimators=300, random_state=42)
    model_high.fit(X_train, y_train)

    mae = mean_absolute_error(y_test, model.predict(X_test))
    
    joblib.dump({
        "model": model, 
        "model_low": model_low, 
        "model_high": model_high,
        "mae": mae, 
        "feature_names": feature_names
    }, MODEL_FILE)

    print(f"✅ Modell trainiert mit {len(feature_names)} Features | MAE: {round(mae,3)}")
    return model

def load_or_train_model():
    if os.path.exists(MODEL_FILE):
        try:
            bundle = joblib.load(MODEL_FILE)
            # Kleiner Check, ob die Feature-Anzahl noch stimmt (falls du upgradest)
            if len(bundle["feature_names"]) != 9:
                print("🔄 Altes Modell erkannt, trainiere neu mit 9 Features...")
                return train_model()
            return bundle
        except:
            return train_model()
    return train_model()

