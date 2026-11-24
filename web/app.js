"use strict";
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const STAGES = ["probe", "download", "analyze", "transcribe", "render", "done"];

const state = { config: null, jobs: [], current: null, tab: "url", file: null, box: [0, 0.62, 0.28, 0.38], pollTimer: null };

async function api(path, opts = {}) {
  const res = await fetch(path, { headers: opts.body && !(opts.body instanceof FormData) ? { "Content-Type": "application/json" } : {}, ...opts });
  const text = await res.text();
  let data; try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!res.ok) throw new Error((data && data.detail && (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail))) || res.statusText);
  return data;
}

// "1:02:03" / "2:03" / "123.5" -> seconds (NaN if unreadable)
const parseTime = (v) => {
  v = String(v || "").trim(); if (!v) return NaN;
  if (!v.includes(":")) return Number(v);
  const parts = v.split(":").map(Number); if (parts.some((p) => !Number.isFinite(p) || p < 0)) return NaN;
  return parts.reduce((acc, p) => acc * 60 + p, 0);
};
const fmtTime = (s) => { s = Math.max(0, Math.round(s)); const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), x = s % 60; return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(x).padStart(2, "0"); };
const ago = (t) => { const d = Date.now() / 1000 - t; if (d < 60) return "just now"; if (d < 3600) return Math.floor(d / 60) + "m ago"; if (d < 86400) return Math.floor(d / 3600) + "h ago"; return Math.floor(d / 86400) + "d ago"; };

// ------------------------------------------------------------- form
async function loadConfig() {
  const c = await api("api/config");
  state.config = c;
  const d = c.defaults;
  $("#count").value = d.count; $("#min_seconds").value = d.min_seconds; $("#max_seconds").value = d.max_seconds;
  $("#captions").checked = d.captions; $("#credit_overlay").checked = d.credit_overlay;
  $("#scene_detection").checked = d.scene_detection; $("#auto_upload").checked = d.auto_upload;
  $("#privacy").value = d.privacy; $("#fps").value = String(d.fps);
  $("#whisper_model").innerHTML = c.whisper_models.map((m) => `<option value="${m}">${m}</option>`).join("");
  $("#whisper_model").value = d.whisper_model;
  const radio = $(`input[name=layout][value=${d.layout}]`); if (radio) radio.checked = true;
  state.box = d.facecam_box.slice();
  $("#chunk_minutes").value = d.chunk_minutes;
  syncBox(); syncLayout(); syncLive(); renderYt();
}

function syncLive() {
  const on = $("#live").checked, d = state.config && state.config.defaults;
  $$(".live-only").forEach((el) => el.classList.toggle("hidden", !on));
  $("#count-label").textContent = on ? "Clips per chunk" : "Clips";
  if (d) $("#count").value = on ? d.clips_per_chunk : d.count;
  $("#submit").textContent = on ? "🔴 Start live clipping" : "Find highlights & render";
}

function renderYt() {
  const y = state.config.youtube;
  const pill = $("#yt-pill");
  pill.textContent = y.authorized ? `YouTube ✓ ${y.uploads_24h}/${y.daily_cap} today` : "YouTube: not connected";
  pill.classList.toggle("on", y.authorized);
  $("#yt-status").textContent = y.authorized
    ? `Connected. ${y.uploads_24h} of ${y.daily_cap} daily uploads used.`
    : (y.client_secret ? "OAuth client found. Run `python -m clipper auth-youtube` (or `docker compose run --service-ports clipper auth-youtube`) to connect." : "Not set up. Follow README → YouTube upload to add data/client_secret.json, then run the auth command.");
  $("#auto_upload").disabled = !y.authorized;
  if (!y.authorized) $("#auto_upload").checked = false;
  renderIg();
}

