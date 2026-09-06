const memoryState = {
  conversationId: null,
  project: null,
  projects: [],
  memories: [],
  proposals: [],
  session: null,
  loading: false,
  lastUrl: location.href,
};

const MEMORY_KIND_LABEL = {
  fact: "事实",
  constraint: "约束",
  decision: "决定",
  hypothesis: "假设",
  procedure: "流程",
  gotcha: "注意项",
  episode: "经验",
  preference: "偏好",
  resource: "资源",
};

const MEMORY_STATE_LABEL = {
  active: "生效",
  disputed: "争议",
  retracted: "撤回",
};

function memoryEsc(value) {
  return String(value ?? "").replace(/[&<>"']/g, char => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[char]));
}

async function memoryApi(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    headers: {
      ...(options.body instanceof FormData ? {} : {"Content-Type": "application/json"}),
      ...(options.headers || {}),
    },
    ...options,
  });
  if (!response.ok) {
    const raw = await response.text();
    let message = raw;
    try {
      message = JSON.parse(raw).detail || raw;
    } catch {}
    const error = new Error(message || String(response.status));
    error.status = response.status;
    throw error;
  }
  return response.json();
}

function memoryToast(message) {
  const element = document.getElementById("toast");
  if (!element) return;
  element.textContent = message;
  element.classList.add("show");
  clearTimeout(memoryToast.timer);
  memoryToast.timer = setTimeout(() => element.classList.remove("show"), 2400);
}

function currentConversationId() {
  const fromUrl = new URL(location.href).searchParams.get("conversation");
  if (fromUrl) return fromUrl;
  return document.querySelector(".conv-item.active")?.dataset.id || null;
}

function memoryCanEdit() {
  const role = memoryState.session?.user?.role;
  return Boolean(role && role !== "viewer");
}

function scopeParts(row) {
  return [
    ["build", row?.build_ref],
    ["branch", row?.branch_ref],
    ["commit", row?.commit_ref],
    ["env", row?.environment_ref],
  ].filter(([, value]) => value);
}

function scopeText(row) {
  const parts = scopeParts(row);
  return parts.length
    ? parts.map(([label, value]) => `${label}=${value}`).join(" · ")
    : "general";
}

function scopeQuery(row) {
  const params = new URLSearchParams({memory_key: row.memory_key});
  for (const [field, value] of [
    ["build_ref", row.build_ref],
    ["branch_ref", row.branch_ref],
    ["commit_ref", row.commit_ref],
    ["environment_ref", row.environment_ref],
  ]) {
    if (value) params.set(field, value);
  }
  return params.toString();
}

