let valueMetricsLoading = false;
let valueMetricsLoadedAt = 0;

function formatDuration(seconds) {
  if (seconds == null || !Number.isFinite(Number(seconds))) return "—";
  const value = Math.max(0, Number(seconds));
  if (value < 60) return `${Math.round(value)}s`;
  if (value < 3600) return `${Math.round(value / 60)}m`;
  if (value < 86400) return `${(value / 3600).toFixed(value < 7200 ? 1 : 0)}h`;
  return `${(value / 86400).toFixed(value < 172800 ? 1 : 0)}d`;
}

function ensureValueMetricsCard() {
  let card = document.getElementById("valueMetricsCard");
  if (card) return card;
  const grid = document.getElementById("metricGrid");
  if (!grid) return null;
  card = document.createElement("section");
  card.id = "valueMetricsCard";
  card.className = "value-metrics-card";
  card.innerHTML = `
    <div class="value-metrics-head">
      <div><small>客户价值</small><b>Verified Issue 指标</b></div>
      <span>7 天</span>
    </div>
    <div class="value-metrics-grid" id="valueMetricsGrid">
      <div><b>—</b><small>本周已验证问题</small></div>
      <div><b>—</b><small>验证耗时中位数</small></div>
      <div><b>—</b><small>Token 精确覆盖</small></div>
      <div><b>—</b><small>成本可信度</small></div>
    </div>
    <p class="value-metrics-note" id="valueMetricsNote">只统计真正通过问题验证门的任务；不会把“回答被人工认可”当成问题已修复。</p>
  `;
  grid.insertAdjacentElement("beforebegin", card);
  return card;
}

function installStyle() {
  if (document.getElementById("valueMetricsStyle")) return;
  const style = document.createElement("style");
  style.id = "valueMetricsStyle";
  style.textContent = `
    .value-metrics-card{margin:12px 0;padding:12px;border:1px solid rgba(26,39,64,.10);border-radius:12px;background:rgba(255,255,255,.78)}
    .value-metrics-head{display:flex;align-items:flex-start;justify-content:space-between;gap:10px;margin-bottom:9px}.value-metrics-head small{display:block;color:#8b93a2;font-size:9px}.value-metrics-head b{display:block;margin-top:2px;font-size:12px}.value-metrics-head>span{font-size:9px;color:#6c7590;background:#f1f3f8;border-radius:999px;padding:3px 6px}
    .value-metrics-grid{display:grid;grid-template-columns:1fr 1fr;gap:6px}.value-metrics-grid>div{padding:8px;border-radius:9px;background:#f7f8fb}.value-metrics-grid b{display:block;font-size:15px;line-height:1.15}.value-metrics-grid small{display:block;margin-top:3px;color:#838c9d;font-size:8.5px}
    .value-metrics-note{margin:8px 1px 0;color:#7d8595;font-size:8.5px;line-height:1.45}
  `;
  document.head.appendChild(style);
}

function renderValueMetrics(metrics) {
  const card = ensureValueMetricsCard();
  const grid = document.getElementById("valueMetricsGrid");
  const note = document.getElementById("valueMetricsNote");
  if (!card || !grid || !note) return;
  const weekly = Number(metrics.weekly_verified_issues || 0);
  const coverage = Math.round(Number(metrics.provider_usage_exact_token_coverage_rate || 0) * 100);
  const costLabel = metrics.cost_available ? "已接入" : "未接入";
  grid.innerHTML = `
    <div><b>${weekly}</b><small>本周已验证问题</small></div>
    <div><b>${formatDuration(metrics.median_time_to_verified_issue_seconds)}</b><small>验证耗时中位数</small></div>
    <div><b>${coverage}%</b><small>Token 精确覆盖</small></div>
    <div><b>${costLabel}</b><small>成本可信度</small></div>
  `;
  note.textContent = metrics.cost_available
    ? "成本来自已持久化的权威账单/计费记录。"
    : "成本暂不展示：当前没有权威 provider 账单记录，系统不会用猜测的模型单价制造 ROI。";
}

async function loadValueMetrics(force = false) {
  const panel = document.getElementById("panel-team");
  if (!panel || (!force && !panel.classList.contains("active"))) return;
  if (valueMetricsLoading) return;
  if (!force && Date.now() - valueMetricsLoadedAt < 15000) return;
  valueMetricsLoading = true;
  try {
    const response = await fetch("/api/metrics", {credentials: "same-origin"});
    if (!response.ok) {
      document.getElementById("valueMetricsCard")?.remove();
      return;
    }
    renderValueMetrics(await response.json());
    valueMetricsLoadedAt = Date.now();
  } catch {
    document.getElementById("valueMetricsCard")?.remove();
  } finally {
    valueMetricsLoading = false;
  }
}

installStyle();
ensureValueMetricsCard();

document.addEventListener("click", event => {
  if (event.target.closest('[data-panel="team"]')) {
    setTimeout(() => loadValueMetrics(true), 0);
  }
});

new MutationObserver(() => {
  const panel = document.getElementById("panel-team");
  if (panel?.classList.contains("active")) {
    ensureValueMetricsCard();
    loadValueMetrics();
  }
}).observe(document.documentElement, {subtree: true, childList: true});