function renderIg() {
  const ig = state.config.instagram || {};
  const pill = $("#ig-pill");
  pill.textContent = ig.connected ? `Instagram ✓ @${ig.username || "?"} ${ig.uploads_24h}/${ig.daily_cap}` : "Instagram: not connected";
  pill.classList.toggle("on", !!ig.connected);
  $("#ig-status").textContent = ig.connected
    ? `Connected as @${ig.username || "?"} (Facebook Page: ${ig.page || "?"}). ${ig.uploads_24h} of ${ig.daily_cap} daily Reels used.`
    : "Not connected. Without it you can still Download a reel, Copy caption, and post it from your phone or instagram.com.";
  $("#ig-connect").classList.toggle("hidden", !!ig.connected);
  $("#ig-connected").classList.toggle("hidden", !ig.connected);
}

async function connectIg() {
  const msg = $("#ig-connect-msg"); msg.className = "small"; msg.textContent = "Connecting…";
  try {
    await api("api/instagram/connect", { method: "POST", body: JSON.stringify({
      app_id: $("#ig-app-id").value.trim(), app_secret: $("#ig-app-secret").value.trim(), user_token: $("#ig-token").value.trim() }) });
    $("#ig-app-secret").value = ""; $("#ig-token").value = "";
    state.config = await api("api/config"); renderYt();
    msg.className = "small ok"; msg.textContent = "Connected ✓";
    lastRenderedClips = ""; renderJob();
  } catch (e) { msg.className = "small err"; msg.textContent = e.message; }
}

// Instagram caption = the description, with at most 30 hashtags and 2200 characters (same rule as the server)
function igCaption(text) {
  let n = 0;
  text = (text || "").replace(/(^|[^\w])#\w+/g, (m) => (++n <= 30 ? m : m.replace(/#\w+/, ""))).replace(/[ \t]{2,}/g, " ").trim();
  return text.length > 2200 ? text.slice(0, 2199).replace(/\s+\S*$/, "") + "…" : text;
}

function syncLayout() {
  const layout = ($("input[name=layout]:checked") || {}).value;
  $("#facecam-box").classList.toggle("hidden", layout !== "facecam");
}

function syncBox() {
  const [x, y, w, h] = state.box;
  $$("#facecam-box .grid4 input").forEach((i) => (i.value = state.box[+i.dataset.k]));
  Object.assign($("#cam-rect").style, { left: x * 100 + "%", top: y * 100 + "%", width: w * 100 + "%", height: h * 100 + "%" });
}

function bindForm() {
  $$(".tab").forEach((t) => t.addEventListener("click", () => {
    state.tab = t.dataset.tab;
    $$(".tab").forEach((x) => x.classList.toggle("active", x === t));
    $$(".tab-panel").forEach((p) => p.classList.toggle("hidden", p.dataset.panel !== state.tab));
  }));
  $$("input[name=layout]").forEach((r) => r.addEventListener("change", syncLayout));
  $("#live").addEventListener("change", syncLive);
  $$("#facecam-box .chip").forEach((c) => c.addEventListener("click", () => { state.box = c.dataset.box.split(",").map(Number); syncBox(); }));
  $$("#facecam-box .grid4 input").forEach((i) => i.addEventListener("input", () => {
    const v = Math.min(1, Math.max(0, parseFloat(i.value) || 0)); state.box[+i.dataset.k] = v; syncBox();
  }));
  const drop = $("#drop");
  const setFile = (f) => { state.file = f; $("#drop-text").textContent = f ? `${f.name} · ${(f.size / 1e6).toFixed(0)} MB` : "Drop a stream recording here or browse"; };
  $("#file").addEventListener("change", (e) => setFile(e.target.files[0]));
  ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => { if (e.dataTransfer.files[0]) setFile(e.dataTransfer.files[0]); });
  $("#job-form").addEventListener("submit", submitJob);
}

function options() {
  const num = (id) => { const v = parseFloat($(id).value); return Number.isFinite(v) ? v : null; };
  return {
    count: num("#count"), min_seconds: num("#min_seconds"), max_seconds: num("#max_seconds"),
    layout: ($("input[name=layout]:checked") || {}).value || null,
    facecam_box: state.box, fps: parseInt($("#fps").value, 10), captions: $("#captions").checked, whisper_model: $("#whisper_model").value,
    credit_overlay: $("#credit_overlay").checked, scene_detection: $("#scene_detection").checked,
    streamer: $("#streamer").value.trim() || null, auto_upload: $("#auto_upload").checked, privacy: $("#privacy").value,
  };
}

