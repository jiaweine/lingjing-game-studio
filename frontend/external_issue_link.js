let externalLinkConversationId = null;
let externalLinkLoading = false;
let externalIssueLatestOutcome = null;

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

function latestIssueOutcome(conversation) {
  const messages = conversation?.messages || [];
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message?.role !== "assistant") continue;
    const outcome = message?.payload?.outcome;
    if (!outcome) continue;
    if (outcome.issue_lifecycle || outcome.requires_project_verification) return outcome;
  }
  return null;
}

function externalLinkToast(message) {
  const element = document.getElementById("toast");
  if (!element) return;
  element.textContent = message;
  element.classList.add("show");
  clearTimeout(externalLinkToast.timer);
  externalLinkToast.timer = setTimeout(() => element.classList.remove("show"), 2600);
}

function installExternalLinkStyle() {
  if (document.getElementById("externalIssueLinkStyle")) return;
  const style = document.createElement("style");
  style.id = "externalIssueLinkStyle";
  style.textContent = `
    .external-issue-card{margin:12px 0 16px;padding:13px;border:1px solid rgba(26,39,64,.10);border-radius:14px;background:rgba(255,255,255,.72)}
    .external-issue-head{display:flex;align-items:flex-start;justify-content:space-between;gap:10px}.external-issue-head small{display:block;color:#8b93a2;font-size:9px}.external-issue-head b{display:block;margin-top:2px;font-size:12px}.external-issue-head span{font-size:9px;color:#5961a8;background:#eef1ff;padding:3px 6px;border-radius:999px}
    .external-issue-form{display:flex;gap:6px;margin-top:9px}.external-issue-form input{min-width:0;flex:1;border:1px solid rgba(26,39,64,.12);border-radius:9px;padding:8px 9px;background:#fff;color:#20283a;font:inherit;font-size:10px;outline:none}.external-issue-form input:focus{border-color:#858ceb;box-shadow:0 0 0 2px rgba(105,112,220,.10)}.external-issue-form button{border:0;border-radius:9px;padding:8px 9px;background:#222a42;color:#fff;font-size:10px;font-weight:650;cursor:pointer}.external-issue-form button:disabled{opacity:.45;cursor:not-allowed}
    .external-issue-note{margin:6px 1px 0;color:#858d9d;font-size:8.5px;line-height:1.45}.external-issue-list{display:grid;gap:7px;margin-top:9px}.external-issue-empty{padding:8px;border-radius:9px;background:#f7f8fb;color:#81899a;font-size:9px}.external-issue-row{padding:8px 9px;border-radius:9px;background:#f7f8fb}.external-issue-top{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px;align-items:center}.external-issue-top a{min-width:0;color:#39415a;text-decoration:none}.external-issue-top a b{display:block;font-size:10px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.external-issue-top a small{display:block;margin-top:2px;color:#858d9d;font-size:8.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.external-issue-remove{border:0;background:transparent;color:#9b5353;cursor:pointer;font-size:9px;padding:4px}.external-issue-remove:disabled{opacity:.4;cursor:not-allowed}
    .external-issue-actions{display:flex;flex-wrap:wrap;gap:5px;margin-top:7px;padding-top:7px;border-top:1px solid rgba(26,39,64,.07)}.external-issue-push{border:1px solid rgba(47,57,83,.13);border-radius:8px;padding:5px 7px;background:#fff;color:#39415a;font-size:8.5px;font-weight:600;cursor:pointer}.external-issue-push[data-kind="verification"]{background:#222a42;color:#fff;border-color:#222a42}.external-issue-push:disabled{opacity:.42;cursor:not-allowed}.external-comment-link{margin-left:auto;align-self:center;color:#66708a;text-decoration:none;font-size:8px}.external-sync-state{margin-top:5px;color:#838b9b;font-size:8px}
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
      <span>显式 Push</span>
    </div>
    <form class="external-issue-form" id="externalIssueLinkForm">
      <input id="externalIssueRef" maxlength="500" autocomplete="off" placeholder="owner/repo#123 或粘贴 Issue URL" />
      <button id="externalIssueLinkBtn" type="submit">绑定</button>
    </form>
    <p class="external-issue-note">关联后可显式新增/更新结果评论；不会自动改 label、assignee、状态或关闭 GitHub Issue。发布凭证只存在服务端。</p>
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

function latestComment(link) {
  const comments = link?.meta?.github_comments || {};
  return comments.verification || comments.reproduction || null;
}

async function pushExternalSummary(linkId, kind, button) {
  const conversationId = externalLinkCurrentConversationId();
  if (!conversationId || !linkId) return;
  button.disabled = true;
  try {
    const response = await fetch(
      `/api/conversations/${encodeURIComponent(conversationId)}/external-links/${encodeURIComponent(linkId)}/push`,
      {
        method: "POST",
        credentials: "same-origin",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({kind}),
      },
    );
    if (!response.ok) {
      let detail = "Push 失败";
      try { detail = (await response.json()).detail || detail; } catch {}
      throw new Error(detail);
    }
    const result = await response.json();
    externalLinkToast(result.updated ? "GitHub 评论已更新" : "GitHub 评论已发布");
    await loadExternalLinks(true);
  } catch (error) {
    externalLinkToast(error.message || "Push 失败");
  } finally {
    button.disabled = !externalLinkCanEdit() || (kind === "verification" && !externalIssueLatestOutcome?.verified);
  }
}

function renderExternalLinks(links) {
  const list = document.getElementById("externalIssueList");
  if (!list) return;
  const canEdit = externalLinkCanEdit();
  const verificationReady = Boolean(externalIssueLatestOutcome?.verified);
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
    const comment = latestComment(link);
    const commentLink = comment?.url
      ? `<a class="external-comment-link" href="${externalLinkEsc(comment.url)}" target="_blank" rel="noopener noreferrer">查看已推送评论</a>`
      : "";
    const verificationTitle = verificationReady ? "推送或更新最终验证评论" : "需先形成 Verifier 权威结论";
    return `
      <div class="external-issue-row" data-external-link-id="${externalLinkEsc(link.id)}">
        <div class="external-issue-top">
          <a href="${externalLinkEsc(link.external_url)}" target="_blank" rel="noopener noreferrer" title="在 GitHub 打开">
            <b>${externalLinkEsc(label)}</b>
            <small>${externalLinkEsc(title)}</small>
          </a>
          <button class="external-issue-remove" type="button" ${canEdit ? "" : "disabled"}>解除</button>
        </div>
        <div class="external-sync-state">${externalLinkEsc(link.sync_state === "linked" ? "已关联，尚未 Push" : `同步状态：${link.sync_state}`)}</div>
        <div class="external-issue-actions">
          <button class="external-issue-push" data-kind="reproduction" type="button" ${canEdit ? "" : "disabled"}>推送复现摘要</button>
          <button class="external-issue-push" data-kind="verification" type="button" title="${externalLinkEsc(verificationTitle)}" ${canEdit && verificationReady ? "" : "disabled"}>推送验证结论</button>
          ${commentLink}
        </div>
      </div>`;
  }).join("");

  list.querySelectorAll(".external-issue-push").forEach(pushButton => {
    pushButton.addEventListener("click", () => {
      const row = pushButton.closest("[data-external-link-id]");
      pushExternalSummary(row?.dataset.externalLinkId, pushButton.dataset.kind, pushButton);
    });
  });
  list.querySelectorAll(".external-issue-remove").forEach(removeButton => {
    removeButton.addEventListener("click", async () => {
      const row = removeButton.closest("[data-external-link-id]");
      const linkId = row?.dataset.externalLinkId;
      const conversationId = externalLinkCurrentConversationId();
      if (!linkId || !conversationId) return;
      removeButton.disabled = true;
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
        removeButton.disabled = !externalLinkCanEdit();
      }
    });
  });
}

async function loadExternalLinks(force = false) {
  ensureExternalLinkCard();
  const conversationId = externalLinkCurrentConversationId();
  if (!conversationId) {
    externalLinkConversationId = null;
    externalIssueLatestOutcome = null;
    renderExternalLinks([]);
    return;
  }
  if (externalLinkLoading) return;
  if (!force && externalLinkConversationId === conversationId) return;
  externalLinkLoading = true;
  try {
    const [linksResponse, conversationResponse] = await Promise.all([
      fetch(`/api/conversations/${encodeURIComponent(conversationId)}/external-links`, {credentials: "same-origin"}),
      fetch(`/api/conversations/${encodeURIComponent(conversationId)}`, {credentials: "same-origin"}),
    ]);
    if (!linksResponse.ok) throw new Error("load failed");
    externalIssueLatestOutcome = conversationResponse.ok
      ? latestIssueOutcome(await conversationResponse.json())
      : null;
    renderExternalLinks(await linksResponse.json());
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
  externalIssueLatestOutcome = null;
  loadExternalLinks(true);
});
