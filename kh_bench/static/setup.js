// Bench setup page: edits the whole bench config in memory, saves with PUT /api/setup.

const $ = (s, el = document) => el.querySelector(s);
let cfg = null;          // working copy
let saved = "";          // JSON of last saved config, for dirty tracking
let meta = {};           // events, presets, config_file
let resources = [];      // discovered VISA addresses

const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const h = (tag, attrs = {}, ...kids) => {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k.startsWith("on")) el[k] = v;
    else if (k === "class") el.className = v;
    else if (v !== undefined && v !== null && v !== false) el.setAttribute(k, v === true ? "" : v);
  }
  kids.flat().forEach((k) => k !== null && k !== undefined && el.append(k));
  return el;
};

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(t._h);
  t._h = setTimeout(() => t.classList.remove("show"), 3500);
}

const api = async (path, opts = {}) => {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) { const e = new Error(typeof data.detail === "string" ? data.detail : r.statusText); e.data = data; e.status = r.status; throw e; }
  return data;
};

function markDirty() {
  const dirty = JSON.stringify(cfg) !== saved;
  $("#savebar").classList.toggle("dirty", dirty);
  $("#dirtyMsg").textContent = dirty ? "Unsaved changes" : "No unsaved changes";
}

// ---------- generic field ----------
// spec: {key, label, type: text|number|textarea|checkbox|select, options, unit, help, step, nullable, wide}
function field(obj, spec, onchange) {
  const id = "f" + Math.random().toString(36).slice(2, 9);
  const v = obj[spec.key];
  let input;
  const set = (val) => { obj[spec.key] = val; markDirty(); onchange && onchange(); };
  if (spec.type === "checkbox") {
    input = h("input", { type: "checkbox", id, checked: !!v, onchange: (e) => set(e.target.checked) });
    return h("label", { class: "fld check", for: id }, input, h("span", {}, spec.label),
      spec.help ? h("small", {}, spec.help) : null);
  }
  if (spec.type === "select") {
    input = h("select", { id, onchange: (e) => set(e.target.value) },
      spec.options.map((o) => h("option", { value: o, selected: o === v }, o)));
  } else if (spec.type === "textarea") {
    input = h("textarea", { id, rows: spec.rows || 3, oninput: (e) => set(e.target.value) });
    input.value = v ?? "";
  } else {
    input = h("input", { id, type: spec.type || "text", step: spec.step || "any", placeholder: spec.placeholder || "",
      list: spec.list, value: v ?? "",
      oninput: (e) => {
        const raw = e.target.value;
        if (spec.type === "number") set(raw === "" ? (spec.nullable ? null : 0) : Number(raw));
        else set(raw === "" && spec.nullable ? null : raw);
      } });
  }
  return h("label", { class: "fld" + (spec.wide ? " wide" : ""), for: id },
    h("span", {}, spec.label, spec.unit ? h("em", {}, ` (${spec.unit})`) : null), input,
    spec.help ? h("small", {}, spec.help) : null);
}
const grid = (...kids) => h("div", { class: "fgrid" }, ...kids);

// ---------- company ----------
function renderCompany() {
  const c = cfg.company;
  $("#companyBody").replaceChildren(grid(
    field(c, { key: "name", label: "Company name" }),
    field(c, { key: "phone", label: "Phone" }),
    field(c, { key: "address", label: "Address", type: "textarea", rows: 2, wide: true }),
    field(c, { key: "cert_title", label: "Certificate title" }),
    field(c, { key: "cert_subtitle", label: "Certificate subtitle" }),
    field(c, { key: "cert_statement", label: "Certification statement", type: "textarea", rows: 4, wide: true,
      help: "{target_soc} is replaced with the profile's target (e.g. 30). Have compliance review this wording." }),
    field(c, { key: "cert_footer", label: "Footer text", wide: true, help: "e.g. document control number / revision" }),
  ));
}

