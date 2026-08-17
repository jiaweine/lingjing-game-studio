const $ = id => document.getElementById(id);

const state = {
  conversation: null,
  scene: "battle_review",
  pending: [],
  assets: [],
  messages: [],
  events: [],
  ws: null,
  busy: false,
  jobId: null,
  progress: [],
  session: null,
  config: null,
  scope: "active",
  members: [],
  workspaces: [],
  control: null,
  feedback: {},
  metrics: null,
  gate: null,
};

const SCENE_NAME = {
  battle_review: "战斗问题复现",
  balance: "数值风险检查",
  regression: "版本回归验证",
  npc: "角色行为检查",
  content_compare: "多素材交叉核对",
};

const ICON = {
  image: "▧",
  video: "▷",
  audio: "∿",
  text: "⌘",
  file: "＋",
  replay: "✓",
};

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[char]));
}

async function api(path, options = {}) {
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
    try { message = JSON.parse(raw).detail || raw; } catch {}
    const error = new Error(message || String(response.status));
    error.status = response.status;
    if (response.status === 401 && state.config?.auth_required) showAuthModal();
    throw error;
  }
  return response.json();
}

function toast(message) {
  const element = $("toast");
  element.textContent = message;
  element.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => element.classList.remove("show"), 2200);
}

