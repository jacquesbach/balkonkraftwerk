# Balkonkraftwerk Analytics Dashboard

Ein intelligentes Monitoring-System für Balkonkraftwerke. Es kombiniert Echtzeit-Daten via MQTT mit Wettervorhersagen von Open-Meteo, um mittels Machine Learning den Ertrag der nächsten Tage vorherzusagen.


## 🚀 Features

* **Echtzeit-Monitoring:** Empfängt sekündliche Live-Werte (Leistung/Watt, Strom/Ampere, Spannung/Volt, Temperatur) des Wechselrichters via MQTT (OpenDTU/AhoyDTU).
* **Energie-Statistiken & Live-Metriken:** Präzise Errechnung der produzierten kWh mittels Trapez-Regel sowie automatische Berechnung der Ersparnis (EUR) und Spitzenwerte.
* **ML-gestützter Forecast:** Vorhersage des Ertrags (Best/Worst/Expected Case) basierend auf Bewölkung und Temperatur.
  * **Quantil-Regression:** Nutzt GradientBoosting zur Berechnung eines 80%-Konfidenzintervalls für die erwartete Tagesproduktion.
  * **SHAP-Integration (Erklärbare KI):** Das System zeigt im Tooltip genau an, *warum* eine bestimmte Vorhersage getroffen wurde (z. B. "Wolkenbedeckung senkt den Ertrag um X Watt").
* **Interaktive Visualisierungen (Chart.js):**
  * **Dynamisches Donut-Chart:** Zeigt die DC-Leistung pro Panel. Durch Antippen/Hovern ändert sich die zentrale Anzeige.
  * **Hourly Heatmap:** Visualisiert historische Erträge auf Stundenbasis für jeden Monat.
* **Automatische Wetterdaten:** Integriert die kostenlose Open-Meteo API für aktuelle Bewölkung und stündliche Prognosen.
* **Leichtgewichtig, Lokal & Self-Healing:** Kein Cloud-Zwang. Speicherung erfolgt in einer lokalen SQLite-Datenbank (`solar_data.db`) mit automatischer Nachberechnung fehlender Tage (Self-Healing). Das Training des ML-Modells läuft automatisch im Hintergrund.
* **Staging-System:** Vollautomatisches Deployment via GitHub Actions auf eine Live- (Port 5000) und Test-Umgebung (Port 5001).


## 🏗️ Tech Stack

* **Backend:** Python 3.11, Flask, Flask-MQTT
* **Machine Learning:** Scikit-Learn (RandomForest, GradientBoosting), SHAP, Pandas, Numpy
* **Frontend:** HTML5, CSS3 (CSS Variables für konsistentes Theming), Vanilla JavaScript (ES6)
* **Charts & Icons:** Chart.js (inkl. datalabels-Plugin), Phosphor Icons
* **Datenbank:** SQLite3

## 📂 Projektstruktur
```text
.
├── .github/workflows/
│   ├── deploy.yml          # Live-Deployment (Main Branch)
│   └── deploy-staging.yml  # Staging-Deployment (Dev Branch)
├── static/
│   ├── css/
│   │   └── styles.css
│   └── js/
│       └── script.js
├── templates/
│   └── index.html
├── app.py
├── requirements.txt
├── .env
├── .gitignore
└── README.md
```


## 🛠 Installation & Setup

### 1. Repository klonen
```bash
git clone https://github.com/jacquesbach/balkonkraftwerk.git
cd balkonkraftwerk
```

### 2. Virtuelle Umgebung & Abhängigkeiten
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Konfiguration (`.env`)
Erstelle eine `.env` Datei im Hauptverzeichnis:
```env
ADMIN_PASS=dein_passwort
LATITUDE=49.xxxx
LONGITUDE=8.xxxx
MQTT_BROKER_URL=deine_ip
MQTT_BROKER_PORT=1883
MQTT_USERNAME=dein_user
MQTT_PASSWORD=dein_pw
```
## ⚙️ Deployment (Systemd)

Um das System mit Live- und Staging-Umgebung dauerhaft zu betreiben, werden zwei separate Instanzen und Services auf dem Ubuntu-Server eingerichtet.

### 1. Verzeichnisstruktur vorbereiten
Klone das Projekt zweimal in unterschiedliche Ordner:
```bash
# Live-Instanz (Port 5000)
git clone [https://github.com/jacquesbach/balkonkraftwerk.git](https://github.com/jacquesbach/balkonkraftwerk.git) /home/ubuntu/balkonkraftwerk

# Staging-Instanz (Port 5001)
git clone -b dev [https://github.com/jacquesbach/balkonkraftwerk.git](https://github.com/jacquesbach/balkonkraftwerk.git) /home/ubuntu/balkonkraftwerk-staging
```

