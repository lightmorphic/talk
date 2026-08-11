"use strict";

const boot = JSON.parse(document.getElementById("boot").textContent);
const S = boot.s;
const $ = (id) => document.getElementById(id);

function api(path, options = {}) {
  options.headers = Object.assign(
    {"X-Talkin-Token": boot.token}, options.headers || {});
  return fetch(path, options).then((r) => {
    if (!r.ok) throw new Error("HTTP " + r.status);
    return r.json();
  });
}

function post(path, body) {
  return api(path, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body || {}),
  });
}

let toastTimer = null;
function toast(message) {
  const el = $("toast");
  el.textContent = message;
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 2200);
}

/* Icons are drawn inline so nothing external is ever loaded. */
const ICON_DELETE =
  '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" ' +
  'stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true">' +
  '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1l1-13M9 7V4h6v3"/></svg>';
const ICON_TICK =
  '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" ' +
  'stroke="currentColor" stroke-width="2.4" stroke-linecap="round" aria-hidden="true">' +
  '<path d="M4 12.5l5 5L20 6.5"/></svg>';

/* Delete buttons confirm by swapping to a tick, never a popup. */
function armDelete(button, onConfirm) {
  button.innerHTML = ICON_DELETE;
  let armed = false;
  button.addEventListener("click", () => {
    if (!armed) {
      armed = true;
      button.innerHTML = ICON_TICK;
      button.classList.add("confirm");
      setTimeout(() => {
        armed = false;
        button.innerHTML = ICON_DELETE;
        button.classList.remove("confirm");
      }, 3000);
      return;
    }
    onConfirm();
  });
}

/* ---- settings save ---- */

function collectConfig() {
  const out = {};
  document.querySelectorAll("[data-conf]").forEach((el) => {
    out[el.dataset.conf] =
      el.type === "checkbox" ? el.checked : el.value;
  });
  return out;
}

$("save").addEventListener("click", () => {
  const langBefore = document.documentElement.lang;
  const conf = collectConfig();
  post("/api/config", conf).then(() => {
    toast(S["settings.saved"]);
    if (conf.language !== langBefore) location.reload();
  }).catch(() => toast(S["error.generic"]));
});

/* ---- microphone test ---- */

$("mic-test").addEventListener("click", () => {
  const result = $("mic-result");
  result.hidden = false;
  result.className = "statusline";
  result.textContent = S["settings.mic_testing"];
  $("mic-test").disabled = true;
  post("/api/mic-test").then((r) => {
    if (r.peak > 0.01) {
      result.className = "statusline good";
      result.textContent = "✓ " + S["settings.mic_test_heard"] + ": “" +
        (r.text || "…") + "”  (" +
        S["settings.mic_test_level"] + " " + Math.round(r.peak * 100) + "%)";
    } else {
      result.className = "statusline bad";
      result.textContent = "! " + S["settings.mic_test_nothing"];
    }
  }).catch(() => {
    result.className = "statusline bad";
    result.textContent = "! " + S["error.mic"];
  }).finally(() => { $("mic-test").disabled = false; });
});

/* ---- dictionary ---- */

function renderDict(entries) {
  const tbody = document.querySelector("#dict-table tbody");
  tbody.textContent = "";
  $("dict-empty").hidden = entries.length > 0;
  entries.forEach((entry) => {
    const tr = document.createElement("tr");
    const heardTd = document.createElement("td");
    heardTd.textContent = entry.heard;
    const sayTd = document.createElement("td");
    sayTd.textContent = entry.say;
    const actionTd = document.createElement("td");
    actionTd.style.width = "2.4rem";
    const del = document.createElement("button");
    del.type = "button";
    del.className = "iconbtn";
    del.title = S["settings.dict.remove"] || "Remove";
    del.setAttribute("aria-label",
      (S["settings.dict.remove"] || "Remove") + ": " + entry.heard);
    armDelete(del, () => {
      post("/api/dictionary/delete", {heard: entry.heard})
        .then((r) => renderDict(r.entries));
    });
    actionTd.appendChild(del);
    tr.append(heardTd, sayTd, actionTd);
    tbody.appendChild(tr);
  });
}