function fmtSize(bytes) {
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)}MB`;
}

function fmtTime(timestamp) {
  if (!timestamp) return "";
  return new Date(timestamp * 1000).toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

function md(text) {
  let value = esc(text || "");
  value = value
    .replace(/^###\s+(.+)$/gm, "<h3>$1</h3>")
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  const lines = value.split("\n");
  const out = [];
  let inList = false;
  for (const line of lines) {
    const match = line.match(/^\s*(\d+)\.\s+(.+)$/);
    if (match) {
      if (!inList) {
        out.push("<ol>");
        inList = true;
      }
      out.push(`<li>${match[2]}</li>`);
      continue;
    }
    if (inList) {
      out.push("</ol>");
      inList = false;
    }
    if (line.trim()) out.push(`<p>${line}</p>`);
  }
  if (inList) out.push("</ol>");
  return out.join("");
}

function kindOf(asset) {
  return asset?.meta?.kind ||
    (/image/.test(asset?.mime) ? "image" :
      /video/.test(asset?.mime) ? "video" :
      /audio/.test(asset?.mime) ? "audio" :
      /text|json|csv|yaml|xml/.test(asset?.mime) ? "text" : "file");
}

function kindLabel(asset) {
  const kind = kindOf(asset);
  const meta = asset.meta || {};
  if (kind === "video") return `视频${meta.duration ? ` · ${meta.duration}s` : ""}`;
  if (kind === "image") return `图片${meta.width ? ` · ${meta.width}×${meta.height}` : ""}`;
  if (kind === "audio") return `音频${meta.duration ? ` · ${meta.duration}s` : ""}`;
  if (kind === "text") return `文本 · ${meta.lines || 0} 行`;
  return fmtSize(asset.size || 0);
}

function applySession(session) {
  state.session = session;
  $("workspaceName").textContent = session?.workspace?.name || "本地演示空间";
  const email = session?.user?.email || "demo@local";
  $("userAvatar").textContent = (email.split("@")[0].slice(0, 1) || "游").toUpperCase();
  $("userAvatar").title = `${email} · ${session?.user?.role || "member"}`;
  $("newTaskBtn").disabled = !canEdit();
  $("newTaskBtn").title = canEdit() ? "新建任务" : "只读成员不能新建任务";
}

function configureInviteAuth() {
  const invited = Boolean(new URLSearchParams(location.search).get("invite"));
  const registerTab = document.querySelector('[data-auth-tab="register"]');
  if (registerTab) registerTab.textContent = invited ? "接受邀请" : "创建空间";
  const workspaceInput = $("registerWorkspace");
  const workspaceField = workspaceInput?.closest("label");
  if (workspaceField) workspaceField.hidden = invited;
  if (workspaceInput) workspaceInput.required = !invited;
  const submit = $("registerForm")?.querySelector(".auth-submit");
  if (submit) submit.textContent = invited ? "注册并加入" : "创建并进入";
}

function clearInviteFromUrl() {
  const url = new URL(location.href);
  if (!url.searchParams.has("invite")) return;
  url.searchParams.delete("invite");
  history.replaceState(null, "", url);
}

function showAuthModal() {
  configureInviteAuth();
  if ($("authModal")) $("authModal").hidden = false;
}

function hideAuthModal() {
  if ($("authModal")) $("authModal").hidden = true;
}

function switchAuthTab(name) {
  document.querySelectorAll("[data-auth-tab]").forEach(button => {
    button.classList.toggle("active", button.dataset.authTab === name);
  });
  $("loginForm").hidden = name !== "login";
  $("registerForm").hidden = name !== "register";
}

async function ensureSession() {
  state.config = await api("/api/config");
  try {
    const session = await api("/api/auth/me");
    applySession(session);
    hideAuthModal();
    await maybeAcceptInvite();
    return true;
  } catch (error) {
    if (error.status === 401 && state.config.auth_required) {
      showAuthModal();
      return false;
    }
    throw error;
  }
}

function bindAuth() {
  document.querySelectorAll("[data-auth-tab]").forEach(button => {
    button.onclick = () => switchAuthTab(button.dataset.authTab);
  });

  $("loginForm").onsubmit = async event => {
    event.preventDefault();
    try {
      const session = await api("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({
          email: $("loginEmail").value.trim(),
          password: $("loginPassword").value,
        }),
      });
      applySession(session);
      hideAuthModal();
      await maybeAcceptInvite();
      toast("登录成功");
      await bootWorkspace();
    } catch (error) {
      toast(error.message);
    }
  };

  $("registerForm").onsubmit = async event => {
    event.preventDefault();
    const inviteToken = new URLSearchParams(location.search).get("invite") || null;
    try {
      const session = await api("/api/auth/register", {
        method: "POST",
        body: JSON.stringify({
          name: $("registerName").value.trim(),
          workspace_name: $("registerWorkspace").value.trim() || "受邀工作空间",
          invite_token: inviteToken,
          email: $("registerEmail").value.trim(),
          password: $("registerPassword").value,
        }),
      });
      applySession(session);
      hideAuthModal();
      if (inviteToken) clearInviteFromUrl();
      else await maybeAcceptInvite();
      toast(inviteToken ? "已加入工作空间" : "工作空间已创建");
      await bootWorkspace();
    } catch (error) {
      toast(error.message);
    }
  };

  $("userAvatar").onclick = async () => {
    if (!state.config?.auth_required) {
      toast(state.session?.workspace?.name || "本地演示空间");
      return;
    }
    try {
      await api("/api/auth/logout", {
        method: "POST",
        body: JSON.stringify({}),
      });
      state.session = null;
      state.conversation = null;
      state.ws?.close();
      showAuthModal();
      toast("已退出登录");
    } catch (error) {
      toast(error.message);
    }
  };
}

async function loadServiceHealth() {
  const element = $("serviceStatus");
  try {
    await api("/api/health");
    element.querySelector("span").textContent = "服务正常";
    element.classList.remove("bad");
  } catch {
    element.querySelector("span").textContent = "服务异常";
    element.classList.add("bad");
  }
}

function setScene(scene) {
  state.scene = scene;
  document.querySelectorAll(".scene-item").forEach(element => {
    element.classList.toggle("active", element.dataset.scene === scene);
  });
  $("conversationTitle").textContent = SCENE_NAME[scene] || "研发任务";
  $("conversationMeta").textContent = "把目标说清楚，系统会持续执行到得到可核验结果。";
}

function setBusy(busy, jobId = null) {
  state.busy = busy;
  state.jobId = busy ? (jobId || state.jobId) : null;
  $("sendBtn").disabled = busy;
  $("stopBtn").hidden = !(busy && state.jobId);
  $("stopBtn").disabled = false;
  $("stopBtn").textContent = "停止";
}

function markCancelled() {
  setBusy(false);
  $("thinkingCard").hidden = true;
  $("taskState").textContent = "已停止";
  $("taskStateHint").textContent = "当前执行已停止，可以修改目标后重新开始。";
  document.querySelector(".task-state-card").className = "task-state-card cancelled";
  renderTaskActions();
}

async function loadConversations() {
  const query = $("taskSearch")?.value.trim() || "";
  const archived = state.scope === "archived";
  const rows = await api(`/api/conversations?limit=100&archived=${archived}&q=${encodeURIComponent(query)}`);
  if (query) trackProductEvent("task.search", null, {query_length: query.length});
  $("conversationList").innerHTML = rows.length
    ? rows.map(conversation => `
        <div class="conv-item ${state.conversation?.id === conversation.id ? "active" : ""}"
             data-id="${conversation.id}">
          <span></span>
          <div>
            <b>${conversation.pinned ? "⌁ " : ""}${esc(conversation.title)}</b>
            <small>${esc(SCENE_NAME[conversation.scene] || conversation.scene)} · ${taskStatusLabel(conversation.status)}</small>
          </div>
        </div>
      `).join("")
    : '<div class="empty-side">还没有历史任务。</div>';
  document.querySelectorAll(".conv-item").forEach(item => {
    item.onclick = () => openConversation(item.dataset.id);
  });
  return rows;
}

function syncConversationUrl(id) {
  if (!id) return;
  const url = new URL(location.href);
  url.searchParams.set("conversation", id);
  history.replaceState(null, "", url);
}

async function newConversation(scene = state.scene) {
  const conversation = await api("/api/conversations", {
    method: "POST",
    body: JSON.stringify({
      title: SCENE_NAME[scene] || "新的研发任务",
      scene,
    }),
  });
  state.conversation = conversation;
  state.messages = [];
  state.assets = [];
  state.events = [];
  state.progress = [];
  state.pending = [];
  state.control = null;
  state.feedback = {};
  state.gate = null;
  setBusy(false);
  state.ws?.close();
  syncConversationUrl(conversation.id);
  renderConversation();
  connectConversation();
  await loadConversations();
  return conversation;
}

async function openConversation(id) {
  const conversation = await api(`/api/conversations/${id}`);
  state.conversation = conversation;
  state.messages = conversation.messages || [];
  state.assets = conversation.assets || [];
  state.events = conversation.events || [];
  state.progress = [];
  state.pending = [];
  setBusy(["queued", "running"].includes(conversation.job?.status), conversation.job?.id || null);
  setScene(conversation.scene || "battle_review");
  state.ws?.close();
  syncConversationUrl(conversation.id);
  await loadConversationControl();
  await loadTeamPanel();
  renderConversation();
  connectConversation();
  await loadConversations();
}

function renderConversation() {
  $("welcomePanel").hidden = state.messages.length > 0;
  $("conversationTitle").textContent =
    state.conversation?.title || SCENE_NAME[state.scene];
  const turns = state.messages.filter(message => message.role === "user").length;
  $("conversationMeta").textContent = state.messages.length
    ? `${turns} 次任务推进 · ${state.assets.length} 份素材`
    : "把目标说清楚，系统会持续执行到得到可核验结果。";
  $("messageList").innerHTML = state.messages.map(renderMessage).join("");
  renderAssets();
  renderPending();
  renderEventHistory();
  renderTaskActions();
  renderApproval();

  const lastAnswer = [...state.messages].reverse().find(message => message.role === "assistant");
  if (lastAnswer?.payload) {
    renderEvidence(lastAnswer.payload.evidence || []);
    renderDeliverables(lastAnswer.payload.deliverables || []);
    renderSuggestions(lastAnswer.payload.suggestions || []);
  } else {
    renderDeliverables([]);
  }
  renderFeedbackState();
  scrollBottom(false);
}

function renderMessage(message) {
  const payload = message.payload || {};
  const assetIds = payload.asset_ids || [];
  const messageAssets = assetIds
    .map(id => state.assets.find(asset => asset.id === id))
    .filter(Boolean);

  if (message.role === "user") {
    return `
      <article class="msg user">
        <div class="msg-body">
          <div class="msg-label">
            <span class="tag">输入</span>
            <b>任务目标</b>
            <time>${message.created_at ? fmtTime(message.created_at) : ""}</time>
          </div>
          <div class="msg-content">
            <div class="goal-summary">
              <span class="goal-marker">↗</span>
              <p>${esc(message.content)}</p>
            </div>
          </div>
          ${renderMsgAssets(messageAssets)}
        </div>
      </article>
    `;
  }

  return `
    <article class="msg assistant" data-message-id="${esc(message.id)}">
      <div class="msg-body">
        <div class="msg-label">
          <span class="tag">交付</span>
          <b>执行结果</b>
          <time>${message.created_at ? fmtTime(message.created_at) : ""}</time>
        </div>
        <div class="msg-content">${md(message.content)}</div>
        <div class="answer-foot">
          <button class="answer-action" type="button" data-copy-result>复制结果</button>
          <span class="answer-feedback-label">结果反馈</span>
          <button class="answer-action feedback-action" type="button" data-feedback="correct">正确</button>
          <button class="answer-action feedback-action" type="button" data-feedback="partial">部分正确</button>
          <button class="answer-action feedback-action" type="button" data-feedback="incorrect">有错误</button>
          <button class="answer-action evidence-action" type="button" data-evidence-useful>证据有用</button>
          <button class="answer-action verify-action" type="button" data-human-verify>人工已验证</button>
        </div>
      </div>
    </article>
  `;
}

function renderMsgAssets(rows) {
  if (!rows.length) return "";
  return `
    <div class="msg-assets">
      ${rows.map(asset => {
        const kind = kindOf(asset);
        const visual = kind === "image"
          ? `<img class="msg-asset-thumb" src="/api/assets/${asset.id}/file" alt="" />`
          : `<span class="thumb">${ICON[kind] || "＋"}</span>`;
        return `
          <div class="msg-asset ${kind}">
            ${visual}
            <div>
              <b>${esc(asset.name)}</b>
              <small>${esc(kindLabel(asset))}</small>
            </div>
          </div>
        `;
      }).join("")}
    </div>
  `;
}

function renderPending() {
  const box = $("pendingAssets");
  box.hidden = !state.pending.length;
  box.innerHTML = state.pending.map((asset, index) => {
    const kind = kindOf(asset);
    const visual = kind === "image"
      ? `<img class="thumb" src="/api/assets/${asset.id}/file" alt="" />`
      : `<span class="thumb">${ICON[kind] || "＋"}</span>`;
    return `
      <div class="pending-asset">
        ${visual}
        <div>
          <b>${esc(asset.name)}</b>
          <small>${esc(kindLabel(asset))}</small>
        </div>
        <button type="button" data-rm="${index}">×</button>
      </div>
    `;
  }).join("");
  box.querySelectorAll("[data-rm]").forEach(button => {
    button.onclick = () => {
      state.pending.splice(Number(button.dataset.rm), 1);
      renderPending();
    };
  });
}

function renderAssets() {
  $("assetList").innerHTML = state.assets.length
    ? state.assets.map(asset => `
        <div class="asset-card">
          <div class="asset-icon">${ICON[kindOf(asset)] || "＋"}</div>
          <div>
            <b>${esc(asset.name)}</b>
            <small>${esc(kindLabel(asset))}<br />${fmtSize(asset.size || 0)}</small>
          </div>
        </div>
      `).join("")
    : '<div class="empty-side">还没有上传素材。</div>';
}

function renderEvidence(rows = []) {
  $("evidenceList").innerHTML = rows.length
    ? rows.map(evidence => {
        const asset = evidence.asset_id
          ? state.assets.find(item => item.id === evidence.asset_id)
          : null;
        const kind = evidence.type || kindOf(asset);
        let media = "";
        if (asset && kind === "image") {
          media = `<div class="evidence-media"><img src="/api/assets/${asset.id}/file" alt="" /></div>`;
        } else if (asset && kind === "video") {
          media = `<div class="evidence-media"><img src="/api/assets/${asset.id}/preview/0" alt="" /></div>`;
        } else if (asset && kind === "audio") {
          media = `<div class="evidence-wave">${"<i></i>".repeat(8)}<span>${asset.meta?.duration || 0}s</span></div>`;
        } else if (asset && kind === "text" && asset.meta?.preview) {
          media = `<pre class="evidence-log">${esc(asset.meta.preview.split("\n").slice(0, 4).join("\n"))}</pre>`;
        }
        return `
          <div class="evidence-card rich">
            ${media}
            <div class="evidence-info">
              <div class="evidence-icon">${ICON[kind] || "◇"}</div>
              <div>
                <em>${esc(evidence.label || "证据")}</em>
                <b>${esc(evidence.title || "")}</b>
                <small>${kind === "replay" ? "同条件再次核验" : "已和当前结论关联"}</small>
              </div>
            </div>
          </div>
        `;
      }).join("")
    : '<div class="empty-side">完成一次任务后显示。</div>';
}

function renderDeliverables(rows = []) {
  const box = $("deliverableList");
  if (!box) return;
  box.innerHTML = rows.length ? rows.map((item, index) => `
    <article class="deliverable-card">
      <div class="deliverable-head"><span>${String(index + 1).padStart(2, "0")}</span><b>${esc(item.title || "研发交付")}</b></div>
      <p>${esc(item.summary || "")}</p>
      <ul>${(item.items || []).map(value => `<li>${esc(value)}</li>`).join("")}</ul>
      <div class="deliverable-foot">
        <small>${(item.evidence_ids || []).length} 条证据关联</small>
        <button type="button" data-copy-deliverable="${index}">复制</button>
      </div>
    </article>
  `).join("") : '<div class="empty-side">完成一次任务后显示。</div>';
  box.querySelectorAll("[data-copy-deliverable]").forEach(button => {
    button.onclick = async () => {
      const item = rows[Number(button.dataset.copyDeliverable)];
      const text = [item.title, item.summary, ...(item.items || []).map(value => `- ${value}`)].filter(Boolean).join("\n");
      await navigator.clipboard?.writeText(text);
      trackProductEvent("deliverable.copy", state.conversation?.id, {type: item.type});
      toast("交付物已复制");
    };
  });
}

function renderSuggestions(rows = []) {
  const suggestions = rows.length
    ? rows
    : ["继续追问", "补充素材", "整理成结论摘要"];
  $("suggestionList").innerHTML = suggestions
    .map(text => `<button type="button">${esc(text)}</button>`)
    .join("");
  $("suggestionList").querySelectorAll("button").forEach(button => {
    button.onclick = () => {
      $("messageInput").value = button.textContent;
      autoSize();
      $("messageInput").focus();
    };
  });
}

function renderEventHistory() {
  const job = state.conversation?.job;
  const allProgress = state.events.filter(event => event.type === "progress");
  const taggedProgress = job?.id
    ? allProgress.filter(event => event.payload?.job_id === job.id)
    : [];
  const useTagged = Boolean(
    job?.id && (["queued", "running"].includes(job.status) || taggedProgress.length)
  );
  const progress = useTagged ? taggedProgress : allProgress;
  state.progress = progress.map(event => event.payload);
  renderProgress();

  if (["queued", "running"].includes(job?.status) && !progress.length) {
    $("taskState").textContent = job.status === "queued" ? "等待执行" : "准备执行";
    $("taskStateHint").textContent = "任务已接收，正在准备执行上下文。";
    document.querySelector(".task-state-card").className = "task-state-card running";
    return;
  }
  if (state.busy) return;

  const terminal = [...state.events].reverse().find(event =>
    ["answer.cancelled", "answer.error"].includes(event.type)
    && (!job?.id || event.payload?.job_id === job.id)
  );
  if (terminal?.type === "answer.cancelled") markCancelled();
  if (terminal?.type === "answer.error") {
    setBusy(false);
    $("taskState").textContent = "执行中断";
    $("taskStateHint").textContent = "本次执行没有完成，可以重试或补充要求。";
    document.querySelector(".task-state-card").className = "task-state-card error";
  }
}

function renderProgress() {
  const rows = state.progress;
  const last = rows.at(-1);
  const card = document.querySelector(".task-state-card");

  if (last) {
    const percent = Math.max(0, Math.min(100, last.percent || 0));
    $("taskState").textContent = percent >= 100 ? "验证完成" : last.step;
    $("taskPercent").textContent = `${percent}%`;
    $("taskProgress").style.width = `${percent}%`;
    $("taskStateHint").textContent = last.detail || "正在执行当前任务。";
    card.className = `task-state-card ${percent >= 100 ? "done" : "running"}`;
  } else {
    $("taskState").textContent = "等待目标";
    $("taskPercent").textContent = "0%";
    $("taskProgress").style.width = "0%";
    $("taskStateHint").textContent = "提交目标后，这里会显示正在做什么。";
    card.className = "task-state-card";
  }

  $("progressList").innerHTML = rows.length
    ? rows.map((progress, index) => {
        const done = index < rows.length - 1 || (progress.percent || 0) >= 100;
        return `
          <div class="progress-row ${done ? "done" : ""}">
            <span>${done ? "✓" : index + 1}</span>
            <div>
              <b>${esc(progress.step)}</b>
              <small>${esc(progress.detail || "")}</small>
            </div>
          </div>
        `;
      }).join("")
    : `
      <div class="progress-empty">
        <span class="empty-mark">↗</span>
        <b>准备好了</b>
        <p>提交一个目标，或者先上传素材。</p>
      </div>
    `;
}

function connectConversation() {
  if (!state.conversation) return;
  const conversationId = state.conversation.id;
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  const afterId = state.events.reduce(
    (latest, event) => Math.max(latest, Number(event.id) || 0),
    0,
  );
  const socket = new WebSocket(
    `${protocol}://${location.host}/ws/conversations/${conversationId}?after_id=${afterId}`
  );
  state.ws = socket;
  socket.onmessage = event => {
    try { handleEvent(JSON.parse(event.data)); } catch {}
  };
  socket.onclose = () => {
    if (state.ws !== socket || state.conversation?.id !== conversationId) return;
    clearTimeout(connectConversation.timer);
    connectConversation.timer = setTimeout(() => {
      if (state.conversation?.id === conversationId) connectConversation();
    }, 900);
  };
}

