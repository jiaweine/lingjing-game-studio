const SCOPE_PREFIX = "【验证范围】";
let comparisonTimer = null;
let comparisonKey = "";

function compact(value) {
  return String(value || "").trim().replace(/\s+/g, " ");
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[char]));
}

function parseScope(text) {
  const line = String(text || "").split("\n").find(row => row.trim().startsWith(SCOPE_PREFIX));
  if (!line) return null;
  const scope = {build_ref: "", branch_ref: "", commit_ref: ""};
  for (const part of line.trim().slice(SCOPE_PREFIX.length).split("|")) {
    const [rawKey, ...rest] = part.split("=");
    const key = compact(rawKey).toLowerCase();
    const value = compact(rest.join("="));
    if (key === "build") scope.build_ref = value;
    if (key === "branch") scope.branch_ref = value;
    if (key === "commit") scope.commit_ref = value;
  }
  return scope.build_ref || scope.branch_ref || scope.commit_ref ? scope : null;
}

function payloadScope(payload = {}) {
  const raw = payload?.context?.verification_scope || {};
  const scope = {
    build_ref: compact(raw.build_ref),
    branch_ref: compact(raw.branch_ref),
    commit_ref: compact(raw.commit_ref),
  };
  return scope.build_ref || scope.branch_ref || scope.commit_ref ? scope : null;
}

function scopeLabel(scope) {
  if (!scope) return "未绑定版本";
  return [scope.build_ref, scope.branch_ref, scope.commit_ref].filter(Boolean).join(" · ") || "未绑定版本";
}

function sameScope(a, b) {
  return ["build_ref", "branch_ref", "commit_ref"].every(key => compact(a?.[key]) === compact(b?.[key]));
}

function verificationCycles(messages = []) {
  let scope = null;
  const cycles = [];
  for (const message of messages) {
    if (message?.role === "user") {
      scope = parseScope(message.content) || scope;
      continue;
    }
    if (message?.role !== "assistant") continue;
    const payload = message.payload || {};
    const effectiveScope = payloadScope(payload) || scope;
    if (!effectiveScope) continue;
    scope = {...effectiveScope};
    cycles.push({
      scope: {...effectiveScope},
      outcome: payload.outcome || null,
      evidence: Array.isArray(payload.evidence) ? payload.evidence : [],
      created_at: message.created_at || null,
      source: payload?.context?.ci_trigger ? "ci" : "interactive",
    });
  }
  return cycles;
}

function evidenceSummary(evidence = []) {
  if (!evidence.length) return "0 条关联证据";
  const names = evidence
    .slice(0, 2)
    .map(item => compact(item.title || item.label || "证据"))
    .filter(Boolean);
  const suffix = evidence.length > 2 ? ` +${evidence.length - 2}` : "";
  return `${evidence.length} 条 · ${names.join(" / ")}${suffix}`;
}

function ensureStyle() {
  if (document.getElementById("issueComparisonStyle")) return;
  const style = document.createElement("style");
  style.id = "issueComparisonStyle";
  style.textContent = `
    .issue-comparison{margin-top:10px;padding-top:10px;border-top:1px solid rgba(26,39,64,.08)}
    .issue-comparison-head{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:7px}.issue-comparison-head b{font-size:11px}.issue-comparison-head span{font-size:8px;color:#6f7890;background:#eef1f6;border-radius:999px;padding:3px 6px}
    .issue-comparison-grid{display:grid;grid-template-columns:1fr 1fr;gap:6px}.issue-comparison-side{min-width:0;padding:8px;border-radius:9px;background:#f7f8fb}.issue-comparison-side>small{display:block;color:#8b93a3;font-size:8px}.issue-comparison-side>strong{display:block;margin-top:2px;font-size:9.5px;line-height:1.35;word-break:break-all}.issue-comparison-outcome{margin-top:6px;font-size:10px;font-weight:650}.issue-comparison-evidence{margin-top:3px;color:#7a8394;font-size:8px;line-height:1.35}.issue-comparison-note{margin:7px 1px 0;color:#737c8f;font-size:8.5px;line-height:1.45}
  `;
  document.head.appendChild(style);
}

function ensureBox() {
  const card = document.getElementById("issueScopeCard");
  if (!card) return null;
  let box = document.getElementById("issueComparison");
  if (box) return box;
  box = document.createElement("div");
  box.id = "issueComparison";
  box.className = "issue-comparison";
  box.hidden = true;
  const outcome = document.getElementById("issueOutcome");
  if (outcome) outcome.insertAdjacentElement("afterend", box);
  else card.appendChild(box);
  return box;
}

function renderComparison(messages = []) {
  const box = ensureBox();
  if (!box) return;
  const cycles = verificationCycles(messages);
  if (cycles.length < 2) {
    box.hidden = true;
    return;
  }
  const baseline = cycles[0];
  const latest = cycles.at(-1);
  if (sameScope(baseline.scope, latest.scope)) {
    box.hidden = true;
    return;
  }

  const baselineOutcome = baseline.outcome?.label || "历史结果";
  const latestOutcome = latest.outcome?.label || "需要确认";
  const authoritative = Boolean(latest.outcome?.verified);
  box.hidden = false;
  box.innerHTML = `
    <div class="issue-comparison-head"><b>修复前后对比</b><span>${cycles.length} 轮验证${latest.source === "ci" ? " · CI 重验" : ""}</span></div>
    <div class="issue-comparison-grid">
      <div class="issue-comparison-side">
        <small>修复前</small><strong>${escapeHtml(scopeLabel(baseline.scope))}</strong>
        <div class="issue-comparison-outcome">${escapeHtml(baselineOutcome)}</div>
        <div class="issue-comparison-evidence">${escapeHtml(evidenceSummary(baseline.evidence))}</div>
      </div>
      <div class="issue-comparison-side">
        <small>修复后</small><strong>${escapeHtml(scopeLabel(latest.scope))}</strong>
        <div class="issue-comparison-outcome">${escapeHtml(latestOutcome)}</div>
        <div class="issue-comparison-evidence">${escapeHtml(evidenceSummary(latest.evidence))}</div>
      </div>
    </div>
    <p class="issue-comparison-note">${authoritative
      ? "最新版本已形成 Verifier 权威结论；可以结合回归清单推进关闭。"
      : "已经形成版本与证据对比，但当前结论还不能自动升级为“修复已验证”。"}</p>
  `;
}

async function refreshComparison() {
  const id = new URL(location.href).searchParams.get("conversation");
  if (!id || !document.getElementById("issueScopeCard")) return;
  try {
    const response = await fetch(`/api/conversations/${encodeURIComponent(id)}`, {credentials: "same-origin"});
    if (!response.ok) return;
    const conversation = await response.json();
    renderComparison(conversation.messages || []);
  } catch {}
}

function scheduleRefresh() {
  clearTimeout(comparisonTimer);
  comparisonTimer = setTimeout(() => {
    const key = `${location.href}|${document.querySelectorAll(".msg.assistant[data-message-id]").length}|${Boolean(document.getElementById("issueScopeCard"))}`;
    if (key === comparisonKey) return;
    comparisonKey = key;
    refreshComparison();
  }, 120);
}

ensureStyle();
scheduleRefresh();
new MutationObserver(scheduleRefresh).observe(document.documentElement, {subtree: true, childList: true});
window.addEventListener("popstate", scheduleRefresh);
