// Estado único de la demo, persistido en localStorage. Los cambios se hacen con
// `update(fn)`, que guarda y avisa a los suscriptores.

import { seed } from "./data.js";

const KEY = "ynara.demo.v2";
const subs = new Set();

function load() {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) || "null");
    if (raw && raw.user && raw.events) {
      const base = seed();
      // v5: la demo pasa al tema de la guía de marca (marfil). Se aplica una sola vez.
      const fresh = !raw.settings?.v5;
      return { ...base, ...raw, user: { ...base.user, ...raw.user }, settings: { ...base.settings, ...raw.settings, ...(fresh ? { theme: "light", v5: true } : {}) } };
    }
  } catch { /* estado corrupto: se reinicia */ }
  return seed();
}

export const state = load();

function save() {
  try { localStorage.setItem(KEY, JSON.stringify(state)); } catch { /* sin almacenamiento: sigue en memoria */ }
}

/** Aplica un cambio. `silent` evita el re-render (para actualizaciones puntuales del DOM). */
export function update(fn, { silent = false } = {}) {
  fn(state);
  save();
  if (!silent) for (const s of subs) s(state);
}

export function subscribe(fn) {
  subs.add(fn);
  return () => subs.delete(fn);
}

export function reset() {
  localStorage.removeItem(KEY);
  location.hash = "";
  location.reload();
}

export function exportData() {
  const data = { exportado: new Date().toISOString(), nota: "Datos de demostración de Ynara", ...state };
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "ynara-mis-datos.json";
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}

/** Aplica tema, modo y preferencias de accesibilidad al documento. */
export function applyPrefs() {
  const c = document.documentElement.classList;
  const s = state.settings;
  const dark = s.theme === "dark" || (s.theme === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
  c.toggle("theme-dark", dark);
  c.remove("text-sm", "text-md", "text-lg");
  c.add(`text-${s.textSize}`);
  c.toggle("motion-off", s.motion === "off");
  c.toggle("motion-on", s.motion === "on");
  document.documentElement.dataset.mode = state.mode;
  document.querySelector('meta[name="color-scheme"]')?.setAttribute("content", dark ? "dark" : "light");
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", dark ? "#242c3f" : "#faf9f5");
}

export function reducedMotion() {
  const m = state.settings.motion;
  if (m === "off") return true;
  if (m === "on") return false;
  return matchMedia("(prefers-reduced-motion: reduce)").matches;
}

export function setMode(id, opts) {
  if (state.mode === id) return;
  update((s) => { s.mode = id; }, opts);
  applyPrefs();
  dispatchEvent(new CustomEvent("ynara:modo"));
}

matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
  if (state.settings.theme === "system") { applyPrefs(); for (const s of subs) s(state); }
});