function installMemoryStyles() {
  if (document.getElementById("contextMemoryStyles")) return;
  const style = document.createElement("style");
  style.id = "contextMemoryStyles";
  style.textContent = `
    .memory-shell{display:grid;gap:12px}
    .memory-project-card,.memory-proposal,.memory-item,.memory-unbound{border:1px solid var(--line,#dfe4ec);border-radius:13px;background:#fff;box-shadow:var(--shadow-sm,0 1px 2px rgba(24,32,51,.05));padding:12px}
    .memory-project-top{display:flex;gap:10px;align-items:flex-start}.memory-project-top>div{min-width:0;flex:1}.memory-project-top b{display:block;font-size:12px}.memory-project-top small{display:block;margin-top:4px;color:var(--ink-3,#7b8798);font-size:9px;line-height:1.45;word-break:break-all}
    .memory-refresh{border:1px solid var(--line,#dfe4ec);border-radius:8px;background:var(--panel-soft,#f7f9fc);padding:5px 7px;font-size:10px;color:var(--ink-2,#4f5c70)}
    .memory-stats{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin-top:10px}.memory-stat{padding:8px;border-radius:9px;background:var(--panel-soft,#f7f9fc);display:grid;gap:2px}.memory-stat b{font-size:15px}.memory-stat small{font-size:8px;color:var(--ink-3,#7b8798)}
    .memory-section-head{display:flex;align-items:baseline;justify-content:space-between;margin:5px 2px 7px}.memory-section-head b{font-size:11px}.memory-section-head small{font-size:8.5px;color:var(--ink-3,#7b8798)}
    .memory-list{display:grid;gap:8px}.memory-proposal{border-color:#eadfc3;background:#fffdf7}.memory-proposal-head,.memory-item-head{display:flex;gap:7px;align-items:center;flex-wrap:wrap}.memory-proposal-head b,.memory-item-head b{font-size:10.5px;min-width:0;overflow:hidden;text-overflow:ellipsis}.memory-badge{display:inline-flex;align-items:center;min-height:20px;padding:2px 6px;border-radius:999px;background:#eef2f7;color:#687488;font-size:8px;font-weight:750}.memory-badge.pending{background:var(--warning-soft,#fff4de);color:var(--warning,#b8781b)}.memory-badge.active{background:var(--success-soft,#e3f4ec);color:var(--success,#148a65)}.memory-badge.disputed{background:var(--warning-soft,#fff4de);color:var(--warning,#b8781b)}.memory-badge.retracted{background:var(--danger-soft,#fdebed);color:var(--danger,#c84c55)}
    .memory-content{margin:9px 0 0;color:var(--ink-2,#4f5c70);font-size:10.5px;line-height:1.55;white-space:pre-wrap;word-break:break-word}.memory-meta{margin-top:8px;display:grid;gap:3px;color:var(--ink-3,#7b8798);font-size:8.5px;line-height:1.45;word-break:break-all}
    .memory-key-field{margin-top:10px;display:grid;gap:4px}.memory-key-field span{font-size:8.5px;color:var(--ink-3,#7b8798)}.memory-key-field input,.memory-unbound input,.memory-unbound select{width:100%;border:1px solid var(--line,#dfe4ec);border-radius:8px;background:#fff;padding:7px 8px;font-size:9.5px}.memory-key-hint{font-size:8px;color:var(--ink-3,#7b8798);line-height:1.4}
    .memory-actions{display:flex;gap:5px;flex-wrap:wrap;margin-top:9px}.memory-actions button,.memory-primary,.memory-secondary{border:0;border-radius:8px;padding:6px 8px;font-size:9px}.memory-actions button,.memory-secondary{background:#eef1f6;color:var(--ink-2,#4f5c70)}.memory-primary{background:var(--accent,#315de8);color:#fff}.memory-actions .memory-danger{background:var(--danger-soft,#fdebed);color:var(--danger,#c84c55)}.memory-actions .memory-warn{background:var(--warning-soft,#fff4de);color:var(--warning,#b8781b)}
    .memory-empty{padding:18px 10px;text-align:center;border:1px dashed var(--line,#dfe4ec);border-radius:12px;color:var(--ink-3,#7b8798);font-size:9.5px;line-height:1.6}.memory-unbound p{margin:4px 0 11px;color:var(--ink-3,#7b8798);font-size:9.5px;line-height:1.55}.memory-unbound-grid{display:grid;gap:7px}.memory-unbound-row{display:grid;grid-template-columns:1fr auto;gap:6px}.memory-unbound label{display:grid;gap:4px;font-size:8.5px;color:var(--ink-3,#7b8798)}
    .memory-history{margin-top:8px;padding-top:8px;border-top:1px solid var(--line,#dfe4ec);display:grid;gap:6px}.memory-history-row{padding:7px;border-radius:8px;background:var(--panel-soft,#f7f9fc)}.memory-history-row b{font-size:9px}.memory-history-row p{margin:3px 0 0;font-size:8.5px;color:var(--ink-2,#4f5c70);line-height:1.45}.memory-history-row small{display:block;margin-top:3px;color:var(--ink-3,#7b8798);font-size:7.8px;word-break:break-all}
    .memory-readonly{padding:7px 9px;border-radius:9px;background:#f2f4f7;color:var(--ink-3,#7b8798);font-size:8.5px;line-height:1.45}
  `;
  document.head.appendChild(style);
}

