# 1. Basis-Image (Python 3.11 schlank)
FROM python:3.11-slim

# 2. Arbeitsverzeichnis im Container
WORKDIR /app

# 3. System-Abhängigkeiten (für ML-Pakete oft nötig)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# 4. Abhängigkeiten kopieren und installieren
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 5. Den Rest der App kopieren
# Dank .dockerignore landen DB und .env nicht hier drin
COPY . .

# 6. Port freigeben (Flask Standard)
EXPOSE 5000

# 7. Startbefehl
CMD ["python", "app.py"]