// ---------- channels ----------
function renderChannels() {
  const tb = $("#chTable tbody");
  tb.replaceChildren();
  cfg.channels.forEach((ch, idx) => {
    const sim = ch.driver === "simulated";
    const out = h("td", { colspan: 9, class: "test-out" });
    const cell = (spec, extra = {}) => {
      const f = field(ch, spec, () => { if (spec.key === "driver") renderChannels(); });
      f.querySelector("span")?.remove();
      Object.assign(f.querySelector("input,select").style, extra);
      return h("td", {}, f);
    };
    tb.append(h("tr", {},
      cell({ key: "id", type: "number", step: 1 }, { width: "56px" }),
      cell({ key: "name" }, { width: "80px" }),
      cell({ key: "driver", type: "select", options: ["simulated", "scpi"] }),
      sim ? h("td", { class: "muted" }, "–") : cell({ key: "resource", list: "visaList", placeholder: "TCPIP::192.168.1.50::INSTR", nullable: true }, { minWidth: "230px" }),
      sim ? h("td", { class: "muted" }, "–") : cell({ key: "preset", type: "select", options: meta.presets }),
      sim ? h("td", { class: "muted" }, "–") : cell({ key: "load_channel", type: "number", step: 1, nullable: true }, { width: "64px" }),
      cell({ key: "max_current_a", type: "number" }, { width: "70px" }),
      sim ? cell({ key: "time_scale", type: "number" }, { width: "70px" }) : h("td", { class: "muted" }, "1×"),
      h("td", { class: "row-actions" },
        h("button", { class: "btn ghost small", onclick: () => testChannel(ch, out) }, "Test"),
        h("button", { class: "btn danger ghost small", title: "Remove", onclick: () => {
          cfg.channels.splice(idx, 1); markDirty(); renderChannels(); } }, "✕")),
    ), h("tr", { class: "test-row" }, out));
  });
  let dl = $("#visaList");
  if (!dl) { dl = h("datalist", { id: "visaList" }); document.body.append(dl); }
  dl.replaceChildren(...resources.map((r) => h("option", { value: r })));
}

async function testChannel(ch, out) {
  out.className = "test-out show";
  out.textContent = `Testing ${ch.name}…`;
  try {
    const r = await api("/api/setup/test-channel", { method: "POST", body: ch });
    out.className = "test-out show " + (r.ok ? "ok" : "bad");
    out.textContent = r.ok ? `✓ ${r.idn}  |  reads ${r.voltage.toFixed(2)} V, ${r.current.toFixed(2)} A`
                           : `✗ ${r.error}`;
  } catch (e) { out.className = "test-out show bad"; out.textContent = `✗ ${e.message}`; }
}

async function discover() {
  const box = $("#discoverOut");
  box.hidden = false;
  box.textContent = "Scanning USB / LAN / serial for instruments…";
  const r = await api("/api/setup/resources");
  resources = r.resources || [];
  renderChannels();
  box.innerHTML = !r.ok ? `Couldn't scan: ${esc(r.error)}. Install NI-VISA (or your load's VISA driver) and try again.`
    : resources.length ? `Found ${resources.length}: <code>${resources.map(esc).join("</code> <code>")}</code><br><small>These now autocomplete in the VISA address boxes. LAN loads may not show up; type <code>TCPIP::&lt;ip&gt;::INSTR</code>.</small>`
    : "No instruments found. Check the cable/power, or type a LAN address like <code>TCPIP::192.168.1.50::INSTR</code>.";
}

function addChannel() {
  const id = Math.max(0, ...cfg.channels.map((c) => c.id)) + 1;
  const last = cfg.channels[cfg.channels.length - 1];
  cfg.channels.push({ ...(last || { driver: "simulated", preset: "rigol_dl3000", max_current_a: 30, time_scale: 1 }),
    id, name: `CH${id}`, resource: last?.driver === "scpi" ? "" : null, load_channel: null });
  markDirty(); renderChannels();
}

// ---------- profiles ----------
const PROFILE_GROUPS = [
  ["Battery", [
    { key: "name", label: "Profile name", help: "What operators pick on the dashboard" },
    { key: "model", label: "Model (printed on cert)" },
    { key: "cells_series", label: "Cells in series", type: "number", step: 1, help: "4 = 12.8 V, 8 = 25.6 V, 16 = 51.2 V" },
    { key: "rated_ah", label: "Rated capacity", type: "number", unit: "Ah" },
  ]],
  ["Discharge", [
    { key: "discharge_a", label: "Discharge current", type: "number", unit: "A", help: "Capped by each channel's Max A" },
    { key: "target_soc", label: "Target SOC", type: "number", unit: "%" },
    { key: "min_final_soc", label: "Minimum final SOC", type: "number", unit: "%", help: "Below this = FAIL (over-discharged)" },
    { key: "cutoff_cell_v", label: "Cutoff voltage", type: "number", unit: "V/cell", help: "Safety stop under load" },
    { key: "max_temp_c", label: "Max temperature", type: "number", unit: "°C" },
    { key: "max_minutes", label: "Timeout", type: "number", unit: "min", nullable: true, help: "Blank = 1.5× expected time" },
  ]],
  ["Checks", [
    { key: "min_start_cell_v", label: "Min start voltage", type: "number", unit: "V/cell", help: "Proves battery is full. 0 = off" },
    { key: "rest_minutes", label: "Rest before OCV", type: "number", unit: "min" },
    { key: "ocv_check", label: "Verify rested OCV", type: "checkbox" },
    { key: "ocv_cell_min", label: "OCV window min", type: "number", unit: "V/cell" },
    { key: "ocv_cell_max", label: "OCV window max", type: "number", unit: "V/cell" },
  ]],
];

