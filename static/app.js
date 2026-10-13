/* VFX Asset Browser — frontend (vanilla, no build) */
const $ = (s) => document.querySelector(s);
const grid = $("#grid"), insp = $("#inspector");

const state = {
  items: [], page: 1, total: 0,
  q: "", kind: "", format: "", alpha: "", min_w: 0,
  fav: 0, cat: "", root: "", missing_only: 0, sort: "recent",
  selected: null, focus: 0,
};

/* ---------------- helpers ---------------- */
function toast(msg, ms = 2600) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(t._h);
  t._h = setTimeout(() => t.classList.remove("show"), ms);
}

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (r.status === 202) return { pending: true, ...(await r.json()).status ? { status: (await r.clone().json()).status } : {} };
  if (!r.ok) {
    let detail = r.statusText;
    try { detail = (await r.json()).detail || detail; } catch (e) {}
    throw new Error(detail);
  }
  return r.json();
}

function fmtDur(s) {
  if (s == null) return "–";
  if (s < 60) return s.toFixed(1) + "s";
  const m = Math.floor(s / 60), r = Math.round(s % 60);
  return m + ":" + String(r).padStart(2, "0");
}
function fmtSize(b) {
  if (b == null) return "–";
  if (b < 1048576) return (b / 1024).toFixed(0) + " KB";
  if (b < 1073741824) return (b / 1048576).toFixed(1) + " MB";
  return (b / 1073741824).toFixed(2) + " GB";
}
function alphaLabel(a) {
  return a === 1 ? "alpha" : a === 0 ? "no alpha" : "alpha ?";
}

/* ---------------- data ---------------- */
function query() {
  const p = new URLSearchParams();
  if (state.q) p.set("q_", state.q);
  if (state.kind) p.set("kind", state.kind);
  if (state.format) p.set("format", state.format);
  if (state.alpha) p.set("alpha", state.alpha);
  if (state.min_w) p.set("min_w", state.min_w);
  if (state.cat) p.set("category", state.cat);
  if (state.root) p.set("root", state.root);
  if (state.fav) p.set("fav", "1");
  if (state.missing_only) p.set("missing_only", "1");
  p.set("sort", state.sort);
  p.set("page", state.page);
  p.set("limit", "240");
  return "/api/assets?" + p;
}

async function load() {
  const data = await api(query());
  state.items = data.items;
  state.total = data.total;
  $("#count").textContent = data.total + " assets";
  renderGrid();
}

/* ---------------- grid ---------------- */
let hoverVideo = null;

function renderGrid() {
  grid.innerHTML = "";
  for (const a of state.items) grid.appendChild(cardEl(a));
  if (state.focus >= 0) focusCard(state.focus);
}

function cardEl(a) {
  const el = document.createElement("div");
  el.className = "card" + (a.missing ? " missing" : "");
  el.dataset.id = a.id;

  const media = document.createElement("div");
  media.className = "media";

  if (a.missing) {
    media.innerHTML = `<div class="errline" style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center">missing file</div>`;
  } else if (a.proxy_status === "ready") {
    const img = new Image();
    img.loading = "lazy";
    img.src = `/api/asset/${a.id}/thumb?t=${a.last_indexed || 0}`;
    media.appendChild(img);
    if (a.kind !== "image") {
      media.addEventListener("mouseenter", () => playHover(a, media));
      media.addEventListener("mouseleave", stopHover);
    }
  } else if (a.proxy_status === "failed") {
    media.innerHTML = `<div class="errline" style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;padding:8px;text-align:center">${esc(a.proxy_error || "processing failed")}</div>`;
  } else {
    media.innerHTML = `<div class="spinner"></div>`;
  }

  const badges = [];
  if (a.kind === "sequence") badges.push("SEQ " + (a.frames || "") + "f");
  if (a.has_alpha === 1) badges.push(`<span class="badge alpha">ALPHA</span>`);
  if (a.ext) badges.push(`<span class="badge fmt">${a.ext.toUpperCase()}</span>`);
  media.insertAdjacentHTML("beforeend", badges[0] ? `<span class="badge">${badges[0]}</span>` : "");

  const star = document.createElement("button");
  star.className = "star" + (a.favorite ? " on" : "");
  star.textContent = "★";
  star.title = "Favorite";
  star.onclick = (e) => {
    e.stopPropagation();
    api(`/api/asset/${a.id}`, { method: "POST", body: JSON.stringify({ favorite: a.favorite ? 0 : 1 }), headers: { "Content-Type": "application/json" } })
      .then(() => { a.favorite = a.favorite ? 0 : 1; star.classList.toggle("on"); load(); });
  };
  media.appendChild(star);
  el.appendChild(media);

  const meta = document.createElement("div");
  meta.className = "meta";
  const sub = [
    a.width && a.height ? `${a.width}×${a.height}` : null,
    a.fps ? a.fps + " fps" : null,
    a.kind === "sequence" ? `${a.frame_start}–${a.frame_end}` : (a.duration ? fmtDur(a.duration) : null),
    alphaLabel(a.has_alpha),
  ].filter(Boolean).join(" · ");
  meta.innerHTML = `<div class="name">${esc(a.name)}</div><div class="sub">${sub || "processing…"}</div>`;
  el.appendChild(meta);

  el.onclick = () => select(a.id);
  return el;
}