async function submitJob(e) {
  e.preventDefault();
  const msg = $("#form-msg"); msg.className = "form-msg"; msg.textContent = "";
  const btn = $("#submit"); btn.disabled = true;
  try {
    let res;
    if (state.tab === "url") {
      const url = $("#url").value.trim();
      if (!url) throw new Error("Paste a VOD URL first.");
      const live = $("#live").checked;
      const extra = live ? { live: true, chunk_minutes: parseInt($("#chunk_minutes").value, 10) || null } : {};
      res = await api("api/jobs", { method: "POST", body: JSON.stringify({ url, ...options(), ...extra }) });
      $("#url").value = "";
    } else {
      if (!state.file) throw new Error("Choose a video file first.");
      const fd = new FormData(); fd.append("file", state.file); fd.append("options", JSON.stringify(options()));
      msg.textContent = "Uploading…";
      res = await uploadWithProgress(fd, (p) => (msg.textContent = `Uploading… ${Math.round(p * 100)}%`));
    }
    msg.className = "form-msg ok"; msg.textContent = $("#live").checked && state.tab === "url" ? "Live clipping started 🔴" : "Job queued 🚀";
    await refreshJobs(); openJob(res.id);
  } catch (err) {
    msg.className = "form-msg err"; msg.textContent = err.message;
  } finally { btn.disabled = false; }
}

function uploadWithProgress(fd, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "api/jobs/upload");
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    xhr.onload = () => { let d; try { d = JSON.parse(xhr.responseText); } catch { d = {}; } xhr.status < 300 ? resolve(d) : reject(new Error(d.detail || xhr.statusText)); };
    xhr.onerror = () => reject(new Error("Upload failed"));
    xhr.send(fd);
  });
}

// ------------------------------------------------------------- jobs list
async function refreshJobs() {
  state.jobs = await api("api/jobs");
  const ul = $("#job-list"); ul.innerHTML = "";
  $("#jobs-empty").classList.toggle("hidden", state.jobs.length > 0);
  $("#jobs-count").textContent = state.jobs.length ? `${state.jobs.length} total` : "";
  for (const j of state.jobs) {
    const li = document.createElement("li");
    li.className = "job" + (state.current === j.id ? " active" : "");
    const title = j.title || j.options.filename || j.source.replace(/^https?:\/\/(www\.)?/, "");
    const isLive = j.options.mode === "live" && j.status === "running";
    li.innerHTML = `<span class="t"></span><span class="badge ${isLive ? "live" : j.status}">${isLive ? "live" : j.status}</span><span class="m"></span>` +
      (j.status === "running" && !isLive ? `<span class="mini"><i style="width:${(j.progress * 100).toFixed(1)}%"></i></span>` : "");
    $(".t", li).textContent = title;
    $(".m", li).textContent = `${ago(j.created_at)} · ${j.error || j.message || ""}`;
    li.addEventListener("click", () => openJob(j.id));
    ul.appendChild(li);
  }
}

// ------------------------------------------------------------- job detail
let lastRenderedClips = "";
async function openJob(id, scroll = true) {
  if (state.current !== id) lastRenderedClips = "";
  state.current = id;
  $$(".job").forEach((el, i) => el.classList.toggle("active", state.jobs[i] && state.jobs[i].id === id));
  $("#detail").classList.remove("hidden");
  await renderJob();
  if (scroll && window.innerWidth < 980) $("#detail").scrollIntoView({ behavior: "smooth" });
}

