let dashboardGrid;
let currentPw = "";

// 1. Grid initialisieren
function initGridstack() {
    dashboardGrid = GridStack.init({
        cellHeight: 110,
        margin: 20,
        animate: true,
        staticGrid: true,
        disableOneColumnMode: false,
        oneColumnModeDomSort: true
    });

    dashboardGrid.on('change', function(event, items) {
        saveLayout();
    });
}

// 2. Layout speichern (Nur in DB und nur wenn PW da ist)
async function saveLayout() {
    if (!dashboardGrid || !currentPw || currentPw === "") {
        return; 
    }
    
    const layoutData = dashboardGrid.save(); 

    try {
        const response = await fetch('/api/layout', {
            method: 'POST',
            headers: { 
                'Content-Type': 'application/json' 
            },
            body: JSON.stringify({ 
                layout: layoutData,
                pw: currentPw 
            })
        });
        
        if (!response.ok) {
            const errorText = await response.text();
            console.error("Speichern fehlgeschlagen:", errorText);
        }
    } catch (e) { 
        console.error("Netzwerkfehler beim Speichern:", e); 
    }
}

// 3. Layout beim Starten laden
async function loadLayout() {
    let savedLayout = null;
    try {
        const response = await fetch('/api/layout');
        if (response.ok) {
            const data = await response.json();
            if (data && data.layout) savedLayout = data.layout;
        }
    } catch (e) { console.error("DB Load failed", e); }

    // Wenn ein Layout auf dem Server existiert, anwenden
    if (savedLayout && dashboardGrid) {
        dashboardGrid.removeAll(); 
        dashboardGrid.load(savedLayout);
        console.log("Globales Layout erfolgreich geladen.");
    }
}

// 4. Reset-Funktion für den Button im Admin-Bereich
async function resetDatabaseLayout() {
    if (!confirm("Möchtest du das Layout für ALLE Nutzer auf den Standard zurücksetzen?")) return;
    
    if (!currentPw) return alert("Bitte erst als Admin einloggen!");

    const res = await fetch('/api/layout', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ 
            layout: "RESET", // Signalwort für das Python-Backend
            pw: currentPw
        })
    });
    
    if (res.ok) {
        location.reload(); // Seite neu laden, um Standard-HTML zu zeigen
    }
}

function resizeForecastCardToFit() {
    if (!window.dashboardGrid) return;
    
    const gridItem = document.getElementById('card-forecast');
    const innerCard = gridItem.querySelector('.card');
    
    if (!gridItem || !innerCard) return;

    // 1. Wir erlauben der Karte kurz, ihre natürliche, volle Höhe anzunehmen
    innerCard.style.height = 'auto';
    const requiredHeightPx = innerCard.scrollHeight;
    
    // 2. Zurücksetzen für das Flex-Layout
    innerCard.style.height = '100%';

    // 3. Höhe einer einzelnen Gridstack-Zelle plus Margin abrufen
    const cellHeight = dashboardGrid.getCellHeight(); 
    const margin = dashboardGrid.getOpts().margin || 20;

    // 4. Ausrechnen, wie viele "Blöcke" (h) wir brauchen, aufgerundet
    const newH = Math.ceil((requiredHeightPx + margin) / (cellHeight + margin));

    // 5. Gridstack anweisen, die neue Höhe anzuwenden
    dashboardGrid.update(gridItem, { h: newH });
}

document.addEventListener("DOMContentLoaded", async () => {
    // --- PHASE 1: Das Gerüst aufbauen ---
    initGridstack();
    await loadLayout(); // Wartet, bis Boxen aus DB oder LocalStorage da sind

    // --- PHASE 2: Startwerte für Datumsfelder setzen ---
    const t = new Date().toISOString().split('T')[0];
    const startInput = document.getElementById('start');
    const endInput = document.getElementById('end');

    if (startInput && endInput) {
        startInput.value = t;
        endInput.value = t;
        startInput.addEventListener('change', updateQuickButtonsActiveState);
        endInput.addEventListener('change', updateQuickButtonsActiveState);
    }

    // --- PHASE 3: Daten in die Boxen pumpen ---
    // Wir prüfen bei jeder Funktion, ob sie existiert, um Fehler zu vermeiden
    try {
        if (typeof fetchData === "function") await fetchData();
        if (typeof updateWeather === "function") updateWeather();
        if (typeof updateLive === "function") updateLive();
        if (typeof updatePeaks === "function") updatePeaks();
        if (typeof updateQuickButtonsActiveState === "function") updateQuickButtonsActiveState();
        
        // ML-Funktionen
        if (typeof loadForecast === "function") loadForecast();
        if (typeof loadGlobalShap === "function") loadGlobalShap();
        if (typeof loadFeatureImportance === "function") loadFeatureImportance();
        
        // Heatmaps
        if (typeof initHeatmapYears === "function") initHeatmapYears();
        if (typeof initHourlyHeatmap === "function") initHourlyHeatmap();

    } catch (err) {
        console.error("Fehler beim initialen Daten-Load:", err);
    }

    // --- PHASE 4: Intervalle für Updates starten ---
    setInterval(() => { if (typeof updateLive === "function") updateLive(); }, 5000);
    setInterval(() => { if (typeof fetchData === "function") fetchData(); }, 60000);
    setInterval(() => { if (typeof updatePeaks === "function") updatePeaks(); }, 60000);
    setInterval(() => { if (typeof checkLoadingStatus === "function") checkLoadingStatus(); }, 500);

    // WICHTIG: Einmal kräftig schütteln, damit Charts ihre Größe im neuen Grid finden
    setTimeout(() => {
        window.dispatchEvent(new Event('resize'));
    }, 200); 
});