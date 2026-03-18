let dashboardGrid;
let currentPw = "";

// 1. Grid initialisieren
function initGridstack() {
    dashboardGrid = GridStack.init({
        cellHeight: 50,
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

function injectDeleteButtons() {
    document.querySelectorAll('.grid-stack-item').forEach(item => {

        // schon vorhanden? -> skip
        if (item.querySelector('.card-delete-btn')) return;

        const btn = document.createElement('div');
        btn.className = 'card-delete-btn';
        btn.innerHTML = '🗑️';

        Object.assign(btn.style, {
            position: 'absolute',
            top: '8px',
            right: '8px',
            cursor: 'pointer',
            fontSize: '14px',
            opacity: '0.7',
            display: currentPw ? 'block' : 'none',
            zIndex: 20
        });

        btn.onclick = (e) => {
            e.stopPropagation();
            removeCard(item.id);
        };

        item.appendChild(btn);
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

let removedCards = [];

function removeCard(cardId) {
    if (!currentPw) return alert("Nur im Admin-Modus möglich");

    const el = document.getElementById(cardId);
    if (!el) return;

    dashboardGrid.removeWidget(el);

    if (!removedCards.includes(cardId)) {
        removedCards.push(cardId);
    }

    saveRemovedCards();
    updateAdminCardList();
}

function restoreCard(cardId) {
    if (!currentPw) return;

    const el = document.getElementById(cardId);

    if (el) {
        dashboardGrid.addWidget(el);
    }

    removedCards = removedCards.filter(id => id !== cardId);

    saveRemovedCards();
    updateAdminCardList();
}

async function loadRemovedCards() {
    const res = await fetch('/api/cards');
    const data = await res.json();

    removedCards = data.removed || [];

    removedCards.forEach(id => {
        const el = document.getElementById(id);
        if (el) dashboardGrid.removeWidget(el);
    });

    updateAdminCardList();
}

async function saveRemovedCards() {
    if (!currentPw) return;

    await fetch('/api/cards', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            removed: removedCards,
            pw: currentPw
        })
    });
}

function updateAdminCardList() {
    const container = document.getElementById('cardManagerList');
    if (!container) return;

    container.innerHTML = '';

    document.querySelectorAll('.grid-stack-item').forEach(el => {
        const id = el.id;
        const isRemoved = removedCards.includes(id);

        const row = document.createElement('div');

        row.style.display = 'flex';
        row.style.justifyContent = 'space-between';
        row.style.marginBottom = '6px';

        row.innerHTML = `
            <span>${id}</span>
            <button onclick="${isRemoved ? `restoreCard('${id}')` : `removeCard('${id}')`}">
                ${isRemoved ? '➕' : '🗑️'}
            </button>
        `;

        container.appendChild(row);
    });
}

function toggleDeleteButtons(show) {
    document.querySelectorAll('.card-delete-btn').forEach(btn => {
        btn.style.display = show ? 'block' : 'none';
    });
}

document.addEventListener("DOMContentLoaded", async () => {
    // --- PHASE 1: Das Gerüst aufbauen ---
    initGridstack();
    await loadLayout(); // Wartet, bis Boxen aus DB oder LocalStorage da sind
    await loadRemovedCards();

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

});