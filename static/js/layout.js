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
    if (!dashboardGrid || !currentPw) return;
    
    const layoutData = dashboardGrid.save(); 

    try {
        const response = await fetch('/api/layout', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ 
                layout: layoutData,
                pw: currentPw 
            })
        });
        
        if (response.ok) {
            console.log("Layout in DB für alle Nutzer aktualisiert.");
        }
    } catch (e) { console.error("DB Save failed", e); }
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