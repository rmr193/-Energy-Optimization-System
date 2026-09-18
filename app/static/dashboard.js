/**
 * GRIDWISE ENTERPRISE — WHITE & MAROON GLASS DASHBOARD LOGIC
 * High-performance SCADA telemetry, Chart.js integrations, and dispatch audit engine.
 */

let allCases = [];
let currentCase = null;
let lastOptimizationResponse = null;

let energyBalanceChart = null;
let batteryChart = null;
let tariffChart = null;

document.addEventListener("DOMContentLoaded", () => {
  initHealthCheck();
  loadSampleCases();
  setupEventListeners();
});

// ------------------------------------------------------------------------------
// Health Readiness Verification
// ------------------------------------------------------------------------------
async function initHealthCheck() {
  const badgeEl = document.getElementById("health-badge");
  try {
    const res = await fetch("/health");
    if (res.ok) {
      const data = await res.json();
      if (data.status === "ok") {
        badgeEl.innerHTML = `<span class="pulse-dot"></span> Online (200 OK)`;
        badgeEl.className = "stat-val status-online";
      }
    }
  } catch (err) {
    badgeEl.innerHTML = `<span class="pulse-dot" style="background:#e11d48;box-shadow:none;"></span> Offline`;
    badgeEl.className = "stat-val text-dim";
  }
}

// ------------------------------------------------------------------------------
// Load Public Sample Cases
// ------------------------------------------------------------------------------
async function loadSampleCases() {
  const selectEl = document.getElementById("scenario-select");
  try {
    const res = await fetch("/api/sample-cases");
    if (!res.ok) throw new Error("Could not retrieve sample cases");
    const data = await res.json();
    allCases = data.cases || [];

    selectEl.innerHTML = "";
    allCases.forEach((c) => {
      const opt = document.createElement("option");
      opt.value = c.id;
      opt.innerText = `${c.id} : ${c.label}`;
      selectEl.appendChild(opt);
    });

    if (allCases.length > 0) {
      selectScenario(allCases[0].id);
      // Auto-run initial scenario for immediate presentation
      runOptimization();
    }
  } catch (err) {
    selectEl.innerHTML = "<option>Error loading scenario database</option>";
    console.error(err);
  }
}

function selectScenario(caseId) {
  const found = allCases.find((c) => c.id === caseId);
  if (!found) return;
  currentCase = found;
  renderOperatorNotes(found.input.operator_notes);
}

function renderOperatorNotes(notes) {
  const listEl = document.getElementById("notes-list");
  const countBadge = document.getElementById("notes-count-badge");
  countBadge.innerText = `${notes.length} Directive Notice${notes.length > 1 ? "s" : ""}`;
  listEl.innerHTML = "";

  notes.forEach((note, idx) => {
    const item = document.createElement("div");
    item.className = "formal-note-card";
    item.innerHTML = `
      <span class="note-tag">LOG [${String(idx).padStart(2, '0')}]</span>
      <span class="note-content-text">${escapeHtml(note)}</span>
    `;
    listEl.appendChild(item);
  });
}

// ------------------------------------------------------------------------------
// Event Listeners
// ------------------------------------------------------------------------------
function setupEventListeners() {
  const selectEl = document.getElementById("scenario-select");
  selectEl.addEventListener("change", (e) => {
    selectScenario(e.target.value);
    runOptimization();
  });

  document.getElementById("run-opt-btn").addEventListener("click", runOptimization);
  document.getElementById("reset-btn").addEventListener("click", () => {
    if (currentCase) {
      selectScenario(currentCase.id);
      runOptimization();
    }
  });

  document.getElementById("copy-json-btn").addEventListener("click", () => {
    if (!lastOptimizationResponse) {
      alert("Please execute an optimization cycle first.");
      return;
    }
    navigator.clipboard.writeText(JSON.stringify(lastOptimizationResponse, null, 2));
    const btn = document.getElementById("copy-json-btn");
    const origHtml = btn.innerHTML;
    btn.innerHTML = `<span>✓ Exported to Clipboard</span>`;
    setTimeout(() => { btn.innerHTML = origHtml; }, 2000);
  });
}

