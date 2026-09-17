const nativeFetch = window.fetch.bind(window);

const SCOPE_PREFIX = "【验证范围】";
const VERIFY_PROMPT = "沿用本任务已经确认的复现条件，在当前修复版本上重新执行相同验证。请对比修复前后的关键证据，并明确给出：仍可复现 / 已无法复现 / 证据不足；最后生成发布前回归清单。";

function compact(value, max = 160) {
  return String(value || "").trim().replace(/\s+/g, " ").slice(0, max);
}

function currentConversationId() {
  return new URL(location.href).searchParams.get("conversation") || "";
}

function readScope() {
  return {
    build_ref: compact(document.getElementById("issueBuildRef")?.value),
    branch_ref: compact(document.getElementById("issueBranchRef")?.value, 200),
    commit_ref: compact(document.getElementById("issueCommitRef")?.value),
  };
}

function hasScope(scope) {
  return Boolean(scope.build_ref || scope.branch_ref || scope.commit_ref);
}

function sameScope(a, b) {
  return ["build_ref", "branch_ref", "commit_ref"].every(key => compact(a?.[key]) === compact(b?.[key]));
}

function scopeLine(scope) {
  const parts = [];
  if (scope.build_ref) parts.push(`Build=${scope.build_ref}`);
  if (scope.branch_ref) parts.push(`Branch=${scope.branch_ref}`);
  if (scope.commit_ref) parts.push(`Commit=${scope.commit_ref}`);
  return parts.length ? `${SCOPE_PREFIX}${parts.join(" | ")}` : "";
}

function parseScopeLine(text) {
  const line = String(text || "").split("\n").find(row => row.trim().startsWith(SCOPE_PREFIX));
  if (!line) return null;
  const value = line.trim().slice(SCOPE_PREFIX.length);
  const scope = {build_ref: "", branch_ref: "", commit_ref: ""};
  for (const part of value.split("|")) {
    const [rawKey, ...rest] = part.split("=");
    const key = String(rawKey || "").trim().toLowerCase();
    const item = compact(rest.join("="), key === "branch" ? 200 : 160);
    if (key === "build") scope.build_ref = item;
    if (key === "branch") scope.branch_ref = item;
    if (key === "commit") scope.commit_ref = item;
  }
  return hasScope(scope) ? scope : null;
}

function scopeFromJob(job) {
  const payload = job?.payload || {};
  const requested = payload.requested_scope || {};
  const project = payload.project_context?.scope || {};
  const scope = {
    build_ref: compact(requested.build_ref || project.build_ref),
    branch_ref: compact(requested.branch_ref || project.branch_ref, 200),
    commit_ref: compact(requested.commit_ref || project.commit_ref),
  };
  return hasScope(scope) ? scope : null;
}

function scopeLabel(scope) {
  if (!scope || !hasScope(scope)) return "未绑定版本";
  return [scope.build_ref, scope.branch_ref, scope.commit_ref].filter(Boolean).join(" · ");
}

function setScope(scope) {
  if (!scope) return;
  const build = document.getElementById("issueBuildRef");
  const branch = document.getElementById("issueBranchRef");
  const commit = document.getElementById("issueCommitRef");
  if (build && !build.matches(":focus")) build.value = scope.build_ref || "";
  if (branch && !branch.matches(":focus")) branch.value = scope.branch_ref || "";
  if (commit && !commit.matches(":focus")) commit.value = scope.commit_ref || "";
}

function allMessageScopes(messages = []) {
  return messages
    .filter(message => message?.role === "user")
    .map(message => parseScopeLine(message?.content))
    .filter(Boolean);
}

function updateScopeSummary(scopes = []) {
  const baseline = scopes[0] || null;
  const latest = scopes.at(-1) || null;
  const baselineEl = document.getElementById("issueBaselineScope");
  const currentEl = document.getElementById("issueCurrentScope");
  if (baselineEl) baselineEl.textContent = scopeLabel(baseline);
  if (currentEl) currentEl.textContent = scopeLabel(latest);
  const comparison = document.getElementById("issueScopeComparison");
  if (comparison) {
    comparison.textContent = baseline && latest && !sameScope(baseline, latest)
      ? "已进入修复版本对比"
      : (baseline ? "当前仍在初始复现版本" : "可选：先绑定发生问题的 Build / Branch / Commit");
  }
}

function setLifecycleHint(message, kind = "") {
  const element = document.getElementById("issueLifecycleHint");
  if (!element) return;
  element.textContent = message;
  element.dataset.kind = kind;
}