function installMemoryPanel() {
  installMemoryStyles();
  const tabs = document.querySelector(".right-tabs");
  const scroll = document.querySelector(".right-scroll");
  if (!tabs || !scroll) return false;

  let tab = document.querySelector('[data-panel="memory"]');
  if (!tab) {
    tab = document.createElement("button");
    tab.type = "button";
    tab.className = "right-tab";
    tab.dataset.panel = "memory";
    tab.textContent = "记忆";
    const teamTab = tabs.querySelector('[data-panel="team"]');
    tabs.insertBefore(tab, teamTab || null);
  }

  let panel = document.getElementById("panel-memory");
  if (!panel) {
    panel = document.createElement("section");
    panel.className = "right-panel";
    panel.id = "panel-memory";
    panel.innerHTML = `
      <div class="panel-intro">
        <span class="eyebrow">Project Memory</span>
        <b>项目记忆</b>
        <p>长期记忆是可审计的项目先验，不是本轮验证证据。待确认提议只有人工批准后才会生效。</p>
      </div>
      <div id="memoryPanelBody" class="memory-shell" style="margin-top:10px">
        <div class="memory-empty">选择一个任务后查看项目记忆。</div>
      </div>
    `;
    const teamPanel = document.getElementById("panel-team");
    scroll.insertBefore(panel, teamPanel || null);
  }

  tab.addEventListener("click", () => {
    document.querySelectorAll(".right-tab").forEach(item => {
      item.classList.toggle("active", item === tab);
    });
    document.querySelectorAll(".right-panel").forEach(item => {
      item.classList.toggle("active", item === panel);
    });
    refreshMemoryPanel(true);
  });
  return true;
}

function memoryBody() {
  return document.getElementById("memoryPanelBody");
}

function renderMemoryLoading() {
  const body = memoryBody();
  if (body) body.innerHTML = '<div class="memory-empty">正在读取项目记忆…</div>';
}

function renderMemoryError(error) {
  const body = memoryBody();
  if (!body) return;
  body.innerHTML = `
    <div class="memory-empty">
      项目记忆读取失败：${memoryEsc(error?.message || "未知错误")}<br />
      <button type="button" class="memory-secondary" id="memoryRetryBtn" style="margin-top:8px">重试</button>
    </div>
  `;
  document.getElementById("memoryRetryBtn")?.addEventListener("click", () => refreshMemoryPanel(true));
}

function renderUnboundMemory() {
  const body = memoryBody();
  if (!body) return;
  const canEdit = memoryCanEdit();
  const options = memoryState.projects.map(project => (
    `<option value="${memoryEsc(project.id)}">${memoryEsc(project.name)}</option>`
  )).join("");
  const defaultName = `${document.getElementById("conversationTitle")?.textContent || "研发项目"} 项目`;
  body.innerHTML = `
    <div class="memory-unbound">
      <div class="memory-proposal-head">
        <span class="memory-badge pending">未绑定项目</span>
        <b>这个任务目前没有 Project Memory</b>
      </div>
      <p>灵境不会根据任务标题、工作空间或相似文本自动猜项目。只有显式绑定后，长期记忆才会跨任务复用。</p>
      ${canEdit ? `
        <div class="memory-unbound-grid">
          <label>绑定到已有项目
            <div class="memory-unbound-row">
              <select id="memoryProjectSelect" ${options ? "" : "disabled"}>${options || '<option>还没有项目</option>'}</select>
              <button id="memoryBindBtn" class="memory-primary" type="button" ${options ? "" : "disabled"}>绑定</button>
            </div>
          </label>
          <label>或创建新项目并绑定
            <div class="memory-unbound-row">
              <input id="memoryProjectName" maxlength="200" value="${memoryEsc(defaultName)}" />
              <button id="memoryCreateBindBtn" class="memory-primary" type="button">创建</button>
            </div>
          </label>
        </div>
      ` : '<div class="memory-readonly">你当前是只读成员，不能创建或绑定项目。</div>'}
    </div>
  `;
  document.getElementById("memoryBindBtn")?.addEventListener("click", bindSelectedProject);
  document.getElementById("memoryCreateBindBtn")?.addEventListener("click", createAndBindProject);
}