// ------------------------------------------------------------------------------
// Optimization Engine Execution
// ------------------------------------------------------------------------------
async function runOptimization() {
  if (!currentCase) return;

  const btn = document.getElementById("run-opt-btn");
  const origHtml = btn.innerHTML;
  btn.innerHTML = `<svg class="spin" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="10"></circle><path d="M12 6v6l4 2"></path></svg> <span>Solving HiGHS LP...</span>`;
  btn.disabled = true;

  try {
    const startTime = performance.now();
    const res = await fetch("/optimize-energy", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(currentCase.input),
    });

    const elapsed = Math.round(performance.now() - startTime);

    if (!res.ok) {
      const err = await res.json();
      alert(`Optimization rejected: ${err.message || err.detail || JSON.stringify(err)}`);
      return;
    }

    const data = await res.json();
    lastOptimizationResponse = data;

    renderKPIs(data, currentCase.input.battery, elapsed);
    renderDirectivesAudit(data.directive_interpretation);
    renderSCADACharts(data.hourly_plan, currentCase.input);
    renderDispatchLedger(data.hourly_plan, currentCase.input);

  } catch (err) {
    console.error(err);
    alert(`Communication fault: ${err.message}`);
  } finally {
    btn.innerHTML = origHtml;
    btn.disabled = false;
  }
}

// ------------------------------------------------------------------------------
// Render KPI Metrics
// ------------------------------------------------------------------------------
function renderKPIs(data, battery, elapsedMs) {
  document.getElementById("metric-grid-kwh").innerText = Number(data.total_grid_kwh).toLocaleString("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 2 });
  document.getElementById("metric-cost-bdt").innerText = Number(data.total_cost_bdt).toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: 0 });
  document.getElementById("metric-peak-kwh").innerText = Number(data.peak_grid_kwh).toLocaleString("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 });

  // Battery neutrality check
  const lastHour = data.hourly_plan[23];
  const eEnd = lastHour ? lastHour.battery_energy_after_kwh : 0;
  const isNeutral = Math.abs(eEnd - battery.initial_energy_kwh) <= 0.05;
  const neutEl = document.getElementById("metric-neutrality");
  const neutSub = document.getElementById("metric-neutrality-sub");

  if (isNeutral) {
    neutEl.innerText = "100%";
    neutEl.className = "kpi-number text-emerald";
    neutSub.innerText = `E_start (${battery.initial_energy_kwh} kWh) == E_23 (${eEnd.toFixed(1)} kWh)`;
  } else {
    neutEl.innerText = "Violation";
    neutEl.className = "kpi-number text-rose";
    neutSub.innerText = `Delta: ${(eEnd - battery.initial_energy_kwh).toFixed(2)} kWh`;
  }

  // Strategy summary banner
  const banner = document.getElementById("strategy-banner");
  const bannerText = document.getElementById("strategy-summary-text");
  bannerText.innerText = `${data.plan_summary} [Optimization Latency: ${elapsedMs} ms | HiGHS Linear Programming Model]`;
  banner.style.display = "flex";
}

// ------------------------------------------------------------------------------
// Render Directive Audit Cards
// ------------------------------------------------------------------------------
function renderDirectivesAudit(directives) {
  const container = document.getElementById("directives-container");
  container.innerHTML = "";

  directives.forEach((d) => {
    const card = document.createElement("div");
    card.className = `audit-directive-card ${d.applies ? "active-rule" : "no-op-rule"}`;

    let detailsHtml = "";
    if (d.structured_adjustment) {
      const adj = d.structured_adjustment;
      const hoursHtml = (adj.hours || [])
        .map((h) => `<span class="rule-chip">${String(h).padStart(2, '0')}:00</span>`)
        .join("");

      let paramsHtml = "";
      if (adj.factor !== undefined) {
        paramsHtml += `<span class="rule-chip rule-chip-highlight">Solar Factor: ${(adj.factor * 100).toFixed(0)}% Usable</span>`;
      }
      if (adj.minimum_energy_kwh !== undefined) {
        paramsHtml += `<span class="rule-chip rule-chip-highlight">Min Reserve: ${adj.minimum_energy_kwh} kWh</span>`;
      }
      if (adj.max_grid_kwh !== undefined) {
        paramsHtml += `<span class="rule-chip rule-chip-highlight">Feeder Cap: ${adj.max_grid_kwh} kWh</span>`;
      }

      detailsHtml = `
        <div class="rule-parameters-row">
          <span style="font-size:0.7rem; color:var(--text-dim); text-transform:uppercase; font-weight:700;">Intervals:</span>
          ${hoursHtml}
          ${paramsHtml}
        </div>
      `;
    }

    card.innerHTML = `
      <div class="card-top-row">
        <span class="rule-type-badge">${d.directive_type}</span>
        <span class="rule-status-badge ${d.applies ? "status-active" : "status-noop"}">
          ${d.applies ? "Enforced by Optimizer" : "Ignored (Non-Operational)"}
        </span>
      </div>
      ${detailsHtml}
      <div class="rule-explanation-text">${escapeHtml(d.explanation)}</div>
    `;

    container.appendChild(card);
  });
}