function playHover(a, media) {
  stopHover();
  const v = document.createElement("video");
  v.src = `/api/asset/${a.id}/proxy?t=${a.last_indexed || 0}`;
  v.muted = true; v.loop = true; v.playsInline = true; v.autoplay = true;
  v.addEventListener("error", () => v.remove());
  media.appendChild(v);
  hoverVideo = v;
}
function stopHover() {
  if (hoverVideo) { hoverVideo.pause(); hoverVideo.remove(); hoverVideo = null; }
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function focusCard(i) {
  const cards = [...grid.children];
  if (!cards.length) return;
  state.focus = Math.max(0, Math.min(i, cards.length - 1));
  cards.forEach((c) => c.classList.remove("kb-focus"));
  cards[state.focus].classList.add("kb-focus");
  cards[state.focus].scrollIntoView({ block: "nearest" });
}

/* ---------------- inspector ---------------- */
async function select(id) {
  const a = await api(`/api/asset/${id}`);
  state.selected = a;
  renderInspector(a);
  insp.classList.remove("hidden");
}

function renderInspector(a) {
  const m = $("#insp-media");
  m.innerHTML = "";
  if (a.missing) {
    m.innerHTML = `<div class="errline" style="display:flex;height:100%;align-items:center;justify-content:center">file missing on disk</div>`;
  } else if (a.kind !== "image" && a.proxy_status === "ready") {
    const v = document.createElement("video");
    v.src = `/api/asset/${a.id}/proxy?t=${a.last_indexed || 0}`;
    v.controls = true; v.loop = true; v.autoplay = true; v.muted = true;
    m.appendChild(v);
  } else if (a.proxy_status === "ready") {
    const img = new Image();
    img.src = `/api/asset/${a.id}/preview?t=${a.last_indexed || 0}`;
    m.appendChild(img);
  } else {
    m.innerHTML = `<div class="spinner" style="display:flex;height:100%;align-items:center;justify-content:center"></div>`;
  }

  const rows = [
    ["Kind", a.kind],
    ["Format", a.format || a.codec || "–"],
    ["Dimensions", a.width ? `${a.width} × ${a.height}` : "–"],
    ["Frame rate", a.fps ? a.fps + " fps" : (a.kind === "sequence" ? "25 fps (assumed)" : "–")],
    ["Duration", a.kind === "sequence" ? `${a.frames} frames` : fmtDur(a.duration)],
    ["Frame range", a.kind === "sequence" ? `${a.frame_start} – ${a.frame_end} (pad ${a.padding}${a.missing_frames ? ", " + a.missing_frames + " missing" : ""})` : "–"],
    ["Bit depth", a.bit_depth ? a.bit_depth + " bit" : "–"],
    ["Alpha", alphaLabel(a.has_alpha)],
    ["Channels", a.channels || "–"],
    ["Size", fmtSize(a.size)],
    ["Category", a.category || "–"],
    ["Added", a.first_seen || "–"],
    ["Indexed", a.last_indexed || "–"],
  ];
  $("#insp-body").innerHTML = `
    <h2>${esc(a.name)}</h2>
    <div class="kv">${rows.map(([k, v]) => `<div class="k">${k}</div><div class="v">${esc(v)}</div>`).join("")}</div>
    ${a.proxy_status === "failed" ? `<div class="errline" style="color:var(--err)">${esc(a.proxy_error || "")}</div>` : ""}
    <div class="actions">
      <button data-act="copy_path">📋 Copy path</button>
      <button data-act="reprocess">🔄 Reprocess</button>
      <button data-act="reveal">📂 Reveal in Explorer</button>
      <button data-act="open">▶ Open in viewer</button>
      <button data-act="fav">${a.favorite ? "★ Unfavorite" : "☆ Favorite"}</button>
    </div>
    <div class="tagline">Tags
      <input id="tags-input" value="${esc(a.tags || "")}" placeholder="comma, separated, tags">
    </div>
    <div class="pathbox">${esc(a.root_path || "")}<br>${a.kind === "sequence" ? "" : esc(a.path)}</div>`;

  document.querySelectorAll("#insp-body [data-act]").forEach((b) => {
    b.onclick = () => doAction(a, b.dataset.act);
  });
  $("#tags-input").onchange = (e) => {
    api(`/api/asset/${a.id}`, { method: "POST", body: JSON.stringify({ tags: e.target.value }), headers: { "Content-Type": "application/json" } })
      .then(() => { toast("Tags saved"); load(); });
  };
}

async function doAction(a, act) {
  try {
    const r = await api(`/api/asset/${a.id}/action`, { method: "POST", body: JSON.stringify({ action: act }), headers: { "Content-Type": "application/json" } });
    if (act === "copy_path") {
      await navigator.clipboard.writeText(r.data);
      toast("Path: " + r.data, 3500);
    } else if (act === "reprocess") {
      toast("Reprocessing…", 3500);
      setTimeout(() => select(a.id), 1500);
    } else if (act === "fav") {
      a.favorite = a.favorite ? 0 : 1;
      renderInspector(a);
      load();
    } else {
      toast("OK");
    }
  } catch (e) {
    toast("Action failed: " + e.message);
  }
}

$("#insp-close").onclick = () => { insp.classList.add("hidden"); state.selected = null; };

/* ---------------- status poll ---------------- */
let readyBefore = 0;
let assetsBefore = -1, missingBefore = -1;
async function pollStatus() {
  try {
    const s = await api("/api/status");
    const busy = (s.jobs.running || 0) + (s.jobs.queued || 0) + s.queue;
    const lines = [
      `${s.assets} assets · ${s.ready} ready`,
      s.scan.running ? `scanning: ${s.scan.found} files found` : "",
      busy ? `processing: ${busy} jobs` : "",
      s.missing ? `${s.missing} missing files` : "",
    ].filter(Boolean);
    $("#status-lines").innerHTML = lines.join("<br>");
    renderRoots(s);
    if (s.assets !== assetsBefore || s.missing !== missingBefore) {
      assetsBefore = s.assets; missingBefore = s.missing;
      loadFacets(); // keep category/format counts current
    }
    if (s.ready !== readyBefore) {
      readyBefore = s.ready;
      if (busy > 0) load(); // refresh grid as previews land
    }
  } catch (e) {}
}

async function renderRoots(s) {
  const roots = await api("/api/roots");
  $("#roots").innerHTML = roots.map((r) =>
    `<div class="root-row">
       <button class="side-item" data-root="${r.id}" title="${esc(r.path)}">${esc(r.path.split(/[\\/]/).pop() || r.path)}<span class="n">${r.n_assets}</span></button>
       <button class="root-del" data-delroot="${r.id}" title="Remove library (files on disk are NOT touched)">✕</button>
     </div>`).join("");
  document.querySelectorAll("[data-root]").forEach((b) => {
    b.onclick = () => {
      state.root = b.dataset.root;
      state.cat = "";
      state.fav = 0;
      state.page = 1;
      document.querySelectorAll(".side-item").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      load();
    };
  });
  document.querySelectorAll("[data-delroot]").forEach((b) => {
    b.onclick = (e) => {
      e.stopPropagation();
      const id = b.dataset.delroot;
      const name = (b.parentElement.querySelector("[data-root]")?.textContent || "this library").trim();
      if (!confirm(`Remove "${name}" from the browser?\n\nOnly the catalog entry and its cached previews are removed — the files on disk are NOT touched.`)) return;
      api(`/api/roots/${id}`, { method: "DELETE" }).then(() => {
        if (state.root === id) state.root = "";
        insp.classList.add("hidden"); state.selected = null;
        loadFacets(); load();
        toast("Library removed");
      }).catch((e2) => toast("Remove failed: " + e2.message));
    };
  });
}

/* ---------------- facets & sidebar ---------------- */
async function loadFacets() {
  try {
    const f = await api("/api/facets");
    const sel = $("#f-format");
    sel.innerHTML = `<option value="">Format: all</option>` + f.formats.map((x) => `<option value="${x.ext}">${x.ext} (${x.n})</option>`).join("");
    const cats = $("#cats");
    cats.innerHTML = f.categories.map((c) => `<button class="side-item" data-cat="${esc(c.category)}">${esc(c.category)}<span class="n">${c.n}</span></button>`).join("");
    cats.querySelectorAll("[data-cat]").forEach((b) => {
      b.onclick = () => {
        state.cat = b.dataset.cat;
        state.root = "";
        state.fav = 0;
        state.page = 1;
        $("#search").value = ""; state.q = "";
        document.querySelectorAll(".side-item").forEach((x) => x.classList.remove("active"));
        b.classList.add("active");
        load();
      };
      if (b.dataset.cat === state.cat) b.classList.add("active"); // survive re-render
    });
  } catch (e) {}
}

/* ---------------- wiring ---------------- */
let searchT;
$("#search").oninput = (e) => {
  clearTimeout(searchT);
  searchT = setTimeout(() => { state.q = e.target.value.trim(); state.page = 1; load(); }, 200);
};
for (const [id, key] of [["#f-kind", "kind"], ["#f-format", "format"], ["#f-alpha", "alpha"], ["#f-sort", "sort"]]) {
  $(id).onchange = (e) => { state[key] = e.target.value; state.page = 1; load(); };
}
$("#f-minw").onchange = (e) => { state.min_w = e.target.value; state.page = 1; load(); };
$("#card-size").oninput = (e) => { document.documentElement.style.setProperty("--cardw", e.target.value + "px"); };
document.querySelectorAll("[data-nav]").forEach((b) => {
  b.onclick = () => {
    document.querySelectorAll(".side-item").forEach((x) => x.classList.remove("active"));
    b.classList.add("active");
    const nav = b.dataset.nav;
    state.fav = 0;
    state.cat = "";
    state.missing_only = 0;
    if (nav === "all") {
      state.root = ""; state.q = "";
      $("#search").value = "";
      $("#f-kind").value = ""; $("#f-format").value = ""; $("#f-alpha").value = ""; $("#f-sort").value = "";
      state.kind = ""; state.format = ""; state.alpha = ""; state.sort = "recent";
      state.min_w = 0; $("#f-minw").value = "";
    }
    if (nav === "fav") state.fav = 1;
    if (nav === "recent") state.sort = "recent";
    if (nav === "missing") state.missing_only = 1;
    state.page = 1;
    load();
  };
});
$("#btn-rescan").onclick = async () => {
  for (const r of (await api("/api/roots"))) {
    await fetch(`/api/roots/${r.id}/rescan`, { method: "POST" });
  }
  toast("Rescan started");
};
$("#btn-addroot").onclick = async () => {
  const p = $("#root-input").value.trim();
  if (!p) return;
  try {
    await api("/api/roots", { method: "POST", body: JSON.stringify({ path: p }), headers: { "Content-Type": "application/json" } });
    $("#root-input").value = "";
    toast("Root added, scanning…");
    pollStatus();
  } catch (e) { toast("Add root failed: " + e.message); }
};

/* keyboard */
document.addEventListener("keydown", (e) => {
  if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;
  const cols = Math.max(1, Math.floor(grid.clientWidth / (parseFloat(getComputedStyle(grid).gap) + 250)));
  if (e.key === "ArrowRight") focusCard(state.focus + 1);
  else if (e.key === "ArrowLeft") focusCard(state.focus - 1);
  else if (e.key === "ArrowDown") focusCard(state.focus + cols);
  else if (e.key === "ArrowUp") focusCard(state.focus - cols);
  else if (e.key === "Enter" && state.items[state.focus]) select(state.items[state.focus].id);
  else if (e.key === "Escape") { insp.classList.add("hidden"); state.selected = null; }
  else if (e.key === "f" && state.items[state.focus]) {
    const a = state.items[state.focus];
    api(`/api/asset/${a.id}`, { method: "POST", body: JSON.stringify({ favorite: a.favorite ? 0 : 1 }), headers: { "Content-Type": "application/json" } }).then(load);
  }
  e.preventDefault();
});

/* boot */
load();
loadFacets();
pollStatus();
setInterval(pollStatus, 2000);
