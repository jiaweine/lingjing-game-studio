let enginePanelConversationId = null;
let enginePanelBusy = false;

function engineEsc(value) {
  return String(value ?? "").replace(/[&<>"']/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[char]));
}

function engineConversationId() {
  return new URL(location.href).searchParams.get("conversation") ||
    document.querySelector(".conv-item.active")?.dataset.id || "";
}

function engineCanEdit() {
  return !document.getElementById("newTaskBtn")?.disabled;
}

function engineToast(message) {
  const element = document.getElementById("toast");
  if (!element) return;
  element.textContent = message;
  element.classList.add("show");
  clearTimeout(engineToast.timer);
  engineToast.timer = setTimeout(() => element.classList.remove("show"), 2800);
}

function installEnginePanelStyle() {
  if (document.getElementById("gameAdapterPanelStyle")) return;
  const style = document.createElement("style");
  style.id = "gameAdapterPanelStyle";
  style.textContent = `
    .engine-adapter-card{margin:12px 0 16px;padding:13px;border:1px solid rgba(26,39,64,.10);border-radius:14px;background:rgba(255,255,255,.72)}
    .engine-adapter-head{display:flex;align-items:flex-start;justify-content:space-between;gap:10px}
    .engine-adapter-head small{display:block;color:#8b93a2;font-size:9px}.engine-adapter-head b{display:block;margin-top:2px;font-size:12px}
    .engine-adapter-head span{font-size:9px;color:#4f6b59;background:#eef7f1;padding:3px 6px;border-radius:999px}
    .engine-adapter-grid{display:grid;grid-template-columns:1fr;gap:6px;margin-top:9px}
    .engine-adapter-grid input{width:100%;box-sizing:border-box;border:1px solid rgba(26,39,64,.12);border-radius:9px;padding:8px 9px;background:#fff;color:#20283a;font:inherit;font-size:9px;outline:none}
    .engine-adapter-grid input:focus{border-color:#858ceb;box-shadow:0 0 0 2px rgba(105,112,220,.10)}
    .engine-adapter-actions{display:flex;gap:6px;margin-top:7px}
    .engine-adapter-actions button{flex:1;border:1px solid rgba(47,57,83,.13);border-radius:9px;padding:7px 8px;background:#fff;color:#39415a;font-size:9px;font-weight:650;cursor:pointer}
    .engine-adapter-actions button[data-capture]{background:#222a42;color:#fff;border-color:#222a42}
    .engine-adapter-actions button:disabled,.engine-adapter-grid input:disabled{opacity:.45;cursor:not-allowed}
    .engine-adapter-note{margin:7px 1px 0;color:#858d9d;font-size:8.5px;line-height:1.5}
    .engine-adapter-status{margin-top:7px;padding:7px 8px;border-radius:9px;background:#f7f8fb;color:#687187;font-size:8.5px;line-height:1.5}
    .engine-adapter-status strong{color:#34405a}.engine-adapter-status[data-state="ok"]{background:#f1f7f2;color:#42604b}.engine-adapter-status[data-state="error"]{background:#fff3f2;color:#8b4c46}
    .asset-origin.engine{display:inline-block;margin-top:4px;padding:2px 5px;border-radius:999px;background:#eef7f1;color:#4f6b59;font-size:7.5px;font-weight:650}
    .asset-card[data-engine-evidence="true"]{border-color:rgba(65,105,78,.18)}
  `;
  document.head.appendChild(style);
}

function ensureEnginePanel() {
  let card = document.getElementById("gameAdapterPanel");
  if (card) return card;
  const panel = document.getElementById("panel-progress");
  if (!panel) return null;

  card = document.createElement("section");
  card.id = "gameAdapterPanel";
  card.className = "engine-adapter-card";
  card.innerHTML = `
    <div class="engine-adapter-head">
      <div><small>真实项目证据</small><b>本地 Unity / GameAdapter</b></div>
      <span>只读</span>
    </div>
    <div class="engine-adapter-grid">
      <input id="gameAdapterEndpoint" value="http://127.0.0.1:9030" autocomplete="off" spellcheck="false" aria-label="GameAdapter endpoint">
      <input id="gameAdapterToken" type="password" value="" autocomplete="off" aria-label="GameAdapter bearer token" placeholder="Bearer token（可选，不保存）">
    </div>
    <div class="engine-adapter-actions">
      <button type="button" data-probe>测试连接</button>
      <button type="button" data-capture>导入引擎证据</button>
    </div>
    <p class="engine-adapter-note">仅适用于 Lingjing backend 与 Unity 在同一台机器的本地/自托管部署。Token 只用于当前请求，不保存。导入的日志、Scene snapshot 和截图仍是未经 Verifier 的外部引擎观察。</p>
    <div class="engine-adapter-status" id="gameAdapterStatus">尚未连接。</div>
  `;

  const issueScope = document.getElementById("issueScopeCard");
  const taskState = panel.querySelector(".task-state-card");
  if (issueScope) issueScope.insertAdjacentElement("afterend", card);
  else if (taskState) taskState.insertAdjacentElement("afterend", card);
  else panel.prepend(card);

  card.querySelector("[data-probe]")?.addEventListener("click", () => probeEngine(card));
  card.querySelector("[data-capture]")?.addEventListener("click", () => captureEngine(card));
  syncEnginePanelState();
  return card;
}

function connectionPayload(card) {
  return {
    endpoint: card.querySelector("#gameAdapterEndpoint")?.value.trim() || "",
    token: card.querySelector("#gameAdapterToken")?.value || null,
  };
}

function setEngineStatus(card, message, state = "") {
  const element = card.querySelector("#gameAdapterStatus");
  if (!element) return;
  element.dataset.state = state;
  element.innerHTML = message;
}

function setEngineBusy(card, busy) {
  enginePanelBusy = busy;
  const disabled = busy || !engineCanEdit() || !engineConversationId();
  card.querySelectorAll("button,input").forEach(element => {
    element.disabled = disabled;
  });
}

function syncEnginePanelState() {
  const card = document.getElementById("gameAdapterPanel");
  if (!card) return;
  const conversationId = engineConversationId();
  if (enginePanelConversationId !== conversationId) {
    enginePanelConversationId = conversationId;
    setEngineStatus(card, conversationId ? "尚未连接。" : "先选择一个任务。");
    const token = card.querySelector("#gameAdapterToken");
    if (token) token.value = "";
  }
  setEngineBusy(card, enginePanelBusy);
}

async function parseError(response, fallback) {
  try {
    const payload = await response.json();
    return payload.detail || fallback;
  } catch {
    return fallback;
  }
}

async function probeEngine(card) {
  const conversationId = engineConversationId();
  if (!conversationId) return engineToast("先选择一个任务");
  setEngineBusy(card, true);
  setEngineStatus(card, "正在检查本地 Unity bridge…");
  try {
    const response = await fetch(
      `/api/conversations/${encodeURIComponent(conversationId)}/game-adapter/probe`,
      {
        method: "POST",
        credentials: "same-origin",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(connectionPayload(card)),
      },
    );
    if (!response.ok) throw new Error(await parseError(response, "连接失败"));
    const result = await response.json();
    const cap = result.capabilities || {};
    setEngineStatus(
      card,
      `<strong>已连接 ${engineEsc(cap.engine || "engine")}</strong> · ${engineEsc(cap.engine_version || "")}<br>Adapter ${engineEsc(cap.adapter_id || "")} · screenshot ${cap.supports_screenshots ? "可用" : "不可用"} · mutating ${cap.mutating_actions ? "开启" : "关闭"}`,
      "ok",
    );
    engineToast("本地 Unity bridge 连接正常");
  } catch (error) {
    setEngineStatus(card, engineEsc(error.message || "连接失败"), "error");
    engineToast(error.message || "连接失败");
  } finally {
    setEngineBusy(card, false);
  }
}

async function captureEngine(card) {
  const conversationId = engineConversationId();
  if (!conversationId) return engineToast("先选择一个任务");
  setEngineBusy(card, true);
  setEngineStatus(card, "正在通过 Frozen Kernel dry-run 采集并校验证据…");
  try {
    const payload = {
      ...connectionPayload(card),
      evidence_requests: ["logs", "snapshot", "screenshot"],
      require_screenshot: false,
    };
    const response = await fetch(
      `/api/conversations/${encodeURIComponent(conversationId)}/game-adapter/capture`,
      {
        method: "POST",
        credentials: "same-origin",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(payload),
      },
    );
    if (!response.ok) throw new Error(await parseError(response, "引擎证据导入失败"));
    const result = await response.json();
    const assets = result.assets || [];
    const kinds = assets.map(asset => asset?.meta?.adapter_evidence_kind).filter(Boolean);
    setEngineStatus(
      card,
      `<strong>已导入 ${assets.length} 份引擎证据</strong> · ${engineEsc(kinds.join(" / ") || "evidence")}<br>${engineEsc(result.evidence_class || "external-engine-observation-unverified")} · Verifier: ${engineEsc(result.verifier_status || "not-run")}`,
      "ok",
    );
    engineToast(`已导入 ${assets.length} 份引擎证据`);
    window.dispatchEvent(new CustomEvent("lingjing:conversation-refresh", {
      detail: {conversationId, source: "game-adapter"},
    }));
  } catch (error) {
    setEngineStatus(card, engineEsc(error.message || "引擎证据导入失败"), "error");
    engineToast(error.message || "引擎证据导入失败");
  } finally {
    setEngineBusy(card, false);
  }
}

installEnginePanelStyle();
ensureEnginePanel();
syncEnginePanelState();

new MutationObserver(() => {
  ensureEnginePanel();
  syncEnginePanelState();
}).observe(document.documentElement, {subtree: true, childList: true});

window.addEventListener("popstate", syncEnginePanelState);
