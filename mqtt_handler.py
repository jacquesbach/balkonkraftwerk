import json
import time
from flask_mqtt import Mqtt

# Globaler Zwischenspeicher innerhalb dieses Moduls
mqtt_values = {
    "ac_power_w": 0.0,
    "dc_power_w": 0.0,
    "panel1_w": 0.0,
    "panel2_w": 0.0,
    "inverter_temp_c": 0.0,
    "last_ts": 0
}

mqtt = Mqtt()

def init_mqtt(app):
    """Initialisiert MQTT mit der Flask-App Konfiguration."""
    mqtt.init_app(app)
    return mqtt

@mqtt.on_connect()
def handle_connect(client, userdata, flags, rc):
    print("MQTT verbunden, abonniere Topics...")
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
        print(f"MQTT Parse Error: {e}")
