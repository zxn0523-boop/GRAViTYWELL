const messages = document.querySelector("#messages");
const form = document.querySelector("#chat-form");
const input = document.querySelector("#message-input");
const sendButton = document.querySelector("#send");
const restartButton = document.querySelector("#restart");
const modeButtons = [...document.querySelectorAll("[data-mode]")];
const modeHelp = document.querySelector("#mode-help");

let sessionId = sessionStorage.getItem("gravitywell-session");
let meetingMode = sessionStorage.getItem("gravitywell-mode") || "same_city";
let modeRequest = "auto";
let recommendationRound = 0;

const modeCopy = {
  same_city: {
    help: "在同一城市内寻找交通体验较公平的会面地点。",
    greeting: "请告诉我：谁从哪里出发、何时见面、想做什么，以及各自乘公共交通还是驾车。",
    placeholder: "例如：我在回龙观，小李在亦庄文化园……",
  },
  intercity: {
    help: "第一版支持两人；默认只比较双方所在城市，不推荐中间第三座城市。",
    greeting: "请告诉我两个人分别从哪座城市的什么位置出发、何时见面、想做什么，以及各自的交通方式。",
    placeholder: "例如：我在上海徐汇，小王在苏州园区，周六想看展……",
  },
};

function addMessage(role, text, timings = null) {
  const article = document.createElement("article");
  article.className = `message ${role}`;
  const label = document.createElement("b");
  label.textContent = role === "user" ? "你" : "GravityWell";
  const paragraph = document.createElement("p");
  paragraph.textContent = text;
  article.append(label, paragraph);
  if (timings && Object.keys(timings).length) {
    const timingLine = document.createElement("small");
    timingLine.className = "timings";
    const entries = Object.entries(timings);
    entries.sort(([first], [second]) =>
      first === "总耗时" ? -1 : second === "总耗时" ? 1 : 0
    );
    timingLine.textContent = "测试耗时 · " + entries.map(
      ([name, milliseconds]) => `${name} ${formatDuration(milliseconds)}`
    ).join(" · ");
    article.append(timingLine);
  }
  messages.append(article);
  article.scrollIntoView({ behavior: "smooth", block: "end" });
  return article;
}

function addThinkingMessage(userText) {
  const isRouteCalculation = /^(确认|正确|是|是的|对|对的|没错|没问题|地址正确|地点正确|可以)[，,。.!！?？\s]*$/.test(userText);
  const stages = isRouteCalculation
    ? [
        [0, "正在查询候选场所、路线和天气"],
        [6000, "正在建立多人路线矩阵"],
        [15000, "正在比较交通体验并整理结果"],
      ]
    : [
        [0, "正在理解你的需求"],
        [4000, "正在整理地点、时间与偏好"],
        [10000, "仍在处理中，请稍候"],
      ];

  const article = document.createElement("article");
  article.className = "message assistant thinking-message";
  article.setAttribute("role", "status");
  const label = document.createElement("b");
  label.textContent = "GravityWell";
  const paragraph = document.createElement("p");
  const stageText = document.createElement("span");
  stageText.textContent = stages[0][1];
  const dots = document.createElement("span");
  dots.className = "thinking-dots";
  dots.setAttribute("aria-hidden", "true");
  for (let index = 0; index < 3; index += 1) {
    dots.append(document.createElement("i"));
  }
  paragraph.append(stageText, dots);
  article.append(label, paragraph);
  messages.append(article);
  article.scrollIntoView({ behavior: "smooth", block: "end" });

  const timers = stages.slice(1).map(([delay, text]) => window.setTimeout(() => {
    stageText.textContent = text;
  }, delay));
  return {
    remove() {
      timers.forEach(window.clearTimeout);
      article.classList.add("thinking-exit");
      window.setTimeout(() => article.remove(), 160);
    },
  };
}