function profileSummary(p) {
  const ah = p.rated_ah * (100 - p.target_soc) / 100;
  const hrs = p.discharge_a ? ah / p.discharge_a : 0;
  const total = hrs * 60 + (p.rest_minutes || 0);
  return `${p.rated_ah} Ah · ${p.cells_series}S ${(p.cells_series * 3.2).toFixed(1)} V · ${p.discharge_a} A → ${p.target_soc}%` +
    ` · removes ${ah.toFixed(1)} Ah · ≈ ${Math.floor(total / 60)}h ${String(Math.round(total % 60)).padStart(2, "0")}m per battery incl. rest`;
}

function renderProfiles(openIdx = -1) {
  const body = $("#profilesBody");
  const wasOpen = [...body.querySelectorAll("details")].map((d) => d.open);
  body.replaceChildren(...cfg.profiles.map((p, idx) => {
    const sum = h("span", { class: "psum" }, profileSummary(p));
    const title = h("b", {}, p.name || "(unnamed)");
    const refresh = () => { sum.textContent = profileSummary(p); title.textContent = p.name || "(unnamed)"; };
    return h("details", { class: "profile", open: idx === openIdx || wasOpen[idx] || undefined },
      h("summary", {}, title, sum),
      ...PROFILE_GROUPS.map(([g, specs]) => h("div", { class: "pgroup" }, h("h4", {}, g),
        grid(...specs.map((s) => field(p, s, refresh))))),
      h("div", { class: "row end" },
        h("button", { class: "btn ghost small", onclick: () => {
          cfg.profiles.splice(idx + 1, 0, { ...structuredClone(p), name: p.name + " (copy)" });
          markDirty(); renderProfiles(idx + 1); } }, "Duplicate"),
        h("button", { class: "btn danger ghost small", onclick: () => {
          if (cfg.profiles.length === 1) return toast("Need at least one profile");
          if (!confirm(`Delete profile "${p.name}"? Past runs keep their data.`)) return;
          cfg.profiles.splice(idx, 1); markDirty(); renderProfiles(); } }, "Delete")),
    );
  }));
}

function addProfile() {
  cfg.profiles.push({ name: "New profile", model: "", cells_series: 4, rated_ah: 100, discharge_a: 25,
    target_soc: 30, min_final_soc: 20, cutoff_cell_v: 2.8, min_start_cell_v: 3.32, rest_minutes: 30,
    ocv_check: true, ocv_cell_min: 3.18, ocv_cell_max: 3.3, max_minutes: null, max_temp_c: 55 });
  markDirty(); renderProfiles(cfg.profiles.length - 1);
}

// ---------- notifications ----------
function renderNotifications() {
  const n = cfg.notifications;
  $("#stationBody").replaceChildren(grid(field(n, { key: "station", label: "Station name",
    help: "Starts every message so you know which bench it came from" })));
  const events = Object.entries(meta.events);
  $("#hooksBody").replaceChildren(...(n.webhooks.length ? n.webhooks.map((wh, idx) => {
    const out = h("div", { class: "test-out" });
    const all = wh.events.includes("*");
    const chips = h("div", { class: "chips" },
      h("label", { class: "chip" + (all ? " on" : "") }, h("input", { type: "checkbox", checked: all, onchange: (e) => {
        wh.events = e.target.checked ? ["*"] : []; markDirty(); renderNotifications(); } }), "All events"),
      ...(all ? [] : events.map(([ev, desc]) => h("label", { class: "chip" + (wh.events.includes(ev) ? " on" : ""), title: desc },
        h("input", { type: "checkbox", checked: wh.events.includes(ev), onchange: (e) => {
          wh.events = e.target.checked ? [...wh.events, ev] : wh.events.filter((x) => x !== ev);
          markDirty(); renderNotifications(); } }), ev))));
    return h("div", { class: "hook" },
      grid(field(wh, { key: "name", label: "Name" }),
        field(wh, { key: "url", label: "URL", placeholder: "http://nodered-pc:1880/kh-bench", wide: true }),
        field(wh, { key: "enabled", label: "Enabled", type: "checkbox" })),
      chips,
      h("div", { class: "row" },
        h("button", { class: "btn ghost small", onclick: async () => {
          out.className = "test-out show"; out.textContent = "Sending test…";
          const r = await api("/api/setup/test-webhook", { method: "POST", body: wh });
          out.className = "test-out show " + (r.ok ? "ok" : "bad");
          out.textContent = r.ok ? "✓ Delivered. Check Node-RED's debug panel." : `✗ ${r.error}`;
          loadLog(); } }, "Send test"),
        h("button", { class: "btn danger ghost small", onclick: () => {
          n.webhooks.splice(idx, 1); markDirty(); renderNotifications(); } }, "Remove")),
      out);
  }) : [h("p", { class: "help" }, "No webhooks yet. Add one and point it at your Node-RED flow (see README › Notifications).")]));
}

