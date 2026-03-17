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

function initAutoHeightGrid() {

    const items = document.querySelectorAll(".grid-stack-item.auto-height");

    items.forEach(item => {

        const content = item.querySelector(".grid-stack-item-content");
        const card = content?.querySelector(".card");

        if (!content || !card) return;

        let resizeTimeout;

        const resize = () => {

            const rect = card.getBoundingClientRect();
            const heightPx = rect.height;

            const cellHeight = dashboardGrid.getCellHeight();
            const newH = Math.ceil(heightPx / cellHeight);

            // 🔥 Nur updaten wenn nötig (sehr wichtig!)
            if (item.gridstackNode.h !== newH) {
                dashboardGrid.update(item, { h: newH });
            }
        };

        const observer = new ResizeObserver(() => {

            clearTimeout(resizeTimeout);

            resizeTimeout = setTimeout(() => {
                resize();
            }, 60); // debounce für Chart.js

        });

        observer.observe(card);

        // 👉 initial nach Render
        setTimeout(resize, 300);

        // 👉 fallback (Fonts / async Layout)
        requestAnimationFrame(() => {
            requestAnimationFrame(resize);
        });
    });
}

document.addEventListener("DOMContentLoaded", async () => {
    // --- PHASE 1: Das Gerüst aufbauen ---
    initGridstack();
    await loadLayout(); // Wartet, bis Boxen aus DB oder LocalStorage da sind
    initAutoHeightGrid();

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