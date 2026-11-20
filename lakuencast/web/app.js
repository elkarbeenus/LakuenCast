// LakuenCast – Oberfläche
const $ = (id) => document.getElementById(id);
let status = null;
let busy = false;
let volumeDragging = false;

async function api(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-LakuenCast": "1" },
    body: JSON.stringify(body || {}),
  });
  const data = await res.json();
  if (data.status) render(data.status);
  if (!data.ok && data.error) showNotice(data.error, "error");
  return data;
}

async function poll() {
  try {
    const res = await fetch("/api/status", { cache: "no-store" });
    render(await res.json());
  } catch (_) {
    $("conn-text").textContent = "LakuenCast läuft nicht";
    $("conn").classList.remove("on");
  }
}

const fmt = (s) => {
  if (!Number.isFinite(s)) return "0:00";
  s = Math.max(0, Math.floor(s));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  return (h ? `${h}:${String(m).padStart(2, "0")}` : `${m}`) + `:${String(sec).padStart(2, "0")}`;
};

function showNotice(text, kind = "warn") {
  const n = $("notice");
  n.hidden = !text;
  n.className = `banner ${kind}`;
  $("notice-text").textContent = text || "";
}

function render(st) {
  if (!st || st.app !== "LakuenCast") return;
  status = st;
  $("version").textContent = st.update.installedDisplay;

  // Verbindung
  const conn = $("conn");
  conn.classList.toggle("on", st.connected);
  $("conn-text").textContent = st.connected && st.selected ? st.selected.name : (st.selected ? "Bereit" : "Kein Gerät gewählt");

  // Geräte
  const list = $("devices");
  const sig = JSON.stringify([st.devices, st.selected && st.selected.id, st.scanning]);
  if (list.dataset.sig !== sig) {
    list.dataset.sig = sig;
    list.innerHTML = "";
    if (!st.devices.length) {
      const e = document.createElement("div");
      e.className = "empty";
      e.textContent = st.scanning ? "Suche Chromecasts im Netzwerk …" : "Kein Chromecast gefunden. Gleiches WLAN? Nochmal suchen.";
      list.appendChild(e);
    }
    for (const d of st.devices) {
      const b = document.createElement("button");
      b.className = "device" + (st.selected && st.selected.id === d.id ? " sel" : "");
      b.innerHTML = `<span>📺</span><span><div class="name"></div><div class="sub"></div></span>`;
      b.querySelector(".name").textContent = d.name;
      b.querySelector(".sub").textContent = [d.model, d.host].filter(Boolean).join(" · ");
      b.onclick = () => api("/api/select", { id: d.id });
      list.appendChild(b);
    }
  }
  $("scan").textContent = st.scanning ? "Suche …" : "Suchen";
  $("scan").disabled = st.scanning;

  // Läuft gerade
  const active = st.mode !== "idle";
  $("now").hidden = !active;
  if (active) {
    const stateText = { starting: "Startet …", mirroring: "Spiegeln", casting: "Video" }[st.mode] || st.mode;
    const ps = st.media.playerState;
    $("now-state").textContent = ps === "PAUSED" ? "Pausiert" : ps === "BUFFERING" ? "Lädt …" : stateText;
    $("now-title").textContent = st.mode === "starting" ? "Verbinde mit dem Fernseher …" : (st.title || "Läuft");
    const isVideo = st.mode === "casting";
    $("progress-wrap").hidden = !(isVideo && st.media.duration);
    $("media-controls").hidden = !isVideo;
    if (isVideo && st.media.duration) {
      $("t-cur").textContent = fmt(st.media.currentTime);
      $("t-dur").textContent = fmt(st.media.duration);
      $("t-fill").style.width = `${Math.min(100, (100 * st.media.currentTime) / st.media.duration)}%`;
    }
    $("playpause").textContent = ps === "PAUSED" ? "▶ Weiter" : "❚❚ Pause";
  }

  $("mirror").disabled = busy || st.mode === "starting" || st.mode === "mirroring";
  $("stop").disabled = !active;
  if (document.activeElement !== $("quality")) $("quality").value = st.quality;
  if (!volumeDragging && st.volume && typeof st.volume.level === "number") {
    $("volume").value = Math.round(st.volume.level * 100);
  }
  if (document.activeElement !== $("autostart")) $("autostart").checked = st.autostart;

  // Hinweise
  if (st.error) showNotice(st.error, "error");
  else if (st.message) showNotice(st.message, "warn");
  else if (!$("notice").dataset.local) showNotice(null);

  // Update
  const u = st.update;
  $("update").hidden = !u.available;
  if (u.available) {
    $("upd-version").textContent = u.available.display;
    const btn = $("upd-btn");
    $("upd-bar").hidden = u.state !== "downloading";
    $("upd-fill").style.width = `${Math.round(u.progress * 100)}%`;
    if (!u.canInstall) {
      $("upd-text").textContent = "Nicht als AppImage gestartet – neue Version bitte bei GitHub laden.";
      btn.textContent = "GitHub";
      btn.disabled = false;
    } else if (["downloading", "installing", "restarting"].includes(u.state)) {
      $("upd-text").textContent = { downloading: "Lade herunter …", installing: "Installiere …", restarting: "Starte neu …" }[u.state];
      btn.disabled = true;
    } else {
      $("upd-text").textContent = u.error ? `Fehler: ${u.error}` : "Lädt die neue Version, ersetzt das AppImage und startet LakuenCast neu.";
      btn.textContent = "Update";
      btn.disabled = false;
    }
  }
}

// ---------- Aktionen ----------
$("scan").onclick = () => api("/api/scan");
$("add-form").onsubmit = (e) => { e.preventDefault(); const h = $("add-host").value.trim(); if (h) api("/api/add", { host: h }); };
$("mirror").onclick = async () => {
  busy = true;
  $("mirror").disabled = true;
  $("mirror").innerHTML = "Startet …<br><small>einen Moment</small>";
  try { await api("/api/mirror", { quality: $("quality").value }); }
  finally {
    busy = false;
    $("mirror").innerHTML = "🖥️ Bildschirm spiegeln<br><small>mit Ton</small>";
    poll();
  }
};
$("stop").onclick = () => api("/api/stop");
$("playpause").onclick = () => api(status && status.media.playerState === "PAUSED" ? "/api/play" : "/api/pause");
document.querySelectorAll("[data-seek]").forEach((b) => (b.onclick = () => api("/api/seek", { delta: +b.dataset.seek })));
$("quality").onchange = () => { if (status && status.mode === "mirroring") $("mirror").click(); };
$("volume").oninput = () => { volumeDragging = true; };
$("volume").onchange = async () => { await api("/api/volume", { level: $("volume").value / 100 }); volumeDragging = false; };
$("autostart").onchange = () => api("/api/autostart", { on: $("autostart").checked });
$("notice-x").onclick = () => { showNotice(null); api("/api/dismiss"); };
$("check").onclick = async () => {
  $("check").textContent = "Suche …";
  await api("/api/update/check");
  $("check").textContent = status && status.update.available ? "Update gefunden" : "Aktuell";
};
$("upd-btn").onclick = () => {
  if (status && !status.update.canInstall) window.open(status.update.available.page, "_blank");
  else api("/api/update/install");
};

poll();
setInterval(poll, 1000);