async function renderJob() {
  if (!state.current) return;
  let j;
  try { j = await api(`api/jobs/${state.current}`); } catch { return; }
  const m = j.manifest;
  $("#d-title").textContent = j.title || (m && m.info.title) || j.options.filename || "Job";
  const session = j.options.mode === "live";
  const chunkOf = j.options.live && j.options.live_session;
  $("#d-sub").textContent = [m && m.streamer && "Credit: " + m.streamer.name,
    session ? "Live session" : chunkOf ? "Live chunk · stream " + j.options.live.label : m && fmtTime(m.info.duration) + " VOD",
    j.source.startsWith("http") ? j.source : null].filter(Boolean).join(" · ");
  $("#d-bar").style.width = (j.status === "done" ? 100 : j.progress * 100) + "%";
  $("#d-bar").classList.toggle("live", session && j.status === "running");
  $("#d-stages").classList.toggle("hidden", session);
  const idx = STAGES.indexOf(j.status === "done" ? "done" : j.stage);
  $$("#d-stages li").forEach((li, i) => { li.className = i < idx || j.status === "done" ? "done" : i === idx && j.status === "running" ? "now" : ""; });
  renderChunks(session ? j : null);
  const msg = $("#d-msg");
  msg.className = "status-msg" + (j.status === "failed" ? " err" : "");
  msg.textContent = j.status === "failed" ? "✘ " + (j.error || "Failed") : j.message || "";
  const warn = (m && m.warnings) || [];
  if (warn.length) { msg.classList.add("warn"); msg.textContent += "\n⚠ " + warn.join("\n⚠ "); }
  $("#d-logs").textContent = (j.logs || []).join("\n");

  const act = $("#d-actions"); act.innerHTML = "";
  const mk = (label, cls, fn) => { const b = document.createElement("button"); b.className = "btn ghost " + cls; b.textContent = label; b.onclick = fn; act.appendChild(b); };
  if (session && j.status === "running") mk(j.cancel ? "Stopping…" : "■ Stop", "danger", async () => {
    if (!confirm("Stop recording? The part recorded since the last chunk still gets clipped.")) return;
    await api(`api/jobs/${j.id}/cancel`, { method: "POST" }); tick();
  });
  else if (j.status === "running" || j.status === "queued") mk("Cancel", "danger", async () => { await api(`api/jobs/${j.id}/cancel`, { method: "POST" }); tick(); });
  if (j.status === "failed" || j.status === "cancelled") mk("Retry", "", async () => { try { const r = await api(`api/jobs/${j.id}/retry`, { method: "POST" }); await refreshJobs(); openJob(r.id); } catch (e) { alert(e.message); } });
  if (j.status !== "running") mk("Delete", "danger", async () => { if (!confirm("Delete this job and its clips?")) return; await api(`api/jobs/${j.id}`, { method: "DELETE" }); state.current = null; $("#detail").classList.add("hidden"); refreshJobs(); });

  if (m) { $("#chart-wrap").classList.remove("hidden"); drawChart(m); } else $("#chart-wrap").classList.add("hidden");
  renderClips(j);
}

function renderChunks(session) {
  const box = $("#d-chunks"), ul = $("ul", box);
  const chunks = session ? state.jobs.filter((x) => x.options.live_session === session.id).reverse() : [];
  box.classList.toggle("hidden", !session);
  if (!session) return;
  ul.innerHTML = chunks.length ? "" : `<li class="muted small">The first chunk is sent after ${state.config.defaults.chunk_minutes}+ min of recording.</li>`;
  for (const c of chunks) {
    const li = document.createElement("li"); li.className = "job";
    li.innerHTML = `<span class="t"></span><span class="badge ${c.status}">${c.status}</span><span class="m"></span>`;
    $(".t", li).textContent = "Stream " + c.options.live.label;
    $(".m", li).textContent = c.error || c.message || "";
    li.addEventListener("click", () => openJob(c.id));
    ul.appendChild(li);
  }
}

