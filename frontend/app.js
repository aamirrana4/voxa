/* Voxa frontend — vanilla JS. Talks only to this server's /api/* (key never leaves the backend). */
"use strict";

/* ---------------- state ---------------- */
const state = {
  tool: "tts",
  provider: "all",
  cache: {},            // provider -> {items:[], page, hasMore}
  voiceById: {},
  search: "", gender: "", lang: "",
  visibleCount: 60,
  selectedVoiceId: null,
  speakers: [{ label: "A", voiceId: "" }, { label: "B", voiceId: "" }],
  myClones: [],
  previewingId: null,
};
try { state.myClones = JSON.parse(localStorage.getItem("voxa_clones") || "[]"); } catch (e) {}

const PROVIDERS = ["elevenlabs", "minimax", "edge", "kokoro", "vbee", "fishaudio", "clone"];
const PROVIDER_LABEL = { elevenlabs: "ElevenLabs", minimax: "MiniMax", edge: "Edge", kokoro: "Kokoro", vbee: "Vbee", fishaudio: "FishAudio", clone: "My Clones" };

/* Apna WhatsApp number yahan likhein (country code ke saath, baghair + ke).
   Example: "923001234567". Pricing page ke Buy buttons isi number par message bhejenge. */
const SUPPORT_WHATSAPP = "92XXXXXXXXXX";