function handleEvent(event) {
  if (event.type === "heartbeat") return;
  if (event.id && state.events.some(item => item.id === event.id)) return;
  state.events.push(event);

  if (event.type === "progress") {
    if (state.conversation?.job) state.conversation.job.status = "running";
    state.progress.push(event.payload);
    renderProgress();
    $("thinkingCard").hidden = false;
    $("thinkingStep").textContent = event.payload.step;
    $("thinkingDetail").textContent = event.payload.detail || "正在继续执行";
    $("thinkingPercent").textContent = `${event.payload.percent || 0}%`;
    $("thinkingBar").style.width = `${event.payload.percent || 0}%`;
    scrollBottom();
    return;
  }

  if (event.type === "notice") {
    toast(event.payload?.title || "任务有新信息");
    return;
  }

  if (event.type === "answer.ready") {
    if (state.conversation?.job) state.conversation.job.status = "completed";
    setBusy(false);
    $("thinkingCard").hidden = true;
    const message = event.payload.message;
    state.messages.push(message);
    const result = event.payload.result || {};
    renderConversation();
    renderEvidence(result.evidence || []);
    renderDeliverables(result.deliverables || []);
    renderSuggestions(result.suggestions || []);
    loadConversationControl().then(() => { renderFeedbackState(); renderTeamPanel(); });
    $("taskState").textContent = "验证完成";
    $("taskPercent").textContent = "100%";
    $("taskProgress").style.width = "100%";
    $("taskStateHint").textContent = "结果已整理，证据与下一步都已保留。";
    document.querySelector(".task-state-card").className = "task-state-card done";
    loadConversations();
    scrollBottom();
    return;
  }

  if (event.type === "answer.cancelled") {
    const wasBusy = state.busy;
    if (state.conversation?.job) state.conversation.job.status = "cancelled";
    markCancelled();
    if (wasBusy) toast("已停止当前任务");
    return;
  }

  if (event.type === "answer.error") {
    if (state.conversation?.job) state.conversation.job.status = "failed";
    setBusy(false);
    $("thinkingCard").hidden = true;
    $("taskState").textContent = "执行中断";
    $("taskStateHint").textContent = "本次执行没有完成，可以重试或补充要求。";
    document.querySelector(".task-state-card").className = "task-state-card error";
    renderTaskActions();
    toast(event.payload?.message || "执行失败");
  }
}

