let externalLinkConversationId = null;
let externalLinkLoading = false;

function externalLinkEsc(value) {
  return String(value ?? "").replace(/[&<>"']/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[char]));
}

function externalLinkCurrentConversationId() {
  return new URL(location.href).searchParams.get("conversation") ||
    document.querySelector(".conv-item.active")?.dataset.id || "";
}

function externalLinkCanEdit() {
  return !document.getElementById("newTaskBtn")?.disabled;
}

function parseGithubIssueRef(raw) {
  const value = String(raw || "").trim();
  let match = value.match(/^https:\/\/github\.com\/([A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+)\/issues\/(\d+)(?:[/?#].*)?$/i);
  if (!match) {
    match = value.match(/^([A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+)#(\d+)$/);
  }
  if (!match) return null;
  const issueNumber = Number(match[2]);
  if (!Number.isInteger(issueNumber) || issueNumber < 1) return null;
  return {repository: match[1], issue_number: issueNumber};
}

function externalLinkToast(message) {
  const element = document.getElementById("toast");
  if (!element) return;
  element.textContent = message;
  element.classList.add("show");
  clearTimeout(externalLinkToast.timer);
  externalLinkToast.timer = setTimeout(() => element.classList.remove("show"), 2200);
}

function installExternalLinkStyle() {
  if (document.getElementById("externalIssueLinkStyle")) return;
  const style = document.createElement("style");
  style.id = "externalIssueLinkStyle";
  style.textContent = `
    .external-issue-card{margin:12px 0 16px;padding:13px;border:1px solid rgba(26,39,64,.10);border-radius:14px;background:rgba(255,255,255,.72)}
    .external-issue-head{display:flex;align-items:flex-start;justify-content:space-between;gap:10px}.external-issue-head small{display:block;color:#8b93a2;font-size:9px}.external-issue-head b{display:block;margin-top:2px;font-size:12px}.external-issue-head span{font-size:9px;color:#5961a8;background:#eef1ff;padding:3px 6px;border-radius:999px}
    .external-issue-form{display:flex;gap:6px;margin-top:9px}.external-issue-form input{min-width:0;flex:1;border:1px solid rgba(26,39,64,.12);border-radius:9px;padding:8px 9px;background:#fff;color:#20283a;font:inherit;font-size:10px;outline:none}.external-issue-form input:focus{border-color:#858ceb;box-shadow:0 0 0 2px rgba(105,112,220,.10)}.external-issue-form button{border:0;border-radius:9px;padding:8px 9px;background:#222a42;color:#fff;font-size:10px;font-weight:650;cursor:pointer}.external-issue-form button:disabled{opacity:.45;cursor:not-allowed}
    .external-issue-note{margin:6px 1px 0;color:#858d9d;font-size:8.5px;line-height:1.45}.external-issue-list{display:grid;gap:6px;margin-top:9px}.external-issue-empty{padding:8px;border-radius:9px;background:#f7f8fb;color:#81899a;font-size:9px}.external-issue-row{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px;align-items:center;padding:8px 9px;border-radius:9px;background:#f7f8fb}.external-issue-row a{min-width:0;color:#39415a;text-decoration:none}.external-issue-row a b{display:block;font-size:10px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.external-issue-row a small{display:block;margin-top:2px;color:#858d9d;font-size:8.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.external-issue-remove{border:0;background:transparent;color:#9b5353;cursor:pointer;font-size:9px;padding:4px}.external-issue-remove:disabled{opacity:.4;cursor:not-allowed}
  `;
  document.head.appendChild(style);
}

function ensureExternalLinkCard() {
  let card = document.getElementById("externalIssueLinkCard");
  if (card) return card;
  const panel = document.getElementById("panel-progress");
  if (!panel) return null;
  card = document.createElement("section");
  card.id = "externalIssueLinkCard";
  card.className = "external-issue-card";
  card.innerHTML = `
    <div class="external-issue-head">
      <div><small>工作流关联</small><b>GitHub Issue</b></div>
      <span>显式关联</span>
    </div>
    <form class="external-issue-form" id="externalIssueLinkForm">
      <input id="externalIssueRef" maxlength="500" autocomplete="off" placeholder="owner/repo#123 或粘贴 Issue URL" />
      <button id="externalIssueLinkBtn" type="submit">绑定</button>
    </form>
    <p class="external-issue-note">这里只建立任务关联；当前不会自动评论、改状态或关闭 GitHub Issue。</p>
    <div class="external-issue-list" id="externalIssueList"><div class="external-issue-empty">尚未关联外部 Issue。</div></div>
  `;
  const issueScopeCard = document.getElementById("issueScopeCard");
  if (issueScopeCard) issueScopeCard.insertAdjacentElement("afterend", card);
  else panel.querySelector(".task-state-card")?.insertAdjacentElement("afterend", card);

  card.querySelector("#externalIssueLinkForm")?.addEventListener("submit", async event => {
    event.preventDefault();
    const conversationId = externalLinkCurrentConversationId();
    if (!conversationId) {
      externalLinkToast("先选择一个任务");
      return;
    }
    const parsed = parseGithubIssueRef(document.getElementById("externalIssueRef")?.value);
    if (!parsed) {
      externalLinkToast("请输入 owner/repo#123 或有效的 GitHub Issue URL");
      document.getElementById("externalIssueRef")?.focus();
      return;
    }
    const button = document.getElementById("externalIssueLinkBtn");
    if (button) button.disabled = true;
    try {
      const response = await fetch(`/api/conversations/${encodeURIComponent(conversationId)}/external-links`, {
        method: "POST",
        credentials: "same-origin",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(parsed),
      });
      if (!response.ok) {
        let detail = "绑定失败";
        try { detail = (await response.json()).detail || detail; } catch {}
        throw new Error(detail);
      }
      const input = document.getElementById("externalIssueRef");
      if (input) input.value = "";
      externalLinkToast("GitHub Issue 已关联");
      await loadExternalLinks(true);
    } catch (error) {
      externalLinkToast(error.message || "绑定失败");
    } finally {
      if (button) button.disabled = !externalLinkCanEdit();
    }
  });
  return card;
}

function renderExternalLinks(links) {
  const list = document.getElementById("externalIssueList");
  if (!list) return;
  const canEdit = externalLinkCanEdit();
  const button = document.getElementById("externalIssueLinkBtn");
  const input = document.getElementById("externalIssueRef");
  if (button) button.disabled = !canEdit;
  if (input) input.disabled = !canEdit;
  if (!links?.length) {
    list.innerHTML = '<div class="external-issue-empty">尚未关联外部 Issue。</div>';
    return;
  }
  list.innerHTML = links.map(link => {
    const label = `${link.repository}#${link.external_key}`;
    const title = link.external_title || "已关联 GitHub Issue";
    return `
      <div class="external-issue-row" data-external-link-id="${externalLinkEsc(link.id)}">
        <a href="${externalLinkEsc(link.external_url)}" target="_blank" rel="noopener noreferrer" title="在 GitHub 打开">
          <b>${externalLinkEsc(label)}</b>
          <small>${externalLinkEsc(title)}</small>
        </a>
        <button class="external-issue-remove" type="button" ${canEdit ? "" : "disabled"}>解除</button>
      </div>`;
  }).join("");
  list.querySelectorAll(".external-issue-remove").forEach(button => {
    button.addEventListener("click", async () => {
      const row = button.closest("[data-external-link-id]");
      const linkId = row?.dataset.externalLinkId;
      const conversationId = externalLinkCurrentConversationId();
      if (!linkId || !conversationId) return;
      button.disabled = true;
      try {
        const response = await fetch(
          `/api/conversations/${encodeURIComponent(conversationId)}/external-links/${encodeURIComponent(linkId)}`,
          {method: "DELETE", credentials: "same-origin"},
        );
        if (!response.ok) {
          let detail = "解除失败";
          try { detail = (await response.json()).detail || detail; } catch {}
          throw new Error(detail);
        }
        externalLinkToast("已解除 GitHub Issue 关联");
        await loadExternalLinks(true);
      } catch (error) {
        externalLinkToast(error.message || "解除失败");
        button.disabled = !externalLinkCanEdit();
      }
    });
  });
}

async function loadExternalLinks(force = false) {
  ensureExternalLinkCard();
  const conversationId = externalLinkCurrentConversationId();
  if (!conversationId) {
    externalLinkConversationId = null;
    renderExternalLinks([]);
    return;
  }
  if (externalLinkLoading) return;
  if (!force && externalLinkConversationId === conversationId) return;
  externalLinkLoading = true;
  try {
    const response = await fetch(
      `/api/conversations/${encodeURIComponent(conversationId)}/external-links`,
      {credentials: "same-origin"},
    );
    if (!response.ok) throw new Error("load failed");
    renderExternalLinks(await response.json());
    externalLinkConversationId = conversationId;
  } catch {
    const list = document.getElementById("externalIssueList");
    if (list) list.innerHTML = '<div class="external-issue-empty">暂时无法读取外部关联。</div>';
  } finally {
    externalLinkLoading = false;
  }
}

installExternalLinkStyle();
ensureExternalLinkCard();
loadExternalLinks(true);

new MutationObserver(() => {
  ensureExternalLinkCard();
  const current = externalLinkCurrentConversationId();
  if (current !== externalLinkConversationId) loadExternalLinks(true);
}).observe(document.documentElement, {subtree: true, childList: true});

window.addEventListener("popstate", () => {
  externalLinkConversationId = null;
  loadExternalLinks(true);
});
