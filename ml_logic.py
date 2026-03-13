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
from config import DB_FILE, MODEL_FILE
from utils import calculate_sun_elevation

def build_training_data():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT day, kwh, avg_clouds, avg_temp FROM daily_stats WHERE kwh IS NOT NULL ORDER BY day")
    rows = c.fetchall()
    conn.close()

    X, y, kwh_history = [], [], []

    for day_str, kwh, clouds, temp in rows:
        date = datetime.datetime.strptime(day_str, "%Y-%m-%d")
        day_of_year = date.timetuple().tm_yday
        sin_day = math.sin(2 * math.pi * day_of_year / 365)
        cos_day = math.cos(2 * math.pi * day_of_year / 365)
        sun_elev = calculate_sun_elevation(date)
        prev_kwh = kwh_history[-1] if kwh_history else 0
        rolling_avg = sum(kwh_history[-7:]) / 7 if len(kwh_history) >= 7 else prev_kwh

        X.append([sin_day, cos_day, clouds or 0, temp or 0, sun_elev, prev_kwh, rolling_avg])
        y.append(kwh)
        kwh_history.append(kwh)

    return np.array(X), np.array(y)

def train_model():
    X, y = build_training_data()
    if len(X) < 8: #15!!!
        print("⚠️ Nicht genug Trainingsdaten.")
        return None

    feature_names = ["sin_day", "cos_day", "clouds", "temperature", "sun_elevation", "prev_kwh", "rolling_avg"]
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, shuffle=False)

    # 1. RandomForestRegressor für den Erwartungswert)
    model = RandomForestRegressor(n_estimators=300, random_state=42)
    model.fit(X_train, y_train)
    
    # 2. Unteres Quantil (z.B. 10% Perzentil - "Worst Case") mit GradientBoostingRegressor
    model_low = GradientBoostingRegressor(loss='quantile', alpha=0.1, n_estimators=300, random_state=42) # 0.1 entspricht dem 10. Perzentil
    model_low.fit(X_train, y_train)

    # 3. Oberes Quantil (z.B. 90% Perzentil - "Best Case") mit GradientBoostingRegressor
    model_high = GradientBoostingRegressor(loss='quantile', alpha=0.9, n_estimators=300, random_state=42) # 0.9 entspricht dem 90. Perzentil
    model_high.fit(X_train, y_train)

    mae = mean_absolute_error(y_test, model.predict(X_test))
    
    joblib.dump({
        "model": model, "model_low": model_low, "model_high": model_high,
        "mae": mae, "feature_names": feature_names
    }, MODEL_FILE)

    print(f"✅ Modell trainiert | MAE: {round(mae,3)}")
    return model

def load_or_train_model():
    if os.path.exists(MODEL_FILE):
        return joblib.load(MODEL_FILE)
    train_model()
    return joblib.load(MODEL_FILE)