async function uploadFiles(files) {
  if (!files?.length) return;
  if (!state.conversation) await newConversation(state.scene);
  for (const file of files) {
    const form = new FormData();
    form.append("file", file);
    form.append("conversation_id", state.conversation.id);
    try {
      const asset = await api("/api/assets", {
        method: "POST",
        body: form,
      });
      state.assets.push(asset);
      state.pending.push(asset);
    } catch (error) {
      toast(`${file.name}: ${error.message}`);
    }
  }
  renderPending();
  renderAssets();
}

async function sendMessage() {
  const input = $("messageInput");
  const content = input.value.trim();
  if (!content || state.busy) return;
  if (!state.conversation) await newConversation(state.scene);
  if (state.conversation?.archived_at) { toast("请先恢复已归档任务，再继续执行"); return; }

  setBusy(true);
  const selectedAssets = state.pending.map(asset => asset.id);
  const optimistic = {
    id: `local-${Date.now()}`,
    role: "user",
    content,
    payload: {asset_ids: selectedAssets},
    created_at: Date.now() / 1000,
  };
  state.messages.push(optimistic);
  state.pending = [];
  input.value = "";
  autoSize();
  renderConversation();

  $("thinkingCard").hidden = false;
  $("thinkingStep").textContent = "接收目标";
  $("thinkingDetail").textContent = "正在准备任务上下文";
  $("thinkingPercent").textContent = "4%";
  $("thinkingBar").style.width = "4%";
  $("taskState").textContent = "开始执行";
  $("taskPercent").textContent = "4%";
  $("taskProgress").style.width = "4%";
  document.querySelector(".task-state-card").className = "task-state-card running";
  scrollBottom();

  try {
    const response = await api(
      `/api/conversations/${state.conversation.id}/messages`,
      {
        method: "POST",
        body: JSON.stringify({
          content,
          asset_ids: selectedAssets,
          provider: "auto",
        }),
      }
    );
    if (response.job_id) {
      state.conversation.job = {
        id: response.job_id,
        status: response.status === "queued" ? "queued" : "running",
      };
    }
    setBusy(true, response.job_id || null);
    const serverMessage = response.message;
    if (serverMessage) {
      const index = state.messages.findIndex(item => item.id === optimistic.id);
      if (index >= 0) state.messages[index] = serverMessage;
    }
  } catch (error) {
    setBusy(false);
    $("thinkingCard").hidden = true;
    state.messages = state.messages.filter(item => item.id !== optimistic.id);
    state.pending = selectedAssets
      .map(id => state.assets.find(asset => asset.id === id))
      .filter(Boolean);
    renderConversation();
    toast(error.message);
  }
}