$("dict-add").addEventListener("click", () => {
  const heard = $("dict-heard").value.trim();
  const say = $("dict-say").value.trim();
  if (!heard || !say) return;
  post("/api/dictionary", {heard, say}).then((r) => {
    $("dict-heard").value = "";
    $("dict-say").value = "";
    renderDict(r.entries);
  });
});

$("dict-import").addEventListener("click", () => $("dict-file").click());
$("dict-file").addEventListener("change", () => {
  const file = $("dict-file").files[0];
  if (!file) return;
  const form = new FormData();
  form.append("file", file);
  api("/api/dictionary/import", {method: "POST", body: form})
    .then((r) => {
      renderDict(r.entries);
      toast(S["settings.dict.imported"]);
    })
    .catch(() => toast(S["settings.dict.import_bad"]));
  $("dict-file").value = "";
});

/* ---- history ---- */

function renderHistory(entries) {
  const tbody = document.querySelector("#history-table tbody");
  tbody.textContent = "";
  $("history-empty").hidden = entries.length > 0;
  entries.forEach((entry) => {
    const tr = document.createElement("tr");
    const when = document.createElement("td");
    when.style.whiteSpace = "nowrap";
    when.style.color = "var(--muted-fg)";
    when.style.fontSize = "0.8125rem";
    when.textContent = new Date(entry.ts * 1000).toLocaleString(
      undefined, {dateStyle: "short", timeStyle: "short"});
    const text = document.createElement("td");
    text.textContent = entry.clean;
    tr.append(when, text);
    tbody.appendChild(tr);
  });
}

/* Text buttons arm in place (turn red), second click confirms. */
function armTextButton(button, onConfirm) {
  let armed = false;
  button.addEventListener("click", () => {
    if (!armed) {
      armed = true;
      button.classList.add("armed");
      setTimeout(() => {
        armed = false;
        button.classList.remove("armed");
      }, 3000);
      return;
    }
    button.classList.remove("armed");
    armed = false;
    onConfirm();
  });
}

armTextButton($("history-clear"), () => {
  post("/api/history/clear").then(() => {
    renderHistory([]);
    toast(S["settings.history.cleared"]);
  });
});

/* ---- maintenance ---- */

$("restart").addEventListener("click", () => {
  post("/api/restart").then(() => toast(S["settings.restarting"]));
});

$("show-log").addEventListener("click", () => {
  const logEl = $("log");
  if (logEl.classList.contains("show")) {
    logEl.classList.remove("show");
    return;
  }
  api("/api/log").then((r) => {
    logEl.textContent = r.lines.join("\n") || "(empty)";
    logEl.classList.add("show");
    logEl.scrollTop = logEl.scrollHeight;
  });
});

/* ---- self-update (Fetch Terminal pattern) ---- */

let latestTag = null;

function setDot(state, tip) {
  const dot = $("update-dot");
  dot.className = "update-dot " + state;
  dot.title = tip;
  dot.setAttribute("aria-label", tip);
}

function checkUpdates() {
  setDot("checking", S["update.checking"]);
  $("update-btn").hidden = true;
  post("/api/update/check").then((r) => {
    if (r.state === "available") {
      latestTag = r.latest;
      setDot("available", S["update.available"] + " " + r.latest);
      const btn = $("update-btn");
      btn.textContent = S["update.button"].replace("{v}", r.latest);
      btn.hidden = false;
    } else if (r.state === "up-to-date") {
      setDot("up-to-date", S["update.uptodate"] + " (v" + r.current + ")");
    } else {
      setDot("error", S["update.error"]);
    }
  }).catch(() => setDot("error", S["update.error"]));
}

$("update-dot").addEventListener("click", checkUpdates);
$("update-btn").addEventListener("click", () => {
  const btn = $("update-btn");
  btn.disabled = true;
  btn.textContent = S["update.installing"];
  post("/api/update/apply", {tag: latestTag}).then((r) => {
    if (!r.ok) {
      btn.disabled = false;
      setDot("error", S["update.error"]);
      checkUpdates();
    }
  }).catch(() => { btn.disabled = false; });
});

/* ---- initial load ---- */

api("/api/dictionary").then((r) => renderDict(r.entries));
api("/api/history").then((r) => renderHistory(r.entries));
checkUpdates();