async function loadLog() {
  const rows = await api("/api/setup/notify-log");
  $("#logTable tbody").innerHTML = rows.length ? rows.map((r) => `<tr><td>${esc(r.time.replace("T", " "))}</td>
    <td>${esc(r.hook)}</td><td>${esc(r.event)}</td>
    <td class="${r.ok ? "res PASS" : "res FAIL"}">${r.ok ? "OK" : "FAILED: " + esc(r.error)}</td></tr>`).join("")
    : `<tr><td colspan="4" class="muted">Nothing sent yet.</td></tr>`;
}

// ---------- advanced ----------
function renderAdvanced() {
  const b = cfg.bench;
  $("#advancedBody").replaceChildren(grid(
    field(b, { key: "sample_interval_s", label: "Poll loads every", type: "number", unit: "s" }),
    field(b, { key: "log_interval_s", label: "Log a data point every", type: "number", unit: "s" }),
    field(b, { key: "db_path", label: "Database file", help: "Restart required" }),
    field(b, { key: "host", label: "Listen address", help: "0.0.0.0 = reachable from other PCs. Restart required" }),
    field(b, { key: "port", label: "Port", type: "number", step: 1, help: "Restart required" }),
  ));
}

// ---------- load / save ----------
function renderAll() {
  renderCompany(); renderChannels(); renderProfiles(); renderNotifications(); renderAdvanced(); markDirty();
}

async function load() {
  const r = await api("/api/setup");
  meta = r;
  cfg = r.config;
  saved = JSON.stringify(cfg);
  $("#cfgFile").innerHTML = `Saves to<br><code>${esc(r.config_file)}</code>`;
  renderAll();
  loadLog();
}

function showErrors(list) {
  const box = $("#errors");
  if (!list) { box.hidden = true; return; }
  box.hidden = false;
  box.innerHTML = `<b>Not saved. Fix these:</b><ul>${list.map((m) => `<li>${esc(m)}</li>`).join("")}</ul>`;
}

async function save() {
  showErrors(null);
  try {
    const r = await api("/api/setup", { method: "PUT", body: cfg });
    saved = JSON.stringify(cfg);
    markDirty();
    toast(r.restart_needed.length ? `Saved. Restart the bench to apply: ${r.restart_needed.join(", ")}` : "Saved and applied");
  } catch (e) {
    const d = e.data?.detail;
    if (Array.isArray(d)) {
      showErrors(d.map((x) => {
        const loc = x.loc.slice(1);
        const msg = x.msg.replace(/^Value error, /, "");
        if (!loc.length) return msg;  // whole-config rule (duplicate ids, missing address...)
        const where = loc[0] === "channels" ? `Channel row ${loc[1] + 1}` :
                      loc[0] === "profiles" ? `Profile "${cfg.profiles[loc[1]]?.name}"` : loc[0];
        return `${where}${loc[2] !== undefined ? " › " + loc[2] : ""}: ${msg}`;
      }));
    } else showErrors([e.message]);
  }
}

$("#saveBtn").onclick = save;
$("#discardBtn").onclick = () => { if (confirm("Discard unsaved changes?")) { cfg = JSON.parse(saved); showErrors(null); renderAll(); } };
$("#addChannelBtn").onclick = addChannel;
$("#addProfileBtn").onclick = addProfile;
$("#addHookBtn").onclick = () => { cfg.notifications.webhooks.push({ name: "Node-RED", url: "", enabled: true, events: ["*"] }); markDirty(); renderNotifications(); };
$("#discoverBtn").onclick = discover;
$("#refreshLogBtn").onclick = loadLog;
window.addEventListener("beforeunload", (e) => { if (JSON.stringify(cfg) !== saved) e.preventDefault(); });
load().catch((e) => toast("Failed to load setup: " + e.message));