function renderProposal(proposal) {
  const matchingKindKeys = [...new Set(
    memoryState.memories
      .filter(row => row.kind === proposal.kind)
      .map(row => row.memory_key)
  )];
  const datalistId = `memory-keys-${proposal.id}`;
  const options = matchingKindKeys.map(key => `<option value="${memoryEsc(key)}"></option>`).join("");
  return `
    <article class="memory-proposal" data-proposal-id="${memoryEsc(proposal.id)}">
      <div class="memory-proposal-head">
        <span class="memory-badge pending">待确认</span>
        <span class="memory-badge">${memoryEsc(MEMORY_KIND_LABEL[proposal.kind] || proposal.kind)}</span>
        <b>${memoryEsc(proposal.suggested_key)}</b>
      </div>
      <p class="memory-content">${memoryEsc(proposal.content)}</p>
      <div class="memory-meta">
        <span>scope · ${memoryEsc(scopeText(proposal))}</span>
        <span>conversation · ${memoryEsc(proposal.conversation_id)}</span>
        <span>source · message:${memoryEsc(proposal.message_id)}</span>
        <span>extractor · ${memoryEsc(proposal.extractor_version || "deterministic")}</span>
      </div>
      ${memoryCanEdit() ? `
        <label class="memory-key-field">
          <span>Memory key</span>
          <input data-proposal-key="${memoryEsc(proposal.id)}" list="${memoryEsc(datalistId)}" value="${memoryEsc(proposal.suggested_key)}" maxlength="240" />
          <datalist id="${memoryEsc(datalistId)}">${options}</datalist>
          <small class="memory-key-hint">如果这是已有事实/规则的更新，请使用同一个 key，让系统生成下一 revision；不要新建近义 key。</small>
        </label>
        <div class="memory-actions">
          <button type="button" class="memory-primary" data-memory-approve="${memoryEsc(proposal.id)}">批准并写入</button>
          <button type="button" class="memory-danger" data-memory-reject="${memoryEsc(proposal.id)}">拒绝</button>
        </div>
      ` : '<div class="memory-readonly" style="margin-top:9px">只读成员可以查看 proposal，但不能批准或拒绝。</div>'}
    </article>
  `;
}

function renderMemoryItem(row) {
  const state = row.state || "active";
  const controls = memoryCanEdit()
    ? (state === "active"
        ? `<button type="button" class="memory-warn" data-memory-state="disputed" data-memory-id="${memoryEsc(row.id)}">标记争议</button><button type="button" class="memory-danger" data-memory-state="retracted" data-memory-id="${memoryEsc(row.id)}">撤回</button>`
        : state === "disputed"
          ? `<button type="button" data-memory-state="active" data-memory-id="${memoryEsc(row.id)}">恢复生效</button><button type="button" class="memory-danger" data-memory-state="retracted" data-memory-id="${memoryEsc(row.id)}">撤回</button>`
          : `<button type="button" data-memory-state="active" data-memory-id="${memoryEsc(row.id)}">恢复生效</button>`)
    : "";
  const validity = [
    row.valid_from ? `valid_from=${row.valid_from}` : "",
    row.valid_to ? `valid_to=${row.valid_to}` : "",
    row.expires_at ? `expires=${row.expires_at}` : "",
  ].filter(Boolean).join(" · ");
  return `
    <article class="memory-item" data-memory-card="${memoryEsc(row.id)}">
      <div class="memory-item-head">
        <span class="memory-badge ${memoryEsc(state)}">${memoryEsc(MEMORY_STATE_LABEL[state] || state)}</span>
        <span class="memory-badge">${memoryEsc(MEMORY_KIND_LABEL[row.kind] || row.kind)}</span>
        <b>${memoryEsc(row.memory_key)} · rev ${memoryEsc(row.revision)}</b>
      </div>
      <p class="memory-content">${memoryEsc(row.content)}</p>
      <div class="memory-meta">
        <span>scope · ${memoryEsc(scopeText(row))}</span>
        <span>source · ${memoryEsc(row.source_type)}:${memoryEsc(row.source_id)}</span>
        <span>confidence ${Number(row.confidence ?? 0).toFixed(2)} · importance ${Number(row.importance ?? 0).toFixed(2)}${row.pinned ? " · pinned" : ""}</span>
        ${validity ? `<span>${memoryEsc(validity)}</span>` : ""}
      </div>
      <div class="memory-actions">
        <button type="button" data-memory-history="${memoryEsc(row.id)}">版本历史</button>
        ${controls}
      </div>
      <div class="memory-history" data-memory-history-body="${memoryEsc(row.id)}" hidden></div>
    </article>
  `;
}