// ------------------------------------------------------------------------------
// Render Dispatch Ledger Table
// ------------------------------------------------------------------------------
function renderDispatchLedger(plan, inputData) {
  const tbody = document.getElementById("schedule-table-body");
  tbody.innerHTML = "";

  const demandMap = {};
  const baseSolarMap = {};
  const tariffMap = {};
  inputData.hours.forEach((h) => {
    demandMap[h.hour] = h.demand_kwh;
    baseSolarMap[h.hour] = h.solar_kwh;
    tariffMap[h.hour] = h.tariff_bdt_per_kwh;
  });

  plan.forEach((entry) => {
    const tr = document.createElement("tr");
    const h = entry.hour;
    const dVal = demandMap[h] ?? 0;
    const sBase = baseSolarMap[h] ?? 0;
    const tariff = tariffMap[h] ?? 0;
    const cost = entry.grid_kwh * tariff;

    let chipClass = "action-idle";
    let chipText = "IDLE";
    if (entry.battery_action === "charge") {
      chipClass = "action-charge";
      chipText = "CHARGE";
    } else if (entry.battery_action === "discharge") {
      chipClass = "action-discharge";
      chipText = "DISCHARGE";
    }

    tr.innerHTML = `
      <td>${String(h).padStart(2, '0')}:00</td>
      <td>${dVal.toFixed(1)}</td>
      <td>${sBase.toFixed(1)}</td>
      <td>${entry.solar_used_kwh.toFixed(1)}</td>
      <td><strong style="color:var(--text-main); font-weight:700;">${entry.grid_kwh.toFixed(1)}</strong></td>
      <td><span class="table-action-chip ${chipClass}">${chipText}</span></td>
      <td>${entry.battery_kwh.toFixed(1)}</td>
      <td>${entry.battery_energy_after_kwh.toFixed(1)}</td>
      <td>${tariff.toFixed(1)}</td>
      <td><strong style="color:var(--maroon-primary); font-weight:700;">${cost.toFixed(0)}</strong></td>
    `;
    tbody.appendChild(tr);
  });
}