const TOOLS = [
  { id: "tts", label: "🎙️ Speak", voices: true },
  { id: "dialogue", label: "💬 Dialogue", voices: true },
  { id: "clone", label: "🧬 Clone Voice" },
  { id: "stt", label: "📝 Transcribe" },
  { id: "dubbing", label: "🌍 Dubbing" },
  { id: "changer", label: "🔁 Voice Changer", voices: true },
  { id: "isolate", label: "🎧 Isolate Voice" },
  { id: "music", label: "🎵 Music" },
  { id: "sfx", label: "✨ Sound FX" },
  { id: "image", label: "🖼️ Images" },
  { id: "pricing", label: "💰 Pricing" },
  { id: "history", label: "🕘 History" },
];

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* ---------------- api helpers ---------------- */
async function apiGet(path) {
  const r = await fetch("/api" + path);
  return r.json();
}
async function apiPost(path, body) {
  const r = await fetch("/api" + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  return r.json();
}
async function apiForm(path, formData) {
  const r = await fetch("/api" + path, { method: "POST", body: formData });
  return r.json();
}
async function apiDelete(path) {
  const r = await fetch("/api" + path, { method: "DELETE" });
  return r.json();
}

function showError(resultEl, msg) {
  resultEl.hidden = false;
  resultEl.innerHTML = `<p class="error">⚠️ ${esc(msg)}</p>`;
}

async function pollTask(taskId, progressEl, label) {
  const bar = progressEl.querySelector(".pct");
  const started = Date.now();
  for (let i = 0; i < 180; i++) {
    await new Promise((res) => setTimeout(res, 4000));
    const t = await apiGet(`/task/${taskId}`);
    if (bar) bar.textContent = `${Math.round((Date.now() - started) / 1000)}s`;
    if (t && t.status === "done") return t;
    if (t && t.status === "error") throw new Error((t && t.error) || (t && t.message) || "Task failed.");
  }
  throw new Error("Timed out waiting for the task. Check History later.");
}

async function runJob({ btn, progressId, resultId, start, render }) {
  const progressEl = $(progressId), resultEl = $(resultId);
  btn.disabled = true;
  resultEl.hidden = true; resultEl.innerHTML = "";
  progressEl.hidden = false;
  try {
    const created = await start();
    const taskId = created && created.task_id;
    if (!taskId) throw new Error((created && (created.error || created.message)) || "Could not start the task.");
    const done = await pollTask(taskId, progressEl);
    render(resultEl, done);
    refreshCredits();
  } catch (e) {
    showError(resultEl, e.message || e);
  } finally {
    progressEl.hidden = true;
    btn.disabled = false;
  }
}

/* Extract downloadable outputs from a finished task */
function extractOutputs(t) {
  const md = (t && t.metadata) || {};
  const out = { audios: [], srts: [], jsons: [], images: [], text: "" };
  const audio = md.audio_url || md.output_uri || t.output_uri;
  if (audio) out.audios.push(audio);
  if (md.music_result && Array.isArray(md.music_result.data)) {
    md.music_result.data.forEach((d) => { if (d && d.audio_url) out.audios.push(d.audio_url); });
  }
  if (Array.isArray(md.result_images)) {
    md.result_images.forEach((im) => { if (im && im.imageUrl) out.images.push(im.imageUrl); });
  }
  if (md.srt_url) out.srts.push(md.srt_url);
  if (md.json_url) out.jsons.push(md.json_url);
  if (md.transcript || t.transcript) out.text = md.transcript || t.transcript;
  return out;
}

function fileName(url, fallback) {
  try {
    const u = new URL(url, location.href);
    const m = u.searchParams.get("name");
    if (m) return m;
    const p = u.pathname.split("/").pop();
    return p || fallback;
  } catch (e) { return fallback; }
}

function audioResultHTML(out, opts) {
  opts = opts || {};
  let h = "";
  out.audios.forEach((a, i) => {
    h += `<audio controls preload="none" src="${esc(a)}"></audio>
      <div class="dl-row">
        <a class="dl-btn" href="${esc(a)}" download="${esc(fileName(a, "voxa-audio-" + (i + 1) + ".mp3"))}">⬇ Download audio${out.audios.length > 1 ? " " + (i + 1) : ""}</a>
      </div>`;
  });
  out.srts.forEach((s, i) => {
    h += `<div class="dl-row"><a class="dl-btn alt" href="${esc(s)}" download="${esc(fileName(s, "voxa-captions.srt"))}">⬇ Download subtitles (SRT)</a></div>`;
  });
  out.jsons.forEach((j, i) => {
    h += `<div class="dl-row"><a class="dl-btn alt" href="${esc(j)}" download="${esc(fileName(j, "voxa-words.json"))}">⬇ Download word timings (JSON)</a></div>`;
  });
  if (out.text) h += `<div class="transcript">${esc(out.text)}</div>`;
  if (!h) h = `<p class="error">No downloadable output found on this task.</p>`;
  return h;
}

/* ---------------- credits ---------------- */
async function refreshCredits() {
  try {
    const d = await apiGet("/credits");
    if (d && typeof d.credits === "number") {
      $("creditsPill").textContent = `🪙 ${d.credits.toLocaleString()} credits`;
    } else {
      $("creditsPill").textContent = "🪙 credits ?";
    }
  } catch (e) { $("creditsPill").textContent = "🪙 offline"; }
}

/* ---------------- tool navigation ---------------- */
function buildNav() {
  const nav = $("toolNav");
  nav.innerHTML = "";
  TOOLS.forEach((t) => {
    const b = document.createElement("button");
    b.className = "tool-tab" + (t.id === state.tool ? " active" : "");
    b.textContent = t.label;
    b.onclick = () => switchTool(t.id);
    nav.appendChild(b);
  });
}
function switchTool(id) {
  state.tool = id;
  buildNav();
  document.querySelectorAll(".tool").forEach((el) => { el.hidden = el.dataset.tool !== id; });
  const tool = TOOLS.find((t) => t.id === id);
  $("voicePanel").style.display = "";
  document.querySelector(".layout").classList.toggle("no-voices", !tool.voices);
  if (id === "history") loadHistory();
  if (id === "clone") renderMyClones();
  if (id === "image") loadImageModels();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

/* ---------------- voice browser ---------------- */
function buildProviderPills() {
  const wrap = $("providerPills");
  wrap.innerHTML = "";
  [["all", "All"]].concat(PROVIDERS.map((p) => [p, PROVIDER_LABEL[p]])).forEach(([val, label]) => {
    const b = document.createElement("button");
    b.className = "pill" + (state.provider === val ? " active" : "");
    b.textContent = label;
    b.onclick = () => { state.provider = val; state.visibleCount = 60; buildProviderPills(); ensureVoices().then(renderVoices); };
    wrap.appendChild(b);
  });
}

async function fetchProviderPage(provider, page) {
  const d = await apiGet(`/voices?provider=${provider}&page=${page}&page_size=100`);
  const items = (d && d.data) || [];
  const pg = (d && d.pagination) || {};
  items.forEach((v) => { state.voiceById[v.voice_id] = v; });
  return { items, hasMore: !!pg.has_more };
}

async function ensureVoices() {
  const need = state.provider === "all" ? PROVIDERS : [state.provider];
  await Promise.all(need.map(async (p) => {
    if (!state.cache[p]) {
      const { items, hasMore } = await fetchProviderPage(p, 1);
      state.cache[p] = { items, page: 1, hasMore };
    }
  }));
  refreshLangFilter();
  if (!state.selectedVoiceId) autoSelectVoice();
}

function allVoices() {
  const list = state.provider === "all" ? PROVIDERS : [state.provider];
  const out = [];
  list.forEach((p) => { (state.cache[p] ? state.cache[p].items : []).forEach((v) => out.push(v)); });
  return out;
}

function filteredVoices() {
  const q = state.search.trim().toLowerCase();
  return allVoices().filter((v) => {
    if (state.gender && (v.gender || "") !== state.gender) return false;
    if (state.lang && (v.language || v.locale || "") !== state.lang) return false;
    if (q) {
      const hay = `${v.name || ""} ${v.description || ""} ${v.language || ""} ${(v.tags || []).join(" ")}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
}

function refreshLangFilter() {
  const sel = $("langFilter");
  const cur = sel.value;
  const langs = {};
  Object.values(state.voiceById).forEach((v) => { const l = v.language || v.locale; if (l) langs[l] = 1; });
  const sorted = Object.keys(langs).sort().slice(0, 80);
  sel.innerHTML = `<option value="">All languages</option>` + sorted.map((l) => `<option ${l === cur ? "selected" : ""}>${esc(l)}</option>`).join("");
}

function autoSelectVoice() {
  for (const p of ["elevenlabs", "minimax", "edge", "kokoro", "vbee", "fishaudio"]) {
    const c = state.cache[p];
    if (c && c.items.length) { selectVoice(c.items[0].voice_id, true); return; }
  }
}

function renderVoices() {
  const list = $("voiceList");
  const f = filteredVoices();
  const vis = f.slice(0, state.visibleCount);
  if (!vis.length) {
    list.innerHTML = `<div class="empty">No voices found. Try another search.</div>`;
  } else {
    list.innerHTML = vis.map((v) => {
      const sel = v.voice_id === state.selectedVoiceId;
      const playing = v.voice_id === state.previewingId;
      return `<div class="voice-card${sel ? " selected" : ""}" data-vid="${esc(v.voice_id)}">
        <button class="play-btn${playing ? " playing" : ""}" data-play="${esc(v.voice_id)}" title="Play sample">${playing ? "⏸" : "▶"}</button>
        <div class="voice-info">
          <div class="voice-name">${esc(v.name || v.voice_id)}${playing ? '<span class="eq"><i></i><i></i><i></i></span>' : ""}</div>
          <div class="voice-meta">${esc(v.language || "")}${v.gender ? " · " + esc(v.gender) : ""}</div>
        </div>
        <span class="voice-provider">${esc(providerOf(v.voice_id))}</span>
        <button class="use-btn" data-use="${esc(v.voice_id)}">${sel ? "✓" : "Use"}</button>
      </div>`;
    }).join("");
  }
  const more = $("loadMoreVoices");
  more.hidden = f.length <= state.visibleCount && !needsApiMore();
  more.disabled = false;
  more.textContent = "Load more";
}

function needsApiMore() {
  if (state.provider === "all") return false;
  const c = state.cache[state.provider];
  return !!(c && c.hasMore);
}

function providerOf(voiceId) {
  const p = String(voiceId).split("_")[0];
  return PROVIDER_LABEL[p] || p;
}

async function loadMore() {
  const btn = $("loadMoreVoices");
  const f = filteredVoices();
  if (state.visibleCount < f.length) {
    state.visibleCount += 60;
    renderVoices();
    return;
  }
  if (needsApiMore()) {
    btn.disabled = true; btn.textContent = "Loading…";
    const p = state.provider;
    const c = state.cache[p];
    try {
      const { items, hasMore } = await fetchProviderPage(p, c.page + 1);
      c.items = c.items.concat(items);
      c.page += 1; c.hasMore = hasMore;
      state.visibleCount += 60;
    } catch (e) { /* ignore */ }
    renderVoices();
  }
}

function selectVoice(voiceId, silent) {
  state.selectedVoiceId = voiceId;
  const v = state.voiceById[voiceId];
  $("ttsVoiceLabel").textContent = v ? `🎙️ ${v.name}` : "No voice selected";
  renderVoices();
  if (!silent && (state.tool === "tts")) { /* keep user on panel */ }
}

/* ---- preview playback (one at a time) ---- */
const previewAudio = $("previewAudio");
function stopPreview() {
  previewAudio.pause();
  previewAudio.removeAttribute("src");
  state.previewingId = null;
  renderVoices();
  renderSpeakerRows();
}
function togglePreview(voiceId) {
  const v = state.voiceById[voiceId];
  if (!v || !v.preview_url) return;
  if (state.previewingId === voiceId) { stopPreview(); return; }
  state.previewingId = voiceId;
  previewAudio.src = v.preview_url;
  previewAudio.play().catch(() => { state.previewingId = null; renderVoices(); });
  renderVoices();
  renderSpeakerRows();
}
previewAudio.addEventListener("ended", stopPreview);
previewAudio.addEventListener("pause", () => { if (state.previewingId) { state.previewingId = null; renderVoices(); renderSpeakerRows(); } });

/* ---------------- TTS ---------------- */
function initTTS() {
  const ta = $("ttsText");
  ta.addEventListener("input", () => { $("ttsCount").textContent = ta.value.length.toLocaleString(); });
  $("ttsClear").onclick = () => { ta.value = ""; $("ttsCount").textContent = "0"; };
  $("ttsFile").addEventListener("change", (e) => {
    const f = e.target.files[0];
    if (!f) return;
    const r = new FileReader();
    r.onload = () => { ta.value = String(r.result || ""); $("ttsCount").textContent = ta.value.length.toLocaleString(); };
    r.readAsText(f);
    e.target.value = "";
  });
  $("ttsSpeed").addEventListener("input", (e) => { $("ttsSpeedVal").textContent = Number(e.target.value).toFixed(2) + "×"; });
  document.querySelectorAll(".tags button").forEach((b) => {
    b.onclick = () => {
      const tag = b.dataset.tag;
      const s = ta.selectionStart || ta.value.length;
      ta.value = ta.value.slice(0, s) + tag + ta.value.slice(ta.selectionEnd || s);
      ta.focus();
      $("ttsCount").textContent = ta.value.length.toLocaleString();
    };
  });
  $("ttsGenerate").onclick = () => {
    const text = ta.value.trim();
    if (!text) { showError($("ttsResult"), "Please enter some text first."); return; }
    if (!state.selectedVoiceId) { showError($("ttsResult"), "Please select a voice from the panel."); return; }
    const fd = new FormData();
    fd.append("text", text);
    fd.append("voice_id", state.selectedVoiceId);
    fd.append("speed", $("ttsSpeed").value);
    fd.append("with_transcript", $("ttsTranscript").checked ? "true" : "false");
    runJob({
      btn: $("ttsGenerate"), progressId: "ttsProgress", resultId: "ttsResult",
      start: () => apiForm("/tts", fd),
      render: (el, done) => {
        const out = extractOutputs(done);
        el.hidden = false;
        el.innerHTML = `<h3>🔊 Your audio is ready</h3>` + audioResultHTML(out);
      },
    });
  };
}

/* ---------------- Dialogue ---------------- */
function renderSpeakerRows() {
  const wrap = $("speakerRows");
  const voices = allVoices();
  wrap.innerHTML = "";
  state.speakers.forEach((sp, idx) => {
    const row = document.createElement("div");
    row.className = "speaker-row";
    const opts = voices.map((v) =>
      `<option value="${esc(v.voice_id)}"${v.voice_id === sp.voiceId ? " selected" : ""}>${esc(v.name || v.voice_id)} (${esc(providerOf(v.voice_id))})</option>`
    ).join("");
    row.innerHTML = `<span class="speaker-badge">${esc(sp.label)}</span>
      <select data-sp="${idx}"><option value="">— choose voice —</option>${opts}</select>
      <button class="mini-play" data-spplay="${idx}" title="Play sample">▶</button>
      ${state.speakers.length > 2 ? `<button class="speaker-del" data-spdel="${idx}" title="Remove">✕</button>` : ""}`;
    wrap.appendChild(row);
  });
  wrap.querySelectorAll("select[data-sp]").forEach((s) => {
    s.onchange = () => { state.speakers[Number(s.dataset.sp)].voiceId = s.value; };
  });
  wrap.querySelectorAll("[data-spplay]").forEach((b) => {
    b.onclick = () => {
      const sp = state.speakers[Number(b.dataset.spplay)];
      if (sp.voiceId) togglePreview(sp.voiceId);
    };
  });
  wrap.querySelectorAll("[data-spdel]").forEach((b) => {
    b.onclick = () => { state.speakers.splice(Number(b.dataset.spdel), 1); relabelSpeakers(); renderSpeakerRows(); };
  });
}
function relabelSpeakers() {
  state.speakers.forEach((sp, i) => { sp.label = String.fromCharCode(65 + i); });
}
function initDialogue() {
  renderSpeakerRows();
  $("addSpeaker").onclick = () => {
    if (state.speakers.length >= 6) return;
    state.speakers.push({ label: "", voiceId: "" });
    relabelSpeakers(); renderSpeakerRows();
  };
  $("dlgDelay").addEventListener("input", (e) => { $("dlgDelayVal").textContent = Number(e.target.value).toFixed(1) + "s"; });
  $("dlgGenerate").onclick = () => {
    const text = $("dlgText").value.trim();
    if (!text) { showError($("dlgResult"), "Please enter dialogue text with A> / B> labels."); return; }
    if (state.speakers.length < 2) { showError($("dlgResult"), "Add at least 2 speakers."); return; }
    if (state.speakers.some((s) => !s.voiceId)) { showError($("dlgResult"), "Choose a voice for every speaker (use ▶ to preview)."); return; }
    const fd = new FormData();
    fd.append("text", text);
    fd.append("speakers", JSON.stringify(state.speakers.map((s) => ({ voice_id: s.voiceId, speed: 1 }))));
    fd.append("delay", $("dlgDelay").value);
    fd.append("with_transcript", $("dlgTranscript").checked ? "true" : "false");
    runJob({
      btn: $("dlgGenerate"), progressId: "dlgProgress", resultId: "dlgResult",
      start: () => apiForm("/dialogue", fd),
      render: (el, done) => {
        el.hidden = false;
        el.innerHTML = `<h3>🎭 Dialogue ready</h3>` + audioResultHTML(extractOutputs(done));
      },
    });
  };
}

/* ---------------- Voice clone ---------------- */
function renderMyClones() {
  const list = $("myClones");
  if (!state.myClones.length) {
    list.innerHTML = `<div class="empty">No cloned voices yet.</div>`;
    return;
  }
  list.innerHTML = "";
  state.myClones.forEach((c, i) => {
    const div = document.createElement("div");
    div.className = "voice-card";
    div.innerHTML = `<div class="voice-info"><div class="voice-name">${esc(c.name)}</div>
      <div class="voice-meta">clone · ${esc(c.id)}</div></div>
      <button class="use-btn" data-ci="${i}">Use</button>
      <button class="icon-btn danger" data-cdel="${i}">Delete</button>`;
    list.appendChild(div);
  });
  list.querySelectorAll("[data-ci]").forEach((b) => {
    b.onclick = () => {
      const c = state.myClones[Number(b.dataset.ci)];
      state.voiceById[c.id] = { voice_id: c.id, name: c.name, language: "", gender: "", preview_url: "" };
      selectVoice(c.id);
      switchTool("tts");
    };
  });
  list.querySelectorAll("[data-cdel]").forEach((b) => {
    b.onclick = async () => {
      const c = state.myClones[Number(b.dataset.cdel)];
      if (!confirm(`Delete cloned voice "${c.name}"?`)) return;
      const numId = String(c.id).replace(/^clone_/, "");
      await apiDelete(`/clone/${numId}`);
      state.myClones.splice(Number(b.dataset.cdel), 1);
      localStorage.setItem("voxa_clones", JSON.stringify(state.myClones));
      renderMyClones();
    };
  });
}
function initClone() {
  renderMyClones();
  $("cloneGenerate").onclick = () => {
    const name = $("cloneName").value.trim();
    const file = $("cloneFile").files[0];
    if (!name) { showError($("cloneResult"), "Please give the voice a name."); return; }
    if (!file) { showError($("cloneResult"), "Please choose an audio sample."); return; }
    if (file.size > 10 * 1024 * 1024) { showError($("cloneResult"), "Audio file must be under 10MB."); return; }
    const fd = new FormData();
    fd.append("voice_name", name);
    fd.append("audio_file", file);
    runJob({
      btn: $("cloneGenerate"), progressId: "cloneProgress", resultId: "cloneResult",
      start: () => apiForm("/clone", fd),
      render: (el, done) => {
        const vid = (done && done.data && done.data.voice_id) || (done && done.voice_id);
        const cloneId = vid ? (String(vid).startsWith("clone_") ? String(vid) : "clone_" + vid) : null;
        el.hidden = false;
        if (cloneId) {
          state.myClones.unshift({ id: cloneId, name });
          localStorage.setItem("voxa_clones", JSON.stringify(state.myClones));
          renderMyClones();
          el.innerHTML = `<h3>🧬 Voice cloned!</h3><p><b>${esc(name)}</b> is ready. Voice ID: <code>${esc(cloneId)}</code></p>
            <div class="dl-row"><button class="dl-btn" id="useCloneBtn">Use this voice →</button></div>`;
          $("useCloneBtn").onclick = () => {
            state.voiceById[cloneId] = { voice_id: cloneId, name, language: "", gender: "", preview_url: "" };
            selectVoice(cloneId);
            switchTool("tts");
          };
        } else {
          showError(el, (done && (done.error || done.message)) || "Cloning failed.");
        }
      },
    });
  };
}

/* ---------------- generic file tools ---------------- */
function initFileTool({ fileId, btnId, progressId, resultId, buildForm, endpoint, title, emptyMsg }) {
  $(btnId).onclick = () => {
    const file = $(fileId).files[0];
    if (!file) { showError($(resultId), emptyMsg || "Please choose a file first."); return; }
    let fd;
    try { fd = buildForm(file); } catch (e) { return; } // buildForm shows its own error UI
    runJob({
      btn: $(btnId), progressId, resultId,
      start: () => apiForm(endpoint, fd),
      render: (el, done) => {
        el.hidden = false;
        el.innerHTML = `<h3>${title}</h3>` + audioResultHTML(extractOutputs(done));
      },
    });
  };
}

function initSTT() {
  initFileTool({
    fileId: "sttFile", btnId: "sttGenerate", progressId: "sttProgress", resultId: "sttResult",
    endpoint: "/stt", title: "📝 Transcription ready", emptyMsg: "Please choose an audio file.",
    buildForm: (file) => {
      const fd = new FormData();
      fd.append("file", file);
      if ($("sttEvents").checked) fd.append("tag_audio_events", "true");
      return fd;
    },
  });
}
function initDubbing() {
  initFileTool({
    fileId: "dubFile", btnId: "dubGenerate", progressId: "dubProgress", resultId: "dubResult",
    endpoint: "/dubbing", title: "🌍 Dubbing ready", emptyMsg: "Please choose an audio file.",
    buildForm: (file) => {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("target_lang", $("dubLang").value);
      fd.append("source_lang", "auto");
      fd.append("num_speakers", $("dubSpeakers").value);
      fd.append("disable_voice_cloning", $("dubNoClone").checked ? "true" : "false");
      return fd;
    },
  });
}
function initChanger() {
  const upd = () => {
    $("chgStabVal").textContent = Math.round($("chgStab").value * 100) + "%";
    $("chgSimVal").textContent = Math.round($("chgSim").value * 100) + "%";
    $("chgStyleVal").textContent = Math.round($("chgStyle").value * 100) + "%";
  };
  ["chgStab", "chgSim", "chgStyle"].forEach((id) => $(id).addEventListener("input", upd));
  initFileTool({
    fileId: "chgFile", btnId: "chgGenerate", progressId: "chgProgress", resultId: "chgResult",
    endpoint: "/voice-changer", title: "🔁 Voice converted", emptyMsg: "Please choose an audio file.",
    buildForm: (file) => {
      const voiceId = $("chgVoice").value;
      if (!voiceId) { showError($("chgResult"), "Please choose a target voice."); throw new Error("no-voice"); }
      const fd = new FormData();
      fd.append("file", file);
      fd.append("voice_id", voiceId);
      fd.append("model_id", $("chgModel").value);
      fd.append("voice_settings", JSON.stringify({
        stability: Number($("chgStab").value),
        similarity_boost: Number($("chgSim").value),
        style: Number($("chgStyle").value),
        use_speaker_boost: $("chgBoost").checked,
      }));
      fd.append("remove_background_noise", $("chgNoise").checked ? "true" : "false");
      return fd;
    },
  });
}
function fillChangerVoices() {
  const sel = $("chgVoice");
  sel.innerHTML = `<option value="">— choose target voice —</option>` + allVoices().map((v) =>
    `<option value="${esc(v.voice_id)}">${esc(v.name || v.voice_id)} (${esc(providerOf(v.voice_id))})</option>`).join("");
}
function initIsolate() {
  initFileTool({
    fileId: "isoFile", btnId: "isoGenerate", progressId: "isoProgress", resultId: "isoResult",
    endpoint: "/voice-isolate", title: "🎧 Voice isolated", emptyMsg: "Please choose an audio file.",
    buildForm: (file) => { const fd = new FormData(); fd.append("file", file); return fd; },
  });
}

/* ---------------- music ---------------- */
function initMusic() {
  const genres = ["lo-fi", "cinematic", "dreamy", "acoustic", "soulful", "indie pop", "house funk", "melancholic", "folk", "dance-punk"];
  $("genreChips").innerHTML = genres.map((g) => `<button class="chip" data-g="${esc(g)}">${esc(g)}</button>`).join("");
  document.querySelectorAll("#genreChips .chip").forEach((c) => {
    c.onclick = () => {
      const idea = $("musIdea");
      idea.value = (idea.value ? idea.value + ", " : "") + c.dataset.g;
      idea.focus();
    };
  });
  $("musGenerate").onclick = () => {
    const idea = $("musIdea").value.trim();
    if (!idea) { showError($("musResult"), "Please describe the music first."); return; }
    const payload = {
      title: $("musTitle").value.trim() || "Untitled",
      model: $("musModel").value,
      generation_type: 1,
      idea,
      lyrics: $("musInstr").checked ? " " : $("musLyrics").value,
      n: Number($("musCount").value),
      instrumental: $("musInstr").checked,
      rewrite_idea_switch: false,
    };
    runJob({
      btn: $("musGenerate"), progressId: "musProgress", resultId: "musResult",
      start: () => apiPost("/music", payload),
      render: (el, done) => {
        el.hidden = false;
        el.innerHTML = `<h3>🎵 Your track is ready</h3>` + audioResultHTML(extractOutputs(done));
      },
    });
  };
}

/* ---------------- sound fx ---------------- */
function initSFX() {
  $("sfxDur").addEventListener("input", (e) => { $("sfxDurVal").textContent = e.target.value + "s"; });
  $("sfxInf").addEventListener("input", (e) => { $("sfxInfVal").textContent = Number(e.target.value).toFixed(2); });
  $("sfxGenerate").onclick = () => {
    const text = $("sfxText").value.trim();
    if (!text) { showError($("sfxResult"), "Please describe the sound first."); return; }
    const payload = {
      text,
      duration_seconds: Number($("sfxDur").value),
      prompt_influence: Number($("sfxInf").value),
      loop: $("sfxLoop").checked,
      model_id: "eleven_text_to_sound_v2",
    };
    runJob({
      btn: $("sfxGenerate"), progressId: "sfxProgress", resultId: "sfxResult",
      start: () => apiPost("/sfx", payload),
      render: (el, done) => {
        el.hidden = false;
        el.innerHTML = `<h3>✨ Sound ready</h3>` + audioResultHTML(extractOutputs(done));
      },
    });
  };
}

/* ---------------- image ---------------- */
async function loadImageModels() {
  const sel = $("imgModel");
  if (sel.dataset.loaded) return;
  try {
    const d = await apiGet("/image/models");
    const models = (d && (d.data || d.models)) || [];
    if (models.length) {
      sel.innerHTML = models.map((m) => {
        const id = m.model_id || m.id || m;
        const name = m.name || id;
        return `<option value="${esc(id)}">${esc(name)}</option>`;
      }).join("");
      sel.dataset.loaded = "1";
    } else {
      sel.innerHTML = `<option value="bytedance-seedream-4.5">Seedream 4.5</option>`;
    }
  } catch (e) {
    sel.innerHTML = `<option value="bytedance-seedream-4.5">Seedream 4.5</option>`;
  }
}
function initImage() {
  $("imgGenerate").onclick = () => {
    const prompt = $("imgPrompt").value.trim();
    if (!prompt) { showError($("imgResult"), "Please describe the image first."); return; }
    const fd = new FormData();
    fd.append("prompt", prompt);
    fd.append("model_id", $("imgModel").value);
    fd.append("generations_count", $("imgCount").value);
    fd.append("model_parameters", JSON.stringify({ aspect_ratio: $("imgRatio").value, resolution: $("imgRes").value }));
    runJob({
      btn: $("imgGenerate"), progressId: "imgProgress", resultId: "imgResult",
      start: () => apiForm("/image", fd),
      render: (el, done) => {
        const out = extractOutputs(done);
        el.hidden = false;
        el.innerHTML = out.images.length
          ? out.images.map((src, i) => `<div><img src="${esc(src)}" alt="Generated image ${i + 1}" loading="lazy">
              <div class="dl-row" style="margin-top:.4rem"><a class="dl-btn" href="${esc(src)}" download="voxa-image-${i + 1}.png">⬇ Download</a></div></div>`).join("")
          : `<p class="error">No images returned.</p>`;
      },
    });
  };
}

/* ---------------- pricing ---------------- */
function initPricing() {
  document.querySelectorAll(".price-btn[data-plan]").forEach((b) => {
    const msg = encodeURIComponent(
      `Assalam o Alaikum! Mujhe Voxa ka "${b.dataset.plan}" plan chahiye. Payment details bhej dein.`
    );
    b.href = `https://wa.me/${SUPPORT_WHATSAPP}?text=${msg}`;
    b.target = "_blank";
    b.rel = "noopener";
  });
}

/* ---------------- history ---------------- */
const TYPE_LABEL = { tts: "Text to speech", dialogue: "Dialogue", "speech-to-text": "Transcription", dubbing: "Dubbing", "voice-changer": "Voice changer", "voice-isolate": "Voice isolate", "sound-effect": "Sound FX", music: "Music", imagen2: "Image" };
async function loadHistory() {
  const list = $("histList");
  list.innerHTML = `<div class="empty">Loading…</div>`;
  const type = $("histType").value;
  try {
    const d = await apiGet(`/tasks?page=1&limit=20${type ? "&type=" + encodeURIComponent(type) : ""}`);
    const items = (d && (d.data || d.tasks)) || [];
    if (!items.length) { list.innerHTML = `<div class="empty">No tasks found.</div>`; return; }
    list.innerHTML = "";
    items.forEach((t) => {
      const id = t.task_id || t.id;
      const div = document.createElement("div");
      div.className = "hist-item";
      div.innerHTML = `<div class="meta">
          <div class="t">${esc(TYPE_LABEL[t.type] || t.type || "Task")}</div>
          <div class="d">${esc(t.created_at || t.createdAt || "")} · ${esc(String(id).slice(0, 8))}</div>
        </div>
        <span class="st ${esc(t.status || "")}">${esc(t.status || "")}</span>
        <button class="icon-btn" data-open="${esc(id)}">Open</button>
        <button class="icon-btn danger" data-del="${esc(id)}">✕</button>
        <div class="hist-detail" style="display:none;flex-basis:100%"></div>`;
      list.appendChild(div);
    });
    list.querySelectorAll("[data-open]").forEach((b) => {
      b.onclick = async () => {
        const detail = b.closest(".hist-item").querySelector(".hist-detail");
        if (detail.style.display === "none") {
          detail.innerHTML = "Loading…";
          detail.style.display = "block";
          try {
            const t = await apiGet(`/task/${b.dataset.open}`);
            detail.innerHTML = audioResultHTML(extractOutputs(t));
          } catch (e) { detail.innerHTML = `<p class="error">Could not load task.</p>`; }
        } else { detail.style.display = "none"; }
      };
    });
    list.querySelectorAll("[data-del]").forEach((b) => {
      b.onclick = async () => {
        if (!confirm("Delete this task?")) return;
        await apiPost("/task/delete", { task_ids: [b.dataset.del] });
        loadHistory();
      };
    });
  } catch (e) {
    list.innerHTML = `<div class="empty">Could not load history.</div>`;
  }
}

/* ---------------- init ---------------- */
function initVoicePanel() {
  buildProviderPills();
  let deb;
  $("voiceSearch").addEventListener("input", (e) => {
    clearTimeout(deb);
    deb = setTimeout(() => { state.search = e.target.value; state.visibleCount = 60; renderVoices(); }, 250);
  });
  $("genderFilter").addEventListener("change", (e) => { state.gender = e.target.value; state.visibleCount = 60; renderVoices(); });
  $("langFilter").addEventListener("change", (e) => { state.lang = e.target.value; state.visibleCount = 60; renderVoices(); });
  $("loadMoreVoices").onclick = loadMore;
  $("voiceList").addEventListener("click", (e) => {
    const play = e.target.closest("[data-play]");
    const use = e.target.closest("[data-use]");
    if (play) togglePreview(play.dataset.play);
    else if (use) selectVoice(use.dataset.use);
  });
}

document.addEventListener("DOMContentLoaded", async () => {
  buildNav();
  initVoicePanel();
  initTTS();
  initDialogue();
  initClone();
  initSTT();
  initDubbing();
  initChanger();
  initIsolate();
  initMusic();
  initSFX();
  initImage();
  initPricing();
  $("histType").addEventListener("change", loadHistory);
  $("creditsPill").onclick = refreshCredits;
  switchTool("tts");
  refreshCredits();
  try {
    await ensureVoices();
    renderVoices();
    renderSpeakerRows();
    fillChangerVoices();
  } catch (e) {
    $("voiceList").innerHTML = `<div class="empty">Could not load voices. Check your connection and API key.</div>`;
  }
});
