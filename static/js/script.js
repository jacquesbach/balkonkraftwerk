let myChart;
let panelChart;
let dcDonutChart;
let activeDonutIndex = null; // null = Gesamtansicht, 0 = Panel 1, 1 = Panel 2
let currentPw = "";

const ROI_DEBUG_FORCE_COMPLETE = false;

async function checkLoadingStatus() {
   try {
       const res = await fetch('/api/status');
       const d = await res.json();


       if (d.loading) {
           document.getElementById('chartLoading').style.display = 'flex';
           document.getElementById('totalLoading').style.display = 'flex';
           document.getElementById('panelChartLoading').style.display = 'flex';
           document.getElementById('hourlyHeatmapLoading').style.display = 'flex';
         } else {
           document.getElementById('chartLoading').style.display = 'none';
           document.getElementById('totalLoading').style.display = 'none';
           document.getElementById('panelChartLoading').style.display = 'none';
           document.getElementById('hourlyHeatmapLoading').style.display = 'none';
       }
   } catch (e) {}
}

async function unlockAdmin() {
   const pw = prompt("Passwort zur Anpassung des Stromtarifs:");
   if (!pw) return;
   const res = await fetch('/api/auth', {
       method: 'POST',
       headers: {
           'Content-Type': 'application/json'
       },
       body: JSON.stringify({
           pw: pw
       })
   });
   if (res.ok) {
       currentPw = pw;
       document.getElementById('unlockBtn').style.display = 'none';
       document.getElementById('adminArea').style.display = 'flex';
       loadTariffs();
   } else {
       alert("Falsches Passwort!");
   }
}

function closeAdmin() {
   document.getElementById('adminArea').style.display = 'none';
   document.getElementById('unlockBtn').style.display = 'block';
}

async function loadTariffs() {
   const res = await fetch(`/api/prices?pw=${currentPw}`);
   if (!res.ok) return;
   const data = await res.json();
   const list = document.getElementById('tariffList');
   list.innerHTML = "";
   data.forEach(t => {
       list.innerHTML += `
        <div style="display:flex; justify-content: space-between; background: #f9f9f9; padding: 8px 12px; border-radius: 8px; border: 1px solid #eee; font-size: 0.9em; align-items: center;">
            <span>Ab: <b>${t.date}</b></span> 
            <span>
                <b style="color: var(--text-dark);">${t.price.toFixed(3)} €</b> 
                <span style="cursor:pointer; color:#ff4b72; margin-left:15px; font-weight:bold;" title="Löschen" onclick="deletePrice('${t.date}')">✕</span>
            </span>
        </div>`;
   });
}

async function savePrice() {
   const date = document.getElementById('pDate').value;
   const price = document.getElementById('pVal').value;
   if (!date || !price) return alert("Bitte Datum und Preis angeben!");
   const res = await fetch('/api/prices', {
       method: 'POST',
       headers: {
           'Content-Type': 'application/json'
       },
       body: JSON.stringify({
           date: date,
           price: parseFloat(price),
           pw: currentPw
       })
   });
   if (res.ok) {
       document.getElementById('pVal').value = '';
       loadTariffs();
       fetchData();
   }
}

async function deletePrice(date) {
   if (!confirm(`Möchtest du den Tarif ab ${date} wirklich löschen?`)) return;
   const res = await fetch('/api/prices', {
       method: 'DELETE',
       headers: {
           'Content-Type': 'application/json'
       },
       body: JSON.stringify({
           date: date,
           pw: currentPw
       })
   });
   if (res.ok) {
       loadTariffs();
       fetchData();
   }
}

// Plugin, um die Wattzahl in die Mitte des Donuts zu schreiben
const centerTextPlugin = {
    id: 'centerText',
    afterDraw: (chart) => {
        const { ctx, chartArea: { top, width, height } } = chart;
        ctx.save();
        
        const centerY = height / 2 + top;
        const centerX = width / 2;

        let titleText = "Gesamt DC";
        let valText = "";
        let unitText = "W";
        let subText = "";

        const data = chart.data.datasets[0].data;
        const totalDC = data.reduce((a, b) => a + b, 0);

        if (activeDonutIndex !== null && data[activeDonutIndex] !== undefined) {
            // --- EINZEL-PANEL ANSICHT ---
            const val = data[activeDonutIndex];
            const label = chart.data.labels[activeDonutIndex];
            const pct = totalDC > 0 ? ((val / totalDC) * 100).toFixed(0) : 0;
            
            titleText = label; 
            valText = val.toFixed(1);
            subText = `${pct}% Anteil`;
        } else {
            // --- GESAMT ANSICHT ---
            valText = totalDC.toFixed(1);
            titleText = "Gesamt DC";
            subText = "";
        }

        // 1. TITEL (Oben)
        ctx.font = 'bold 14px sans-serif';
        ctx.fillStyle = '#888';
        ctx.textAlign = 'center';
        ctx.fillText(titleText, centerX, centerY - 25);

        // 2. HAUPTWERT (Mitte)
        const valFont = 'bold 30px sans-serif';
        const unitFont = '600 11px sans-serif';
        const marginLeft = 4;

        ctx.font = valFont;
        const valWidth = ctx.measureText(valText).width;
        ctx.font = unitFont;
        const unitWidth = ctx.measureText(unitText).width;
        const totalWidth = valWidth + marginLeft + unitWidth;
        const startX = centerX - (totalWidth / 2);

        ctx.textAlign = 'left';
        ctx.textBaseline = 'middle';
        ctx.font = valFont;
        ctx.fillStyle = '#2b2b36';
        ctx.fillText(valText, startX, centerY);

        ctx.font = unitFont;
        ctx.fillStyle = '#ff4b72';
        ctx.fillText(unitText, startX + valWidth + marginLeft, centerY);

        // 3. SUB-TEXT (Unten)
        if (subText !== "") {
            ctx.font = '12px sans-serif';
            ctx.fillStyle = '#888';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText(subText, centerX, centerY + 25);
        }
        
        ctx.restore();
    }
};