function renderClips(j) {
  const m = j.manifest, wrap = $("#clips");
  const uploads = j.uploads || [];
  const key = JSON.stringify([j.id, m && m.clips.map((c) => [c.name, c.rendered_at || 0])]);
  if (!m) { wrap.innerHTML = ""; lastRenderedClips = ""; return; }
  if (key !== lastRenderedClips) {
    lastRenderedClips = key; wrap.innerHTML = "";
    const land = m.settings.render.layout === "landscape";
    m.clips.forEach((c) => {
      const el = $("#clip-tpl").content.firstElementChild.cloneNode(true);
      el.dataset.clip = c.name; if (land) el.classList.add("land");
      const base = `media/${j.id}/clips/`;
      const ver = c.rendered_at ? `?v=${Math.round(c.rendered_at)}` : "";   // new file after a re-cut
      const v = $("video", el); v.src = base + c.name + ".mp4" + ver; if (c.thumbnail) v.poster = base + c.name + ".jpg" + ver;
      bindTrim(j, c, el, v);
      $(".score", el).textContent = `🔥 ${c.score.toFixed(1)} · ${fmtTime(c.stream_start ?? c.start)} · ${Math.round(c.duration)}s`;
      const src = $(".src", el); if (c.source_link) src.href = c.source_link; else src.remove();
      $(".c-title", el).value = c.title; $(".c-desc", el).value = c.description;
      $(".transcript", el).textContent = c.transcript ? `“${c.transcript}”` : "";
      const dl = $(".dl", el); dl.href = base + c.name + ".mp4"; dl.download = `${(c.title || c.name).replace(/[^\w\- ]+/g, "").trim().slice(0, 60) || c.name}.mp4`;
      $(".save", el).onclick = () => saveClip(j.id, c.name, el);
      $(".yt", el).onclick = async () => {
        await saveClip(j.id, c.name, el, true);
        try { await api(`api/jobs/${j.id}/clips/${c.name}/youtube`, { method: "POST" }); setUp(el, "Queued for upload…", ""); }
        catch (e) { setUp(el, e.message, "err"); }
      };
      $(".yt", el).disabled = !state.config.youtube.authorized;
      $(".yt", el).title = state.config.youtube.authorized ? "Upload to YouTube" : "Connect YouTube first (see Settings)";
      const igOn = !!(state.config.instagram || {}).connected;
      $(".ig", el).disabled = !igOn;
      $(".ig", el).title = igOn ? "Publish as a Reel on Instagram (public, posts right away)" : "Connect Instagram first (see Settings)";
      $(".ig", el).onclick = async () => {
        if (!confirm(`Publish "${$(".c-title", el).value}" as a public Reel on @${state.config.instagram.username}? It goes live right away.`)) return;
        await saveClip(j.id, c.name, el, true);
        try { await api(`api/jobs/${j.id}/clips/${c.name}/instagram`, { method: "POST" }); setStatus(el, ".ig-status", "Queued for Instagram…", ""); tick(); }
        catch (e) { setStatus(el, ".ig-status", e.message, "err"); }
      };
      $(".copy-cap", el).onclick = async () => {
        const text = igCaption($(".c-desc", el).value);
        try { await navigator.clipboard.writeText(text); setStatus(el, ".ig-status", "Caption copied ✓ (paste it in Instagram)", "ok"); }
        catch { prompt("Copy the caption:", text); }
      };
      wrap.appendChild(el);
    });
  }
  for (const el of $$(".clip", wrap)) {
    const mine = uploads.filter((x) => x.clip === el.dataset.clip);
    const u = mine.filter((x) => (x.platform || "youtube") === "youtube").pop();
    if (u) {
      if (u.status === "done") setUp(el, "", "ok", u.url);
      else if (u.status === "failed") setUp(el, "✘ YouTube: " + u.error, "err");
      else setUp(el, u.status === "uploading" ? "Uploading to YouTube…" : "Queued for YouTube…", "");
    }
    const g = mine.filter((x) => x.platform === "instagram").pop();
    if (g) {
      if (g.status === "done") setStatus(el, ".ig-status", "", "ok", g.url, "✔ Live on Instagram ↗");
      else if (g.status === "failed") setStatus(el, ".ig-status", "✘ " + g.error, "err");
      else setStatus(el, ".ig-status", g.status === "uploading" ? (g.message || "Uploading to Instagram…") : "Queued for Instagram…", "");
    }
  }
}

