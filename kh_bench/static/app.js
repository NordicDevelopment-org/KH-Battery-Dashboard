// KH Battery Bench dashboard. Polls /api/state once a second; card DOM is built
// once per channel and updated in place so scanner input never loses focus.

const $ = (s, el = document) => el.querySelector(s);
const cards = new Map();   // channel id -> {el, refs}
let config = null;
let jobId = null;
let lastStatusSig = "";

const api = async (path, opts = {}) => {
  const r = await fetch(path, {
    headers: { "Content-Type": "application/json" }, ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) { const e = new Error(data.detail || r.statusText); e.status = r.status; throw e; }
  return data;
};

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(t._h);
  t._h = setTimeout(() => t.classList.remove("show"), 3500);
}

const fmt = (v, d = 2) => (v === null || v === undefined ? "–" : Number(v).toFixed(d));
const fmtDur = (s) => {
  if (s === null || s === undefined) return "–";
  s = Math.round(s);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  return h ? `${h}h ${String(m).padStart(2, "0")}m` : `${m}m ${String(s % 60).padStart(2, "0")}s`;
};
const store = {
  get: (k, d) => { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
  set: (k, v) => { try { localStorage.setItem(k, v); } catch {} },
};

// ---------- setup ----------
async function init() {
  config = await api("/api/config");
  const opts = config.profiles.map((p) => `<option>${p.name}</option>`).join("");
  $("#defaultProfile").innerHTML = opts;
  $("#defaultProfile").value = store.get("profile", config.profiles[0]?.name);
  $("#defaultProfile").onchange = (e) => {
    store.set("profile", e.target.value);
    cards.forEach((c) => (c.refs.profile.value = e.target.value));
  };
  $("#operator").value = store.get("operator", "");
  $("#operator").oninput = (e) => store.set("operator", e.target.value);

  $("#newJobBtn").onclick = () => { $("#jobForm").reset(); $("#jobDialog").showModal(); };
  $("#jobDialog").addEventListener("close", createJob);
  $("#jobSelect").onchange = (e) => selectJob(e.target.value);
  $("#startAllBtn").onclick = startAll;
  $("#stopAllBtn").onclick = stopAll;
  let st;
  $("#search").oninput = () => { clearTimeout(st); st = setTimeout(loadRuns, 250); };

  await loadJobs(store.get("job", null));
  poll();
  setInterval(poll, 1000);
  setInterval(loadRuns, 10000);
}

async function loadJobs(selectId) {
  const jobs = await api("/api/jobs");
  const sel = $("#jobSelect");
  sel.innerHTML = `<option value="">— No job (unassigned) —</option>` + jobs.map((j) =>
    `<option value="${j.id}">${j.customer}${j.lot ? " · " + j.lot : ""}${j.po_number ? " · PO " + j.po_number : ""}  (${j.pass_count}/${j.run_count})</option>`).join("");
  if (selectId && jobs.some((j) => String(j.id) === String(selectId))) sel.value = selectId;
  selectJob(sel.value);
  return jobs;
}

function selectJob(id) {
  jobId = id ? Number(id) : null;
  store.set("job", id || "");
  const label = $("#jobSelect").selectedOptions[0]?.textContent || "";
  $("#jobTitle").innerHTML = jobId ? `${esc(label.replace(/\s+\(\d+\/\d+\)$/, ""))} <small>runs in this job</small>`
                                   : `Recent runs <small>select a job to print a batch certificate</small>`;
  $("#batchCertBtn").style.display = jobId ? "" : "none";
  $("#csvBtn").style.display = jobId ? "" : "none";
  if (jobId) {
    $("#batchCertBtn").href = `/api/jobs/${jobId}/certificate.pdf`;
    $("#csvBtn").href = `/api/jobs/${jobId}/report.csv`;
  }
  loadRuns();
}

async function createJob() {
  if ($("#jobDialog").returnValue !== "ok") return;
  const f = new FormData($("#jobForm"));
  try {
    const job = await api("/api/jobs", { method: "POST", body: Object.fromEntries(f) });
    await loadJobs(job.id);
    toast(`Job created: ${job.customer}`);
  } catch (e) { toast(e.message); }
}

// ---------- channel cards ----------
function buildCard(ch) {
  const el = $("#cardTpl").content.firstElementChild.cloneNode(true);
  const q = (s) => $(s, el);
  const refs = {
    name: q(".ch-name"), pill: q(".pill"), serial: q(".serial"), profile: q(".profile"),
    startSoc: q(".start-soc"), start: q(".start"), serialBig: q(".serial-big"),
    socNum: q(".soc-num"), socTarget: q(".soc-target"), fill: q(".fill"), mark: q(".target-mark"),
    v: q(".v"), i: q(".i"), ah: q(".ah"), eta: q(".eta"), etaLbl: q(".eta-lbl"),
    spark: q(".spark polyline"), msg: q(".live .msg"), offMsg: q(".offline .msg"),
    stop: q(".stop"), cert: q(".cert"), clear: q(".clear"), reconnect: q(".reconnect"),
  };
  refs.name.textContent = ch.name;
  refs.profile.innerHTML = $("#defaultProfile").innerHTML;
  refs.profile.value = $("#defaultProfile").value;
  refs.serial.oninput = () => el.classList.toggle("staged", !!refs.serial.value.trim());
  refs.serial.onkeydown = (e) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    focusNextEmpty(ch.id);  // barcode scanners send Enter after the code
  };
  refs.start.onclick = () => startChannel(ch.id);
  refs.stop.onclick = async () => {
    if (!confirm(`Stop ${ch.name}? The run will be marked FAIL/ABORTED.`)) return;
    await api(`/api/channels/${ch.id}/stop`, { method: "POST" });
  };
  refs.clear.onclick = async () => {
    await api(`/api/channels/${ch.id}/clear`, { method: "POST" });
    refs.serial.value = "";
    el.classList.remove("staged");
    poll().then(() => refs.serial.focus());
  };
  refs.reconnect.onclick = () => api(`/api/channels/${ch.id}/reconnect`, { method: "POST" }).then(poll);
  $("#grid").appendChild(el);
  cards.set(ch.id, { el, refs, ch });
}

