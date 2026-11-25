const api = "/api/v1/sessions";
const byId = (id) => document.getElementById(id);
const history = byId("history");
const transcript = byId("transcript");
const messages = byId("messages");
const welcome = byId("welcome");
const composer = byId("composer");
const question = byId("question");
const send = byId("send");
const status = byId("status");
const menu = byId("menu");
const newChat = byId("new-chat");
const examples = [...document.querySelectorAll("[data-question]")];
let sessions = [];
let active = null;
let busy = false;
let needsReload = false;
let turns = [];

function element(tag, className, content) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (content !== undefined) node.textContent = content;
  return node;
}

function appendInlineMarkdown(parent, text) {
  const pattern = /(\*\*[^*\n]+\*\*|`[^`\n]+`|\[[^\]\n]+\]\([^\s)]+\))/g;
  let cursor = 0;
  for (const match of text.matchAll(pattern)) {
    parent.append(document.createTextNode(text.slice(cursor, match.index)));
    const token = match[0];
    if (token.startsWith("**")) {
      parent.append(element("strong", "", token.slice(2, -2)));
    } else if (token.startsWith("`")) {
      parent.append(element("code", "", token.slice(1, -1)));
    } else {
      const split = token.indexOf("](");
      const href = token.slice(split + 2, -1);
      let url;
      try {
        url = new URL(href, window.location.href);
      } catch {
        url = null;
      }
      if (url && ["http:", "https:", "mailto:"].includes(url.protocol)) {
        const link = element("a", "", token.slice(1, split));
        link.href = url.href;
        if (url.protocol !== "mailto:") {
          link.target = "_blank";
          link.rel = "noopener noreferrer";
        }
        parent.append(link);
      } else {
        parent.append(document.createTextNode(token));
      }
    }
    cursor = match.index + token.length;
  }
  parent.append(document.createTextNode(text.slice(cursor)));
}

function tableCells(line) {
  if (!line) return [];
  const row = line.trim().replace(/^\\(?=\|)/, "").replace(/\|\\$/, "|");
  if (!/(?<!\\)\|/.test(row)) return [];
  return row.replace(/^\||(?<!\\)\|$/g, "").split(/(?<!\\)\|/)
    .map((cell) => cell.trim().replace(/\\\|/g, "|"));
}