function setStatus(el, sel, text, cls, url, linkText) {
  const p = $(sel, el); p.className = `small ${sel.slice(1)} ${cls}`; p.textContent = text;
  if (url) { const a = document.createElement("a"); a.href = url; a.target = "_blank"; a.rel = "noopener"; a.textContent = linkText || url; p.appendChild(a); }
}

function bindTrim(j, c, el, video) {
  const t0 = c.stream_start ?? c.start;            // clip start on the stream/VOD timeline
  const startI = $(".t-start", el), endI = $(".t-end", el), len = $(".t-len", el);
  startI.value = fmtTime(t0); endI.value = fmtTime(t0 + c.duration);
  const check = () => {
    const s = parseTime(startI.value), e = parseTime(endI.value), d = e - s;
    const ok = Number.isFinite(d) && d >= 3 && d <= 180;
    len.textContent = Number.isFinite(d) ? `${Math.round(d)} s${ok ? "" : " (must be 3–180 s)"}` : "Use h:mm:ss";
    len.classList.toggle("err", !ok); $(".re-cut", el).disabled = !ok;
    return ok ? [s, e] : null;
  };
  [startI, endI].forEach((i) => i.addEventListener("input", check));
  $$(".nudge .chip", el).forEach((b) => b.addEventListener("click", () => {
    const inp = b.dataset.k === "start" ? startI : endI, v = parseTime(inp.value);
    if (Number.isFinite(v)) { inp.value = fmtTime(Math.max(0, v + Number(b.dataset.d))); check(); }
  }));
  $(".set-start", el).onclick = () => { startI.value = fmtTime(t0 + video.currentTime); check(); };
  $(".set-end", el).onclick = () => { endI.value = fmtTime(t0 + video.currentTime); check(); };
  $(".re-cut", el).onclick = async () => {
    const r = check(); if (!r) return;
    await saveClip(j.id, c.name, el, true);   // keep title/description edits
    try {
      await api(`api/jobs/${j.id}/clips/${c.name}/trim`, { method: "POST", body: JSON.stringify({ start: r[0], end: r[1] }) });
      setUp(el, `Re-cutting ${fmtTime(r[0])}–${fmtTime(r[1])}… (about a minute)`, ""); tick();
    } catch (e) { setUp(el, e.message, "err"); }
  };
  if (c.warning) setUp(el, "⚠ " + c.warning, "err");
  check();
}

function setUp(el, text, cls, url) {
  const p = $(".up-status", el); p.className = "small up-status " + cls; p.textContent = text;
  if (url) { const a = document.createElement("a"); a.href = url; a.target = "_blank"; a.rel = "noopener"; a.textContent = "✔ Live on YouTube ↗"; p.appendChild(a); }
}

async function saveClip(jobId, name, el, quiet) {
  try {
    await api(`api/jobs/${jobId}/clips/${name}`, { method: "PATCH", body: JSON.stringify({ title: $(".c-title", el).value, description: $(".c-desc", el).value }) });
    if (!quiet) setUp(el, "Saved", "ok");
  } catch (e) { setUp(el, e.message, "err"); }
}

function drawChart(m) {
  const cv = $("#chart"), dpr = window.devicePixelRatio || 1;
  const w = cv.clientWidth, h = 120;
  cv.width = w * dpr; cv.height = h * dpr; const g = cv.getContext("2d"); g.scale(dpr, dpr);
  const s = m.chart.score, dur = m.chart.duration; if (!s.length) return;
  const max = Math.max(...s, 1), min = Math.min(...s, 0);
  const y = (v) => h - 12 - ((v - min) / (max - min || 1)) * (h - 24);
  g.clearRect(0, 0, w, h);
  g.fillStyle = "rgba(255,179,71,.16)";
  for (const c of m.clips) g.fillRect((c.start / dur) * w, 0, Math.max(3, (c.duration / dur) * w), h);
  const grad = g.createLinearGradient(0, 0, w, 0); grad.addColorStop(0, "#ff3d8b"); grad.addColorStop(1, "#ffb347");
  g.beginPath(); s.forEach((v, i) => { const x = (i / (s.length - 1)) * w; i ? g.lineTo(x, y(v)) : g.moveTo(x, y(v)); });
  g.strokeStyle = grad; g.lineWidth = 1.6; g.stroke();
  g.lineTo(w, h); g.lineTo(0, h); g.closePath();
  const fill = g.createLinearGradient(0, 0, 0, h); fill.addColorStop(0, "rgba(255,61,139,.35)"); fill.addColorStop(1, "rgba(255,61,139,0)");
  g.fillStyle = fill; g.fill();
  g.fillStyle = "#a99cc7"; g.font = "11px Inter, sans-serif";
  for (let i = 0; i <= 4; i++) { const t = (dur * i) / 4; g.fillText(fmtTime(t), Math.min(w - 34, (i / 4) * w + 4), h - 2); }
}