async function stopCurrentJob() {
  if (!state.busy || !state.jobId) return;
  const button = $("stopBtn");
  button.disabled = true;
  button.textContent = "停止中";
  try {
    const job = await api(`/api/jobs/${state.jobId}/cancel`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    if (job.status === "cancelled") {
      if (state.conversation?.job) state.conversation.job.status = "cancelled";
      markCancelled();
      toast("已停止当前任务");
    }
  } catch (error) {
    button.disabled = false;
    button.textContent = "停止";
    toast(error.message);
  }
}

function autoSize() {
  const input = $("messageInput");
  input.style.height = "auto";
  input.style.height = `${Math.min(130, input.scrollHeight)}px`;
}

function scrollBottom(smooth = true) {
  const scroller = $("chatScroll");
  requestAnimationFrame(() => {
    scroller.scrollTo({
      top: scroller.scrollHeight,
      behavior: smooth ? "smooth" : "auto",
    });
  });
}

function taskStatusLabel(status) {
  return {active: "进行中", waiting_approval: "等待确认", blocked: "受阻", verified: "已验证", stopped: "已停止"}[status] || "进行中";
}

function canEdit() {
  return state.session?.user?.role !== "viewer";
}

function isManager() {
  return ["owner", "admin"].includes(state.session?.user?.role);
}

async function trackProductEvent(name, conversationId = state.conversation?.id, payload = {}) {
  try {
    await api("/api/product-events", {method: "POST", body: JSON.stringify({name, conversation_id: conversationId || null, payload})});
  } catch {}
}

async function loadConversationControl() {
  if (!state.conversation?.id) return;
  try {
    state.control = await api(`/api/conversations/${state.conversation.id}/control`);
    state.gate = state.control.quality_gate || null;
    state.feedback = {};
    for (const row of state.control.feedback || []) {
      if (row.user_id === state.session?.user?.id) state.feedback[row.message_id] = row;
    }
  } catch {
    state.control = {approvals: [], feedback: [], quality_gate: null};
    state.gate = null;
  }
}

function renderTaskActions() {
  if (!state.conversation) return;
  const jobStatus = state.conversation.job?.status;
  const editable = canEdit();
  $("retryTaskBtn").hidden = !editable || !["failed", "cancelled"].includes(jobStatus);
  $("renameTaskBtn").hidden = !editable;
  $("pinTaskBtn").hidden = !editable;
  $("archiveTaskBtn").hidden = !editable;
  $("pinTaskBtn").textContent = state.conversation.pinned ? "取消置顶" : "置顶";
  $("archiveTaskBtn").textContent = state.conversation.archived_at ? "恢复" : "归档";
  $("deleteTaskBtn").hidden = !isManager();
  const archived = Boolean(state.conversation.archived_at);
  $("messageInput").disabled = archived || !editable;
  $("messageInput").placeholder = editable ? "描述你要完成的研发任务…" : "只读成员可以查看任务，但不能修改或执行";
  $("sendBtn").disabled = state.busy || archived || !editable;
  document.querySelectorAll(".attach-action").forEach(button => { button.disabled = archived || !editable; });
}

function pendingDeleteApproval() {
  return (state.control?.approvals || []).find(row => row.action === "conversation.delete" && row.status === "pending");
}

function renderApproval() {
  const card = $("approvalCard");
  if (!card) return;
  const approval = pendingDeleteApproval();
  card.hidden = !approval;
  if (!approval) return;
  const controls = isManager() ? `
    <div class="approval-actions">
      <button type="button" data-approval-reject>取消删除</button>
      <button class="danger" type="button" data-approval-confirm>确认永久删除</button>
    </div>` : '<small>等待工作空间管理员确认。</small>';
  card.innerHTML = `<div><span class="eyebrow">需要确认</span><b>永久删除任务和任务素材</b><p>${esc(approval.reason || "此操作不可恢复。")}</p></div>${controls}`;
  card.querySelector("[data-approval-reject]")?.addEventListener("click", () => resolveDeleteApproval(approval, false));
  card.querySelector("[data-approval-confirm]")?.addEventListener("click", () => resolveDeleteApproval(approval, true));
}

async function requestDeleteTask() {
  if (!state.conversation) return;
  try {
    const approval = await api(`/api/conversations/${state.conversation.id}/delete-request`, {method: "POST", body: "{}"});
    state.control = state.control || {approvals: []};
    state.control.approvals = [approval, ...(state.control.approvals || []).filter(row => row.id !== approval.id)];
    state.conversation.status = "waiting_approval";
    renderConversation();
    toast("删除请求已进入确认状态");
  } catch (error) { toast(error.message); }
}

async function resolveDeleteApproval(approval, approved) {
  try {
    const resolved = await api(`/api/approvals/${approval.id}/resolve`, {method: "POST", body: JSON.stringify({approved})});
    if (!approved) {
      await loadConversationControl();
      state.conversation.status = "active";
      renderConversation();
      toast("已取消删除");
      return;
    }
    await api(`/api/conversations/${state.conversation.id}?approval_id=${encodeURIComponent(resolved.id)}`, {method: "DELETE"});
    state.ws?.close();
    state.conversation = null;
    state.messages = [];
    state.assets = [];
    state.control = null;
    toast("任务及其素材已永久删除");
    await bootWorkspace();
  } catch (error) { toast(error.message); }
}

async function retryCurrentTask() {
  const jobId = state.conversation?.job?.id;
  if (!jobId) return;
  try {
    const response = await api(`/api/jobs/${jobId}/retry`, {method: "POST", body: "{}"});
    state.conversation.job = {id: response.job_id, status: response.status || "queued"};
    state.progress = [];
    setBusy(true, response.job_id);
    $("thinkingCard").hidden = false;
    $("thinkingStep").textContent = "重新执行";
    $("thinkingDetail").textContent = "正在从上一次任务上下文恢复执行";
    renderConversation();
    toast("已重新执行");
  } catch (error) { toast(error.message); }
}

async function renameCurrentTask() {
  if (!state.conversation) return;
  const value = window.prompt("任务名称", state.conversation.title || "");
  if (!value?.trim() || value.trim() === state.conversation.title) return;
  try {
    state.conversation = {...state.conversation, ...(await api(`/api/conversations/${state.conversation.id}`, {method: "PATCH", body: JSON.stringify({title: value.trim()})}))};
    renderConversation();
    await loadConversations();
  } catch (error) { toast(error.message); }
}

async function togglePinTask() {
  if (!state.conversation) return;
  try {
    state.conversation = {...state.conversation, ...(await api(`/api/conversations/${state.conversation.id}`, {method: "PATCH", body: JSON.stringify({pinned: !Boolean(state.conversation.pinned)})}))};
    renderConversation();
    await loadConversations();
  } catch (error) { toast(error.message); }
}

async function toggleArchiveTask() {
  if (!state.conversation) return;
  const restoring = Boolean(state.conversation.archived_at);
  try {
    const row = await api(`/api/conversations/${state.conversation.id}/${restoring ? "restore" : "archive"}`, {method: "POST", body: "{}"});
    state.conversation = {...state.conversation, ...row};
    renderConversation();
    await loadConversations();
    toast(restoring ? "任务已恢复" : "任务已归档");
  } catch (error) { toast(error.message); }
}

async function saveFeedback(messageId, patch) {
  const previous = state.feedback[messageId] || {verdict: "partial", evidence_useful: null, human_verified: 0, note: ""};
  const payload = {...previous, ...patch};
  try {
    const row = await api(`/api/messages/${messageId}/feedback`, {
      method: "PUT",
      body: JSON.stringify({
        verdict: payload.verdict,
        evidence_useful: payload.evidence_useful,
        human_verified: Boolean(payload.human_verified),
        note: payload.note || "",
      }),
    });
    state.feedback[messageId] = row;
    state.gate = await api(`/api/quality-gate?conversation_id=${encodeURIComponent(state.conversation.id)}`);
    renderFeedbackState();
    renderTeamPanel();
    toast(Boolean(row.human_verified) ? "已标记人工验证" : "结果反馈已记录");
  } catch (error) { toast(error.message); }
}

function renderFeedbackState() {
  $("messageList").querySelectorAll(".msg.assistant[data-message-id]").forEach(article => {
    const messageId = article.dataset.messageId;
    const feedback = state.feedback[messageId];
    article.querySelectorAll("[data-feedback]").forEach(button => {
      button.classList.toggle("active", feedback?.verdict === button.dataset.feedback);
      button.disabled = !canEdit();
    });
    const evidenceButton = article.querySelector("[data-evidence-useful]");
    if (evidenceButton) {
      evidenceButton.classList.toggle("active", feedback?.evidence_useful === 1 || feedback?.evidence_useful === true);
      evidenceButton.disabled = !canEdit();
    }
    const verifyButton = article.querySelector("[data-human-verify]");
    if (verifyButton) {
      verifyButton.classList.toggle("active", Boolean(feedback?.human_verified));
      verifyButton.disabled = !canEdit();
    }
  });
}

async function loadWorkspaces() {
  try {
    state.workspaces = await api("/api/workspaces");
    renderWorkspaceMenu();
  } catch (error) { toast(error.message); }
}

function renderWorkspaceMenu() {
  const menu = $("workspaceMenu");
  if (!menu) return;
  menu.innerHTML = state.workspaces.map(row => `<button type="button" data-workspace-id="${esc(row.id)}" class="${row.id === state.session?.workspace?.id ? "active" : ""}"><b>${esc(row.name)}</b><small>${esc(row.role)}</small></button>`).join("") || '<div class="empty-side">没有其他工作空间。</div>';
  menu.querySelectorAll("[data-workspace-id]").forEach(button => {
    button.onclick = async () => {
      if (button.dataset.workspaceId === state.session?.workspace?.id) { menu.hidden = true; return; }
      try {
        const session = await api(`/api/workspaces/${button.dataset.workspaceId}/switch`, {method: "POST", body: "{}"});
        applySession(session);
        menu.hidden = true;
        state.conversation = null;
        state.ws?.close();
        await bootWorkspace();
      } catch (error) { toast(error.message); }
    };
  });
}

async function maybeAcceptInvite() {
  const token = new URLSearchParams(location.search).get("invite");
  if (!token || !state.session) return;
  try {
    const session = await api(`/api/invites/${encodeURIComponent(token)}/accept`, {method: "POST", body: "{}"});
    applySession(session);
    clearInviteFromUrl();
    toast("已加入工作空间");
  } catch (error) {
    if (/已失效|已使用/.test(error.message)) {
      clearInviteFromUrl();
      return;
    }
    toast(error.message);
  }
}

async function loadTeamPanel() {
  try {
    state.members = await api("/api/workspace/members");
  } catch { state.members = []; }
  if (isManager()) {
    try { state.metrics = await api("/api/metrics"); } catch { state.metrics = null; }
    try { state.invites = await api("/api/workspace/invites"); } catch { state.invites = []; }
  } else {
    state.metrics = null;
    state.invites = [];
  }
  renderTeamPanel();
}

function renderTeamPanel() {
  const assignee = $("assigneeSelect");
  if (!assignee) return;
  assignee.innerHTML = '<option value="">未指定</option>' + state.members.map(member => `<option value="${esc(member.id)}">${esc(member.name || member.email)}</option>`).join("");
  assignee.value = state.conversation?.assigned_to || "";
  assignee.disabled = !state.conversation || !canEdit();

  $("memberList").innerHTML = state.members.length ? state.members.map(member => {
    const controls = isManager() ? `<select data-member-role="${esc(member.id)}"><option value="owner">所有者</option><option value="admin">管理员</option><option value="member">成员</option><option value="viewer">只读</option></select><button type="button" data-remove-member="${esc(member.id)}">移除</button>` : `<em>${esc(member.role)}</em>`;
    return `<div class="member-row"><span class="member-avatar">${esc((member.name || member.email || "成").slice(0, 1).toUpperCase())}</span><div><b>${esc(member.name || member.email)}</b><small>${esc(member.email)}</small></div><div class="member-controls">${controls}</div></div>`;
  }).join("") : '<div class="empty-side">还没有团队成员。</div>';
  $("memberList").querySelectorAll("[data-member-role]").forEach(select => {
    const member = state.members.find(row => row.id === select.dataset.memberRole);
    select.value = member?.role || "member";
    select.onchange = () => updateMemberRole(select.dataset.memberRole, select.value);
  });
  $("memberList").querySelectorAll("[data-remove-member]").forEach(button => button.onclick = () => removeMember(button.dataset.removeMember));
  $("inviteForm").hidden = !isManager();
  $("inviteList").innerHTML = (state.invites || []).filter(row => row.status === "pending").map(row => `<div class="invite-row"><div><b>${esc(row.email || "通用邀请")}</b><small>${esc(row.role)}</small></div><button type="button" data-copy-invite="${esc(row.token)}">复制链接</button><button type="button" data-revoke-invite="${esc(row.id)}">撤销</button></div>`).join("") || (isManager() ? '<div class="empty-side">没有待使用邀请。</div>' : "");
  $("inviteList").querySelectorAll("[data-copy-invite]").forEach(button => button.onclick = () => copyInvite(button.dataset.copyInvite));
  $("inviteList").querySelectorAll("[data-revoke-invite]").forEach(button => button.onclick = () => revokeInvite(button.dataset.revokeInvite));

  const gate = state.gate;
  $("qualityGate").innerHTML = gate ? `<div class="gate-state ${gate.approved ? "approved" : "pending"}"><b>${gate.approved ? "人工质量门已通过" : "等待人工质量门"}</b><small>${esc(gate.reason || "")}</small></div>` : '<div class="empty-side">任务结果产生后显示质量门。</div>';
  const metrics = state.metrics;
  $("metricGrid").innerHTML = metrics ? [
    ["任务完成", `${Math.round((metrics.first_task_completion_rate || 0) * 100)}%`],
    ["首次可核验", metrics.avg_time_to_verified_seconds == null ? "—" : `${Math.round(metrics.avg_time_to_verified_seconds)}s`],
    ["执行中断", `${Math.round((metrics.interruption_rate || 0) * 100)}%`],
    ["失败恢复", `${Math.round((metrics.recovery_rate || 0) * 100)}%`],
    ["人工介入", `${Math.round((metrics.manual_intervention_rate || 0) * 100)}%`],
    ["继续任务", `${Math.round((metrics.continuation_rate || 0) * 100)}%`],
    ["证据打开", `${Math.round((metrics.evidence_open_rate || 0) * 100)}%`],
    ["结果采纳", `${Math.round((metrics.result_adoption_rate || 0) * 100)}%`],
  ].map(([label, value]) => `<div><b>${value}</b><small>${label}</small></div>`).join("") : "";
}

async function createInvite(event) {
  event.preventDefault();
  try {
    const invite = await api("/api/workspace/invites", {method: "POST", body: JSON.stringify({email: $("inviteEmail").value.trim() || null, role: $("inviteRole").value})});
    state.invites = [invite, ...(state.invites || [])];
    $("inviteEmail").value = "";
    renderTeamPanel();
    await copyInvite(invite.token);
  } catch (error) { toast(error.message); }
}

async function copyInvite(token) {
  const url = new URL(location.href);
  url.search = "";
  url.searchParams.set("invite", token);
  await navigator.clipboard?.writeText(url.toString());
  toast("邀请链接已复制");
}

async function revokeInvite(inviteId) {
  try {
    await api(`/api/workspace/invites/${inviteId}`, {method: "DELETE"});
    state.invites = (state.invites || []).filter(row => row.id !== inviteId);
    renderTeamPanel();
  } catch (error) { toast(error.message); }
}

async function updateMemberRole(userId, role) {
  try {
    await api(`/api/workspace/members/${userId}`, {method: "PATCH", body: JSON.stringify({role})});
    await loadTeamPanel();
  } catch (error) { toast(error.message); await loadTeamPanel(); }
}

async function removeMember(userId) {
  try {
    await api(`/api/workspace/members/${userId}`, {method: "DELETE"});
    await loadTeamPanel();
  } catch (error) { toast(error.message); }
}

async function assignTask(userId) {
  if (!state.conversation) return;
  try {
    const row = await api(`/api/conversations/${state.conversation.id}`, {method: "PATCH", body: JSON.stringify({assigned_to: userId || null})});
    state.conversation = {...state.conversation, ...row};
    trackProductEvent("task.handoff", state.conversation.id, {assigned: Boolean(userId)});
    toast("负责人已更新");
  } catch (error) { toast(error.message); await loadTeamPanel(); }
}

function bindUI() {
  document.querySelectorAll(".scene-item").forEach(button => {
    button.onclick = () => {
      const targetScene = button.dataset.scene;
      if (!state.conversation || state.conversation.scene !== targetScene) {
        newConversation(targetScene);
      } else {
        setScene(targetScene);
      }
    };
  });

  document.querySelectorAll(".quick-card").forEach(button => {
    button.onclick = () => {
      $("messageInput").value = button.dataset.prompt || "";
      autoSize();
      $("messageInput").focus();
    };
  });

  document.querySelectorAll(".attach-btn").forEach(button => {
    button.onclick = () => {
      $("fileInput").accept = button.dataset.accept || "*/*";
      $("fileInput").click();
    };
  });

  $("fileInput").onchange = event => {
    uploadFiles([...event.target.files]);
    event.target.value = "";
  };

  $("sendBtn").onclick = sendMessage;
  $("stopBtn").onclick = stopCurrentJob;
  $("messageInput").oninput = autoSize;
  $("messageInput").onkeydown = event => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendMessage();
    }
  };

  $("newTaskBtn").onclick = () => newConversation(state.scene);
  $("refreshConvBtn").onclick = loadConversations;
  let searchTimer;
  $("taskSearch").oninput = () => { clearTimeout(searchTimer); searchTimer = setTimeout(loadConversations, 180); };
  document.querySelectorAll("[data-task-scope]").forEach(button => {
    button.onclick = () => {
      state.scope = button.dataset.taskScope;
      document.querySelectorAll("[data-task-scope]").forEach(item => item.classList.toggle("active", item === button));
      loadConversations();
    };
  });
  $("retryTaskBtn").onclick = retryCurrentTask;
  $("renameTaskBtn").onclick = renameCurrentTask;
  $("pinTaskBtn").onclick = togglePinTask;
  $("archiveTaskBtn").onclick = toggleArchiveTask;
  $("deleteTaskBtn").onclick = requestDeleteTask;
  $("workspaceSwitchBtn").onclick = async () => {
    const menu = $("workspaceMenu");
    menu.hidden = !menu.hidden;
    $("workspaceSwitchBtn").setAttribute("aria-expanded", String(!menu.hidden));
    if (!menu.hidden) await loadWorkspaces();
  };
  document.addEventListener("click", event => {
    if (!$("workspaceMenu").hidden && !$("workspaceMenu").contains(event.target) && !$("workspaceSwitchBtn").contains(event.target)) {
      $("workspaceMenu").hidden = true;
      $("workspaceSwitchBtn").setAttribute("aria-expanded", "false");
    }
  });
  $("assigneeSelect").onchange = event => assignTask(event.target.value);
  $("inviteForm").onsubmit = createInvite;

  document.querySelectorAll(".right-tab").forEach(button => {
    button.onclick = () => {
      document.querySelectorAll(".right-tab").forEach(item => {
        item.classList.toggle("active", item === button);
      });
      document.querySelectorAll(".right-panel").forEach(panel => {
        panel.classList.toggle(
          "active",
          panel.id === `panel-${button.dataset.panel}`
        );
      });
      if (button.dataset.panel === "evidence") trackProductEvent("evidence.open", state.conversation?.id);
      if (button.dataset.panel === "team") loadTeamPanel();
    };
  });

  $("assetLibraryBtn").onclick = () => {
    document.querySelector('[data-panel="assets"]').click();
    toast("已打开当前任务素材");
  };

  $("copyLinkBtn").onclick = async () => {
    try {
      await navigator.clipboard?.writeText(location.href);
      toast("任务链接已复制");
    } catch {
      toast("复制失败，请手动复制地址");
    }
  };


  $("messageList").addEventListener("click", event => {
    const article = event.target.closest(".msg.assistant[data-message-id]");
    if (!article) return;
    const messageId = article.dataset.messageId;
    const copy = event.target.closest("[data-copy-result]");
    if (copy) {
      const text = article.querySelector(".msg-content")?.innerText || "";
      navigator.clipboard?.writeText(text);
      trackProductEvent("result.copy", state.conversation?.id);
      toast("结果已复制");
      return;
    }
    const feedback = event.target.closest("[data-feedback]");
    if (feedback) { saveFeedback(messageId, {verdict: feedback.dataset.feedback}); return; }
    if (event.target.closest("[data-evidence-useful]")) {
      const previous = state.feedback[messageId];
      saveFeedback(messageId, {verdict: previous?.verdict || "correct", evidence_useful: !(previous?.evidence_useful === 1 || previous?.evidence_useful === true)});
      return;
    }
    if (event.target.closest("[data-human-verify]")) {
      const previous = state.feedback[messageId];
      saveFeedback(messageId, {verdict: previous?.verdict || "correct", human_verified: !Boolean(previous?.human_verified)});
    }
  });

  let dragDepth = 0;
  document.addEventListener("dragenter", event => {
    if (!event.dataTransfer?.types?.includes("Files")) return;
    dragDepth += 1;
    $("dropMask").hidden = false;
  });
  document.addEventListener("dragleave", () => {
    dragDepth = Math.max(0, dragDepth - 1);
    if (!dragDepth) $("dropMask").hidden = true;
  });
  document.addEventListener("dragover", event => {
    if (event.dataTransfer?.types?.includes("Files")) event.preventDefault();
  });
  document.addEventListener("drop", event => {
    if (!event.dataTransfer?.files?.length) return;
    event.preventDefault();
    dragDepth = 0;
    $("dropMask").hidden = true;
    uploadFiles([...event.dataTransfer.files]);
  });

  document.addEventListener("keydown", event => {
    if (
      (event.key === "n" || event.key === "N")
      && !event.metaKey
      && !event.ctrlKey
      && !["INPUT", "TEXTAREA"].includes(document.activeElement?.tagName)
    ) {
      if (canEdit()) newConversation(state.scene);
    }
  });
}

async function bootWorkspace() {
  await loadServiceHealth();
  const rows = await loadConversations();
  const requested = new URLSearchParams(location.search).get("conversation");
  if (requested) {
    try {
      await openConversation(requested);
      return;
    } catch {
      toast("任务链接已失效，已打开最近任务");
    }
  }
  if (rows.length) {
    await openConversation(rows[0].id);
  } else if (canEdit()) {
    await newConversation("battle_review");
  } else {
    state.conversation = null;
    state.messages = [];
    state.assets = [];
    toast("当前工作空间还没有任务");
  }
}

async function boot() {
  bindAuth();
  bindUI();
  try {
    const ready = await ensureSession();
    if (ready) await bootWorkspace();
  } catch (error) {
    console.error(error);
    toast("工作台初始化失败");
  }
}

boot();