function renderMarkdown(text) {
  const fragment = document.createDocumentFragment();
  const lines = text.replace(/\r\n?/g, "\n").split("\n");
  let paragraph = [];
  let list = null;

  const flushParagraph = () => {
    if (!paragraph.length) return;
    const node = element("p");
    appendInlineMarkdown(node, paragraph.join("\n"));
    fragment.append(node);
    paragraph = [];
  };
  const closeList = () => { list = null; };

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    const fence = line.match(/^ {0,3}(`{3,}|~{3,})(.*)$/);
    if (fence) {
      flushParagraph();
      closeList();
      const marker = fence[1];
      const closingFence = new RegExp(`^ {0,3}${marker[0]}{${marker.length},}[ \\t]*$`);
      const code = [];
      while (++index < lines.length && !closingFence.test(lines[index])) code.push(lines[index]);
      const pre = element("pre");
      pre.append(element("code", "", code.join("\n")));
      fragment.append(pre);
      continue;
    }
    if (!line.trim()) {
      flushParagraph();
      closeList();
      continue;
    }
    const columns = tableCells(line);
    const separators = columns.length ? tableCells(lines[index + 1]) : [];
    if (columns.length && columns.length === separators.length
        && separators.every((cell) => /^:?-{3,}:?$/.test(cell))) {
      flushParagraph();
      closeList();
      const wrapper = element("div", "message-table");
      const table = element("table");
      const head = element("thead");
      const body = element("tbody");
      const appendRow = (section, cells, tag) => {
        const row = element("tr");
        for (const [column, separator] of separators.entries()) {
          const cell = element(tag);
          if (tag === "th") cell.scope = "col";
          if (separator.endsWith(":")) {
            cell.className = separator.startsWith(":") ? "align-center" : "align-right";
          }
          appendInlineMarkdown(cell, cells[column] ?? "");
          row.append(cell);
        }
        section.append(row);
      };
      appendRow(head, columns, "th");
      index += 1;
      while (index + 1 < lines.length) {
        const cells = tableCells(lines[index + 1]);
        if (!cells.length) break;
        appendRow(body, cells, "td");
        index += 1;
      }
      table.append(head, body);
      wrapper.append(table);
      fragment.append(wrapper);
      continue;
    }
    const heading = line.match(/^ {0,3}(#{1,6})[ \t]+(.+)$/);
    if (heading) {
      flushParagraph();
      closeList();
      const node = element(`h${Math.min(heading[1].length + 1, 4)}`);
      appendInlineMarkdown(node, heading[2]);
      fragment.append(node);
      continue;
    }
    const item = line.match(/^\s*(?:[-*+]|(\d{1,9})[.)])[ \t]+(.+)$/);
    if (item) {
      flushParagraph();
      const tag = item[1] === undefined ? "ul" : "ol";
      if (!list || list.tagName.toLowerCase() !== tag) {
        list = element(tag);
        if (tag === "ol") list.start = Number(item[1]);
        fragment.append(list);
      }
      const node = element("li");
      appendInlineMarkdown(node, item[2]);
      list.append(node);
      continue;
    }
    const quote = line.match(/^>\s?(.*)$/);
    if (quote) {
      flushParagraph();
      closeList();
      const node = element("blockquote");
      appendInlineMarkdown(node, quote[1]);
      fragment.append(node);
      continue;
    }
    if (/^\s*([-*_])(?:\s*\1){2,}\s*$/.test(line)) {
      flushParagraph();
      closeList();
      fragment.append(element("hr"));
      continue;
    }
    closeList();
    paragraph.push(line);
  }
  flushParagraph();
  return fragment;
}

function setBusy(value, message = "") {
  busy = value;
  status.textContent = message;
  newChat.disabled = value;
  send.disabled = value || needsReload;
  question.readOnly = value;
  for (const button of [...history.children, ...examples]) button.disabled = value;
}

async function request(url, options) {
  const response = await fetch(url, options);
  const body = await response.json();
  if (!response.ok) {
    const error = new Error(body.error?.message || "请求失败");
    error.code = body.error?.code;
    throw error;
  }
  return body;
}

function messagePath(id) {
  return `${api}/${encodeURIComponent(id)}/messages`;
}

function toggleMenu(open) {
  byId("sidebar").classList.toggle("open", open);
  menu.setAttribute("aria-expanded", String(open));
}

function renderHistory() {
  history.replaceChildren();
  for (const [index, session] of sessions.entries()) {
    const title = session.title || `对话 ${sessions.length - index}`;
    const button = element("button", "", title);
    button.disabled = busy;
    button.title = title;
    if (session.session_id === active) button.setAttribute("aria-current", "true");
    button.addEventListener("click", () => openChat(session.session_id));
    history.append(button);
  }
}

function renderMessage(turn) {
  const assistant = turn.role === "assistant";
  const article = element("article", assistant ? "message assistant" : "message user");
  if (assistant) {
    const label = element("header");
    const avatar = element("span", "avatar");
    avatar.setAttribute("aria-hidden", "true");
    avatar.append(element("i"), element("b"));
    label.append(avatar, document.createTextNode("Aurora"));
    article.append(label);
  }
  const body = element("div", "message-body");
  if (assistant) {
    body.append(renderMarkdown(turn.content));
  } else {
    body.textContent = turn.content;
  }
  article.append(body);
  if (assistant && turn.retrieval_sources?.length) {
    const details = element("details");
    const list = element("ul");
    for (const { source, heading } of turn.retrieval_sources) {
      list.append(element("li", "", heading ? `${heading} · ${source}` : source));
    }
    details.append(element("summary", "", `检索资料（${turn.retrieval_sources.length}）`), list);
    article.append(details);
  }
  return article;
}

function renderTurns(pending) {
  welcome.hidden = turns.length > 0 || Boolean(pending);
  messages.replaceChildren(...turns.map(renderMessage));
  if (pending) messages.append(renderMessage({ role: "user", content: pending }));
  transcript.scrollTop = transcript.scrollHeight;
}

function rememberTitle() {
  const session = sessions.find(({ session_id }) => session_id === active);
  if (session) session.title = turns.find(({ role }) => role === "user")?.content;
}

async function openChat(id) {
  if (busy) return;
  toggleMenu(false);
  setBusy(true, "正在读取对话…");
  let feedback = "";
  try {
    const { messages: saved } = await request(messagePath(id));
    if (active !== id) question.value = "";
    active = id;
    needsReload = false;
    turns = saved;
    rememberTitle();
    renderHistory();
    renderTurns();
  } catch (error) {
    feedback = error.code ? error.message : "无法读取对话，请稍后重试";
  } finally {
    setBusy(false, feedback);
  }
}

async function createChat() {
  const { session_id } = await request(api, { method: "POST" });
  active = session_id;
  sessions.unshift({ session_id });
  turns = [];
  needsReload = false;
  renderHistory();
  renderTurns();
}

newChat.addEventListener("click", async () => {
  if (busy) return;
  toggleMenu(false);
  setBusy(true, "正在创建对话…");
  let feedback = "";
  try {
    await createChat();
    question.value = "";
  } catch (error) {
    feedback = error.code ? error.message : "无法创建对话，请稍后重试";
  } finally {
    setBusy(false, feedback);
    question.focus();
  }
});

question.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" || event.shiftKey || event.isComposing || event.keyCode === 229) return;
  event.preventDefault();
  composer.requestSubmit();
});

composer.addEventListener("submit", async (event) => {
  event.preventDefault();
  const content = question.value;
  if (busy || needsReload || !content.trim()) return;
  question.value = "";
  setBusy(true, "Aurora 正在回答…");
  let feedback = "";
  let sent = false;
  try {
    if (!active) await createChat();
    renderTurns(content);
    sent = true;
    const reply = await request(messagePath(active), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    });
    turns.push({ role: "user", content }, reply);
  } catch (error) {
    question.value = content;
    feedback = error.code ? error.message : "请求中断，请重试";
    if (error.code === "service_not_ready") feedback += "，请检查本地课件索引和模型配置";
    if (sent && !error.code) {
      // The answer may have been saved before the connection was interrupted.
      try {
        const { messages: saved } = await request(messagePath(active));
        if (saved.length > turns.length) {
          if (saved[turns.length]?.content === content) question.value = "";
          feedback = "连接中断，已重新读取会话历史";
        }
        turns = saved;
      } catch {
        needsReload = true;
        feedback = "无法确认发送结果，请重新打开当前会话查看历史";
      }
    }
  } finally {
    rememberTitle();
    renderHistory();
    renderTurns();
    setBusy(false, feedback);
    question.focus();
  }
});

menu.addEventListener("click", () => toggleMenu(menu.getAttribute("aria-expanded") !== "true"));
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && menu.getAttribute("aria-expanded") === "true") {
    toggleMenu(false);
    menu.focus();
  }
});
for (const button of examples) {
  button.addEventListener("click", () => {
    question.value = button.dataset.question;
    question.focus();
  });
}

async function start() {
  setBusy(true, "正在读取历史会话…");
  let feedback = "";
  try {
    ({ sessions } = await request(api));
    renderHistory();
  } catch {
    feedback = "无法加载历史会话，刷新页面可重试";
  } finally {
    setBusy(false, feedback);
  }
}

start();