function renderCandidates(items, messageArticle) {
  if (!items.length || !messageArticle) return;
  recommendationRound += 1;
  messageArticle.classList.add("with-candidates");
  const group = document.createElement("section");
  group.className = "candidate-round";
  const heading = document.createElement("h2");
  heading.className = "round-heading";
  heading.textContent = `第 ${recommendationRound} 轮推荐`;
  group.append(heading);
  items.forEach((item, index) => {
    const card = document.createElement("article");
    card.className = "candidate";
    const routeHtml = item.routes.map(route =>
      `<p><strong>${escapeHtml(route.participant_name)}</strong>：${escapeHtml(route.summary)}</p>`
    ).join("");
    const warningHtml = item.warnings.map(warning =>
      `<p class="warning">注意：${escapeHtml(warning)}</p>`
    ).join("");
    const weatherHtml = item.weather
      ? `<p class="weather">天气：${escapeHtml(item.weather.date)} · ${escapeHtml(item.weather.day_weather || "未知")} / ${escapeHtml(item.weather.night_weather || "未知")} · ${escapeHtml(item.weather.night_temperature || "?")}～${escapeHtml(item.weather.day_temperature || "?")}℃</p>`
      : "";
    const breakdownHtml = Object.entries(item.score_breakdown || {}).map(
      ([label, score]) => `<span>${escapeHtml(label)} ${escapeHtml(score)}</span>`
    ).join("");
    const kindLabels = { venue: "室内场所", park: "公园", district: "街区/公共空间", attraction: "景点/文化场所" };
    const metaParts = [item.address, kindLabels[item.place_kind] || item.type_name || "场所"];
    if (item.meeting_city) metaParts.unshift(`会面城市 ${item.meeting_city}`);
    if (item.map_rating != null) metaParts.push(`高德评分 ${item.map_rating}`);
    const openingHtml = item.opening_hours
      ? `<p class="opening">营业时间：${escapeHtml(item.opening_hours)}${item.opening_verified ? " · 已核验计划到达后至少营业 1 小时" : ""}</p>`
      : "";
    const gatewayHtml = item.meeting_city
      ? `<p class="gateway"><strong>邻城方案：</strong>在 ${escapeHtml(item.meeting_city)} 见面${item.gateway_name ? ` · 参考到达门户 ${escapeHtml(item.gateway_name)}` : ""}</p>`
      : "";
    card.innerHTML = `
      <span class="score">${item.score} 分</span>
      <h2>${index + 1}. ${escapeHtml(item.name)}</h2>
      <p class="meta">${metaParts.map(escapeHtml).join(" · ")}</p>
      ${gatewayHtml}
      <div class="breakdown">${breakdownHtml}</div>
      <div class="routes">${routeHtml}</div>
      ${weatherHtml}
      ${openingHtml}
      ${item.recommendation_reason ? `<p class="reason">${escapeHtml(item.recommendation_reason)}</p>` : ""}
      ${warningHtml}
      <button class="accept" data-accept>采纳这个结果并清空会话</button>`;
    card.querySelector("[data-accept]").addEventListener("click", acceptResult);
    group.append(card);
  });
  messageArticle.append(group);
  group.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function sendMessage(text) {
  setBusy(true);
  addMessage("user", text);
  const thinkingMessage = addThinkingMessage(text);
  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, mode: modeRequest, message: text }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "请求失败");
    if (modeRequest !== "auto" && data.mode && data.mode !== "auto" && data.mode !== modeRequest) {
      modeRequest = "auto";
    }
    if (data.mode && data.mode !== "auto" && data.mode !== meetingMode) {
      meetingMode = data.mode;
      sessionStorage.setItem("gravitywell-mode", meetingMode);
      applyModeCopy();
    }
    sessionId = data.session_id;
    if (sessionId) sessionStorage.setItem("gravitywell-session", sessionId);
    else sessionStorage.removeItem("gravitywell-session");
    thinkingMessage.remove();
    const assistantMessage = addMessage("assistant", data.reply, data.timings_ms);
    renderCandidates(data.candidates || [], assistantMessage);
  } catch (error) {
    thinkingMessage.remove();
    addMessage("assistant", `没有完成这一步：${error.message}`);
  } finally {
    setBusy(false);
  }
}

async function acceptResult() {
  if (!sessionId) return;
  setBusy(true);
  try {
    const response = await fetch(`/api/sessions/${sessionId}/accept`, { method: "POST" });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "采纳失败");
    addMessage("assistant", data.reply);
    document.querySelectorAll("[data-accept]").forEach(button => {
      button.disabled = true;
      button.textContent = "本次会话已结束";
    });
    sessionStorage.removeItem("gravitywell-session");
    sessionId = null;
    modeRequest = "auto";
  } catch (error) {
    addMessage("assistant", error.message);
  } finally {
    setBusy(false);
  }
}

async function restart() {
  if (sessionId) await fetch(`/api/sessions/${sessionId}`, { method: "DELETE" });
  sessionId = null;
  sessionStorage.removeItem("gravitywell-session");
  modeRequest = "auto";
  meetingMode = "same_city";
  sessionStorage.setItem("gravitywell-mode", meetingMode);
  messages.replaceChildren();
  recommendationRound = 0;
  applyModeCopy();
  addMessage("assistant", modeCopy[meetingMode].greeting);
}

async function switchMode(nextMode) {
  if (nextMode === meetingMode) return;
  setBusy(true);
  try {
    if (sessionId) await fetch(`/api/sessions/${sessionId}`, { method: "DELETE" });
    meetingMode = nextMode;
    modeRequest = nextMode;
    sessionId = null;
    recommendationRound = 0;
    sessionStorage.removeItem("gravitywell-session");
    sessionStorage.setItem("gravitywell-mode", meetingMode);
    messages.replaceChildren();
    applyModeCopy();
    addMessage("assistant", modeCopy[meetingMode].greeting);
  } finally {
    setBusy(false);
  }
}

function applyModeCopy() {
  modeButtons.forEach(button => {
    button.classList.toggle("active", button.dataset.mode === meetingMode);
    button.setAttribute("aria-pressed", String(button.dataset.mode === meetingMode));
  });
  modeHelp.textContent = modeCopy[meetingMode].help;
  input.placeholder = modeCopy[meetingMode].placeholder;
  if (!sessionId && recommendationRound === 0) {
    const initialMessage = messages.querySelector(".message.assistant p");
    if (initialMessage) initialMessage.textContent = modeCopy[meetingMode].greeting;
  }
}

function setBusy(busy) {
  sendButton.disabled = busy;
  input.disabled = busy;
  restartButton.disabled = busy;
  modeButtons.forEach(button => { button.disabled = busy; });
  sendButton.textContent = busy ? "处理中" : "发送";
}

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"
  })[char]);
}

function formatDuration(milliseconds) {
  return milliseconds >= 1000
    ? `${(milliseconds / 1000).toFixed(2)} 秒`
    : `${milliseconds} 毫秒`;
}

form.addEventListener("submit", event => {
  event.preventDefault();
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  sendMessage(text);
});

restartButton.addEventListener("click", restart);
modeButtons.forEach(button => {
  button.addEventListener("click", () => switchMode(button.dataset.mode));
});

applyModeCopy();
