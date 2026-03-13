import os
import time
from flask import Flask
from dotenv import load_dotenv
from config import MQTT_CONFIG
from database import init_db, self_heal_daily_stats, force_rebuild_daily_stats
from ml_logic import load_or_train_model
from mqtt_handler import init_mqtt
from routes import api_bp

# System-Einstellungen
os.environ['TZ'] = 'Europe/Berlin'
if hasattr(time, 'tzset'):
    time.tzset()
load_dotenv()

app = Flask(__name__, template_folder='templates')
app.config.update(MQTT_CONFIG)

# Initialisierung
mqtt = init_mqtt(app)
app.register_blueprint(api_bp)

if __name__ == '__main__':
    init_db()

    # 🔥 EINMAL ausführen, danach wieder auskommentieren!
    force_rebuild_daily_stats()

    self_heal_daily_stats()
    
    print("System erfolgreich gestartet. Warte auf Daten...")
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, use_reloader=False)