// ------------------------------------------------------------- settings
async function openSettings() {
  const data = await api("api/streamers");
  $("#enforce").checked = data.enforce;
  const rows = $("#streamer-rows"); rows.innerHTML = "";
  data.streamers.forEach(addRow);
  if (!data.streamers.length) addRow();
  renderYt();
  $("#settings").showModal();
}

function addRow(s = {}) {
  const row = document.createElement("div"); row.className = "srow";
  row.innerHTML = `<label>Name<input class="s-name" placeholder="StreamerName"></label>
    <label>Match (comma separated)<input class="s-match" placeholder="streamername, twitch.tv/streamername"></label>
    <label>Credit URL<input class="s-url" placeholder="https://twitch.tv/streamername"></label>
    <button type="button" class="btn ghost danger s-del" aria-label="Remove">✕</button>
    <label class="perm">Permission note<input class="s-perm" placeholder="e.g. clip policy on stream panel, checked 2026-11-18"></label>`;
  $(".s-name", row).value = s.name || ""; $(".s-match", row).value = (s.match || []).join(", ");
  $(".s-url", row).value = s.credit_url || ""; $(".s-perm", row).value = s.permission || "";
  $(".s-del", row).onclick = () => row.remove();
  $("#streamer-rows").appendChild(row);
}

async function saveStreamers() {
  const streamers = $$(".srow").map((r) => ({
    name: $(".s-name", r).value.trim(), match: $(".s-match", r).value.split(",").map((x) => x.trim()).filter(Boolean),
    credit_url: $(".s-url", r).value.trim(), permission: $(".s-perm", r).value.trim(),
  })).filter((s) => s.name);
  try { await api("api/streamers", { method: "PUT", body: JSON.stringify({ enforce: $("#enforce").checked, streamers }) }); $("#settings").close(); }
  catch (e) { alert(e.message); }
}

// ------------------------------------------------------------- polling
async function tick() {
  try {
    await refreshJobs();
    const cur = state.jobs.find((j) => j.id === state.current);
    if (cur) await renderJob();
    if (Math.random() < 0.1) { state.config = await api("api/config"); renderYt(); }
  } catch (e) { /* server restarting */ }
  const busy = state.jobs.some((j) => j.status === "running" || j.status === "queued");
  clearTimeout(state.pollTimer);
  state.pollTimer = setTimeout(tick, busy ? 1500 : 6000);
}

let resizeT; window.addEventListener("resize", () => { clearTimeout(resizeT); resizeT = setTimeout(() => state.current && renderJob(), 250); });
$("#open-settings").addEventListener("click", openSettings);
$("#add-streamer").addEventListener("click", () => addRow());
$("#save-streamers").addEventListener("click", saveStreamers);
$("#ig-connect-btn").addEventListener("click", connectIg);
$("#ig-disconnect").addEventListener("click", async () => {
  if (!confirm("Disconnect Instagram? You'd need to paste the setup values again to reconnect.")) return;
  await api("api/instagram", { method: "DELETE" }); state.config = await api("api/config"); renderYt(); lastRenderedClips = ""; renderJob();
});
bindForm();
loadConfig().then(tick).then(() => { if (state.jobs[0]) openJob(state.jobs[0].id, false); });
