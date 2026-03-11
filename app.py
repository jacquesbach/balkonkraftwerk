from flask import Flask, request, jsonify, render_template, send_from_directory
import sqlite3
import json
import datetime
import requests
import os
from dotenv import load_dotenv
import time
import math
import numpy as np
import joblib
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
from sklearn.base import clone
from sklearn.metrics import mean_absolute_error
from sklearn.model_selection import train_test_split
import shap
from flask_mqtt import Mqtt

os.environ['TZ'] = 'Europe/Berlin'
time.tzset()
load_dotenv()

app = Flask(__name__, template_folder='.')
loading_status = {"loading": False}

# ================= KONFIGURATION =================
DB_FILE = "solar_data.db"
ADMIN_PASS = os.getenv("ADMIN_PASS")
LATITUDE = float(os.getenv("LATITUDE", 0.0))
LONGITUDE = float(os.getenv("LONGITUDE", 0.0))
GAP_THRESHOLD = 45  # 3 x 15 Sekunden Logging
MODEL_FILE = "pv_model.pkl"
app.config['MQTT_BROKER_URL'] = os.getenv("MQTT_BROKER_URL")
app.config['MQTT_BROKER_PORT'] = int(os.getenv("MQTT_BROKER_PORT", 1883))
app.config['MQTT_USERNAME'] = os.getenv("MQTT_USERNAME")
app.config['MQTT_PASSWORD'] = os.getenv("MQTT_PASSWORD")
app.config['MQTT_TLS_ENABLED'] = False

TRAPEZOID_SQL = f"""
CASE
    WHEN prev_t IS NOT NULL
         AND dt > 0
         AND dt <= {GAP_THRESHOLD}
    THEN ((prev_w + w) / 2.0) * (dt / 3600.0)
    ELSE 0
END
"""
# =================================================

mqtt = Mqtt(app)

# Globaler Zwischenspeicher
mqtt_values = {
    "ac_power_w": 0.0,
    "dc_power_w": 0.0,
    "panel1_w": 0.0,
    "panel2_w": 0.0,
    "inverter_temp_c": 0.0,
    "last_ts": 0  # Zeitstempel der DTU
}

@mqtt.on_connect()
def handle_connect(client, userdata, flags, rc):
    # Topics basierend auf deinem Screenshot:
    mqtt.subscribe('inverter_stuebli/Balkonkraftwerk/ch0')
    mqtt.subscribe('inverter_stuebli/Balkonkraftwerk/ch1')
    mqtt.subscribe('inverter_stuebli/Balkonkraftwerk/ch2')

