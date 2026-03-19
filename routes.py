from flask import Blueprint, request, jsonify, render_template
import sqlite3
import datetime
import requests
import json
import time
import math
import numpy as np
import shap
from config import (ADMIN_PASS, LATITUDE, LONGITUDE, TRAPEZOID_SQL, GAP_THRESHOLD, MODEL_FILE)
from utils import (calculate_eur, calculate_sun_elevation, get_weather_forecast)
from database import get_db_connection, finalize_day
from ml_logic import load_or_train_model, build_training_data
from mqtt_handler import mqtt_values

# Blueprint erstellen
api_bp = Blueprint('api', __name__)

# Globaler Status für das Frontend
loading_status = {"loading": False}

@api_bp.route('/')
def index():
    return render_template('index.html')

@api_bp.route('/api/auth', methods=['POST'])
def auth():
    data = request.json
    if data and data.get('pw') == ADMIN_PASS:
        return jsonify({"status": "ok"}), 200
    return jsonify({"error": "Falsches Passwort"}), 403


@api_bp.route('/api/update')
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
    
    conn = get_db_connection()
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


@api_bp.route('/api/live')
def live():
    conn = get_db_connection()
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

@api_bp.route('/api/widget')
def widget():
    conn = get_db_connection()
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


@api_bp.route('/api/status')
def status():
    return jsonify(loading_status)


@api_bp.route('/api/data')
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

    conn = get_db_connection()
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

@api_bp.route('/api/prices', methods=['GET', 'POST', 'DELETE'])
def manage_prices():
    pw = request.args.get('pw') or (request.json and request.json.get('pw'))
    if pw != ADMIN_PASS:
        return jsonify({"error": "Falsches Passwort"}), 401

    conn = get_db_connection()
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


@api_bp.route('/api/weather')
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


@api_bp.route('/api/roi')
def get_roi():

    conn = get_db_connection()
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


@api_bp.route('/api/peaks')
def get_peaks():
    conn = get_db_connection()
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

@api_bp.route('/api/heatmap')
def get_heatmap():

    year = request.args.get("year")

    conn = get_db_connection()
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

@api_bp.route('/api/heatmap_hourly')
def get_heatmap_hourly():
    month = request.args.get("month") # Format "YYYY-MM"
    
    # Timeout hinzugefügt, damit er im Zweifel wartet statt zu blockieren
    conn = get_db_connection(timeout=10)
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

@api_bp.route('/api/forecast')
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
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT kwh FROM daily_stats ORDER BY day DESC LIMIT 7")
    last_rows = [r[0] for r in c.fetchall()]
    conn.close()

    last_rows.reverse()

    # 🔹 Preise laden
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT valid_from, price FROM prices ORDER BY valid_from DESC")
    prices = [{"date": r[0], "price": r[1]} for r in c.fetchall()]
    conn.close()
    
    for date_str, cloud, temp, daylight, sunshine in forecast_data:

        date = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        doy = date.timetuple().tm_yday

        sin_day = math.sin(2 * math.pi * doy / 365)
        cos_day = math.cos(2 * math.pi * doy / 365)
        sun_elev = calculate_sun_elevation(date)

        prev_kwh = last_rows[-1] if last_rows else 0
        rolling_avg = sum(last_rows[-7:]) / 7 if len(last_rows) >= 7 else prev_kwh
        
        X = np.array([[
            sin_day, 
            cos_day, 
            cloud or 0, 
            temp or 0, 
            sun_elev, 
            prev_kwh, 
            rolling_avg,
            daylight or 0,
            sunshine or 0
        ]])

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


@api_bp.route('/api/backtest')
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

@api_bp.route('/api/feature-importance')
def feature_importance():

    model_bundle = load_or_train_model()
    model = model_bundle["model"]
    
    feature_names = model_bundle["feature_names"]

    importances = model.feature_importances_

    result = [
        {
            "feature": name,
            "importance": round(float(imp), 4)
        }
        for name, imp in zip(feature_names, importances)
    ]

    return jsonify(sorted(result, key=lambda x: x["importance"], reverse=True))


@api_bp.route('/api/shap')
def shap_values():

    model_bundle = load_or_train_model()

    if model_bundle is None:
        return jsonify({"error": "Model not trained"}), 400

    model = model_bundle["model"]
    explainer = shap.TreeExplainer(model)
    feature_names = model_bundle["feature_names"]

    forecast_data = get_weather_forecast(days=7)

    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT kwh FROM daily_stats ORDER BY day DESC LIMIT 7")
    last_rows = [r[0] for r in c.fetchall()]
    conn.close()

    last_rows.reverse()

    results = []

    for date_str, cloud, temp, daylight, sunshine in forecast_data:

        date = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        doy = date.timetuple().tm_yday

        sin_day = math.sin(2 * math.pi * doy / 365)
        cos_day = math.cos(2 * math.pi * doy / 365)

        sun_elev = calculate_sun_elevation(date)
        
        prev_kwh = last_rows[-1] if last_rows else 0
        rolling_avg = sum(last_rows[-7:]) / 7 if len(last_rows) >= 7 else prev_kwh

        X = np.array([[
            sin_day, 
            cos_day, 
            cloud or 0, 
            temp or 0, 
            sun_elev, 
            prev_kwh, 
            rolling_avg,
            daylight or 0,
            sunshine or 0
        ]])

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

@api_bp.route('/api/shap-summary')
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

@api_bp.route('/api/layout', methods=['GET'])
def get_layout():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT value FROM user_settings WHERE key = 'dashboard_layout'")
    row = c.fetchone()
    conn.close()
    if row:
        return jsonify({"layout": json.loads(row[0])}), 200
    return jsonify({"layout": None}), 200

@api_bp.route('/api/layout', methods=['POST'])
def save_layout():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Ungültiges JSON"}), 400
            
        layout_json = data.get('layout')
        password = data.get('pw')

        if password != ADMIN_PASS:
            return jsonify({"error": "Nicht autorisiert"}), 403

        conn = get_db_connection()
        c = conn.cursor()

        if layout_json == "RESET":
            c.execute("DELETE FROM user_settings WHERE key = 'dashboard_layout'")
            print("Layout wurde zurückgesetzt.")
        elif layout_json is not None:
            # WICHTIG: Wir konvertieren das Objekt explizit in einen String für die DB
            layout_string = json.dumps(layout_json)
            c.execute("INSERT OR REPLACE INTO user_settings (key, value) VALUES (?, ?)", 
                      ('dashboard_layout', layout_string))
            print("Layout erfolgreich gespeichert.")
        else:
            conn.close()
            return jsonify({"error": "Kein Layout-Inhalt empfangen"}), 400

        conn.commit()
        conn.close()
        return jsonify({"status": "gespeichert"}), 200

    except Exception as e:
        print(f"Server-Fehler: {str(e)}")
        return jsonify({"error": "Interner Server Fehler", "details": str(e)}), 500
