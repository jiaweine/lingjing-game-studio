const IDENTITY_REASON_LABELS = {
  "strong-stable-token-overlap": "稳定语义高度重合",
  "moderate-stable-token-overlap": "稳定语义部分重合",
  "same-value-stripped-skeleton": "去掉版本/数值后结构一致",
  "similar-value-stripped-skeleton": "去掉版本/数值后结构相似",
  "shared-stable-identifiers": "稳定实体标识一致",
  "proposal-matches-key-semantics": "内容与现有 key 语义一致",
};

function identityEsc(value) {
  return String(value ?? "").replace(/[&<>"']/g, char => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[char]));
}

async function identityApi(path) {
  const response = await fetch(path, {credentials: "same-origin"});
  if (!response.ok) {
    const raw = await response.text();
    let message = raw;
    try {
      message = JSON.parse(raw).detail || raw;
    } catch {}
    throw new Error(message || String(response.status));
  }
  return response.json();
}

function identityToast(message) {
  const element = document.getElementById("toast");
  if (!element) return;
  element.textContent = message;
  element.classList.add("show");
  clearTimeout(identityToast.timer);
  identityToast.timer = setTimeout(() => element.classList.remove("show"), 2400);
}

function identityConversationId() {
  const fromUrl = new URL(location.href).searchParams.get("conversation");
  return fromUrl || document.querySelector(".conv-item.active")?.dataset.id || null;
}

function identityInstallStyles() {
  if (document.getElementById("memoryIdentityStyles")) return;
  const style = document.createElement("style");
  style.id = "memoryIdentityStyles";
  style.textContent = `
    .memory-identity-tools{margin-top:9px;padding-top:9px;border-top:1px dashed var(--line,#dfe4ec);display:grid;gap:7px}
    .memory-identity-check,.memory-identity-adopt{width:max-content;max-width:100%;border:0;border-radius:8px;padding:6px 8px;font-size:9px;cursor:pointer}
    .memory-identity-check{background:#eef1f6;color:var(--ink-2,#4f5c70)}
    .memory-identity-check:disabled{opacity:.58;cursor:wait}
    .memory-identity-result{padding:8px 9px;border:1px solid var(--line,#dfe4ec);border-radius:9px;background:var(--panel-soft,#f7f9fc);font-size:8.5px;color:var(--ink-3,#7b8798);line-height:1.5}
    .memory-identity-result b{display:block;color:var(--ink-2,#4f5c70);font-size:9.5px;word-break:break-all}
    .memory-identity-result p{margin:4px 0 0}.memory-identity-reasons{margin-top:5px;display:flex;gap:4px;flex-wrap:wrap}
    .memory-identity-reasons span{padding:2px 5px;border-radius:999px;background:#fff;border:1px solid var(--line,#dfe4ec);font-size:7.8px}
    .memory-identity-adopt{margin-top:6px;background:var(--accent-soft,#eef2ff);color:var(--accent,#315de8)}
    .memory-identity-warn{margin-top:5px;color:var(--warning,#b8781b)}
  `;
  document.head.appendChild(style);
}

function identityReasonText(reason) {
  if (IDENTITY_REASON_LABELS[reason]) return IDENTITY_REASON_LABELS[reason];
  if (String(reason).startsWith("scope:")) return `作用域：${String(reason).slice(6)}`;
  return String(reason);
}

function renderIdentityResult(card, proposalId, result) {
  const target = card.querySelector("[data-memory-identity-result]");
  if (!target) return;
  const best = result.candidates?.[0] || null;
  const truncated = result.candidate_heads_truncated
    ? '<div class="memory-identity-warn">候选 head 达到扫描上限；建议保持人工复核，不要仅凭该结果归链。</div>'
    : "";

  if (!result.recommended_key) {
    target.innerHTML = `
      <b>未找到足够可靠的现有 identity</b>
      <p>系统已选择 abstain。保持当前 suggested key，或由你手工选择已有 key。</p>
      ${best ? `<p>最高候选 ${identityEsc(best.memory_key)} · score ${Number(best.score || 0).toFixed(3)} / threshold ${Number(result.threshold || 0).toFixed(2)}</p>` : ""}
      ${truncated}
    `;
    return;
  }

  const reasons = (best?.reasons || []).map(reason => (
    `<span>${identityEsc(identityReasonText(reason))}</span>`
  )).join("");
  const input = card.querySelector(`[data-proposal-key="${CSS.escape(proposalId)}"]`);
  target.innerHTML = `
    <b>可能是现有 identity：${identityEsc(result.recommended_key)}</b>
    <p>score ${Number(result.best_score || 0).toFixed(3)} · margin ${Number(result.score_margin || 0).toFixed(3)}。这只是 shadow suggestion，不会自动修改 proposal。</p>
    ${reasons ? `<div class="memory-identity-reasons">${reasons}</div>` : ""}
    ${input ? `<button class="memory-identity-adopt" type="button" data-memory-identity-adopt="${identityEsc(proposalId)}">采用建议 key</button>` : ""}
    ${truncated}
  `;
  target.querySelector("[data-memory-identity-adopt]")?.addEventListener("click", () => {
    const field = card.querySelector(`[data-proposal-key="${CSS.escape(proposalId)}"]`);
    if (!field) return;
    field.value = result.recommended_key;
    field.focus();
    identityToast("已填入建议 key；仍需你显式批准后才会写入 Project Memory");
  });
}

async function inspectIdentity(card, proposalId, button) {
  button.disabled = true;
  button.textContent = "正在检查…";
  const target = card.querySelector("[data-memory-identity-result]");
  if (target) target.innerHTML = "正在比较现有 memory identities…";
  try {
    const conversationId = identityConversationId();
    if (!conversationId) throw new Error("请先选择任务");
    const binding = await identityApi(`/api/conversations/${encodeURIComponent(conversationId)}/project`);
    const projectId = binding.project?.id;
    if (!projectId) throw new Error("当前任务尚未绑定项目");
    const result = await identityApi(
      `/api/projects/${encodeURIComponent(projectId)}/memory-proposals/${encodeURIComponent(proposalId)}/identity-suggestions`
    );
    renderIdentityResult(card, proposalId, result);
  } catch (error) {
    if (target) target.innerHTML = `<b>Identity suggestion 读取失败</b><p>${identityEsc(error.message)}</p>`;
  } finally {
    button.disabled = false;
    button.textContent = "检查现有 identity";
  }
}

function enhanceIdentityCard(card) {
  if (!(card instanceof HTMLElement) || card.dataset.identityEnhanced === "1") return;
  const proposalId = card.dataset.proposalId;
  if (!proposalId) return;
  card.dataset.identityEnhanced = "1";
  const tools = document.createElement("div");
  tools.className = "memory-identity-tools";
  tools.innerHTML = `
    <button class="memory-identity-check" type="button" data-memory-identity-check="${identityEsc(proposalId)}">检查现有 identity</button>
    <div class="memory-identity-result" data-memory-identity-result="${identityEsc(proposalId)}" hidden></div>
  `;
  const anchor = card.querySelector(".memory-key-field") || card.querySelector(".memory-meta") || card;
  anchor.insertAdjacentElement("afterend", tools);
  const button = tools.querySelector("[data-memory-identity-check]");
  const target = tools.querySelector("[data-memory-identity-result]");
  button?.addEventListener("click", () => {
    if (target) target.hidden = false;
    inspectIdentity(card, proposalId, button);
  });
}

function enhanceIdentityCards() {
  document.querySelectorAll(".memory-proposal").forEach(enhanceIdentityCard);
}

identityInstallStyles();
enhanceIdentityCards();
new MutationObserver(enhanceIdentityCards).observe(document.documentElement, {
  childList: true,
  subtree: true,
});
