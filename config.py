import os
import time
from dotenv import load_dotenv

# Zeitzone setzen
os.environ['TZ'] = 'Europe/Berlin'
time.tzset()

# Umgebungsvariablen laden
load_dotenv()

# ================= KONFIGURATION =================
DB_FILE = "solar_data.db"
ADMIN_PASS = os.getenv("ADMIN_PASS")
LATITUDE = float(os.getenv("LATITUDE", 0.0))
LONGITUDE = float(os.getenv("LONGITUDE", 0.0))
GAP_THRESHOLD = 45  # 3 x 15 Sekunden Logging
MODEL_FILE = "pv_model.pkl"

# MQTT Konfiguration (für die Flask-App Initialisierung)
MQTT_CONFIG = {
    'MQTT_BROKER_URL': os.getenv("MQTT_BROKER_URL"),
    'MQTT_BROKER_PORT': int(os.getenv("MQTT_BROKER_PORT", 1883)),
    'MQTT_USERNAME': os.getenv("MQTT_USERNAME"),
    'MQTT_PASSWORD': os.getenv("MQTT_PASSWORD"),
    'MQTT_TLS_ENABLED': False
}

# ================= SQL SCHNIPSEL =================
TRAPEZOID_SQL = f"""
CASE
    WHEN prev_t IS NOT NULL
         AND dt > 0
         AND dt <= {GAP_THRESHOLD}
    THEN ((prev_w + w) / 2.0) * (dt / 3600.0)
    ELSE 0
END
"""