@mqtt.on_message()
def handle_mqtt_message(client, userdata, message):
    payload = message.payload.decode()
    topic = message.topic
    
    try:
        data = json.loads(payload)

        mqtt_values["last_ts"] = time.time()

        if 'ch0' in topic:
            mqtt_values["ac_power_w"] = data.get("P_AC", 0.0)
            mqtt_values["dc_power_w"] = data.get("P_DC", 0.0)
            mqtt_values["inverter_temp_c"] = data.get("Temp", 0.0)
        elif 'ch1' in topic:
            mqtt_values["panel1_w"] = data.get("P_DC", 0.0)
        elif 'ch2' in topic:
            mqtt_values["panel2_w"] = data.get("P_DC", 0.0)
    
    except Exception as e:
        # Falls mal ein kaputtes JSON kommt, stürzt der Thread nicht ab
        print(f"MQTT Parse Error: {e}")

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    # 1. Haupttabelle für Rohdaten
    c.execute('''
        CREATE TABLE IF NOT EXISTS data (
            t TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            w REAL,
            a REAL,
            v REAL,
            clouds REAL,
            ac_power_w REAL,
            dc_power_w REAL,
            panel1_w REAL,
            panel2_w REAL,
            inverter_temp_c REAL
        )
    ''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_data_t ON data(t)')
    
    # 2. Tabelle für aggregierte Tagesstatistiken
    c.execute('''
        CREATE TABLE IF NOT EXISTS daily_stats (
            day TEXT PRIMARY KEY,
            kwh REAL,
            eur REAL,
            avg_clouds REAL,
            max_w REAL,
            avg_temp REAL,
            max_w_panel1 REAL,
            max_w_panel2 REAL,
            kwh_panel1 REAL,
            kwh_panel2 REAL,
            kwh_dc_total REAL
        )
    ''')
    
    # 3. Globale Gesamt-Statistiken
    c.execute('''
        CREATE TABLE IF NOT EXISTS stats (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            total_kwh REAL,
            total_eur REAL
        )
    ''')
    c.execute("INSERT OR IGNORE INTO stats (id, total_kwh, total_eur) VALUES (1, 0, 0)")
    
    # 4. Preis-Tabelle
    c.execute('''
        CREATE TABLE IF NOT EXISTS prices (
            valid_from DATE PRIMARY KEY, 
            price REAL
        )
    ''')

    # Initialen Preis setzen, falls Tabelle leer
    c.execute("SELECT COUNT(*) FROM prices")
    if c.fetchone()[0] == 0:
        c.execute("INSERT INTO prices (valid_from, price) VALUES ('2026-01-01', 0.329)")
        
    conn.commit()
    conn.close()
    print("Datenbank erfolgreich initialisiert.")

def force_rebuild_daily_stats():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    print("🔥 Starte kompletten Neuaufbau von daily_stats...")

    # 1️⃣ daily_stats komplett leeren
    c.execute("DELETE FROM daily_stats")

    # 2️⃣ stats sauber zurücksetzen
    c.execute("""
        UPDATE stats
        SET total_kwh = 0,
            total_eur = 0
        WHERE id = 1
    """)

    conn.commit()

    # 3️⃣ Alle Tage aus Rohdaten holen außer heute
    today = datetime.date.today().strftime("%Y-%m-%d")
    c.execute("""
        SELECT DISTINCT date(t)
        FROM data
        WHERE date(t) < ?
    """, (today,))
    days = [row[0] for row in c.fetchall()]

    conn.close()

    # 4️⃣ Für jeden Tag neu berechnen
    for d in days:
        finalize_day(d)

    print(f"✅ Rebuild abgeschlossen. {len(days)} Tage neu berechnet.")

def finalize_day(day):
    # Heute niemals finalisieren
    today = datetime.date.today().strftime("%Y-%m-%d")
    if day >= today:
        return

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    # --- NEU: Trapez-Regel dynamisch für die anderen Spalten klonen ---
    trap_p1 = TRAPEZOID_SQL.replace("prev_w", "prev_p1").replace("+ w", "+ p1")
    trap_p2 = TRAPEZOID_SQL.replace("prev_w", "prev_p2").replace("+ w", "+ p2")
    trap_dc = TRAPEZOID_SQL.replace("prev_w", "prev_dc").replace("+ w", "+ dc")

    c.execute(f"""
        WITH base AS (
            SELECT 
                t,
                w,
                panel1_w as p1,
                panel2_w as p2,
                dc_power_w as dc,
                LAG(t) OVER (ORDER BY t) as prev_t,
                LAG(w) OVER (ORDER BY t) as prev_w,
                LAG(panel1_w) OVER (ORDER BY t) as prev_p1,
                LAG(panel2_w) OVER (ORDER BY t) as prev_p2,
                LAG(dc_power_w) OVER (ORDER BY t) as prev_dc,
                (strftime('%s', t) - strftime('%s', LAG(t) OVER (ORDER BY t))) as dt,
                clouds
            FROM data
            WHERE date(t) = ?
        )
        SELECT
            SUM({TRAPEZOID_SQL}) as total_wh,
            AVG(clouds),
            MAX(w),
            MAX(p1),
            MAX(p2),
            SUM({trap_p1}) as wh_p1,
            SUM({trap_p2}) as wh_p2,
            SUM({trap_dc}) as wh_dc
        FROM base
    """, (day,))

    row = c.fetchone()

    if row and row[0] is not None:

        total_wh = float(row[0])
        avg_clouds = float(row[1]) if row[1] is not None else 0.0
        max_w = float(row[2]) if row[2] is not None else 0.0
        max_w_p1 = float(row[3]) if row[3] is not None else 0.0
        max_w_p2 = float(row[4]) if row[4] is not None else 0.0
        wh_p1 = float(row[5]) if row[5] is not None else 0.0
        wh_p2 = float(row[6]) if row[6] is not None else 0.0
        wh_dc = float(row[7]) if row[7] is not None else 0.0
        avg_temp = get_historical_avg_temp(day)

        kwh = total_wh / 1000.0
        kwh_p1 = wh_p1 / 1000.0
        kwh_p2 = wh_p2 / 1000.0
        kwh_dc = wh_dc / 1000.0

        # Preis sauber aus prices-Tabelle holen
        c.execute("""
            SELECT valid_from, price 
            FROM prices 
            ORDER BY valid_from DESC
        """)
        prices = c.fetchall()

        def get_price_for_date(date_str):
            for p in prices:
                if date_str >= p[0]:
                    return p[1]
            return 0.35

        price = get_price_for_date(day)
        prices_list = [{"date": p[0], "price": p[1]} for p in prices]
        eur = calculate_eur(kwh, day, prices_list)

        # 🔒 Speicherung mit hoher Präzision (DB)
        kwh_db = round(kwh, 6)
        eur_db = round(eur, 6)
        kwh_p1_db = round(kwh_p1, 6)
        kwh_p2_db = round(kwh_p2, 6)
        kwh_dc_db = round(kwh_dc, 6)

        c.execute("""
            INSERT OR REPLACE INTO daily_stats 
            (day, kwh, eur, avg_clouds, avg_temp, max_w, max_w_panel1, max_w_panel2, kwh_panel1, kwh_panel2, kwh_dc_total)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            day,
            kwh_db,
            eur_db,
            round(avg_clouds, 2),
            round(avg_temp, 2),
            round(max_w, 1),
            round(max_w_p1, 1),
            round(max_w_p2, 1),
            kwh_p1_db,
            kwh_p2_db,
            kwh_dc_db
        ))

        conn.commit()

    conn.close()
    
    # Modell neu trainieren nach Tagesabschluss
    train_model()

def self_heal_daily_stats():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    # Alle Tage aus Rohdaten holen außer heute
    today = datetime.date.today().strftime("%Y-%m-%d")
    c.execute("""
        SELECT DISTINCT date(t)
        FROM data
        WHERE date(t) < ?
        ORDER BY date(t)
    """, (today,))
    data_days = [row[0] for row in c.fetchall()]

    # Alle Tage aus daily_stats holen
    c.execute("SELECT day FROM daily_stats")
    existing_days = {row[0] for row in c.fetchall()}

    conn.close()

    missing_days = [d for d in data_days if d not in existing_days]

    if missing_days:
        print(f"Self-Heal: {len(missing_days)} fehlende Tage werden berechnet...")
        for day in missing_days:
            finalize_day(day)
        print("Self-Heal abgeschlossen.")

def trapezoid_wh(prev_w, w, dt):
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

def build_training_data():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    c.execute("""
        SELECT day, kwh, avg_clouds, avg_temp
        FROM daily_stats
        WHERE kwh IS NOT NULL
        ORDER BY day
    """)

    rows = c.fetchall()
    conn.close()

    X = []
    y = []

    kwh_history = []

    for i, (day_str, kwh, clouds, temp) in enumerate(rows):

        date = datetime.datetime.strptime(day_str, "%Y-%m-%d")
        day_of_year = date.timetuple().tm_yday

        sin_day = math.sin(2 * math.pi * day_of_year / 365)
        cos_day = math.cos(2 * math.pi * day_of_year / 365)

        sun_elev = calculate_sun_elevation(date)

        prev_kwh = kwh_history[-1] if kwh_history else 0

        if len(kwh_history) >= 7:
            rolling_avg = sum(kwh_history[-7:]) / 7
        else:
            rolling_avg = prev_kwh

        X.append([
            sin_day,
            cos_day,
            clouds or 0,
            temp or 0,
            sun_elev,
            prev_kwh,
            rolling_avg
        ])

        y.append(kwh)
        kwh_history.append(kwh)

    return np.array(X), np.array(y)


def train_model():
    X, y = build_training_data()

    if len(X) < 8: #15!!!
        print("⚠️ Nicht genug Trainingsdaten.")
        return None

    feature_names = [
        "sin_day",
        "cos_day",
        "clouds",
        "temperature",
        "sun_elevation",
        "prev_kwh",
        "rolling_avg"
    ]

    X_train, X_test, y_train, y_test = train_test_split(
        X, y,
        test_size=0.2,
        shuffle=False
    )

    # 1. RandomForestRegressor für den Erwartungswert)
    model = RandomForestRegressor(
        n_estimators=300,
        random_state=42
    )
    model.fit(X_train, y_train)

    # 2. Unteres Quantil (z.B. 10% Perzentil - "Worst Case") mit GradientBoostingRegressor
    model_low = GradientBoostingRegressor(
        loss='quantile', 
        alpha=0.1,  # 0.1 entspricht dem 10. Perzentil
        n_estimators=300, 
        random_state=42
    )
    model_low.fit(X_train, y_train)

    # 3. Oberes Quantil (z.B. 90% Perzentil - "Best Case") mit GradientBoostingRegressor
    model_high = GradientBoostingRegressor(
        loss='quantile', 
        alpha=0.9,  # 0.9 entspricht dem 90. Perzentil
        n_estimators=300, 
        random_state=42
    )
    model_high.fit(X_train, y_train)

    y_pred = model.predict(X_test)
    mae = mean_absolute_error(y_test, y_pred)
    
    joblib.dump({
        "model": model,
        "model_low": model_low,
        "model_high": model_high,
        "mae": mae,
        "feature_names": feature_names
    }, MODEL_FILE)

    print(f"✅ Modell trainiert | MAE: {round(mae,3)}")

    return model

def load_or_train_model():
    if os.path.exists(MODEL_FILE):
        return joblib.load(MODEL_FILE)
    train_model()
    return joblib.load(MODEL_FILE)

def get_weather_forecast(days=7):
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

@app.route('/')
def index():
    return render_template('templates/index.html')

@app.route('/api/auth', methods=['POST'])
def auth():
    data = request.json
    if data and data.get('pw') == ADMIN_PASS:
        return jsonify({"status": "ok"}), 200
    return jsonify({"error": "Falsches Passwort"}), 403


@app.route('/api/update')
def update():
    w = request.args.get('watt', 0, type=float)
    a = request.args.get('ampere', 0, type=float)
    v = request.args.get('volt', 0, type=float)

    local_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    clouds = None
    try:
        url = f"https://api.open-meteo.com/v1/forecast?latitude={LATITUDE}&longitude={LONGITUDE}&current=cloud_cover"
        r = requests.get(url, timeout=2)
        if r.status_code == 200:
            clouds = r.json().get('current', {}).get('cloud_cover')
    except:
        pass
    
    # --- 40-SEKUNDEN-CHECK ---
    # Wir prüfen, ob der DTU-Zeitstempel (ts) aktuell ist
    current_time = time.time()
    if (current_time - mqtt_values["last_ts"]) > 40:
        mqtt_data = [None] * 5
    else:
        mqtt_data = [
            mqtt_values["ac_power_w"],
            mqtt_values["dc_power_w"],
            mqtt_values["panel1_w"],
            mqtt_values["panel2_w"],
            mqtt_values["inverter_temp_c"]
        ]
    
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    c.execute(
        "INSERT INTO data (t, w, a, v, clouds, ac_power_w, dc_power_w, panel1_w, panel2_w, inverter_temp_c) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (local_time, w, a, v, clouds, *mqtt_data)
    )
    conn.commit()

    # 🔥 Fallback-Check für gestern
    yesterday = (datetime.datetime.now() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")

    c.execute("SELECT 1 FROM daily_stats WHERE day = ?", (yesterday,))
    exists = c.fetchone()

    conn.close()

    if not exists:
        finalize_day(yesterday)

    return jsonify({"status": "ok"})


@app.route('/api/live')
def live():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT w, a, v, t, panel1_w, panel2_w FROM data ORDER BY t DESC LIMIT 1")
    row = c.fetchone()
    conn.close()
    if row:
        return jsonify({
            "w": row[0], 
            "a": row[1], 
            "v": row[2], 
            "t": row[3],
            "panel1_w": row[4] if row[4] is not None else 0.0,
            "panel2_w": row[5] if row[5] is not None else 0.0
        })
    return jsonify({"w": 0, "a": 0, "v": 0, "t": 0, "panel1_w": 0, "panel2_w": 0})

@app.route('/api/widget')
def widget():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    # 1. Aktuelle Live-Werte
    c.execute("SELECT w, a, v FROM data ORDER BY t DESC LIMIT 1")
    live = c.fetchone() or (0, 0, 0)

    # 2. Heutige kWh berechnen
    today = datetime.date.today().strftime("%Y-%m-%d")

    c.execute(f"""
        WITH base AS (
            SELECT
                t,
                w,
                LAG(t) OVER (ORDER BY t) as prev_t,
                LAG(w) OVER (ORDER BY t) as prev_w,
                (strftime('%s', t) - strftime('%s', LAG(t) OVER (ORDER BY t))) as dt
            FROM data
            WHERE date(t) = ?
        )
        SELECT SUM({TRAPEZOID_SQL}) as total_wh
        FROM base
    """, (today,))
    
    result = c.fetchone()
    total_wh = result[0] or 0.0

    conn.close()
    return jsonify({
        "w": round(live[0], 1),
        "ma": round(live[1] * 1000, 0),
        "v": round(live[2], 1),
        "kwh": round(total_wh / 1000.0, 3)
    })


@app.route('/api/status')
def status():
    return jsonify(loading_status)


@app.route('/api/data')
def get_data():
    loading_status["loading"] = True
    start = request.args.get('start', '2020-01-01')
    end = request.args.get('end', '2099-12-31')
    show_p1 = request.args.get('p1', 'false').lower() == 'true'
    show_p2 = request.args.get('p2', 'false').lower() == 'true'

    start_dt = datetime.datetime.strptime(start, "%Y-%m-%d")
    end_dt = datetime.datetime.strptime(end, "%Y-%m-%d") + datetime.timedelta(days=1)
    end_str = end_dt.strftime("%Y-%m-%d")

    diff_hours_total = (end_dt - start_dt).total_seconds() / 3600

    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    # Trapez-Klone für die Panels (wie zuvor besprochen)
    trap_p1 = TRAPEZOID_SQL.replace("prev_w", "prev_p1").replace("+ w", "+ p1")
    trap_p2 = TRAPEZOID_SQL.replace("prev_w", "prev_p2").replace("+ w", "+ p2")

    # Preislogik
    c.execute("SELECT valid_from, price FROM prices ORDER BY valid_from DESC")
    prices = [{"date": row["valid_from"], "price": row["price"]} for row in c.fetchall()]

    def get_price_for_date(date_str):
        for p in prices:
            if date_str >= p["date"]:
                return p["price"]
        return 0.35

    # ============================================================
    # 🔵 LANGZEIT: > 90 Tage → Monatsaggregation (kWh) + Tages-EUR
    # ============================================================

    if diff_hours_total > 24 * 90:

        # 1️⃣ kWh monatsweise aus daily_stats
        c.execute("""
            SELECT
                strftime('%Y-%m', day) as month,
                SUM(kwh) as month_kwh,
                SUM(kwh_panel1) as month_p1,
                SUM(kwh_panel2) as month_p2
            FROM daily_stats
            WHERE day >= ? AND day < ?
            GROUP BY month
            ORDER BY month
        """, (start, end_str))

        monthly_rows = c.fetchall()
        monthly_data = {row["month"]: {
            "kwh": float(row["month_kwh"] or 0.0),
            "p1": float(row["month_p1"] or 0.0),
            "p2": float(row["month_p2"] or 0.0)
        } for row in monthly_rows}

        # 2️⃣ EUR taggenau aus daily_stats summieren
        c.execute("""
            SELECT day, kwh
            FROM daily_stats
            WHERE day >= ? AND day < ?
        """, (start, end_str))

        total_euro = 0.0
        total_kwh = 0.0

        for row in c.fetchall():
            day = row["day"]
            kwh = float(row["kwh"] or 0.0)

            total_kwh += kwh
            eur = calculate_eur(kwh, day, prices)
            total_euro += eur

        # 3️⃣ HEUTE LIVE ergänzen (falls im Zeitraum)
        today = datetime.date.today().strftime("%Y-%m-%d")

        if start <= today < end_str:

            c.execute(f"""
                WITH base AS (
                    SELECT 
                        t,
                        w,
                        panel1_w as p1,
                        panel2_w as p2,
                        LAG(t) OVER (ORDER BY t) as prev_t,
                        LAG(w) OVER (ORDER BY t) as prev_w,
                        LAG(panel1_w) OVER (ORDER BY t) as prev_p1,
                        LAG(panel2_w) OVER (ORDER BY t) as prev_p2,
                        (strftime('%s', t) - strftime('%s', LAG(t) OVER (ORDER BY t))) as dt
                    FROM data
                    WHERE date(t) = ?
                )
                SELECT
                      SUM({TRAPEZOID_SQL}),
                      SUM({trap_p1}),
                      SUM({trap_p2})
                FROM base
            """, (today,))

            res = c.fetchone()
            today_kwh = (res[0] or 0.0)/1000
            today_p1 = (res[1] or 0.0)/1000
            today_p2 = (res[2] or 0.0)/1000
            month_key = today[:7]
            if month_key not in monthly_data:
                monthly_data[month_key] = {"kwh":0, "p1":0, "p2":0}
            monthly_data[month_key]["kwh"] += today_kwh
            monthly_data[month_key]["p1"] += today_p1
            monthly_data[month_key]["p2"] += today_p2

            total_kwh += today_kwh
            total_euro += today_kwh * get_price_for_date(today)

        # 4️⃣ History bauen
        history = []

        for month in sorted(monthly_data.keys()):
            item = {"t": month, "kwh": round(monthly_data[month]["kwh"], 3)}
            if show_p1:
                item["p1_kwh"] = round(monthly_data[month]["p1"], 3)
            if show_p2:
                item["p2_kwh"] = round(monthly_data[month]["p2"], 3)
            
            # EUR Berechnung bleibt wie im Original
            c.execute("SELECT day, kwh FROM daily_stats WHERE strftime('%Y-%m', day) = ?", (month,))
            item["eur"] = round(sum(calculate_eur(float(row["kwh"] or 0.0), row["day"], prices) for row in c.fetchall()), 2)
            history.append(item)
        
        conn.close()
        loading_status["loading"] = False

        return jsonify({
            "history": history,
            "total_kwh": round(total_kwh, 3),
            "total_euro": round(total_euro, 2)
        })

    # ============================================================
    # 🔵 ≤ 90 Tage → Original Rohdaten-Logik
    # ============================================================

    if diff_hours_total <= 72:
        bucket_expr = "strftime('%Y-%m-%d %H:00', prev_t)"
    else:
        bucket_expr = "strftime('%Y-%m-%d', prev_t)"

    c.execute(f"""
        WITH base AS (
            SELECT 
                t,
                w,
                panel1_w as p1,
                panel2_w as p2,
                LAG(t) OVER (ORDER BY t) as prev_t,
                LAG(w) OVER (ORDER BY t) as prev_w,
                LAG(panel1_w) OVER (ORDER BY t) as prev_p1,
                LAG(panel2_w) OVER (ORDER BY t) as prev_p2,
                (strftime('%s', t) - strftime('%s', LAG(t) OVER (ORDER BY t))) as dt
            FROM data
            WHERE t >= ? AND t < ?
        ),
        energy AS (
            SELECT
                prev_t,
                w,
                prev_w,
                dt,
                {TRAPEZOID_SQL} as wh,
                {trap_p1} as wh_p1,
                {trap_p2} as wh_p2
            FROM base
        )
        SELECT
            {bucket_expr} as bucket,
            SUM(wh) as b_wh,
            SUM(wh_p1) as b_p1,
            SUM(wh_p2) as b_p2
        FROM energy
        WHERE prev_t IS NOT NULL
        GROUP BY bucket
        ORDER BY bucket
    """, (start, end_str))

    rows = c.fetchall()

    history = []
    total_kwh = 0.0
    total_euro = 0.0

    for row in rows:
        bucket_iso = row["bucket"]
        kwh = (row["b_wh"] or 0.0) / 1000.0
    
        if diff_hours_total <= 72:
            dt_obj = datetime.datetime.strptime(bucket_iso, "%Y-%m-%d %H:%M")
            bucket_display = dt_obj.strftime("%d.%m. %H:00")
            date_part = dt_obj.strftime("%Y-%m-%d")
        else:
            bucket_display = bucket_iso
            date_part = bucket_iso
    
        eur = calculate_eur(kwh, date_part, prices)

        item = {"t": bucket_display, "kwh": round(kwh, 3), "eur": round(eur, 2)}
        if show_p1:
            item["p1_kwh"] = round((row["b_p1"] or 0.0) / 1000.0, 3)
        if show_p2:
            item["p2_kwh"] = round((row["b_p2"] or 0.0) / 1000.0, 3)
        
        history.append(item)
        total_kwh += kwh
        total_euro += eur
    
    conn.close()
    loading_status["loading"] = False

    return jsonify({
        "history": history,
        "total_kwh": round(total_kwh, 3),
        "total_euro": round(total_euro, 2)
    })

@app.route('/api/prices', methods=['GET', 'POST', 'DELETE'])
def manage_prices():
    pw = request.args.get('pw') or (request.json and request.json.get('pw'))
    if pw != ADMIN_PASS:
        return jsonify({"error": "Falsches Passwort"}), 401

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    if request.method == 'GET':
        c.execute("SELECT valid_from, price FROM prices ORDER BY valid_from DESC")
        data = [{"date": r[0], "price": r[1]} for r in c.fetchall()]
        conn.close()
        return jsonify(data)

    elif request.method == 'POST':
        data = request.json
        c.execute("INSERT OR REPLACE INTO prices (valid_from, price) VALUES (?, ?)",
                  (data['date'], data['price']))
        conn.commit()
        conn.close()
        return jsonify({"status": "ok"})

    elif request.method == 'DELETE':
        date_to_del = request.json.get('date')
        c.execute("DELETE FROM prices WHERE valid_from = ?", (date_to_del,))
        conn.commit()
        conn.close()
        return jsonify({"status": "ok"})


@app.route('/api/weather')
def weather():
    try:
        # Abruf von Temperatur und Wetter-Code
        url = f"https://api.open-meteo.com/v1/forecast?latitude={LATITUDE}&longitude={LONGITUDE}&current=temperature_2m,weather_code"
        r = requests.get(url, timeout=5)
        d = r.json().get('current', {})
        code = d.get('weather_code', 0)
        temp = round(d.get('temperature_2m', 0), 1)

        # Mapping von WMO-Codes zu Text und OpenWeather-Icons (damit das Frontend weiter funktioniert)
        wmo_mapping = {
            0: ("Sonnig", "01d"),
            1: ("Heiter", "02d"), 2: ("Wolkig", "03d"), 3: ("Bedeckt", "04d"),
            45: ("Neblig", "50d"), 48: ("Reifnebel", "50d"),
            51: ("Nieselregen", "09d"), 53: ("Nieselregen", "09d"), 55: ("Nieselregen", "09d"),
            61: ("Leichter Regen", "10d"), 63: ("Regen", "10d"), 65: ("Starker Regen", "10d"),
            71: ("Schneefall", "13d"), 73: ("Schneefall", "13d"), 75: ("Schneefall", "13d"),
            80: ("Regenschauer", "09d"), 81: ("Regenschauer", "09d"), 82: ("Starker Schauer", "09d"),
            95: ("Gewitter", "11d")
        }

        desc, icon = wmo_mapping.get(code, ("Unbekannt", "01d"))

        return jsonify({
            "temp": temp,
            "desc": desc,
            "icon": icon
        })
    except Exception as e:
        print(f"Wetter-Fehler: {e}")
        return jsonify({"temp": "--", "desc": "Fehler", "icon": ""})


@app.route('/api/roi')
def get_roi():

    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # 🔹 Preise laden
    c.execute("SELECT valid_from, price FROM prices ORDER BY valid_from DESC")
    prices = [{"date": r["valid_from"], "price": r["price"]} for r in c.fetchall()]

    total_kwh = 0.0
    total_eur = 0.0

    # =========================
    # 1️⃣ HISTORISCHE TAGE
    # =========================
    c.execute("""
        SELECT day, kwh
        FROM daily_stats
        ORDER BY day
    """)

    for row in c.fetchall():

        day = row["day"]
        kwh = float(row["kwh"] or 0.0)

        total_kwh += kwh
        total_eur += calculate_eur(kwh, day, prices)

    # =========================
    # 2️⃣ HEUTE LIVE
    # =========================

    today = datetime.date.today().strftime("%Y-%m-%d")

    c.execute(f"""
        WITH base AS (
            SELECT
                t,
                w,
                LAG(t) OVER (ORDER BY t) as prev_t,
                LAG(w) OVER (ORDER BY t) as prev_w,
                (strftime('%s', t) - strftime('%s', LAG(t) OVER (ORDER BY t))) as dt
            FROM data
            WHERE date(t) = ?
        )
        SELECT SUM({TRAPEZOID_SQL})
        FROM base
    """, (today,))

    today_wh = c.fetchone()[0] or 0.0
    today_kwh = today_wh / 1000.0

    total_kwh += today_kwh
    total_eur += calculate_eur(today_kwh, today, prices)

    conn.close()

    # =========================
    # ROI Berechnung
    # =========================

    cost = 50.0

    percent = (total_eur / cost) * 100 if cost > 0 else 0

    return jsonify({
        "total_kwh": round(total_kwh, 2),
        "total_eur": round(total_eur, 2),
        "cost": cost,
        "percent": min(round(percent, 1), 100)
    })


@app.route('/api/peaks')
def get_peaks():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    today = datetime.date.today().strftime("%Y-%m-%d")
    
    # 🔹 Tagespeaks live aus der 'data' Tabelle
    c.execute("""
        SELECT MAX(w), MAX(panel1_w), MAX(panel2_w) 
        FROM data 
        WHERE date(t) = ?
    """, (today,))
    row_today = c.fetchone()
    
    daily_peak = row_today[0] or 0.0
    daily_peak_p1 = row_today[1] or 0.0
    daily_peak_p2 = row_today[2] or 0.0

    # 🔹 All-Time Peaks aus daily_stats abrufen
    c.execute("SELECT MAX(max_w), MAX(max_w_panel1), MAX(max_w_panel2) FROM daily_stats")
    row_alltime = c.fetchone()
    
    at_peak_db = row_alltime[0] or 0.0
    at_p1_db = row_alltime[1] or 0.0
    at_p2_db = row_alltime[2] or 0.0

    # 🔹 Vergleich: Historisch vs. Heute (falls heute ein Rekordtag ist)
    alltime_peak = max(at_peak_db, daily_peak)
    alltime_peak_p1 = max(at_p1_db, daily_peak_p1)
    alltime_peak_p2 = max(at_p2_db, daily_peak_p2)

    conn.close()
    
    return jsonify({
        "daily_peak": round(daily_peak, 1),
        "daily_peak_p1": round(daily_peak_p1, 1),
        "daily_peak_p2": round(daily_peak_p2, 1),
        "alltime_peak": round(alltime_peak, 1),
        "alltime_peak_p1": round(alltime_peak_p1, 1),
        "alltime_peak_p2": round(alltime_peak_p2, 1)
    })

@app.route('/api/heatmap')
def get_heatmap():

    year = request.args.get("year")

    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # 🔹 verfügbare Jahre
    if not year:
        c.execute("""
            SELECT DISTINCT strftime('%Y', day) as year
            FROM daily_stats
            ORDER BY year DESC
        """)
        years = [row["year"] for row in c.fetchall()]
        conn.close()
        return jsonify({"years": years})

    # 🔹 Preise laden
    c.execute("SELECT valid_from, price FROM prices ORDER BY valid_from DESC")
    prices = [{"date": r[0], "price": r[1]} for r in c.fetchall()]

    def get_price_for_date(date_str):
        for p in prices:
            if date_str >= p["date"]:
                return p["price"]
        return 0.35

    start = f"{year}-01-01"
    end = f"{int(year)+1}-01-01"
    
    today = datetime.date.today().strftime("%Y-%m-%d")
    
    # 🔹 Vergangene Tage aus daily_stats (heute bewusst ausgeschlossen)
    c.execute("""
        SELECT day, kwh, eur, max_w
        FROM daily_stats
        WHERE day >= ?
          AND day < ?
          AND day < ?
        ORDER BY day
    """, (start, end, today))
    
    rows = c.fetchall()
    
    heatmap = []
    max_kwh = 0.0
    
    for row in rows:
        kwh = row["kwh"] or 0.0
        eur = calculate_eur( row["kwh"] or 0.0, row["day"], prices )
        max_w = row["max_w"] or 0.0
    
        max_kwh = max(max_kwh, kwh)
    
        heatmap.append({
            "date": row["day"],
            "kwh": round(kwh, 4),
            "eur": round(eur, 2),
            "max_w": round(max_w, 1)
        })
    
    # 🔹 Heutiger Tag live aus data (nur wenn Jahr passt)
    if today.startswith(year):
    
        c.execute(f"""
            WITH base AS (
                SELECT 
                    t,
                    w,
                    LAG(t) OVER (ORDER BY t) as prev_t,
                    LAG(w) OVER (ORDER BY t) as prev_w,
                    (strftime('%s', t) - strftime('%s', LAG(t) OVER (ORDER BY t))) as dt
                FROM data
                WHERE date(t) = ?
            )
            SELECT
                SUM({TRAPEZOID_SQL}),
                MAX(w)
            FROM base
        """, (today,))
    
        result = c.fetchone()
    
        if result and result[0] is not None:
    
            kwh_today = result[0] / 1000.0
            eur_today = calculate_eur(kwh_today, today, prices)
            max_w_today = result[1] or 0.0
    
            # 🔐 Sicherheitscheck: nur hinzufügen wenn nicht vorhanden
            if not any(d["date"] == today for d in heatmap):
    
                max_kwh = max(max_kwh, kwh_today)
    
                heatmap.append({
                    "date": today,
                    "kwh": round(kwh_today, 4),
                    "eur": round(eur_today, 2),
                    "max_w": round(max_w_today, 1)
                })

    conn.close()

    return jsonify({
        "heatmap": heatmap,
        "max": max_kwh
    })

@app.route('/api/heatmap_hourly')
def get_heatmap_hourly():
    month = request.args.get("month") # Format "YYYY-MM"
    
    # Timeout hinzugefügt, damit er im Zweifel wartet statt zu blockieren
    conn = sqlite3.connect(DB_FILE, timeout=10.0)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    # 🔹 Wenn kein Monat übergeben wird, verfügbare Monate ermitteln
    if not month:
        c.execute("""
            SELECT DISTINCT strftime('%Y-%m', day) as month 
            FROM daily_stats 
            ORDER BY month DESC
        """)
        months = [row["month"] for row in c.fetchall()]
        conn.close()
        return jsonify({"months": months})
        
    # 🔹 Zeitbereich in Python berechnen (Verhindert den SQL-Table-Scan!)
    # Aus "2026-03" machen wir Start: "2026-03-01 00:00:00" und Ende: "2026-04-01 00:00:00"
    y, m = map(int, month.split('-'))
    start_date = f"{y:04d}-{m:02d}-01 00:00:00"
    next_m = m + 1 if m < 12 else 1
    next_y = y if m < 12 else y + 1
    end_date = f"{next_y:04d}-{next_m:02d}-01 00:00:00"

    # 🔹 Trapez-Formeln für die beiden Panels
    trap_p1 = TRAPEZOID_SQL.replace("prev_w", "prev_p1").replace("+ w", "+ p1")
    trap_p2 = TRAPEZOID_SQL.replace("prev_w", "prev_p2").replace("+ w", "+ p2")
    
    try:
        # 🔹 Stunden-Buckets für den gesamten Monat live aus Rohdaten berechnen
        c.execute(f"""
            WITH base AS (
                SELECT 
                    t, 
                    panel1_w as p1, 
                    panel2_w as p2,
                    LAG(t) OVER (ORDER BY t) as prev_t,
                    LAG(panel1_w) OVER (ORDER BY t) as prev_p1,
                    LAG(panel2_w) OVER (ORDER BY t) as prev_p2,
                    (strftime('%s', t) - strftime('%s', LAG(t) OVER (ORDER BY t))) as dt
                FROM data
                WHERE t >= ? AND t < ? 
            ),
            energy AS (
                SELECT
                    strftime('%d', prev_t) as day_num,
                    strftime('%H', prev_t) as hour_num,
                    {trap_p1} as wh_p1,
                    {trap_p2} as wh_p2
                FROM base
                WHERE prev_t IS NOT NULL AND dt > 0 AND dt < 3600
            )
            SELECT 
                day_num, 
                hour_num, 
                SUM(wh_p1) as p1_wh, 
                SUM(wh_p2) as p2_wh
            FROM energy
            GROUP BY day_num, hour_num
        """, (start_date, end_date)) # Hier übergeben wir die schnellen Datumswerte
        
        rows = c.fetchall()
        
        # Daten für das Frontend aufbereiten
        heatmap_data = {}
        max_wh = 0.0
        
        for r in rows:
            d = r["day_num"]
            h = r["hour_num"]
            p1 = float(r["p1_wh"] or 0.0)
            p2 = float(r["p2_wh"] or 0.0)
            
            total = p1 + p2
            if total > max_wh: 
                max_wh = total
                
            if d not in heatmap_data:
                heatmap_data[d] = {}
                
            heatmap_data[d][h] = {"p1": p1, "p2": p2}
            
        return jsonify({
            "data": heatmap_data, 
            "max": max_wh
        })

    finally:
        # 🔹 WICHTIG: Das 'finally' garantiert, dass die Datenbankverbindung 
        # IMMER geschlossen wird (Sperre aufgehoben), selbst wenn die Abfrage abbricht!
        conn.close()

@app.route('/api/forecast')
def forecast():
  
    try:
        model_bundle = load_or_train_model()
    except Exception as e:
        print("Forecast Fallback aktiviert:", e)

    if model_bundle is None:
        return jsonify({"error": "Not enough training data yet."}), 400

    model = model_bundle["model"]
    model_low = model_bundle["model_low"]
    model_high = model_bundle["model_high"]
    mae = model_bundle["mae"]
    feature_names = model_bundle["feature_names"]

    # 🔒 SHAP Explainer sicher erzeugen
    try:
        explainer = shap.TreeExplainer(
            model,
            feature_perturbation="tree_path_dependent"
        )
    except Exception as e:
        print("SHAP Explainer Fehler:", e)
        explainer = None

    forecast_data = get_weather_forecast(days=7)
    predictions = []

    # 🔹 Letzte 7 Tage für Rolling Features
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT kwh FROM daily_stats ORDER BY day DESC LIMIT 7")
    last_rows = [r[0] for r in c.fetchall()]
    conn.close()

    last_rows.reverse()

    # 🔹 Preise laden
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT valid_from, price FROM prices ORDER BY valid_from DESC")
    prices = [{"date": r[0], "price": r[1]} for r in c.fetchall()]
    conn.close()

    for date_str, cloud, temp in forecast_data:

        date = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        doy = date.timetuple().tm_yday

        sin_day = math.sin(2 * math.pi * doy / 365)
        cos_day = math.cos(2 * math.pi * doy / 365)
        sun_elev = calculate_sun_elevation(date)

        prev_kwh = last_rows[-1] if last_rows else 0
        rolling_avg = sum(last_rows[-7:]) / 7 if len(last_rows) >= 7 else prev_kwh

        X = np.array([[sin_day, cos_day,
                       cloud or 0,
                       temp or 0,
                       sun_elev,
                       prev_kwh,
                       rolling_avg]])

        median = max(float(model.predict(X)[0]), 0)
        lower = max(float(model_low.predict(X)[0]), 0)
        upper = max(float(model_high.predict(X)[0]), 0)

        # SHAP sicher berechnen
        shap_dict = {}

        if explainer is not None:
            try:
                shap_values = explainer.shap_values(X)[0]
                shap_dict = {
                    feature_names[i]: round(float(shap_values[i]), 4)
                    for i in range(len(feature_names))
                }
            except Exception as e:
                print("SHAP Werte Fehler:", e)

        # =====================================================
        # Baseline & erklärter Anteil berechnen
        # =====================================================
        
        if shap_dict:
            shap_sum = float(sum(shap_dict.values()))
        else:
            shap_sum = 0.0
        
        baseline = float(median - shap_sum)
        
        explained_kwh = shap_sum
        explained_ratio = explained_kwh / max(median, 0.1)

        last_rows.append(median)

        # Saisonwirkung berechnen (Vektorlänge)
        shap_sin = shap_dict.get("sin_day", 0)
        shap_cos = shap_dict.get("cos_day", 0)
        
        season_strength = math.sqrt(shap_sin**2 + shap_cos**2)
        
        if shap_sin >= 0:
            season_label = "Sommerlicher Einfluss"
        else:
            season_label = "Winterlicher Einfluss"
        
        season_strength_normalized = season_strength / max(abs(median), 0.1)
        
        predictions.append({
            "date": date_str,
            "kwh_pred": round(median, 3),
            "kwh_lower": round(lower, 3),
            "kwh_upper": round(upper, 3),
            "eur_pred": round(calculate_eur(median, date_str, prices), 2),
            "shap": shap_dict,
        
            "baseline": round(baseline, 4),
            "shap_sum": round(shap_sum, 4),
            "explained_kwh": round(explained_kwh, 4),
            "explained_ratio": round(explained_ratio, 4),
        
            "season_strength": round(season_strength, 4),
            "season_strength_normalized": round(season_strength_normalized, 4),
            "season_label": season_label
        })
        
    return jsonify({
        "forecast": predictions,
        "mae": round(mae, 3)
    })


@app.route('/api/backtest')
def backtest():

    model_bundle = load_or_train_model()
    model = model_bundle["model"]

    X, y = build_training_data()

    if len(X) < 20:
        return jsonify({"error": "Zu wenig Daten"}), 400

    preds = model.predict(X)

    results = []

    for i in range(len(y) - 14, len(y)):
        results.append({
            "actual": round(float(y[i]), 3),
            "predicted": round(float(preds[i]), 3),
            "error": round(float(abs(y[i] - preds[i])), 3)
        })

    return jsonify(results)

@app.route('/api/feature-importance')
def feature_importance():

    model_bundle = load_or_train_model()
    model = model_bundle["model"]

    feature_names = [
        "sin_day",
        "cos_day",
        "clouds",
        "temperature",
        "sun_elevation",
        "prev_kwh",
        "rolling_avg"
    ]

    importances = model.feature_importances_

    result = [
        {
            "feature": name,
            "importance": round(float(imp), 4)
        }
        for name, imp in zip(feature_names, importances)
    ]

    return jsonify(sorted(result, key=lambda x: x["importance"], reverse=True))


@app.route('/api/shap')
def shap_values():

    model_bundle = load_or_train_model()

    if model_bundle is None:
        return jsonify({"error": "Model not trained"}), 400

    model = model_bundle["model"]
    explainer = shap.TreeExplainer(model)
    feature_names = model_bundle["feature_names"]

    forecast_data = get_weather_forecast(days=7)

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT kwh FROM daily_stats ORDER BY day DESC LIMIT 7")
    last_rows = [r[0] for r in c.fetchall()]
    conn.close()

    last_rows.reverse()

    results = []

    for date_str, cloud, temp in forecast_data:

        date = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        doy = date.timetuple().tm_yday

        sin_day = math.sin(2 * math.pi * doy / 365)
        cos_day = math.cos(2 * math.pi * doy / 365)

        sun_elev = calculate_sun_elevation(date)
        
        prev_kwh = last_rows[-1] if last_rows else 0
        rolling_avg = sum(last_rows[-7:]) / 7 if len(last_rows) >= 7 else prev_kwh
        
        X = np.array([[sin_day, cos_day, cloud or 0, temp or 0, sun_elev, prev_kwh, rolling_avg]])
        
        prediction = float(model.predict(X)[0])
        
        shap_vals = explainer.shap_values(X)[0]
        
        explanation = []
        
        for fname, sval in zip(feature_names, shap_vals):
            explanation.append({
                "feature": fname,
                "impact": round(float(sval), 4)
            })

        last_rows.append(prediction)

        results.append({
            "date": date_str,
            "prediction": round(prediction, 3),
            "shap_values": explanation
        })

    return jsonify(results)


@app.route('/api/shap-summary')
def shap_summary():

    model_bundle = load_or_train_model()

    if model_bundle is None:
        return jsonify({"error": "Model not trained"}), 400

    model = model_bundle["model"]
    explainer = shap.TreeExplainer(model)
    feature_names = model_bundle["feature_names"]

    X, y = build_training_data()

    if len(X) < 10:
        return jsonify({"error": "Not enough data"}), 400

    shap_values = explainer.shap_values(X)

    mean_importance = np.abs(shap_values).mean(axis=0)

    result = [
        {
            "feature": fname,
            "mean_abs_shap": round(float(val), 4)
        }
        for fname, val in zip(feature_names, mean_importance)
    ]

    return jsonify(sorted(result, key=lambda x: x["mean_abs_shap"], reverse=True))

if __name__ == '__main__':
    init_db()

    # 🔥 EINMAL ausführen, danach wieder auskommentieren!
    # force_rebuild_daily_stats()

    self_heal_daily_stats()

    app.run(host='0.0.0.0', port=5000, use_reloader=False)