function renderBoundMemory() {
  const body = memoryBody();
  if (!body || !memoryState.project) return;
  const activeCount = memoryState.memories.filter(row => row.state === "active").length;
  const attentionCount = memoryState.memories.filter(row => row.state !== "active").length;
  const project = memoryState.project;
  body.innerHTML = `
    <div class="memory-project-card">
      <div class="memory-project-top">
        <div>
          <span class="memory-badge active">已绑定项目</span>
          <b style="margin-top:7px">${memoryEsc(project.name)}</b>
          <small>${memoryEsc(project.id)}${project.default_branch ? ` · default_branch=${memoryEsc(project.default_branch)}` : ""}</small>
        </div>
        <button type="button" class="memory-refresh" id="memoryRefreshBtn">刷新</button>
      </div>
      <div class="memory-stats">
        <div class="memory-stat"><b>${memoryState.proposals.length}</b><small>待确认</small></div>
        <div class="memory-stat"><b>${activeCount}</b><small>生效 head</small></div>
        <div class="memory-stat"><b>${attentionCount}</b><small>争议 / 撤回</small></div>
      </div>
    </div>

    <div>
      <div class="memory-section-head"><b>待确认提议</b><small>人工批准前不属于项目事实</small></div>
      <div class="memory-list">
        ${memoryState.proposals.length ? memoryState.proposals.map(renderProposal).join("") : '<div class="memory-empty">当前没有待确认记忆。</div>'}
      </div>
    </div>

    <div>
      <div class="memory-section-head"><b>当前 Memory Heads</b><small>跨全部 build / branch / commit / environment scope</small></div>
      <div class="memory-list">
        ${memoryState.memories.length ? memoryState.memories.map(renderMemoryItem).join("") : '<div class="memory-empty">这个项目还没有已生效或已治理的长期记忆。</div>'}
      </div>
    </div>
  `;

  document.getElementById("memoryRefreshBtn")?.addEventListener("click", () => refreshMemoryPanel(true));
  body.querySelectorAll("[data-memory-approve]").forEach(button => {
    button.addEventListener("click", () => approveMemoryProposal(button.dataset.memoryApprove));
  });
  body.querySelectorAll("[data-memory-reject]").forEach(button => {
    button.addEventListener("click", () => rejectMemoryProposal(button.dataset.memoryReject));
  });
  body.querySelectorAll("[data-memory-state]").forEach(button => {
    button.addEventListener("click", () => setMemoryState(button.dataset.memoryId, button.dataset.memoryState));
  });
  body.querySelectorAll("[data-memory-history]").forEach(button => {
    button.addEventListener("click", () => toggleMemoryHistory(button.dataset.memoryHistory));
  });
}