function installStyle() {
  if (document.getElementById("issueLifecycleStyle")) return;
  const style = document.createElement("style");
  style.id = "issueLifecycleStyle";
  style.textContent = `
    .issue-scope-card{margin:12px 0 16px;padding:14px;border:1px solid rgba(26,39,64,.10);border-radius:14px;background:rgba(255,255,255,.72)}
    .issue-scope-head{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;margin-bottom:10px}.issue-scope-head small{display:block;color:#7b8496;font-size:11px;margin-bottom:3px}.issue-scope-head b{font-size:13px}.issue-scope-badge{font-size:10px;padding:4px 7px;border-radius:999px;background:#eef1ff;color:#5961a8;white-space:nowrap}
    .issue-scope-fields{display:grid;grid-template-columns:1fr 1fr;gap:7px}.issue-scope-fields label:first-child{grid-column:1/-1}.issue-scope-fields span{display:block;font-size:10px;color:#7b8496;margin:0 0 4px 2px}.issue-scope-fields input{box-sizing:border-box;width:100%;border:1px solid rgba(26,39,64,.12);border-radius:9px;padding:8px 9px;background:#fff;color:#20283a;font:inherit;font-size:11px;outline:none}.issue-scope-fields input:focus{border-color:#858ceb;box-shadow:0 0 0 2px rgba(105,112,220,.10)}
    .issue-scope-history{margin-top:10px;padding-top:9px;border-top:1px solid rgba(26,39,64,.08);display:grid;grid-template-columns:1fr 1fr;gap:8px}.issue-scope-history small{display:block;color:#939aaa;font-size:9px}.issue-scope-history b{display:block;font-size:10px;line-height:1.4;margin-top:2px;word-break:break-all}.issue-scope-status{grid-column:1/-1;color:#757e91;font-size:10px}
    .issue-verify-action{width:100%;margin-top:10px;border:0;border-radius:10px;padding:9px 10px;background:#222a42;color:#fff;font-weight:650;cursor:pointer}.issue-verify-action:disabled{opacity:.45;cursor:not-allowed}.issue-lifecycle-hint{margin:7px 2px 0;color:#7b8496;font-size:10px;line-height:1.45}.issue-lifecycle-hint[data-kind="error"]{color:#a34747}.issue-lifecycle-hint[data-kind="ok"]{color:#33735b}
  `;
  document.head.appendChild(style);
}

function installCard() {
  if (document.getElementById("issueScopeCard")) return;
  const stateCard = document.querySelector("#panel-progress .task-state-card");
  if (!stateCard) return;
  const card = document.createElement("div");
  card.className = "issue-scope-card";
  card.id = "issueScopeCard";
  card.innerHTML = `
    <div class="issue-scope-head">
      <div><small>问题版本</small><b>绑定复现与修复验证范围</b></div>
      <span class="issue-scope-badge">Bug → Fix</span>
    </div>
    <div class="issue-scope-fields">
      <label><span>Build</span><input id="issueBuildRef" maxlength="160" placeholder="例如 1.4.7-rc2" /></label>
      <label><span>Branch</span><input id="issueBranchRef" maxlength="200" placeholder="release/1.4" /></label>
      <label><span>Commit</span><input id="issueCommitRef" maxlength="160" placeholder="例如 a1b2c3d" /></label>
    </div>
    <div class="issue-scope-history">
      <div><small>初始复现</small><b id="issueBaselineScope">未绑定版本</b></div>
      <div><small>最近验证</small><b id="issueCurrentScope">未绑定版本</b></div>
      <div class="issue-scope-status" id="issueScopeComparison">可选：先绑定发生问题的 Build / Branch / Commit</div>
    </div>
    <button class="issue-verify-action" id="verifyFixBtn" type="button">用此版本重新验证修复</button>
    <div class="issue-lifecycle-hint" id="issueLifecycleHint">首次复现时填问题版本；修复后改成新版本，再点上面的按钮。</div>
  `;
  stateCard.insertAdjacentElement("afterend", card);

  document.getElementById("verifyFixBtn")?.addEventListener("click", () => {
    const scope = readScope();
    if (!hasScope(scope)) {
      setLifecycleHint("先填写至少一个 Build、Branch 或 Commit，避免修复验证失去版本边界。", "error");
      document.getElementById("issueBuildRef")?.focus();
      return;
    }
    const input = document.getElementById("messageInput");
    const send = document.getElementById("sendBtn");
    if (!input || !send || send.disabled || input.disabled) {
      setLifecycleHint("当前任务还不能开始新的验证；请等待正在运行的任务结束或先恢复任务。", "error");
      return;
    }
    input.value = VERIFY_PROMPT;
    input.dispatchEvent(new Event("input", {bubbles: true}));
    setLifecycleHint("正在沿用本任务复现上下文，对当前版本发起修复验证。", "ok");
    send.click();
  });
}

function syncButtonState() {
  const button = document.getElementById("verifyFixBtn");
  const send = document.getElementById("sendBtn");
  if (!button || !send) return;
  const hasResult = Boolean(document.querySelector(".msg.assistant[data-message-id]"));
  button.disabled = !hasResult || send.disabled;
  button.title = !hasResult ? "先完成一次问题复现，再验证修复版本" : "沿用当前任务的复现上下文重新验证";
}