// ------------------------------------------------------------------------------
// Chart.js White & Maroon SCADA Visualizations
// ------------------------------------------------------------------------------
function renderSCADACharts(plan, inputData) {
  const labels = plan.map((e) => `${String(e.hour).padStart(2, '0')}:00`);
  const demand = inputData.hours.map((h) => h.demand_kwh);
  const grid = plan.map((e) => e.grid_kwh);
  const solarUsed = plan.map((e) => e.solar_used_kwh);
  const batteryDischarge = plan.map((e) => (e.battery_action === "discharge" ? e.battery_kwh : 0));
  const batteryEnergy = plan.map((e) => e.battery_energy_after_kwh);
  const tariff = inputData.hours.map((h) => h.tariff_bdt_per_kwh);

  const batteryCapacity = inputData.battery.capacity_kwh;
  const baseMinReserve = inputData.battery.minimum_energy_kwh;

  Chart.defaults.font.family = "'Plus Jakarta Sans', sans-serif";
  Chart.defaults.font.size = 11;
  Chart.defaults.color = "#475569";

  const gridLineColor = "rgba(136, 19, 55, 0.06)";

  // 1. Energy Balance Stacked Bar & Line
  if (energyBalanceChart) energyBalanceChart.destroy();
  const ctx1 = document.getElementById("energyBalanceChart").getContext("2d");
  energyBalanceChart = new Chart(ctx1, {
    type: "bar",
    data: {
      labels: labels,
      datasets: [
        {
          label: "Solar Generation Used",
          data: solarUsed,
          backgroundColor: "rgba(5, 150, 105, 0.8)",
          stack: "supply",
          borderRadius: 3,
        },
        {
          label: "BESS Discharge",
          data: batteryDischarge,
          backgroundColor: "rgba(217, 119, 6, 0.8)",
          stack: "supply",
          borderRadius: 3,
        },
        {
          label: "Grid Utility Purchase",
          data: grid,
          backgroundColor: "rgba(136, 19, 55, 0.75)",
          stack: "supply",
          borderRadius: 3,
        },
        {
          label: "Campus Demand Target",
          data: demand,
          type: "line",
          borderColor: "#9f1239",
          borderWidth: 2.2,
          pointRadius: 2.5,
          pointBackgroundColor: "#881337",
          fill: false,
          tension: 0.25,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: {
          position: "top",
          labels: { boxWidth: 10, boxHeight: 10, usePointStyle: true, pointStyle: "circle", color: "#334155" },
        },
      },
      scales: {
        x: { ticks: { color: "#64748b" }, grid: { color: gridLineColor } },
        y: { ticks: { color: "#64748b" }, grid: { color: gridLineColor }, title: { display: true, text: "Energy (kWh)", color: "#64748b" } },
      },
    },
  });

  // 2. Battery Dynamics Line Chart with Capacity Thresholds
  if (batteryChart) batteryChart.destroy();
  const ctx2 = document.getElementById("batteryChart").getContext("2d");
  batteryChart = new Chart(ctx2, {
    type: "line",
    data: {
      labels: labels,
      datasets: [
        {
          label: "BESS Stored Energy (kWh)",
          data: batteryEnergy,
          borderColor: "#881337",
          backgroundColor: "rgba(136, 19, 55, 0.08)",
          fill: true,
          tension: 0.3,
          borderWidth: 2.4,
          pointRadius: 3,
          pointBackgroundColor: "#881337",
        },
        {
          label: `Max Capacity (${batteryCapacity} kWh)`,
          data: Array(24).fill(batteryCapacity),
          borderColor: "rgba(71, 85, 105, 0.4)",
          borderDash: [5, 5],
          pointRadius: 0,
          borderWidth: 1.5,
          fill: false,
        },
        {
          label: `Base Min Reserve (${baseMinReserve} kWh)`,
          data: Array(24).fill(baseMinReserve),
          borderColor: "rgba(225, 29, 72, 0.75)",
          borderDash: [4, 4],
          pointRadius: 0,
          borderWidth: 1.5,
          fill: false,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          position: "top",
          labels: { boxWidth: 10, boxHeight: 10, usePointStyle: true, pointStyle: "circle", color: "#334155" },
        },
      },
      scales: {
        x: { ticks: { color: "#64748b" }, grid: { color: gridLineColor } },
        y: { ticks: { color: "#64748b" }, grid: { color: gridLineColor }, title: { display: true, text: "Storage (kWh)", color: "#64748b" } },
      },
    },
  });

  // 3. Tariff Peak Shaving Dual-Axis Chart
  if (tariffChart) tariffChart.destroy();
  const ctx3 = document.getElementById("tariffChart").getContext("2d");
  tariffChart = new Chart(ctx3, {
    type: "bar",
    data: {
      labels: labels,
      datasets: [
        {
          label: "Managed Grid Purchase (kWh)",
          data: grid,
          backgroundColor: "rgba(136, 19, 55, 0.7)",
          borderRadius: 3,
          yAxisID: "yGrid",
        },
        {
          label: "Grid Tariff Rate (BDT/kWh)",
          data: tariff,
          type: "line",
          borderColor: "#be123c",
          backgroundColor: "transparent",
          borderWidth: 2.2,
          pointRadius: 2.5,
          pointBackgroundColor: "#be123c",
          yAxisID: "yTariff",
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: {
          position: "top",
          labels: { boxWidth: 10, boxHeight: 10, usePointStyle: true, pointStyle: "circle", color: "#334155" },
        },
      },
      scales: {
        x: { ticks: { color: "#64748b" }, grid: { color: gridLineColor } },
        yGrid: {
          type: "linear",
          position: "left",
          ticks: { color: "#881337" },
          grid: { color: gridLineColor },
          title: { display: true, text: "Grid Import (kWh)", color: "#881337" },
        },
        yTariff: {
          type: "linear",
          position: "right",
          ticks: { color: "#be123c" },
          grid: { drawOnChartArea: false },
          title: { display: true, text: "Tariff (BDT/kWh)", color: "#be123c" },
        },
      },
    },
  });
}

function escapeHtml(str) {
  if (!str) return "";
  return str.replace(/[&<>'"]/g, (tag) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "'": "&#39;",
    '"': "&quot;",
  }[tag] || tag));
}
