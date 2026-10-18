// U1 DOOM page: a start screen and a three-step wizard (setup, Bluetooth, play).
// Polls /api/state and the printer's Bluetooth through launcher.py.

const $ = (id) => document.getElementById(id);
let current = null;          // last /api/state answer
let bluetoothState = null;   // last /api/bt answer, null while it fails
let step = 0;
let bluetoothBusy = false;

const ERRORS = {
  ssh_off: "SSH ist aus. Am Drucker Root Access einschalten.",
  auth: "Anmeldung abgelehnt. Passwort prüfen.",
  unreachable: "Der Drucker antwortet nicht.",
  lost: "Verbindung zum Drucker verloren.",
  offline: "Nicht verbunden.",
  printing: "Der Drucker druckt gerade. DOOM wartet, bis er fertig ist.",
  wad_missing: "Das Spiel liegt nicht auf dem Drucker. Erst kopieren.",
  no_adapter: "Kein Bluetooth-Adapter am Drucker. USB-Dongle einstecken.",
  bt_failed: "Das Bluetooth-Skript auf dem Drucker ist fehlgeschlagen.",
};
const errorText = (r) => ERRORS[r.error] || [r.error, r.detail].filter(Boolean).join(": ");

const WADS = {
  "freedoom1.wad": ["Freedoom: Phase 1", "Vier Episoden wie im ersten DOOM"],
  "freedoom2.wad": ["Freedoom: Phase 2", "32 Level wie in DOOM II"],
  "doom1.wad": ["DOOM", "Shareware, erste Episode"],
  "doom.wad": ["DOOM", "Die Vollversion"],
  "doom2.wad": ["DOOM II", "Hell on Earth"],
};
const FILES = { doom: "Das Spiel", "run.sh": "Startet DOOM auf dem Display", "bt.py": "Bluetooth auf dem Drucker" };
const FILE_STATES = {
  current: ["Aktuell", ""], changed: ["Geändert", "busy"], missing: ["Fehlt", "off"], unknown: ["Nicht verbunden", "off"],
};

async function api(path, body) {
  const options = body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  };
  const answer = await fetch(path, options);
  return answer.json();
}

function setStatus(el, text, kind) {
  el.textContent = text;
  el.className = el.className.replace(/ ?status--\w+/g, "") + (kind ? ` status--${kind}` : "");
}

