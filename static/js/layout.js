let dashboardGrid;
let currentPw = sessionStorage.getItem('admin_pw') || "";

// 1. Grid initialisieren
function initGridstack() {
    const isAdmin = sessionStorage.getItem('admin_pw') !== null;

    dashboardGrid = GridStack.init({
        cellHeight: 110,
        margin: 20,
        animate: true,
        staticGrid: !isAdmin, // Wenn nicht Admin, dann gesperrt (kein Drag/Resize)
        disableOneColumnMode: false,
        oneColumnModeDomSort: true
    });

    // Falls das PW schon im Speicher war (nach Refresh), UI direkt anpassen
    if (isAdmin) {
        currentPw = sessionStorage.getItem('admin_pw');
        document.getElementById('unlockBtn').style.display = 'none';
        document.getElementById('adminArea').style.display = 'flex';
        // Hier ggf. loadTariffs() aufrufen, falls script.js schon geladen ist
    }

    dashboardGrid.on('change', function(event, items) {
        saveLayout();
    });
}

// 3. Layout speichern (Auth-Check + DB vs. LocalStorage)
async function saveLayout() {
    if (!dashboardGrid) return;
    const layoutData = dashboardGrid.save(); 

    // Wir schauen nach, ob ein Passwort im SessionStorage liegt 
    // (Das müsstest du in deiner unlockAdmin() Funktion dort speichern)
    const storedPw = sessionStorage.getItem('admin_pw');

    if (storedPw) {
        try {
            const response = await fetch('/api/layout', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ 
                    layout: layoutData,
                    pw: storedPw 
                })
            });
            
            if (response.ok) {
                console.log("Layout in DB gespeichert.");
                return; 
            }
        } catch (e) { console.error("DB Save failed", e); }
    }

    // Fallback: LocalStorage für Gäste
    localStorage.setItem('balkonkraftwerk_layout', JSON.stringify(layoutData));
    console.log("Layout lokal im Browser gespeichert.");
}

// 4. Layout beim Starten laden
async function loadLayout() {
    let savedLayout = null;
    try {
        const response = await fetch('/api/layout');
        if (response.ok) {
            const data = await response.json();
            if (data && data.layout) savedLayout = data.layout;
        }
    } catch (e) { console.error("DB Load failed", e); }

    if (!savedLayout) {
        const localData = localStorage.getItem('balkonkraftwerk_layout');
        if (localData) savedLayout = JSON.parse(localData);
    }

    if (savedLayout && dashboardGrid) {
        dashboardGrid.removeAll(); 
        dashboardGrid.load(savedLayout);
        console.log("Layout sauber neu geladen.");
    }
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