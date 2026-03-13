import sqlite3
import datetime
from config import DB_FILE, TRAPEZOID_SQL
from utils import get_historical_weather_data, calculate_eur

def get_db_connection(timeout=None):
    """Hilfsfunktion für eine saubere DB-Verbindung."""
    if timeout is not None:
        conn = sqlite3.connect(DB_FILE, timeout=timeout)
    else:
        conn = sqlite3.connect(DB_FILE)
        
    conn.row_factory = sqlite3.Row
    return conn

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
            daylight_duration REAL,
            sunshine_duration REAL,
            max_w_panel1 REAL,
            max_w_panel2 REAL,
            kwh_panel1 REAL,
            kwh_panel2 REAL,
            kwh_dc_total REAL
        )
    ''')

    # 2. AUTOMATISCHES UPGRADE für neue Spalten
    c.execute("PRAGMA table_info(daily_stats)")
    existing_columns = [col[1] for col in c.fetchall()]

    if "daylight_duration" not in existing_columns:
        print("Migriere Datenbank: Spalte daylight_duration wird hinzugefügt...")
        c.execute("ALTER TABLE daily_stats ADD COLUMN daylight_duration REAL")
    
    if "sunshine_duration" not in existing_columns:
        print("Migriere Datenbank: Spalte sunshine_duration wird hinzugefügt...")
        c.execute("ALTER TABLE daily_stats ADD COLUMN sunshine_duration REAL")

    # 3. Restliche Spalten (max_w_panel1, etc.) sicherstellen
    # Falls du die auch noch nicht hast, kannst du das Muster einfach fortsetzen:
    for col in ["max_w_panel1", "max_w_panel2", "kwh_panel1", "kwh_panel2", "kwh_dc_total"]:
        if col not in existing_columns:
            c.execute(f"ALTER TABLE daily_stats ADD COLUMN {col} REAL")
    
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
        weather = get_historical_weather_data(day)
        avg_temp = weather["temp"]
        daylight_s = weather["daylight_duration"]
        sunshine_s = weather["sunshine_duration"]

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
            (day, kwh, eur, avg_clouds, avg_temp, daylight_duration, sunshine_duration, max_w, max_w_panel1, max_w_panel2, kwh_panel1, kwh_panel2, kwh_dc_total)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            day,
            kwh_db,
            eur_db,
            round(avg_clouds, 2),
            round(avg_temp, 2),
            round(daylight_s, 1),
            round(sunshine_s, 1), # In Sekunden
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
    from ml_logic import train_model
    train_model()


def self_heal_daily_stats():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    # Alle Tage aus Rohdaten holen außer heute
    today = datetime.date.today().strftime("%Y-%m-%d")
    c.execute("SELECT DISTINCT date(t) FROM data WHERE date(t) < ? ORDER BY date(t)", (today,))
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

def force_rebuild_daily_stats():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    print("Starte kompletten Neuaufbau von daily_stats...")
    
    # daily_stats komplett leeren
    c.execute("DELETE FROM daily_stats")

    # stats sauber zurücksetzen
    c.execute("UPDATE stats SET total_kwh = 0, total_eur = 0 WHERE id = 1")
    conn.commit()

    # Alle Tage aus Rohdaten holen außer heute
    today = datetime.date.today().strftime("%Y-%m-%d")
    c.execute("SELECT DISTINCT date(t) FROM data WHERE date(t) < ?", (today,))
    days = [row[0] for row in c.fetchall()]
    conn.close()

    # Für jeden Tag neu berechnen
    for d in days:
        finalize_day(d)
    print(f"Rebuild abgeschlossen. {len(days)} Tage neu berechnet.")