const size = (n) => n > 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.ceil(n / 1e3)} kB`;

// Steps

const kind = (icon) => icon.startsWith("input-gaming") ? "gamepad" : icon.startsWith("audio") ? "audio" : "other";
const filesReady = () => !!current && current.connected && current.files.length > 0
  && current.files.every((f) => f.printer === "current");
const padConnected = () => !!bluetoothState
  && bluetoothState.devices.some((d) => d.connected && kind(d.icon) === "gamepad");
const playing = () => !!current && (current.running || []).includes("doom");
const DONE = [filesReady, padConnected, playing];
const firstOpenStep = () => !filesReady() ? 0 : !padConnected() ? 1 : 2;

function showStep(i) {
  const entering = i !== step;
  step = i;
  document.querySelectorAll(".panel").forEach((p) => { p.hidden = Number(p.dataset.panel) !== i; });
  renderSteps();
  if (i === 1 && entering) loadBluetooth();
}

function renderSteps() {
  document.querySelectorAll(".step").forEach((b) => {
    const i = Number(b.dataset.step);
    b.classList.toggle("is-current", i === step);
    b.classList.toggle("is-done", DONE[i]());
  });
  document.querySelectorAll("[data-go]").forEach((b) => {
    const to = Number(b.dataset.go);
    const from = Number(b.closest(".panel").dataset.panel);
    b.classList.toggle("btn-fire", to > from && DONE[from]());   // "Weiter" glows once the step is done
  });
}

// Printer state

function render(s) {
  current = s;
  if (document.activeElement !== $("host")) $("host").value = s.host || "";
  setStatus($("conn"), s.connected ? `Verbunden mit ${s.host}` : "Nicht verbunden", s.connected ? "" : "off");
  $("conn-msg").textContent = !s.connected && s.error ? errorText(s) : "";
  renderFiles(s);
  renderPlay(s);
  renderSteps();
  // Steps 2 and 3 can be looked at any time; they say what is still missing.
  const notice = !s.connected ? "Noch nicht mit dem Drucker verbunden, siehe Schritt 1."
    : !filesReady() ? "Noch nicht alle Dateien aktuell auf dem Drucker, siehe Schritt 1." : "";
  document.querySelectorAll(".notice").forEach((p) => { p.textContent = notice; });
}

function renderFiles(s) {
  $("files").replaceChildren(...s.files.map((f) => {
    const li = document.createElement("li");
    const [label, state] = FILE_STATES[f.printer];
    li.innerHTML = `<span class="file-name"><b></b><small></small></span><span class="file-size">${size(f.size)}</span>
      <span class="status${state ? ` status--${state}` : ""}">${label}</span>`;
    li.querySelector("b").textContent = f.name;
    li.querySelector("small").textContent = FILES[f.name] || (f.name.endsWith(".wad") ? "Spieldaten" : "");
    return li;
  }));
  const task = s.task && s.task.name === "install" ? s.task : null;
  const copying = !!(task && !task.error);
  $("install").hidden = $("install-ok").hidden = false;
  if (filesReady()) $("install").hidden = true;
  else $("install-ok").hidden = true;
  $("install").disabled = !s.connected || copying || playing();
  $("progress").hidden = !copying;
  if (copying) {
    const percent = Math.round(100 * (task.done || 0) / (task.total || 1));
    $("progress-bar").style.width = `${percent}%`;
    $("install-msg").textContent = task.file ? `Kopiere ${task.file} … ${percent} %` : "Vergleiche …";
    $("install-msg").style.color = "var(--muted)";
  } else {
    $("install-msg").textContent = task && task.error ? task.error : "";
    $("install-msg").style.color = "";
  }
}

function renderPlay(s) {
  const printing = ["printing", "paused"].includes(s.print_state);
  const wads = (s.wads || []).slice().sort();
  const installed = ["doom", "run.sh"].every((n) => s.files.some((f) => f.name === n && f.printer !== "missing"));
  if (!s.connected) setStatus($("play-state"), "Nicht verbunden", "off");
  else if (playing()) setStatus($("play-state"), "DOOM läuft auf dem Display", "");
  else if (printing) setStatus($("play-state"), "Der Drucker druckt, DOOM ist gesperrt", "busy");
  else if (!installed || !wads.length) setStatus($("play-state"), "Noch nicht auf dem Drucker, siehe Schritt 1", "off");
  else setStatus($("play-state"), "Bereit", "");
  renderWads(wads);
  $("start").disabled = !s.connected || playing() || printing || !installed || !wads.length;
  $("stop").disabled = !s.connected || !playing();
  if (!bluetoothBusy) $("scan").disabled = !s.connected;
}

const selectedWad = () => document.querySelector('input[name="wad"]:checked')?.value;

function renderWads(wads) {
  const box = $("wads");
  if (box.dataset.list === wads.join()) return;   // keeps the choice while polling
  box.dataset.list = wads.join();
  const chosen = wads.includes(selectedWad()) ? selectedWad() : wads[0];
  box.replaceChildren(...wads.map((w) => {
    const [title, text] = WADS[w] || [w, ""];
    const label = document.createElement("label");
    label.className = "wad";
    label.innerHTML = '<input type="radio" name="wad"><span class="wad-title"></span><span class="wad-text"></span><span class="wad-file"></span>';
    const input = label.querySelector("input");
    input.value = w;
    input.checked = w === chosen;
    label.querySelector(".wad-title").textContent = title;
    label.querySelector(".wad-text").textContent = text;
    label.querySelector(".wad-file").textContent = w;
    return label;
  }));
}

async function refresh() {
  try {
    const s = await api("/api/state");
    render(s);
    if ($("log-box").open && s.connected) $("log").textContent = (await api("/api/log")).log;
  } catch {
    setStatus($("conn"), "Seite getrennt: launcher.py läuft nicht mehr", "error");
  }
}

// Faster while copying, so the progress bar moves smoothly.
async function poll() {
  await refresh();
  setTimeout(poll, current && current.task && !current.task.error ? 1000 : 3000);
}

// Bluetooth

const ICONS = {
  gamepad: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M7 7h10a5 5 0 0 1 4.9 6l-.8 4a2.5 2.5 0 0 1-4.3 1.2L14.5 16h-5l-2.3 2.2A2.5 2.5 0 0 1 2.9 17l-.8-4A5 5 0 0 1 7 7Zm0 3v1.5H5.5v1H7V14h1v-1.5h1.5v-1H8V10H7Zm9.5 0a1 1 0 1 0 0 2 1 1 0 0 0 0-2Zm-2 2a1 1 0 1 0 0 2 1 1 0 0 0 0-2Z"/></svg>',
  audio: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M4 9h4l5-4v14l-5-4H4V9Zm12.5 3a4.5 4.5 0 0 0-2.5-4v8a4.5 4.5 0 0 0 2.5-4Zm-2.5-8v2a7 7 0 0 1 0 12v2a9 9 0 0 0 0-16Z"/></svg>',
  other: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M11 2h1l6 5.5-4.5 4.5 4.5 4.5L12 22h-1v-8l-4.3 4.3-1.4-1.4L10.6 12 5.3 6.7l1.4-1.4L11 9.6V2Zm2 3.8v4.4l2.2-2.2L13 5.8Zm0 8v4.4l2.2-2.2-2.2-2.2Z"/></svg>',
};

// The controller or speaker shown in the big tile: the connected one, else a paired one.
function renderSlot(id, devices, which) {
  const slot = $(id);
  const mine = (devices || []).filter((d) => kind(d.icon) === which);
  const d = mine.find((x) => x.connected) || mine.find((x) => x.paired);
  slot.querySelector(".slot-name").textContent = !devices ? "…" : d ? d.name || d.address : "Noch keiner";
  setStatus(slot.querySelector(".status"),
    !devices ? "" : !d ? "Nicht gekoppelt" : d.connected ? "Verbunden" : "Gekoppelt, nicht verbunden",
    d && d.connected ? "" : "off");
  slot.classList.toggle("is-on", !!(d && d.connected));
}

function renderBluetooth(r) {
  bluetoothState = r.error ? null : r;
  renderSlot("slot-pad", r.devices, "gamepad");
  renderSlot("slot-audio", r.devices, "audio");
  renderSteps();
  if (r.error) {
    setStatus($("adapter"), errorText(r), r.error === "offline" ? "off" : "error");
    $("devices").replaceChildren();
    return;
  }
  const a = r.adapters[0];
  if (!a) setStatus($("adapter"), ERRORS.no_adapter, "busy");
  else setStatus($("adapter"), `${a.name || "Adapter"} (${a.address})${a.powered ? "" : ", aus"}`, a.powered ? "" : "off");
  $("devices").replaceChildren(...r.devices.filter((d) => d.paired || d.name).map((d) => {
    const li = document.createElement("li");
    const state = d.connected ? "Verbunden" : d.paired ? "Gekoppelt" : "Gefunden";
    li.innerHTML = `${ICONS[kind(d.icon)]}<span class="name"><b></b><small></small></span><span class="status ${d.connected ? "" : "status--off"}">${state}</span>`;
    li.querySelector("b").textContent = d.name || d.address;
    li.querySelector("small").textContent = d.address;
    const button = (label, command, fire) => {
      const b = document.createElement("button");
      b.className = `btn btn-small${fire ? " btn-fire" : ""}`;
      b.textContent = label;
      b.onclick = () => bluetooth(command, d.address, `${label} …`);
      li.append(b);
    };
    if (!d.paired) button("Koppeln", "pair", true);
    else button(d.connected ? "Trennen" : "Verbinden", d.connected ? "disconnect" : "connect", !d.connected);
    if (d.paired) button("Entfernen", "remove");
    return li;
  }));
}

async function bluetooth(command, address, busyText) {
  $("bt-msg").textContent = busyText;
  $("bt-msg").style.color = "var(--muted)";
  $("scan").disabled = bluetoothBusy = true;
  try {
    const r = await api("/api/bt", { command, address });
    renderBluetooth(r.error ? await api("/api/bt") : r);
    $("bt-msg").textContent = r.error ? errorText(r) : "";
    $("bt-msg").style.color = "";
  } finally {
    bluetoothBusy = false;
    $("scan").disabled = !(current && current.connected);
  }
}

async function loadBluetooth() {
  try {
    if (current && current.connected) renderBluetooth(await api("/api/bt"));
    else renderBluetooth({ error: "offline" });
  } catch {
    // launcher.py is gone; refresh() already says so in the header
  }
}

// Buttons

$("go").onclick = () => {
  document.body.classList.add("in-app");
  showStep(firstOpenStep());
};
$("home").onclick = () => {
  document.body.classList.remove("in-app");
  $("go").focus();
};
document.querySelectorAll(".step").forEach((b) => { b.onclick = () => showStep(Number(b.dataset.step)); });
document.querySelectorAll("[data-go]").forEach((b) => { b.onclick = () => showStep(Number(b.dataset.go)); });

$("connect").onclick = async () => {
  $("conn-msg").textContent = "";
  $("connect").disabled = true;
  try {
    render(await api("/api/connect", { host: $("host").value, password: $("password").value }));
  } finally {
    $("connect").disabled = false;
  }
  $("password").value = "";
  loadBluetooth();
};
for (const id of ["host", "password"]) $(id).onkeydown = (e) => { if (e.key === "Enter") $("connect").click(); };
$("install").onclick = async () => { await api("/api/install", {}); refresh(); };
$("start").onclick = async () => {
  const r = await api("/api/start", { wad: selectedWad() });
  $("play-msg").textContent = r.error ? errorText(r) : "";
  setTimeout(refresh, 1500);
};
$("stop").onclick = async () => { await api("/api/stop", {}); setTimeout(refresh, 1500); };
$("scan").onclick = () => bluetooth("scan", "", "Suche läuft, 10 Sekunden …");
$("log-box").ontoggle = refresh;

poll().then(loadBluetooth);
setInterval(() => { if (!bluetoothBusy) loadBluetooth(); }, 10000);