function focusNextEmpty(fromId) {
  const ids = [...cards.keys()];
  const start = ids.indexOf(fromId);
  for (let k = 1; k <= ids.length; k++) {
    const c = cards.get(ids[(start + k) % ids.length]);
    if (c.el.classList.contains("v-setup") && !c.refs.serial.value.trim()) { c.refs.serial.focus(); return; }
  }
  document.activeElement.blur();
  toast("All open channels staged — press Start all staged");
}

function updateCard(ch) {
  const { el, refs } = cards.get(ch.id) || (buildCard(ch), cards.get(ch.id));
  const L = ch.live || {};
  const view = ch.status === "offline" ? "offline" : ch.status === "idle" ? "setup" : "live";
  el.className = `card v-${view} s-${ch.status}` + (L.result ? ` r-${L.result}` : "") +
    (view === "setup" && refs.serial.value.trim() ? " staged" : "");

  const pillTxt = L.result || { idle: "ready", discharging: "discharging", resting: "resting", offline: "offline" }[ch.status] || ch.status;
  refs.pill.textContent = pillTxt;
  refs.pill.className = "pill " + (L.result || ch.status);

  if (view === "offline") { refs.offMsg.textContent = ch.message || "Not connected"; return; }
  if (view === "setup") return;

  const soc = L.soc ?? L.start_soc;
  refs.serialBig.textContent = L.serial || "";
  refs.serialBig.title = `${L.serial} · ${L.profile}`;
  refs.socNum.textContent = soc === undefined ? "–" : `${fmt(soc, 1)}%`;
  refs.socTarget.textContent = `target ≤ ${L.target_soc}%`;
  refs.fill.style.width = `${Math.max(0, Math.min(100, soc ?? 0))}%`;
  refs.mark.style.left = `${L.target_soc}%`;
  refs.v.textContent = fmt(L.voltage, 2);
  refs.i.textContent = fmt(L.current, 1);
  refs.ah.textContent = fmt(L.ah, 1);
  if (ch.status === "resting") { refs.etaLbl.textContent = "OCV in"; refs.eta.textContent = fmtDur(L.rest_left_s); }
  else if (ch.status === "done") { refs.etaLbl.textContent = "OCV"; refs.eta.textContent = L.ocv_v ? fmt(L.ocv_v, 2) : "–"; }
  else { refs.etaLbl.textContent = "ETA"; refs.eta.textContent = fmtDur(L.eta_s); }
  refs.msg.textContent = ch.message || "";

  const h = ch.history || [];
  if (h.length > 1) {
    const vs = h.map((p) => p[1]);
    const lo = Math.min(...vs), hi = Math.max(...vs), span = hi - lo || 1;
    const t0 = h[0][0], tspan = h[h.length - 1][0] - t0 || 1;
    refs.spark.setAttribute("points", h.map((p) =>
      `${((p[0] - t0) / tspan * 200).toFixed(1)},${(38 - (p[1] - lo) / span * 36).toFixed(1)}`).join(" "));
  } else refs.spark.setAttribute("points", "");

  const done = ch.status === "done";
  refs.stop.style.display = done ? "none" : "";
  refs.clear.style.display = done ? "" : "none";
  refs.cert.style.display = done && L.run_id ? "" : "none";
  if (L.run_id) refs.cert.href = `/api/runs/${L.run_id}/certificate.pdf`;
  refs.cert.textContent = L.result === "PASS" ? "Certificate" : "Report";
}

