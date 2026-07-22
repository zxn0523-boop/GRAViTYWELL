const messages = document.querySelector("#messages");
const form = document.querySelector("#chat-form");
const input = document.querySelector("#message-input");
const sendButton = document.querySelector("#send");
const restartButton = document.querySelector("#restart");

let sessionId = sessionStorage.getItem("gravitywell-session");
let recommendationRound = 0;

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
    if (item.map_rating != null) metaParts.push(`高德评分 ${item.map_rating}`);
    const openingHtml = item.opening_hours
      ? `<p class="opening">营业时间：${escapeHtml(item.opening_hours)}${item.opening_verified ? " · 已核验计划到达后至少营业 1 小时" : ""}</p>`
      : "";
    card.innerHTML = `
      <span class="score">${item.score} 分</span>
      <h2>${index + 1}. ${escapeHtml(item.name)}</h2>
      <p class="meta">${metaParts.map(escapeHtml).join(" · ")}</p>
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
  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, message: text }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "请求失败");
    sessionId = data.session_id;
    if (sessionId) sessionStorage.setItem("gravitywell-session", sessionId);
    else sessionStorage.removeItem("gravitywell-session");
    const assistantMessage = addMessage("assistant", data.reply, data.timings_ms);
    renderCandidates(data.candidates || [], assistantMessage);
  } catch (error) {
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
  messages.replaceChildren();
  recommendationRound = 0;
  addMessage("assistant", "本次会话已清空。请重新告诉我参与者、出发地、时间和想做的事。");
}

function setBusy(busy) {
  sendButton.disabled = busy;
  input.disabled = busy;
  restartButton.disabled = busy;
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