async function bindProject(projectId) {
  const conversationId = currentConversationId();
  if (!conversationId || !projectId) return;
  await memoryApi(`/api/projects/${encodeURIComponent(projectId)}/conversations/${encodeURIComponent(conversationId)}`, {
    method: "POST",
    body: JSON.stringify({}),
  });
  memoryToast("项目已绑定；后续任务可复用经过治理的长期记忆");
  await refreshMemoryPanel(true);
}

async function bindSelectedProject() {
  const select = document.getElementById("memoryProjectSelect");
  if (!select?.value) return;
  try {
    await bindProject(select.value);
  } catch (error) {
    memoryToast(error.message);
  }
}

async function createAndBindProject() {
  const input = document.getElementById("memoryProjectName");
  const name = input?.value.trim();
  if (!name) {
    memoryToast("请填写项目名称");
    return;
  }
  try {
    const project = await memoryApi("/api/projects", {
      method: "POST",
      body: JSON.stringify({name}),
    });
    await bindProject(project.id);
  } catch (error) {
    memoryToast(error.message);
  }
}

async function approveMemoryProposal(proposalId) {
  if (!memoryState.project) return;
  const input = memoryBody()?.querySelector(`[data-proposal-key="${CSS.escape(proposalId)}"]`);
  const memoryKey = input?.value.trim();
  if (!memoryKey) {
    memoryToast("Memory key 不能为空");
    return;
  }
  try {
    await memoryApi(`/api/projects/${encodeURIComponent(memoryState.project.id)}/memory-proposals/${encodeURIComponent(proposalId)}/approve`, {
      method: "POST",
      body: JSON.stringify({
        memory_key: memoryKey,
        note: "项目记忆面板人工确认",
      }),
    });
    memoryToast("记忆已批准并写入新的 authoritative revision");
    await refreshMemoryPanel(true);
  } catch (error) {
    memoryToast(error.message);
  }
}

async function rejectMemoryProposal(proposalId) {
  if (!memoryState.project) return;
  try {
    await memoryApi(`/api/projects/${encodeURIComponent(memoryState.project.id)}/memory-proposals/${encodeURIComponent(proposalId)}/reject`, {
      method: "POST",
      body: JSON.stringify({note: "用户在项目记忆面板明确拒绝"}),
    });
    memoryToast("记忆提议已拒绝，不会进入长期记忆");
    await refreshMemoryPanel(true);
  } catch (error) {
    memoryToast(error.message);
  }
}

async function setMemoryState(memoryId, nextState) {
  if (!memoryState.project) return;
  const row = memoryState.memories.find(item => item.id === memoryId);
  if (!row) return;
  try {
    await memoryApi(`/api/projects/${encodeURIComponent(memoryState.project.id)}/memory-state`, {
      method: "POST",
      body: JSON.stringify({
        memory_key: row.memory_key,
        state: nextState,
        build_ref: row.build_ref || null,
        branch_ref: row.branch_ref || null,
        commit_ref: row.commit_ref || null,
        environment_ref: row.environment_ref || null,
        note: `项目记忆面板人工设置为 ${nextState}`,
      }),
    });
    memoryToast(`记忆状态已更新为：${MEMORY_STATE_LABEL[nextState] || nextState}`);
    await refreshMemoryPanel(true);
  } catch (error) {
    memoryToast(error.message);
  }
}

