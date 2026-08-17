const $ = id => document.getElementById(id);

const state = {
  conversation: null,
  scene: "battle_review",
  providers: [],
  pending: [],
  assets: [],
  messages: [],
  events: [],
  ws: null,
  busy: false,
  progress: [],
  session: null,
  config: null,
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
}

function showAuthModal() {
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
      toast("登录成功");
      await bootWorkspace();
    } catch (error) {
      toast(error.message);
    }
  };

  $("registerForm").onsubmit = async event => {
    event.preventDefault();
    try {
      const session = await api("/api/auth/register", {
        method: "POST",
        body: JSON.stringify({
          name: $("registerName").value.trim(),
          workspace_name: $("registerWorkspace").value.trim(),
          email: $("registerEmail").value.trim(),
          password: $("registerPassword").value,
        }),
      });
      applySession(session);
      hideAuthModal();
      toast("Workspace 已创建");
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

async function loadProviders() {
  try {
    state.providers = await api("/api/providers");
  } catch {
    state.providers = [];
  }
  const select = $("providerSelect");
  if (select) select.innerHTML = '<option value="auto">自动选择</option>';
}

function renderProviderModal() {
  $("providerGrid").innerHTML = state.providers
    .filter(provider => provider.key !== "auto")
    .map(provider => `
      <div class="provider-card">
        <div class="provider-logo">${esc((provider.name || "?").slice(0, 2))}</div>
        <div>
          <b>${esc(provider.name)} <small>${esc(provider.vendor || "")}</small></b>
          <p>${esc(provider.note || "可替换推理服务")}</p>
          <small>
            ${provider.multimodal ? "支持图像 / 多模态" : "文本推理"}
            ${provider.supports_video ? " · 视频" : ""}
            ${provider.supports_audio ? " · 音频" : ""}
            ${provider.model ? ` · ${esc(provider.model)}` : ""}
          </small>
        </div>
        <span class="provider-status ${provider.configured ? "ok" : ""}">
          ${provider.configured ? "可用" : "未配置"}
        </span>
      </div>
    `).join("");
}

async function loadConversations() {
  const rows = await api("/api/conversations");
  $("conversationList").innerHTML = rows.length
    ? rows.map(conversation => `
        <div class="conv-item ${state.conversation?.id === conversation.id ? "active" : ""}"
             data-id="${conversation.id}">
          <span></span>
          <div>
            <b>${esc(conversation.title)}</b>
            <small>${esc(SCENE_NAME[conversation.scene] || conversation.scene)}</small>
          </div>
        </div>
      `).join("")
    : '<div class="empty-side">还没有历史任务。</div>';
  document.querySelectorAll(".conv-item").forEach(item => {
    item.onclick = () => openConversation(item.dataset.id);
  });
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
  setScene(conversation.scene || "battle_review");
  state.ws?.close();
  syncConversationUrl(conversation.id);
  renderConversation();
  connectConversation();
  await loadConversations();
}

function renderConversation() {
  $("welcomePanel").hidden = state.messages.length > 0;
  $("conversationTitle").textContent =
    state.conversation?.title || SCENE_NAME[state.scene];
  $("conversationMeta").textContent = state.messages.length
    ? `${Math.ceil(state.messages.length / 2)} 次任务推进 · ${state.assets.length} 份素材`
    : "把目标说清楚，系统会持续执行到得到可核验结果。";
  $("messageList").innerHTML = state.messages.map(renderMessage).join("");
  renderAssets();
  renderPending();
  renderEventHistory();

  const lastAnswer = [...state.messages].reverse().find(message => message.role === "assistant");
  if (lastAnswer?.payload) {
    renderEvidence(lastAnswer.payload.evidence || []);
    renderSuggestions(lastAnswer.payload.suggestions || []);
  }
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
    <article class="msg assistant">
      <div class="msg-body">
        <div class="msg-label">
          <span class="tag">交付</span>
          <b>执行结果</b>
          <time>${message.created_at ? fmtTime(message.created_at) : ""}</time>
        </div>
        <div class="msg-content">${md(message.content)}</div>
        <div class="answer-foot">
          <button class="answer-action" type="button" data-copy-result>复制结果</button>
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
  const progress = state.events.filter(event => event.type === "progress");
  if (progress.length) {
    state.progress = progress.map(event => event.payload);
    renderProgress();
  } else if (!state.messages.length) {
    state.progress = [];
    renderProgress();
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
  const socket = new WebSocket(
    `${protocol}://${location.host}/ws/conversations/${conversationId}`
  );
  state.ws = socket;
  socket.onmessage = event => {
    try { handleEvent(JSON.parse(event.data)); } catch {}
  };
  socket.onerror = () => {};
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
    state.busy = false;
    $("sendBtn").disabled = false;
    $("thinkingCard").hidden = true;
    const message = event.payload.message;
    state.messages.push(message);
    const result = event.payload.result || {};
    renderConversation();
    renderEvidence(result.evidence || []);
    renderSuggestions(result.suggestions || []);
    $("taskState").textContent = "验证完成";
    $("taskPercent").textContent = "100%";
    $("taskProgress").style.width = "100%";
    $("taskStateHint").textContent = "结果已整理，证据与下一步都已保留。";
    document.querySelector(".task-state-card").className = "task-state-card done";
    loadConversations();
    scrollBottom();
    return;
  }

  if (event.type === "answer.error") {
    state.busy = false;
    $("sendBtn").disabled = false;
    $("thinkingCard").hidden = true;
    $("taskState").textContent = "执行中断";
    $("taskStateHint").textContent = "本次执行没有完成，可以重试或补充要求。";
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

  state.busy = true;
  $("sendBtn").disabled = true;
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
    const serverMessage = response.message;
    if (serverMessage) {
      const index = state.messages.findIndex(item => item.id === optimistic.id);
      if (index >= 0) state.messages[index] = serverMessage;
    }
  } catch (error) {
    state.busy = false;
    $("sendBtn").disabled = false;
    $("thinkingCard").hidden = true;
    state.messages = state.messages.filter(item => item.id !== optimistic.id);
    state.pending = selectedAssets
      .map(id => state.assets.find(asset => asset.id === id))
      .filter(Boolean);
    renderConversation();
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
  $("messageInput").oninput = autoSize;
  $("messageInput").onkeydown = event => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendMessage();
    }
  };

  $("newTaskBtn").onclick = () => newConversation(state.scene);
  $("refreshConvBtn").onclick = loadConversations;

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
    };
  });

  $("providerBtn").onclick = () => {
    $("providerModal").hidden = false;
  };
  $("providerModal").querySelector(".modal-close").onclick = () => {
    $("providerModal").hidden = true;
  };
  $("providerModal").onclick = event => {
    if (event.target === $("providerModal")) $("providerModal").hidden = true;
  };

  $("assetLibraryBtn").onclick = () => {
    document.querySelector('[data-panel="assets"]').click();
    toast("已打开当前任务素材");
  };

  $("shareBtn").onclick = async () => {
    try {
      await navigator.clipboard?.writeText(location.href);
      toast("任务链接已复制");
    } catch {
      toast("复制失败，请手动复制地址");
    }
  };

  $("moreBtn").onclick = () => toast("更多任务操作会在这里集中管理");
  $("settingsBtn").onclick = () => toast("Workspace 设置即将开放");

  $("messageList").addEventListener("click", event => {
    const button = event.target.closest("[data-copy-result]");
    if (!button) return;
    const article = button.closest(".msg");
    const text = article?.querySelector(".msg-content")?.innerText || "";
    navigator.clipboard?.writeText(text);
    toast("结果已复制");
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
      newConversation(state.scene);
    }
  });
}

async function bootWorkspace() {
  await Promise.allSettled([
    loadProviders(),
    loadConversations(),
    loadServiceHealth(),
  ]);
  const rows = await api("/api/conversations");
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
  } else {
    await newConversation("battle_review");
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
