# ☀️ Balkonkraftwerk Analytics

Ein intelligentes, lokal gehostetes Dashboard zur Überwachung, Analyse und Vorhersage von Balkonkraftwerken. Dieses Projekt kombiniert Echtzeit-MQTT-Daten (kompatibel mit AhoyDTU / OpenDTU) mit modernem Machine Learning (Scikit-Learn), um nicht nur historische Daten zu visualisieren, sondern auch präzise, wetterbasierte Leistungsprognosen zu erstellen.

## ✨ Features

* **📡 Echtzeit-Monitoring:** Empfängt sekündliche Updates des Wechselrichters via MQTT (Leistung, Strom, Spannung, Temperatur).
* **🧠 KI-gestützter Forecast:** * Nutzt **Quantil-Regression** (GradientBoosting) zur Berechnung eines 80%-Konfidenzintervalls für die erwartete Tagesproduktion.
  * **SHAP-Integration (Erklärbare KI):** Das System zeigt im Tooltip genau an, *warum* eine bestimmte Vorhersage getroffen wurde (z. B. "Wolkenbedeckung senkt den Ertrag um X Watt").
* **📊 Interaktive Visualisierungen (Chart.js):**
  * **Dynamisches Donut-Chart:** Zeigt die DC-Leistung pro Panel. Durch Antippen/Hovern ändert sich die zentrale Anzeige.
  * **Hourly Heatmap:** Visualisiert historische Erträge auf Stundenbasis für jeden Monat.
  * **Live-Metriken:** Automatische Berechnung von kWh, Ersparnis (EUR) und Spitzenwerten.
* **🌦 Automatische Wetterdaten:** Integriert die kostenlose Open-Meteo API für aktuelle Bewölkung und stündliche Prognosen.
* **💾 Leichtgewichtig & Lokal:** Kein Cloud-Zwang. Speicherung erfolgt in einer lokalen SQLite-Datenbank (`solar_data.db`). Automatisches Training des ML-Modells im Hintergrund.

## 🛠 Tech Stack

* **Backend:** Python 3.11, Flask, Flask-MQTT
* **Machine Learning:** Scikit-Learn (RandomForest, GradientBoosting), SHAP, Pandas, Numpy
* **Frontend:** HTML5, CSS3 (CSS Variables für konsistentes Theming), Vanilla JavaScript (ES6)
* **Charts & Icons:** Chart.js (inkl. datalabels-Plugin), Phosphor Icons
* **Datenbank:** SQLite3

## 📂 Projektstruktur

\`\`\`text
/
├── app.py                 # Hauptanwendung (Flask Backend, MQTT, ML-Logik)
├── requirements.txt       # Python-Abhängigkeiten
├── .env                   # (Nicht in Git) Deine geheimen Zugangsdaten
├── .env.example           # Vorlage für Umgebungsvariablen
├── .gitignore             # Schützt sensible Daten vor dem Upload
├── pv_model.pkl           # (Wird automatisch generiert) Trainiertes ML-Modell
├── solar_data.db          # (Wird automatisch generiert) SQLite Datenbank
├── static/
│   ├── css/
│   │   └── style.css      # Dashboard Styling
│   └── js/
│       └── script.js      # Frontend Logik (API-Calls, Chart.js)
└── templates/
    └── index.html         # Dashboard HTML-Template
\`\`\`

## 🚀 Installation & Setup

### 1. Repository klonen
\`\`\`bash
git clone https://github.com/DEIN_USERNAME/DEIN_REPO.git
cd DEIN_REPO
\`\`\`

### 2. Virtuelle Umgebung & Abhängigkeiten
Es wird empfohlen, eine virtuelle Python-Umgebung zu nutzen:
\`\`\`bash
python -m venv venv
source venv/bin/activate  # Unter Windows: venv\Scripts\activate
pip install -r requirements.txt
\`\`\`

### 3. Umgebungsvariablen (.env) konfigurieren
Kopiere die Vorlage und trage deine spezifischen Daten ein:
\`\`\`bash
cp .env.example .env
\`\`\`
Öffne die `.env` Datei und passe folgende Werte an:
* `ADMIN_PASS`: (Optional für zukünftige Admin-Routen)
* `LATITUDE` / `LONGITUDE`: Deine Standortkoordinaten (für Open-Meteo Vorhersagen).
* `MQTT_BROKER_URL`, `MQTT_USERNAME`, `MQTT_PASSWORD`: Deine MQTT-Broker Daten.

*(Hinweis: Das System lauscht standardmäßig auf `inverter/+/status`, `inverter/+/ch0/P` etc. Stelle sicher, dass dein Wechselrichter/deine DTU diese Topics sendet).*

### 4. Anwendung starten
\`\`\`bash
python app.py
\`\`\`
Die Datenbank (`solar_data.db`) wird beim ersten Start automatisch mit dem korrekten Schema initialisiert.
Das Dashboard ist nun unter `http://localhost:5000` erreichbar.

## ⚙️ Automatisierung (Hintergrund-Tasks)

Das Backend nutzt das `schedule` Modul in einem separaten Thread, um:
* **Stündlich:** Die Durchschnittsdaten der letzten Stunde in die Heatmap-Tabelle (`data`) zu schreiben und das Machine-Learning-Modell (`pv_model.pkl`) mit den neuesten Daten neu zu trainieren.
* **Um Mitternacht:** Die Tagesstatistik (Tagesertrag, Max-Werte, Ersparnis) zu berechnen und in die Tabelle `daily_stats` zu aggregieren.

## 🤝 Mitwirken (Contributing)

Pull Requests sind herzlich willkommen. Für größere Änderungen öffne bitte zuerst ein Issue, um zu diskutieren, was du ändern möchtest.

1. Forke das Projekt
2. Erstelle deinen Feature Branch (`git checkout -b feature/AmazingFeature`)
3. Commit deine Änderungen (`git commit -m 'Add some AmazingFeature'`)
4. Push auf den Branch (`git push origin feature/AmazingFeature`)
5. Öffne einen Pull Request

## 📄 Lizenz

Dieses Projekt ist unter der MIT-Lizenz lizenziert. Weitere Details findest du in der Datei `LICENSE`.