async function poll() {
  try {
    const state = await api("/api/state");
    state.forEach(updateCard);
    const n = (f) => state.filter(f).length;
    const counts = [
      ["Ready", n((c) => c.status === "idle")],
      ["Discharging", n((c) => c.status === "discharging")],
      ["Resting", n((c) => c.status === "resting")],
      ["Pass", n((c) => c.live?.result === "PASS")],
      ["Fail", n((c) => c.live?.result === "FAIL")],
      ["Offline", n((c) => c.status === "offline")],
    ];
    $("#counts").innerHTML = counts.filter(([, v]) => v).map(([k, v]) => `<span class="count">${k} ${v}</span>`).join("");
    const sig = state.map((c) => c.status + (c.live?.result || "")).join();
    if (sig !== lastStatusSig) { lastStatusSig = sig; loadJobs(jobId); }  // refreshes counts + runs
  } catch (e) { $("#counts").innerHTML = `<span class="count">Server unreachable</span>`; }
}

// ---------- start / stop ----------
async function startChannel(id, { quiet = false } = {}) {
  const { refs, ch } = cards.get(id);
  const serial = refs.serial.value.trim();
  if (!serial) { if (!quiet) toast(`${ch.name}: scan a serial first`); return false; }
  if (!$("#operator").value.trim()) { toast("Enter operator name first"); $("#operator").focus(); return false; }
  const body = { serial, profile: refs.profile.value, start_soc: Number(refs.startSoc.value || 100),
                 job_id: jobId, operator: $("#operator").value.trim() };
  try {
    await api(`/api/channels/${id}/start`, { method: "POST", body });
  } catch (e) {
    if (e.status === 409 && confirm(`${e.message}\n\nRun it again anyway?`)) {
      await api(`/api/channels/${id}/start`, { method: "POST", body: { ...body, force: true } });
    } else { toast(`${ch.name}: ${e.message}`); return false; }
  }
  refs.serial.value = "";
  return true;
}

async function startAll() {
  if (!jobId && !confirm("No job selected — runs won't be on a batch certificate. Continue?")) return;
  let started = 0;
  for (const [id, c] of cards) {
    if (c.el.classList.contains("v-setup") && c.refs.serial.value.trim())
      if (await startChannel(id, { quiet: true })) started++;
  }
  toast(started ? `Started ${started} channel${started > 1 ? "s" : ""}` : "Nothing staged — scan serials into ready channels");
  poll();
}

async function stopAll() {
  if (!confirm("Stop ALL running channels? Those runs will be marked aborted.")) return;
  await Promise.all([...cards.keys()].map((id) => api(`/api/channels/${id}/stop`, { method: "POST" })));
  toast("Stop sent to all channels");
}

// ---------- runs table ----------
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

async function loadRuns() {
  const q = $("#search").value.trim();
  let runs;
  try {
    runs = q ? await api(`/api/runs?serial=${encodeURIComponent(q)}`)
         : jobId ? await api(`/api/jobs/${jobId}/runs`) : await api("/api/runs?limit=50");
  } catch { return; }
  const tbody = $("#runs tbody");
  if (!runs.length) { tbody.innerHTML = `<tr><td colspan="10" class="reason">No runs yet.</td></tr>`; return; }
  tbody.innerHTML = runs.map((r) => {
    const res = r.result || (r.status === "resting" ? "RESTING" : "RUNNING");
    return `<tr>
      <td class="mono">${esc(r.serial)}</td>
      <td>${esc(r.model || r.profile)}</td>
      <td>${esc(r.channel)}</td>
      <td>${esc((r.started_at || "").replace("T", " ").slice(0, 16))}</td>
      <td class="num">${fmt(r.ah_removed, 2)}</td>
      <td class="num">${r.final_soc == null ? "–" : fmt(r.final_soc, 1) + "%"}</td>
      <td class="num">${fmt(r.ocv_v, 2)}</td>
      <td><span class="res ${esc(res)}">${esc(res)}</span>${r.fail_reason ? `<div class="reason">${esc(r.fail_reason)}</div>` : ""}</td>
      <td class="mono">${esc(r.cert_no || "")}</td>
      <td><a href="/api/runs/${r.id}/certificate.pdf" target="_blank">${r.result === "PASS" ? "Cert" : "Report"}</a><a href="/api/runs/${r.id}/samples.csv">Data</a></td>
    </tr>`;
  }).join("");
}

init().catch((e) => toast("Failed to load: " + e.message));