async function toggleMemoryHistory(memoryId) {
  if (!memoryState.project) return;
  const row = memoryState.memories.find(item => item.id === memoryId);
  const target = memoryBody()?.querySelector(`[data-memory-history-body="${CSS.escape(memoryId)}"]`);
  if (!row || !target) return;
  if (!target.hidden) {
    target.hidden = true;
    return;
  }
  target.hidden = false;
  target.innerHTML = '<div class="memory-history-row">正在读取 revision history…</div>';
  try {
    const history = await memoryApi(
      `/api/projects/${encodeURIComponent(memoryState.project.id)}/memory-history?${scopeQuery(row)}`
    );
    target.innerHTML = history.length
      ? history.map(item => `
          <div class="memory-history-row">
            <b>rev ${memoryEsc(item.revision)} · ${memoryEsc(MEMORY_STATE_LABEL[item.state] || item.state)}</b>
            <p>${memoryEsc(item.content)}</p>
            <small>${memoryEsc(item.source_type)}:${memoryEsc(item.source_id)} · ${memoryEsc(scopeText(item))}</small>
          </div>
        `).join("")
      : '<div class="memory-history-row">没有 revision history。</div>';
  } catch (error) {
    target.innerHTML = `<div class="memory-history-row">${memoryEsc(error.message)}</div>`;
  }
}

async function refreshMemoryPanel(force = false) {
  const panel = document.getElementById("panel-memory");
  if (!panel?.classList.contains("active") && !force) return;
  if (memoryState.loading) return;
  const conversationId = currentConversationId();
  memoryState.conversationId = conversationId;
  if (!conversationId) {
    const body = memoryBody();
    if (body) body.innerHTML = '<div class="memory-empty">选择一个任务后查看项目记忆。</div>';
    return;
  }

  memoryState.loading = true;
  renderMemoryLoading();
  try {
    // Workspace membership can change independently of the page lifecycle. Re-authorize
    // the current principal on every governance refresh instead of caching an old role.
    memoryState.session = await memoryApi("/api/auth/me");
    const binding = await memoryApi(`/api/conversations/${encodeURIComponent(conversationId)}/project`);
    memoryState.project = binding.project || null;
    if (!memoryState.project) {
      memoryState.memories = [];
      memoryState.proposals = [];
      memoryState.projects = await memoryApi("/api/projects");
      renderUnboundMemory();
      return;
    }
    const projectId = encodeURIComponent(memoryState.project.id);
    const [memories, proposals] = await Promise.all([
      memoryApi(`/api/projects/${projectId}/memory-heads?include_nonactive=true&limit=500`),
      memoryApi(`/api/projects/${projectId}/memory-proposals?status=pending&limit=200`),
    ]);
    memoryState.memories = Array.isArray(memories) ? memories : [];
    memoryState.proposals = Array.isArray(proposals) ? proposals : [];
    renderBoundMemory();
  } catch (error) {
    if (error.status === 401) memoryState.session = null;
    renderMemoryError(error);
  } finally {
    memoryState.loading = false;
  }
}

function installConversationTracking() {
  const originalReplaceState = history.replaceState.bind(history);
  history.replaceState = (...args) => {
    const result = originalReplaceState(...args);
    window.dispatchEvent(new Event("lingjing:locationchange"));
    return result;
  };
  window.addEventListener("popstate", () => window.dispatchEvent(new Event("lingjing:locationchange")));
  window.addEventListener("lingjing:locationchange", () => {
    const next = currentConversationId();
    if (next === memoryState.conversationId) return;
    memoryState.conversationId = next;
    memoryState.project = null;
    memoryState.memories = [];
    memoryState.proposals = [];
    refreshMemoryPanel(false);
  });

  const messageList = document.getElementById("messageList");
  if (messageList) {
    let timer;
    new MutationObserver(() => {
      if (!document.getElementById("panel-memory")?.classList.contains("active")) return;
      clearTimeout(timer);
      timer = setTimeout(() => refreshMemoryPanel(true), 250);
    }).observe(messageList, {childList: true, subtree: true});
  }

  setInterval(() => {
    if (location.href === memoryState.lastUrl) return;
    memoryState.lastUrl = location.href;
    window.dispatchEvent(new Event("lingjing:locationchange"));
  }, 800);
}

function bootMemoryPanel() {
  if (!installMemoryPanel()) {
    setTimeout(bootMemoryPanel, 50);
    return;
  }
  installConversationTracking();
}

bootMemoryPanel();
