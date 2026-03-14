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
        // Wir versuchen es direkt beim API-Endpunkt
        const response = await fetch('/api/layout');
        if (response.ok) {
            const data = await response.json();
            if (data && data.layout) {
                savedLayout = data.layout;
                console.log("Layout aus DB geladen");
            }
        }
    } catch (error) {
        console.warn("API Layout nicht verfügbar, versuche LocalStorage...");
    }

    // Wenn DB nicht ging oder leer war, schau im LocalStorage nach
    if (!savedLayout) {
        const localData = localStorage.getItem('balkonkraftwerk_layout');
        if (localData) {
            savedLayout = JSON.parse(localData);
            console.log("Layout aus LocalStorage geladen");
        }
    }

    if (savedLayout && dashboardGrid) {
        dashboardGrid.load(savedLayout);
    }
}

// Beim Laden der Seite ausführen (füge das zu deinen anderen Init-Funktionen hinzu)
document.addEventListener("DOMContentLoaded", async () => {
    initGridstack();
    await loadLayout();
    
    // Nach dem Laden des Layouts kann es helfen, Chart.js einen Resize-Befehl zu geben
    window.dispatchEvent(new Event('resize')); 
});