async function updateLive() {
   try {
       const res = await fetch('/api/live');
       const d = await res.json();
       
       // --- ZEITPRÜFUNG ---
       const now = new Date();
       const dataTime = d.t ? new Date(d.t) : null;
       // Differenz in Minuten berechnen
       const diffMinutes = dataTime ? (now - dataTime) / 1000 / 60 : Infinity;
       const isOffline = diffMinutes > 5;
       
       if (isOffline) {
        document.getElementById('liveW').innerHTML = `<span style="color: #888;">---</span>`;
        document.getElementById('liveA').innerHTML = `<span style="color: #888;">---</span>`;
        document.getElementById('liveV').innerHTML = `<span style="color: #888;">---</span>`;
      } else {
        document.getElementById('liveW').innerHTML = `${d.w.toFixed(1)}<span class="unit" style="color: var(--accent);">W</span>`;
        const mA = d.a * 1000;
        document.getElementById('liveA').innerHTML = `${mA.toFixed(1)}<span class="unit" style="color: var(--accent);">mA</span>`;
        document.getElementById('liveV').innerHTML = `${d.v.toFixed(1)}<span class="unit" style="color: var(--accent);">V</span>`;
      }
      const timeElement = document.getElementById('liveTime');
      if (isOffline) {
        timeElement.innerText = `(Balkonkraftwerk offline)`;
        timeElement.style.color = "#ff4b72"; // Rot-Ton passend zu deinem Design
        timeElement.style.fontWeight = "bold";
      } else if (d.t) {
        const dt = new Date(d.t);
        const formatted = dt.toLocaleDateString('de-DE') + ", " + dt.toLocaleTimeString('de-DE');
        timeElement.innerText = `(letzte Aktualisierung: ${formatted})`;
        timeElement.style.fontWeight = "normal";
      }

      // Wenn offline, setzen wir die Donut-Werte auf 0
      const p1 = isOffline ? 0 : (d.panel1_w || 0);
      const p2 = isOffline ? 0 : (d.panel2_w || 0);
      const totalDC = p1 + p2;
      
      let p1Pct = 0, p2Pct = 0;
      if (totalDC > 0) {
        p1Pct = (p1 / totalDC) * 100;
        p2Pct = (p2 / totalDC) * 100;
      }
      
      document.getElementById('percP1').innerText = `${p1Pct.toFixed(0)}%`;
      document.getElementById('percP2').innerText = `${p2Pct.toFixed(0)}%`;

        // --- Donut Chart ---
        if (!dcDonutChart) {
            const ctx = document.getElementById('dcDonutChart').getContext('2d');
            dcDonutChart = new Chart(ctx, {
                type: 'doughnut',
                data: {
                    labels: ['Panel 1', 'Panel 2'],
                    datasets: [{
                        data: [p1, p2],
                        backgroundColor: ['#4682B4', '#be5103'],
                        borderWidth: 0,
                        hoverOffset: 10
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    cutout: '75%',
                    plugins: {
                        tooltip: { enabled: false },
                        legend: { display: false }
                    },
                    // Hover für Desktop
                    onHover: (event, chartElement) => {
                        const newIndex = chartElement.length > 0 ? chartElement[0].index : null;
                        if (activeDonutIndex !== newIndex) {
                            activeDonutIndex = newIndex;
                            dcDonutChart.draw();
                        }
                    },
                    // Click/Tap für Mobile & Desktop zum Abwählen
                    onClick: (event, chartElement) => {
                        const newIndex = chartElement.length > 0 ? chartElement[0].index : null;
                        activeDonutIndex = newIndex;
                        dcDonutChart.draw();
                    }
                },
                plugins: [centerTextPlugin]
            });
        } else {
            dcDonutChart.data.datasets[0].data = [p1, p2];
            dcDonutChart.update();
        }

   } catch (e) {}
}


function updatePeaks() {
   fetch('/api/peaks')
       .then(response => response.json())
       .then(data => {
           document.getElementById('peak-today').innerText =
               data.daily_peak.toFixed(1);

           document.getElementById('peak-today-sub').innerText =
               data.daily_peak.toFixed(1) + " W";

           document.getElementById('peak-alltime').innerText =
               data.alltime_peak.toFixed(1) + " W";
       });
}


async function updateWeather() {
   try {
       const res = await fetch('/api/weather');
       const d = await res.json();
       const iconUrl = `https://openweathermap.org/img/wn/${d.icon}@2x.png`;
       if (d.desc.includes("API Fehler") || d.desc.includes("Kein API Key")) {
           document.getElementById('weather').innerHTML = `⚠️ <span style="color:#ff4b72;">${d.desc}</span>`;
       } else {
           document.getElementById('weather').innerHTML = `${d.temp}°C  |  <img src="${iconUrl}" style="vertical-align:middle; width:30px;"> ${d.desc}`;
       }
   } catch (e) {}
}

async function fetchData() {
   document.getElementById('chartLoading').style.display = 'flex';
   document.getElementById('totalLoading').style.display = 'flex';
   document.getElementById('panelChartLoading').style.display = 'flex';
   const res = await fetch(`/api/data?start=${document.getElementById('start').value}&end=${document.getElementById('end').value}&p1=true&p2=true`);
   const data = await res.json();
   document.getElementById('totalKWh').innerHTML = `${data.total_kwh.toFixed(2)}<span class="unit">kWh</span>`;
   document.getElementById('totalEur').innerHTML = `${data.total_euro.toFixed(2)} €`;
   if (myChart) myChart.destroy();
   myChart = new Chart(document.getElementById('mainChart').getContext('2d'), {
       data: {
           labels: data.history.map(h => h.t),
           datasets: [{
                   type: 'bar',
                   label: 'Energie (kWh)',
                   data: data.history.map(h => h.kwh),
                   backgroundColor: '#ff4b72',
                   borderRadius: 6,
                   yAxisID: 'y'
               },
               {
                   type: 'line',
                   label: 'Ersparnis (€)',
                   data: data.history.map(h => h.eur),
                   borderColor: '#2b2b36',
                   borderWidth: 3,
                   tension: 0.4,
                   pointRadius: 0,
                   yAxisID: 'y1'
               }
           ]
       },
       options: {
           responsive: true,
           maintainAspectRatio: false,
           plugins: {
               legend: {
                   display: false
               }
           },
           scales: {
               x: {
                   grid: {
                       display: false
                   }
               },
               y: {
                   position: 'left',
                   beginAtZero: true,
                   title: {
                       display: true,
                       text: 'kWh',
                       font: {
                           size: 11
                       }
                   },
                   grid: {
                       color: '#f0f0f0',
                       borderDash: [5, 5]
                   }
               },
               y1: {
                   position: 'right',
                   beginAtZero: true,
                   title: {
                       display: true,
                       text: 'Euro (€)',
                       font: {
                           size: 11
                       }
                   },
                   grid: {
                       drawOnChartArea: false
                   },
                   ticks: {
                       callback: function(value) {
                           return value.toFixed(2) + ' €';
                       }
                   }
               }
           }
       }
   });
   if (panelChart) panelChart.destroy();
   panelChart = new Chart(document.getElementById('panelChart').getContext('2d'), {
        type: 'bar',
        data: {
            labels: data.history.map(h => h.t),
            datasets: [
                {
                    label: 'Panel 1 (DC)',
                    data: data.history.map(h => h.p1_kwh || 0),
                    backgroundColor: '#4682B4',
                    borderRadius: 4
                },
                {
                    label: 'Panel 2 (DC)',
                    data: data.history.map(h => h.p2_kwh || 0),
                    backgroundColor: '#be5103',
                    borderRadius: 4
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    display: true, // Hier lassen wir die Legende an, um P1/P2 zu unterscheiden
                    position: 'top',
                    labels: { boxWidth: 12, font: { size: 10 } }
                }
            },
            scales: {
                x: {
                    stacked: true, // Stapelung aktivieren
                    grid: { display: false }
                },
                y: {
                    stacked: true, // Stapelung aktivieren
                    beginAtZero: true,
                    title: {
                        display: true,
                        text: 'kWh (DC)',
                        font: { size: 11 }
                    },
                    grid: {
                        color: '#f0f0f0',
                        borderDash: [5, 5]
                    }
                }
            }
        }
    });
    updateRangeSummary();
    updateROI();
}

function formatDate(d) {
   return d.toISOString().split('T')[0];
}

function setRange(type) {
   const end = new Date();
   let start = new Date();

   if (type === 'today') {
       // bleibt heute
   } else if (type === '3days') {
       start.setDate(end.getDate() - 2);
   } else if (type === 'week') {
       start.setDate(end.getDate() - 7);
   } else if (type === 'month') {
       start.setMonth(end.getMonth() - 1);
   } else if (type === 'year') {
       start.setFullYear(end.getFullYear() - 1);
   }

   document.getElementById('start').value = formatDate(start);
   document.getElementById('end').value = formatDate(end);
   updateQuickButtonsActiveState();
   fetchData();
}


function updateQuickButtonsActiveState() {
   const startVal = document.getElementById("start").value;
   const endVal = document.getElementById("end").value;

   if (!startVal || !endVal) return;

   const today = new Date();
   const todayStr = formatDate(today);

   const d3 = new Date();
   d3.setDate(today.getDate() - 2);
   const d3Str = formatDate(d3);

   const dWeek = new Date();
   dWeek.setDate(today.getDate() - 7);
   const dWeekStr = formatDate(dWeek);

   const dMonth = new Date();
   dMonth.setMonth(today.getMonth() - 1);
   const dMonthStr = formatDate(dMonth);

   const dYear = new Date();
   dYear.setFullYear(today.getFullYear() - 1);
   const dYearStr = formatDate(dYear);

   document.querySelectorAll(".quick-btn").forEach(btn => {
       btn.classList.remove("quick-active");
   });

   if (startVal === todayStr && endVal === todayStr) {
       document.getElementById("btnToday")?.classList.add("quick-active");
   } else if (startVal === d3Str && endVal === todayStr) {
       document.getElementById("btn3days")?.classList.add("quick-active");
   } else if (startVal === dWeekStr && endVal === todayStr) {
       document.getElementById("btnWeek")?.classList.add("quick-active");
   } else if (startVal === dMonthStr && endVal === todayStr) {
       document.getElementById("btnMonth")?.classList.add("quick-active");
   } else if (startVal === dYearStr && endVal === todayStr) {
       document.getElementById("btnYear")?.classList.add("quick-active");
   }
}

function toggleRangeMenu() {
   const panel = document.getElementById('controls-panel');
   const icon = document.getElementById('range-icon');
   panel.classList.toggle('expanded');
   icon.innerText = panel.classList.contains('expanded') ? '▲' : '▼';
}

function updateRangeSummary() {
   const start = document.getElementById('start').value;
   const end = document.getElementById('end').value;

   // Formatiert das Datum von YYYY-MM-DD auf DD.MM.
   const fmt = (str) => {
       const parts = str.split('-');
       return parts.length === 3 ? `${parts[2]}.${parts[1]}.${parts[0]}` : str;
   };

   document.getElementById('range-display-text').innerText = `Zeitraum: ${fmt(start)} bis ${fmt(end)}`;
}

// Hilfsfunktion: Schließt das Menü auf Mobile nach der Auswahl
function applyAndClose() {
   fetchData();
   if (window.innerWidth <= 600) {
       toggleRangeMenu();
   }
}

async function updateROI() {
   try {
       const res = await fetch('/api/roi');
       const d = await res.json();

       document.getElementById('roi-saved').innerText =
           d.total_eur.toLocaleString('de-DE', {
               style: 'currency',
               currency: 'EUR'
           });

       document.getElementById('roi-kwh').innerText =
           `Gesamt erzeugt: ${d.total_kwh} kWh`;

       document.getElementById('roi-percent').innerText = `${d.percent}%`;
       document.getElementById('roi-bar').style.width = `${d.percent}%`;

       const roiCard = document.querySelector('.card-roi');
       const badgeContainer = document.getElementById('roi-achieved-container');

       if (d.percent >= 100 || ROI_DEBUG_FORCE_COMPLETE) {
           roiCard.classList.add('roi-complete');

           if (!document.getElementById('roi-badge')) {
               badgeContainer.innerHTML =
                   `<div id="roi-badge" class="roi-badge">
                ROI erreicht 🎉
             </div>`;
           }
       } else {
           roiCard.classList.remove('roi-complete');
           badgeContainer.innerHTML = '';
       }
       updateROIForecast();
   } catch (e) {
       console.error("ROI Fehler", e);
   }

}

function getLogIntensity(value, max) {
   if (value <= 0 || max <= 0) return 0;

   const logValue = Math.log(value + 1);
   const logMax = Math.log(max + 1);

   return logValue / logMax;
}

async function initHeatmapYears() {
   const res = await fetch("/api/heatmap");
   const data = await res.json();

   const select = document.getElementById("heatmapYearSelect");
   select.innerHTML = "";

   data.years.forEach(y => {
       const opt = document.createElement("option");
       opt.value = y;
       opt.textContent = y;
       select.appendChild(opt);
   });

   if (data.years.length > 0) {
       loadHeatmap(data.years[0]);
   }
}

async function loadHeatmap(year) {

   const overlay = document.getElementById("chartLoading");
   overlay.style.display = "flex";

   const container = document.getElementById("heatmapContainer");
   container.innerHTML = "";

   const res = await fetch(`/api/heatmap?year=${year}`);
   const data = await res.json();

   const max = data.max || 0.001;
   let lastMonth = null;

   data.heatmap.forEach(day => {

       const currentMonth = day.date.substring(0, 7);

       /* Monatslabel einfügen */
       if (currentMonth !== lastMonth) {
           const separator = document.createElement("div");
           separator.style.gridColumn = "1 / -1";
           separator.style.height = "8px";
           separator.style.margin = "6px 0";
           separator.style.position = "relative";

           const label = document.createElement("div");
           label.textContent = new Date(day.date)
               .toLocaleString("de-DE", {
                   month: "short"
               });
           label.style.fontSize = "0.75em";
           label.style.fontWeight = "600";
           label.style.color = "var(--text-muted)";
           label.style.marginBottom = "4px";

           separator.appendChild(label);
           container.appendChild(separator);

           lastMonth = currentMonth;
       }

       function getLogRatio(value, max) {
           if (!max || value <= 0) return 0;

           const logValue = Math.log(value + 1);
           const logMax = Math.log(max + 1);

           return logValue / logMax;
       }

       const ratio = getLogRatio(day.kwh, max);
       const intensity = Math.min(1, ratio);

       const cell = document.createElement("div");
       cell.className = "heatmap-cell";

       if (day.kwh > 0) {
           cell.style.background =
               `rgba(255, 75, 114, ${0.15 + intensity * 0.75})`;
       }

       /* Tooltip */
       const tooltip = document.createElement("div");
       tooltip.className = "heatmap-tooltip";

       tooltip.innerHTML = `
  <div style="font-weight:700; margin-bottom:4px;">
      ${day.date}
  </div>
  <div>${day.kwh.toFixed(3)} kWh</div>
  <div>${day.eur.toFixed(2)} € Ersparnis</div>
  <div style="margin-top:4px; font-weight:600;">
      Peak: ${day.max_w ? day.max_w.toFixed(1) : "0.0"} W
  </div>
  `;

       cell.appendChild(tooltip);

       /* Klick → Chart öffnen */
       cell.addEventListener("click", () => {
           document.getElementById("start").value = day.date;
           document.getElementById("end").value = day.date;
           fetchData();

           window.scrollTo({
               top: document.querySelector(".chart-card").offsetTop - 20,
               behavior: "smooth"
           });
       });

       container.appendChild(cell);
   });

   overlay.style.display = "none";
}

document.getElementById("heatmapYearSelect")
   .addEventListener("change", e => loadHeatmap(e.target.value));

document.addEventListener("DOMContentLoaded", initHeatmapYears);

async function initHourlyHeatmap() {
    const res = await fetch("/api/heatmap_hourly");
    const data = await res.json();

    const select = document.getElementById("hourlyMonthSelect");
    select.innerHTML = "";

    data.months.forEach(m => {
        const opt = document.createElement("option");
        opt.value = m;
        // Bsp: "2026-03" -> "März 2026"
        const dateObj = new Date(m + "-01");
        opt.textContent = dateObj.toLocaleDateString("de-DE", { month: "long", year: "numeric" });
        select.appendChild(opt);
    });

    if (data.months.length > 0) {
        loadHourlyHeatmap(data.months[0]);
    }
    
    select.addEventListener("change", (e) => loadHourlyHeatmap(e.target.value));
}

async function loadHourlyHeatmap(month) {
    document.getElementById('hourlyHeatmapLoading').style.display = 'flex';

    const container = document.getElementById("hourlyHeatmapContainer");
    container.innerHTML = "";

    try {
      const res = await fetch(`/api/heatmap_hourly?month=${month}`);
      const data = await res.json();
      
      // Obere linke Ecke (leer)
      container.appendChild(document.createElement("div"));
      
      // Kopfzeile: Stunden 0 bis 23
      for (let h = 0; h < 24; h++) {
          const hLbl = document.createElement("div");
          hLbl.className = "hourly-label-x";
          hLbl.textContent = h;
          container.appendChild(hLbl);
      }

      // Herausfinden, wie viele Tage der Monat hat
      const [yearStr, monthStr] = month.split('-');
      const daysInMonth = new Date(yearStr, monthStr, 0).getDate();

      // Zeilen generieren (Tage)
      for (let d = 1; d <= daysInMonth; d++) {
          const dayKey = d.toString().padStart(2, '0');
          
          // Label für die Y-Achse (Tag)
          const dLbl = document.createElement("div");
          dLbl.className = "hourly-label-y";
          dLbl.textContent = d;
          container.appendChild(dLbl);

          // Zellen für diesen Tag generieren
          for (let h = 0; h < 24; h++) {
              const hourKey = h.toString().padStart(2, '0');
              const cell = document.createElement("div");
              cell.className = "hourly-cell";

              const cellData = data.data[dayKey] && data.data[dayKey][hourKey];
              let p1Wh = 0, p2Wh = 0, totalWh = 0;

              if (cellData) {
                  p1Wh = cellData.p1;
                  p2Wh = cellData.p2;
                  totalWh = p1Wh + p2Wh;
              }

              if (totalWh > 0) {
                  // Dominanz berechnen
                  const ratio = p2Wh / totalWh;
                  // Intensität (im Vergleich zur sonnigsten Stunde des Monats)
                  const maxWh = data.max || 1000;
                  const intensity = Math.min(1, totalWh / maxWh);

                  // Farben: Panel 1 (#4682B4) und Panel 2 (#be5103)
                  const cP1 = { r: 70, g: 130, b: 180 };
                  const cP2 = { r: 190, g: 81, b: 3 };

                  const r = Math.round(cP1.r + (cP2.r - cP1.r) * ratio);
                  const g = Math.round(cP1.g + (cP2.g - cP1.g) * ratio);
                  const b = Math.round(cP1.b + (cP2.b - cP1.b) * ratio);

                  // Grundsättigung 0.1, damit es nie unsichtbar wird + intensity
                  cell.style.background = `rgba(${r}, ${g}, ${b}, ${0.1 + intensity * 0.9})`;
              }

              // Tooltip hinzufügen (identisch zur anderen Heatmap)
              const tooltip = document.createElement("div");
              tooltip.className = "heatmap-tooltip";
              const p1P = totalWh > 0 ? ((p1Wh/totalWh)*100).toFixed(0) : 0;
              const p2P = totalWh > 0 ? ((p2Wh/totalWh)*100).toFixed(0) : 0;
              
              tooltip.innerHTML = `
                  <div style="font-weight:700; margin-bottom:4px;">${d}.${monthStr}.${yearStr} | ${h}:00 - ${h+1}:00 Uhr</div>
                  <div style="color:#4682B4">P1: ${(p1Wh/1000).toFixed(3)} kWh (${p1P}%)</div>
                  <div style="color:#be5103">P2: ${(p2Wh/1000).toFixed(3)} kWh (${p2P}%)</div>
                  <div style="margin-top:4px; font-size:0.85em; border-top:1px solid #eee; padding-top:4px;">Gesamt: ${(totalWh/1000).toFixed(3)} kWh</div>
              `;
              cell.appendChild(tooltip);

              container.appendChild(cell);
          }
      }
    } catch (error) {
      console.error("Fehler beim Laden der Stunden-Heatmap:", error);
    } finally {
      // 2. Ladeanzeige am Ende garantiert wieder ausblenden
      document.getElementById('hourlyHeatmapLoading').style.display = 'none';
    }
  }

// Beim Laden der Seite aufrufen
document.addEventListener("DOMContentLoaded", initHourlyHeatmap);

function prettyFeatureName(key) {
   const featureNames = {
       clouds: "Mittlerer Bewölkungsgrad",
       rolling_avg: "Durchschnittlicher Ertrag der letzten Tage",
       prev_kwh: "Ertrag des Vortages",
       temperature: "Mittlere Tageslufttemperatur",
       sun_elevation: "Sonnenstand zur Mittagszeit",
       month_sin: "Monatliche Saisonkomponente",
       sin_day: "Saisonale Phase (Sinus)",
       cos_day: "Saisonale Phase (Cosinus)",
       daylight: "Tageslichtdauer",
       sunshine: "Sonnenscheindauer"
   };
   return featureNames[key] || key;
}

async function loadForecast() {

   const response = await fetch("/api/forecast");
   const data = await response.json();

   console.log("Forecast API:", data);

   const forecast = data.forecast || [];
   const mae = data.mae || 0;

   if (forecast.length === 0) {
       console.warn("Keine Forecast Daten vorhanden");
       return;
   }

   const totalKwh = forecast.reduce((sum, d) => sum + d.kwh_pred, 0);
   const totalEur = forecast.reduce((sum, d) => sum + d.eur_pred, 0);

   document.getElementById('forecast-total-kwh').innerHTML = `${totalKwh.toFixed(2)}<span class="unit" style="color: var(--accent);">kWh</span>`;

   document.getElementById("forecast-total-eur").innerHTML = `${totalEur.toFixed(2)}<span class="unit" style="color: var(--accent);">€</span>`;

   document.getElementById("forecast-mae").innerHTML = `${mae.toFixed(2)}<span class="unit" style="color: var(--accent);">kWh</span>`;


   // =========================
   // Forecast Chart mit Unsicherheitsband
   // =========================

   const labels = forecast.map(f => {
       const parts = f.date.split("-");
       return `${parts[2]}.${parts[1]}.${parts[0]}`;
   });
   const values = forecast.map(f => f.kwh_pred);

   const lower = forecast.map(f => Number(f.kwh_lower));
   const upper = forecast.map(f => Number(f.kwh_upper));

   const ctx = document.getElementById("forecastChart");

   if (window.forecastChartInstance) {
       window.forecastChartInstance.destroy();
   }

   window.forecastChartInstance = new Chart(ctx, {
       type: 'line',
       data: {
           labels: labels,
           datasets: [
            {
                label: '90% Quantil',
                data: upper,
                borderColor: 'transparent',
                pointRadius: 0,
                fill: false,
                tooltipHidden: true
            },
            {
                label: 'Unsicherheitsband (80%)',
                data: lower,
                borderColor: 'transparent',
                borderWidth: 0,
                pointRadius: 0,
                fill: '-1',
                backgroundColor: 'rgba(239,68,68,0.18)',
            },
            {
                label: 'Prognose',
                data: values,
                borderColor: '#ef4444',
                backgroundColor: '#ef4444',
                tension: 0.3,
                pointRadius: 6,
                pointHoverRadius: 10,
                hitRadius: 20,
                fill: false
            }
        ]
       },
       options: {
           responsive: true,
           interaction: {
               mode: 'index',
               intersect: false
           },
           plugins: {
               legend: {
                   display: false
               },
                tooltip: {
                    enabled: false, // Standard deaktivieren
                    external: function(context) {
                        let tooltipEl = document.getElementById('forecast-chart-tooltip');

                        if (!tooltipEl) {
                            tooltipEl = document.createElement('div');
                            tooltipEl.id = 'forecast-chart-tooltip';
                            // Wir fügen deine CSS-Klasse hinzu!
                            tooltipEl.classList.add('heatmap-tooltip');
                            // Überschreiben einiger Werte für die Chart-Positionierung
                            Object.assign(tooltipEl.style, {
                                opacity: 1,
                                pointerEvents: 'none',
                                position: 'absolute',
                                transition: 'opacity 0.15s ease',
                                bottom: 'auto', // Reset von deinem CSS
                                left: '0px',
                                top: '0px',
                                transform: 'translate(-50%, -110%)', // Zentriert über dem Punkt
                                whiteSpace: 'nowrap',
                                zIndex: '100'
                            });
                            document.body.appendChild(tooltipEl);
                        }

                        const tooltipModel = context.tooltip;
                        if (tooltipModel.opacity === 0) {
                            tooltipEl.style.opacity = 0;
                            return;
                        }

                        if (tooltipModel.body) {
                            const index = tooltipModel.dataPoints[0].dataIndex;
                            const f = forecast[index];
                            
                            tooltipEl.innerHTML = `
                                <div style="font-weight:700; margin-bottom:6px; border-bottom:1px solid rgba(0,0,0,0.08); padding-bottom:4px; color: var(--text-dark);">
                                    ${labels[index]}
                                </div>
                                
                                <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 4px; font-weight:600;">
                                    <span style="width: 8px; height: 8px; background: #ef4444; border-radius: 50%; display: inline-block; color: #ef4444;"></span>
                                    Prognose: ${f.kwh_pred.toFixed(3)} kWh
                                </div>

                                <div style="font-size: 0.9em; color: var(--text-dark); display: flex; flex-direction: column; gap: 2px; padding-left: 16px;">
                                    <div style="display: flex; align-items: center; gap: 6px;">
                                        <span style="width: 6px; height: 2px; background: #ccc; display: inline-block;"></span>
                                        <span>Max (90% Quantil): <strong>${Number(f.kwh_upper).toFixed(2)} kWh</strong></span>
                                    </div>
                                    <div style="display: flex; align-items: center; gap: 6px;">
                                        <span style="width: 6px; height: 2px; background: #ccc; display: inline-block;"></span>
                                        <span>Min (10% Quantil): <strong>${Number(f.kwh_lower).toFixed(2)} kWh</strong></span>
                                    </div>
                                </div>

                                <div style="font-size: 0.75em; color: #999; font-style: italic; margin-top: 6px; padding-top: 4px; border-top: 1px dashed #eee;">
                                    80% Wahrscheinlichkeits-Intervall
                                </div>
                            `;
                          }

                        const position = context.chart.canvas.getBoundingClientRect();
                        tooltipEl.style.opacity = 1;
                        tooltipEl.style.left = position.left + window.pageXOffset + tooltipModel.caretX + 'px';
                        tooltipEl.style.top = position.top + window.pageYOffset + tooltipModel.caretY + 'px';
                    }
                }
           },
           scales: {
               y: {
                   beginAtZero: true
               }
           },
           onClick: (event, elements, chart) => {
               const points = chart.getElementsAtEventForMode(
                   event,
                   'index', {
                       intersect: false
                   },
                   true
               );
               if (points.length) {
                   const index = points[0].index;
                   showShapDetails(forecast[index]);
               }
           }
       }
   });
}

async function loadFeatureImportance() {

   try {
       const response = await fetch("/api/feature-importance");
       const data = await response.json();

       if (!Array.isArray(data)) return;

       const sorted = data.sort((a, b) => b.importance - a.importance);
       const labels = sorted.map(item => prettyFeatureName(item.feature));
       const values = sorted.map(item => item.importance);

       new Chart(document.getElementById("featureImportanceChart"), {
           type: 'bar',
           data: {
               labels: labels,
               datasets: [{
                   data: values
               }]
           },
           options: {
               indexAxis: 'y',
               plugins: {
                   legend: {
                       display: false
                   }
               }
           }
       });

   } catch (error) {
       console.error("Feature Importance Fehler:", error);
   }
}

async function loadGlobalShap() {

   try {
       const response = await fetch("/api/shap-summary");
       const data = await response.json();

       if (!Array.isArray(data)) return;

       const labels = data.map(item =>
           prettyFeatureName(item.feature)
       );
       const values = data.map(item =>
           item.mean_abs_shap
       );

       new Chart(document.getElementById("globalShapChart"), {
           type: 'bar',
           data: {
               labels: labels,
               datasets: [{
                   data: values
               }]
           },
           options: {
               indexAxis: 'y',
               plugins: {
                   legend: {
                       display: false
                   }
               }
           }
       });

   } catch (error) {
       console.error("Global SHAP Fehler:", error);
   }
}


function showShapDetails(point) {

   // ===== Punkt global merken für ResizeObserver =====
   window._lastShapPoint = point;

   const isTouchDevice = window.matchMedia("(hover: none)").matches;

   const card = document.getElementById("shapDetailCard");
   card.style.display = "block";

   const parts = point.date.split("-");
   const formattedDate = `${parts[2]}.${parts[1]}.${parts[0]}`;
   const shap = point.shap;

   const featureNames = {
       clouds: "Mittlerer Bewölkungsgrad",
       rolling_avg: "Durchschnittlicher Ertrag der letzten Tage",
       prev_kwh: "Ertrag des Vortages",
       temperature: "Mittlere Tageslufttemperatur",
       sun_elevation: "Sonnenstand zur Mittagszeit",
       month_sin: "Monatliche Saisonkomponente",
       sin_day: "Saisonale Phase (Sinus)",
       cos_day: "Saisonale Phase (Cosinus)",
       daylight: "Tageslichtdauer",
       sunshine: "Sonnenscheindauer"
   };
   // Test Änderung Main Branch
   const container = document.getElementById("shapForcePlot");

   // ===== ResizeObserver nur einmal registrieren =====
   if (!container._resizeObserverAttached) {

       const observer = new ResizeObserver(() => {
           if (window._lastShapPoint) {
               showShapDetails(window._lastShapPoint);
           }
       });

       observer.observe(container);
       container._resizeObserverAttached = true;
   }

   container.innerHTML = "";
   container.style.position = "relative";
   container.style.display = "flex";
   container.style.alignItems = "center";
   container.style.justifyContent = "center";
   container.style.height = "100px";
   container.style.overflow = "visible";

   const baseline = point.baseline || 0;
   const explainedKwh = point.explained_kwh || 0;
   const explainedRatio = point.explained_ratio || 0;
   const prediction = point.kwh_pred || 0;
   const explainedPercent = (explainedRatio * 100).toFixed(1);
   const directionText = explainedKwh >= 0 ? "über" : "unter";

   document.getElementById("shapDetailkWh").innerText =
       "Prognose für " + formattedDate + ": " + prediction.toFixed(2) + " kWh";

   document.getElementById("modelBaseline").innerText =
       baseline.toFixed(2) + " kWh";

   document.getElementById("modelEffect").innerText =
       (explainedKwh >= 0 ? "+" : "") +
       explainedKwh.toFixed(2) + " kWh";

   document.getElementById("modelDeviation").innerText =
       explainedPercent + "% " + directionText;

   let positiveDrivers = [];
   let negativeDrivers = [];

   Object.entries(point.shap || {}).forEach(([key, value]) => {
       if (value > 0) positiveDrivers.push({
           key,
           value
       });
       if (value < 0) negativeDrivers.push({
           key,
           value
       });
   });

   positiveDrivers.sort((a, b) => b.value - a.value);
   negativeDrivers.sort((a, b) => a.value - b.value);

   const topPositive = positiveDrivers.slice(0, 2).map(d => d.key);
   const topNegative = negativeDrivers.slice(0, 2).map(d => d.key);

   let driverText = "";
   if (explainedKwh >= 0 && topPositive.length > 0) {
       driverText = "hauptsächlich getrieben durch " +
           topPositive.map(prettyFeatureName).join(" und ");
   } else if (explainedKwh < 0 && topNegative.length > 0) {
       driverText = "hauptsächlich gebremst durch " +
           topNegative.map(prettyFeatureName).join(" und ");
   }

   document.getElementById("modelInterpretation").innerText =
       "Die Prognose liegt " + explainedPercent + "% " + directionText +
       " dem durchschnittlichen Modellniveau, " +
       driverText + ".";

   const axis = document.createElement("div");
   axis.style.position = "absolute";
   axis.style.left = "50%";
   axis.style.top = "0";
   axis.style.bottom = "30px";
   axis.style.width = "2px";
   axis.style.background = "#bdbdbd";
   container.appendChild(axis);

   const entries = Object.entries(point.shap)
       .map(([key, value]) => ({
           key,
           value
       }))
       .sort((a, b) => a.value - b.value);

   const negatives = entries.filter(e => e.value < 0)
       .sort((a, b) => a.value - b.value);

   const positives = entries.filter(e => e.value >= 0)
       .sort((a, b) => b.value - a.value);

   const orderedEntries = [...negatives, ...positives];

   const negativeColors = ["#c97b84", "#d48f96", "#dea3a8", "#e8b7bb", "#f1cbcd"];
   const positiveColors = ["#6c8ebf", "#7fa0cc", "#92b2d9", "#a5c4e6", "#b8d6f2"];

   const colorMap = {};
   let negIndex = 0;
   let posIndex = 0;

   orderedEntries.forEach((entry) => {
       if (entry.value < 0) {
           colorMap[entry.key] = negativeColors[negIndex++ % negativeColors.length];
       } else {
           colorMap[entry.key] = positiveColors[posIndex++ % positiveColors.length];
       }
   });

   const legendContainer = document.getElementById("shapLegend");
   legendContainer.innerHTML = "";

   const legendItems = {};
   const bars = {};
   let activeFeature = null;

   orderedEntries.forEach((entry) => {

       const legendItem = document.createElement("div");
       legendItem.className = "shap-legend-item";

       const colorBox = document.createElement("div");
       colorBox.className = "shap-color-box";
       colorBox.style.background = colorMap[entry.key];

       const label = document.createElement("div");
       label.innerText = featureNames[entry.key] || entry.key;

       const effect = document.createElement("div");
       effect.className = "shap-effect";

       const sign = entry.value >= 0 ? "+" : "";
       effect.innerText = `${sign}${entry.value.toFixed(2)} kWh`;

       legendItem.appendChild(colorBox);
       legendItem.appendChild(label);
       legendItem.appendChild(effect);

       legendContainer.appendChild(legendItem);
       legendItems[entry.key] = legendItem;
   });

   const maxWidth = container.offsetWidth / 2;
   const scaleFactor = maxWidth / prediction;

   function activateFeature(key) {
       Object.entries(legendItems).forEach(([k, item]) => {
           item.classList.toggle("active", k === key);
           item.classList.toggle("inactive", k !== key);
       });
       Object.entries(bars).forEach(([k, b]) => {
           b.classList.toggle("active", k === key);
           b.classList.toggle("inactive", k !== key);
       });
   }

   function resetAll() {
       activeFeature = null;
       Object.values(legendItems).forEach(item =>
           item.classList.remove("inactive", "active"));
       Object.values(bars).forEach(b =>
           b.classList.remove("inactive", "active"));
   }

   let currentLeftOffset = 0;
   let currentRightOffset = 0;
   const centerX = container.offsetWidth / 2;

   orderedEntries.forEach((entry) => {

       const rawWidth = Math.abs(entry.value) * scaleFactor;
       const width = Math.max(rawWidth, 14);

       const bar = document.createElement("div");
       bar.classList.add("shap-bar");

       bar.style.position = "absolute";
       bar.style.height = "28px";
       bar.style.width = width + "px";
       bar.style.borderRadius = "4px";
       bar.style.cursor = "pointer";
       bar.style.top = "40px";
       bar.style.background = colorMap[entry.key];

       if (entry.value < 0) {
           currentLeftOffset += width;
           bar.style.left = (centerX - currentLeftOffset) + "px";
       } else {
           bar.style.left = (centerX + currentRightOffset) + "px";
           currentRightOffset += width;
       }

       bars[entry.key] = bar;

       bar.addEventListener("click", (event) => {
           event.stopPropagation();
           if (activeFeature === entry.key) {
               resetAll();
               return;
           }
           activeFeature = entry.key;
           activateFeature(entry.key);
       });

       if (!isTouchDevice) {
           bar.addEventListener("mouseenter", () => {
               if (activeFeature === null)
                   activateFeature(entry.key);
           });
           bar.addEventListener("mouseleave", () => {
               if (activeFeature === null)
                   resetAll();
           });
       }

       container.appendChild(bar);
   });

   document.addEventListener("click", function(event) {
       if (!activeFeature) return;
       const clickedInsideBar = Object.values(bars)
           .some(bar => bar.contains(event.target));
       if (!clickedInsideBar) resetAll();
   });

   document.getElementById("seasonLabel").innerText =
       point.season_label;

   document.getElementById("seasonStrength").innerText =
       point.season_strength.toFixed(2);

   document.getElementById("seasonStrengthNormalized").innerText =
       (point.season_strength_normalized * 100).toFixed(1);

   const strength = point.season_strength_normalized;
   let text = "";

   if (strength > 0.25) {
       text = "Die Jahreszeit hat einen starken Einfluss auf die Prognose.";
   } else if (strength > 0.12) {
       text = "Die Jahreszeit beeinflusst den Ertrag moderat.";
   } else {
       text = "Die Saison spielt nur eine untergeordnete Rolle.";
   }

   document.getElementById("seasonInterpretation").innerText = text;
}


const t = new Date().toISOString().split('T')[0];
document.getElementById('start').value = t;
document.getElementById('end').value = t;
document.getElementById('start').addEventListener('change', updateQuickButtonsActiveState);
document.getElementById('end').addEventListener('change', updateQuickButtonsActiveState);
fetchData();
updateWeather();
updateLive();
updatePeaks();
updateQuickButtonsActiveState();
loadForecast();
loadGlobalShap();
loadFeatureImportance();
setInterval(updateLive, 5000);
setInterval(fetchData, 60000);
setInterval(updatePeaks, 60000);
setInterval(checkLoadingStatus, 500);