function normalizeVisibleStatuses() {
  const replacements = [
    ["待复核", "需要确认"],
    ["等待确认", "需要确认"],
    ["需修正", "需处理"],
    ["已停止", "需处理"],
  ];
  document.querySelectorAll(".conv-item small").forEach(element => {
    let value = element.textContent || "";
    for (const [from, to] of replacements) value = value.replace(from, to);
    if (element.textContent !== value) element.textContent = value;
  });
  const taskState = document.getElementById("taskState");
  if (taskState?.textContent === "等待人工复核") taskState.textContent = "需要确认";
  if (["执行中断", "已停止"].includes(taskState?.textContent || "")) taskState.textContent = "需处理";
  document.querySelectorAll(".msg.assistant .msg-label .tag").forEach(tag => {
    if (tag.textContent === "交付") tag.textContent = "结果";
  });
}

function hydrateFromConversation(conversation) {
  installCard();
  const scopes = allMessageScopes(conversation?.messages || []);
  const jobScope = scopeFromJob(conversation?.job);
  if (jobScope && (!scopes.length || !sameScope(scopes.at(-1), jobScope))) scopes.push(jobScope);
  if (scopes.length) setScope(scopes.at(-1));
  updateScopeSummary(scopes);
  syncButtonState();
  normalizeVisibleStatuses();
}

window.fetch = async (input, init = {}) => {
  const url = typeof input === "string" ? input : input?.url || "";
  const method = String(init?.method || (typeof input !== "string" ? input?.method : "GET") || "GET").toUpperCase();
  const messageMatch = url.match(/\/api\/conversations\/([^/?]+)\/messages(?:\?|$)/);
  let submittedScope = null;
  let nextInit = init;

  if (messageMatch && method === "POST" && typeof init.body === "string") {
    try {
      const body = JSON.parse(init.body);
      const scope = readScope();
      if (hasScope(scope)) {
        submittedScope = scope;
        const line = scopeLine(scope);
        if (line && !String(body.content || "").includes(SCOPE_PREFIX)) {
          body.content = `${String(body.content || "").trim()}\n\n${line}`;
        }
        body.build_ref = scope.build_ref || null;
        body.branch_ref = scope.branch_ref || null;
        body.commit_ref = scope.commit_ref || null;
        nextInit = {...init, body: JSON.stringify(body)};
      }
    } catch {}
  }

  const response = await nativeFetch(input, nextInit);

  if (messageMatch && method === "POST" && response.ok && submittedScope) {
    const current = document.getElementById("issueCurrentScope");
    if (current) current.textContent = scopeLabel(submittedScope);
    const baseline = document.getElementById("issueBaselineScope");
    if (baseline && baseline.textContent === "未绑定版本") baseline.textContent = scopeLabel(submittedScope);
    const comparison = document.getElementById("issueScopeComparison");
    if (comparison && baseline) {
      const baselineScope = parseScopeLine(`${SCOPE_PREFIX}Build=${baseline.textContent}`);
      comparison.textContent = baseline.textContent !== scopeLabel(submittedScope) ? "已进入修复版本对比" : "当前仍在初始复现版本";
      void baselineScope;
    }
    setLifecycleHint("版本范围已随任务消息保存；后续修复验证会继续留在同一问题上下文。", "ok");
  }

  const conversationMatch = url.match(/\/api\/conversations\/([^/?]+)(?:\?|$)/);
  if (conversationMatch && method === "GET" && response.ok && !url.includes("/control")) {
    response.clone().json().then(data => hydrateFromConversation(data)).catch(() => {});
  }
  return response;
};

function refreshFromRenderedMessages() {
  installCard();
  syncButtonState();
  normalizeVisibleStatuses();
}

installStyle();
installCard();
normalizeVisibleStatuses();

const observer = new MutationObserver(refreshFromRenderedMessages);
observer.observe(document.documentElement, {subtree: true, childList: true, attributes: true, attributeFilter: ["disabled"]});

const originalReplaceState = history.replaceState.bind(history);
history.replaceState = (...args) => {
  const result = originalReplaceState(...args);
  setTimeout(() => {
    const id = currentConversationId();
    if (!id) return;
    nativeFetch(`/api/conversations/${encodeURIComponent(id)}`, {credentials: "same-origin"})
      .then(response => response.ok ? response.json() : null)
      .then(data => { if (data) hydrateFromConversation(data); })
      .catch(() => {});
  }, 0);
  return result;
};

window.addEventListener("popstate", () => {
  const id = currentConversationId();
  if (!id) return;
  nativeFetch(`/api/conversations/${encodeURIComponent(id)}`, {credentials: "same-origin"})
    .then(response => response.ok ? response.json() : null)
    .then(data => { if (data) hydrateFromConversation(data); })
    .catch(() => {});
});
