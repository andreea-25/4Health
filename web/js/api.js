// Client comun pentru API-ul 4Health. Inclus în toate paginile.
// Paginile sunt servite de FastAPI la /web/..., deci API-ul e pe același origin.
// Dacă deschizi paginile cu Live Server (port 5500), API-ul e căutat pe localhost:8000.
const API_BASE = location.port === "5500" ? "http://localhost:8000" : "";

const Auth = {
  get token() { try { return localStorage.getItem("h4_token"); } catch { return null; } },
  get role() { try { return localStorage.getItem("h4_role"); } catch { return null; } },
  get name() { try { return localStorage.getItem("h4_name"); } catch { return null; } },
  save({ access_token, role, name }) {
    localStorage.setItem("h4_token", access_token);
    localStorage.setItem("h4_role", role);
    localStorage.setItem("h4_name", name);
  },
  logout() {
    ["h4_token", "h4_role", "h4_name"].forEach(k => localStorage.removeItem(k));
    location.href = "index.html";
  },
  // Redirecționează la login dacă utilizatorul nu are rolul cerut.
  require(role) {
    if (!this.token || (role && this.role !== role)) {
      location.href = "index.html?next=" + encodeURIComponent(location.pathname.split("/").pop());
      throw new Error("neautentificat");
    }
  },
  homeFor(role) { return role === "doctor" ? "interfata_medic.html" : "interfata_pacient.html"; },
};

async function api(path, { method = "GET", body, auth = true } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (auth && Auth.token) headers.Authorization = "Bearer " + Auth.token;
  const res = await fetch(API_BASE + path, { method, headers, body: body ? JSON.stringify(body) : undefined });
  if (res.status === 401 && auth && Auth.token) { Auth.logout(); return; }
  if (res.status === 204) return null;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    let msg = data.detail || "Eroare server.";
    if (Array.isArray(msg)) msg = msg.map(e => (e.loc ? e.loc.slice(-1)[0] + ": " : "") + e.msg).join("; ");
    const err = new Error(msg);
    err.status = res.status;
    throw err;
  }
  return data;
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function fmtDate(iso) {
  if (!iso) return "";
  return new Date(iso).toLocaleDateString("ro-RO", { day: "2-digit", month: "short", year: "numeric" });
}

function toast(msg, type = "ok") {
  let t = document.getElementById("toast");
  if (!t) { t = document.createElement("div"); t.id = "toast"; document.body.appendChild(t); }
  t.textContent = msg;
  t.className = "show " + type;
  clearTimeout(t._h);
  t._h = setTimeout(() => (t.className = ""), 3500);
}

// Randare comună a medicilor recomandați (folosită în chat și în profilul pacientului)
function doctorCards(doctors) {
  if (!doctors || !doctors.length) return "";
  return `<div class="doctor-list">${doctors.map(d => `
    <div class="doctor-card">
      <div class="doctor-name">${esc(d.name)}</div>
      <div class="muted">${esc(d.specialty_label)} · ${esc(d.clinic)}${d.city ? ", " + esc(d.city) : ""}</div>
    </div>`).join("")}</div>`;
}