### 2. Systemd Services anlegen
Erstelle zwei Service-Dateien, um beide Instanzen unabhängig voneinander zu steuern.

**Live-Service:** `sudo nano /etc/systemd/system/balkonkraftwerk.service`
```ini
[Unit]
Description=Balkonkraftwerk Analytics - Live (Port 5000)
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/balkonkraftwerk
ExecStart=/home/ubuntu/balkonkraftwerk/venv/bin/python3 app.py
Restart=always
Environment="PORT=5000"

[Install]
WantedBy=multi-user.target
```

**Staging-Service:** `sudo nano /etc/systemd/system/balkonkraftwerk-staging.service`
```ini
[Unit]
Description=Balkonkraftwerk Analytics - Staging (Port 5001)
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/balkonkraftwerk-staging
ExecStart=/home/ubuntu/balkonkraftwerk-staging/venv/bin/python3 app.py
Restart=always
Environment="PORT=5001"

[Install]
WantedBy=multi-user.target
```

### 3. Dienste starten
```bash
sudo systemctl daemon-reload

# Live starten
sudo systemctl enable --now balkonkraftwerk

# Staging starten
sudo systemctl enable --now balkonkraftwerk-staging
```

### ⚠️ Wichtig: GitHub Actions & CI/CD Setup

Damit das automatische Deployment funktioniert, müssen zwei Dinge konfiguriert sein:

#### 1. Pfade in den Workflows
Stelle sicher, dass die Zielverzeichnisse in deinen `.github/workflows/*.yml` Dateien exakt mit der Serverstruktur übereinstimmen:
* **deploy-staging.yml:** Zielpfad `/home/ubuntu/balkonkraftwerk-staging`
* **deploy.yml:** Zielpfad `/home/ubuntu/balkonkraftwerk`

#### 2. GitHub Secrets
Hinterlege in deinem GitHub-Repository unter `Settings > Secrets and variables > Actions` folgende Secrets, damit die Action auf deinen Server zugreifen kann:

| Secret Name | Beschreibung |
| :--- | :--- |
| `SERVER_IP` | Die IP-Adresse deines Ubuntu-Servers |
| `SERVER_USER` | Dein Benutzername (z. B. `ubuntu`) |
| `SSH_PRIVATE_KEY` | Dein privater SSH-Schlüssel (für den passwortlosen Login) |

---

## 🧪 Entwicklung & Staging
Das Projekt nutzt einen automatisierten **Staging-Workflow** via GitHub Actions:

1. **Entwicklung:** Änderungen werden in den `dev` Branch gepusht. Die GitHub Action führt einen Deploy auf dem Server im Verzeichnis `/home/ubuntu/balkonkraftwerk-staging` aus.
2. **Vorschau:** Änderungen sind sofort unter `http://deine-ip:5001` sichtbar.
3. **Production:** Nach einem erfolgreichen Merge von `dev` in den `main` Branch aktualisiert die Action das Live-Verzeichnis und startet den Dienst `balkonkraftwerk.service` neu.

**Staging-URL:** `http://deine-ip:5001`  
**Live-URL:** `http://deine-ip:5000`

## 📈 Machine Learning Info
Die App verwendet einen `RandomForestRegressor` für den Erwartungswert und zwei `GradientBoostingRegressor`, um ein Konfidenzintervall (Quantile Regression) zu berechnen. 
* **Input-Features:** Sonnenstand, Tag des Jahres (Sin/Cos), Bewölkung, Temperatur, Rollierender Durchschnitt (7 Tage).
* **Modell-Datei:** `pv_model.pkl` (wird nach jedem Tagesabschluss automatisch neu trainiert).

## 🤝 Mitwirken (Contributing)

Pull Requests sind herzlich willkommen. Für größere Änderungen öffne bitte zuerst ein Issue, um zu diskutieren, was du ändern möchtest.

1. Forke das Projekt
2. Erstelle deinen Feature Branch (`git checkout -b feature/AmazingFeature`)
3. Commit deine Änderungen (`git commit -m 'Add some AmazingFeature'`)
4. Push auf den Branch (`git push origin feature/AmazingFeature`)
5. Öffne einen Pull Request
