// Microinteracciones: el detalle chico que hace que la interfaz se sienta viva.
//  · split()      titulares que entran palabra por palabra, detrás de una máscara
//  · num()        cifras que ruedan dígito a dígito (odómetro)
//  · indicator()  un único marcador que se desliza entre pestañas
//  · roll()       texto que rueda al pasar el puntero
//  · scramble()   etiquetas mono que se resuelven de izquierda a derecha
//  · rotator()    sugerencias que rotan en el campo de texto
//  · imán, cursor con etiqueta, sonidos y vibración
// Todo respeta «menos movimiento»: sin animación, el resultado final aparece igual.

import { state, reducedMotion } from "./state.js";
import { h } from "./ui.js";

const fine = () => matchMedia("(hover: hover) and (pointer: fine)").matches;

/* ───────────── Titulares por palabra ─────────────
   split("Buen día, ", ["em", "Mateo."]) → cada palabra sube desde su máscara. */
export function split(...parts) {
  let i = 0;
  const out = [];
  for (const part of parts) {
    const [tag, text] = Array.isArray(part) ? part : [null, part];
    for (const tok of String(text).split(/(\s+)/)) {
      if (!tok) continue;
      if (/^\s+$/.test(tok)) { out.push(" "); continue; }
      const inner = h("span", { style: { "--wi": i++ } }, tag ? h(tag, null, tok) : tok);
      out.push(h("span", { class: "w" }, inner));
    }
  }
  return h("span", { class: "split" }, out);
}

/* ───────────── Cifras que ruedan ─────────────
   num("15:30", "proximo") recuerda el valor anterior de esa clave y rueda desde ahí. */
const lastNum = {};
export function num(value, key) {
  const el = h("span", { class: "num", role: "img" });
  let cols = [];
  const build = (str, from) => {
    cols = [];
    el.replaceChildren(...[...str].map((ch, i) => {
      if (!/\d/.test(ch)) return h("span", { class: "nx", "aria-hidden": "true" }, ch);
      const strip = h("span", { class: "ns", style: { "--n": from?.[i] ?? 0, "--di": i } }, [..."0123456789"].map((d) => h("b", null, d)));
      cols.push([i, strip]);
      return h("span", { class: "nd", "aria-hidden": "true" }, strip);
    }));
  };
  el.set = (v) => {
    const str = String(v);
    el.setAttribute("aria-label", str);
    const prev = el.dataset.v;
    if (prev == null || prev.length !== str.length) {
      const from = key && lastNum[key]?.length === str.length ? lastNum[key] : null;
      build(str, from && !reducedMotion() ? from : reducedMotion() ? str : null);
    }
    el.dataset.v = str;
    if (key) lastNum[key] = str;
    const apply = () => { for (const [i, strip] of cols) strip.style.setProperty("--n", str[i]); };
    if (prev == null && !reducedMotion()) requestAnimationFrame(() => requestAnimationFrame(apply)); else apply();
  };
  el.set(value);
  return el;
}

/* ───────────── Marcador deslizante ─────────────
   Un solo elemento que viaja hasta la opción activa. Como las pantallas se
   reconstruyen, recuerda la última posición por clave y parte desde ahí. */
const lastPos = {};
export function indicator(container, active, key) {
  const bar = h("i", { class: "ind", "aria-hidden": "true" });
  container.append(bar);
  const measure = (el) => {
    if (!el || !el.isConnected) return null;
    const c = container.getBoundingClientRect(), a = el.getBoundingClientRect();
    if (!a.width) return null;
    return { x: a.left - c.left - container.clientLeft, y: a.top - c.top - container.clientTop, w: a.width, hh: a.height };
  };
  const put = (p, animate) => {
    bar.classList.toggle("go", animate);
    bar.style.setProperty("--x", `${p.x}px`); bar.style.setProperty("--y", `${p.y}px`);
    bar.style.setProperty("--w", `${p.w}px`); bar.style.setProperty("--hh", `${p.hh}px`);
    bar.style.opacity = "1";
  };
  const to = (el) => {
    const p = measure(el);
    if (!p) { bar.style.opacity = "0"; delete lastPos[key]; return; }
    const from = lastPos[key];
    lastPos[key] = p;
    if (from && !reducedMotion()) { put(from, false); bar.getBoundingClientRect(); put(p, true); } else put(p, false);
  };
  let current = active;
  const move = (el) => { current = el; to(el); };
  // Si cambia el ancho (tipografías que terminan de cargar, giro del teléfono), se reubica sin animar.
  const settle = () => { if (!bar.isConnected) return; const p = measure(current); if (p) { lastPos[key] = p; put(p, false); } };
  requestAnimationFrame(() => to(active));
  document.fonts?.ready.then(settle);
  if (window.ResizeObserver) new ResizeObserver(settle).observe(container);
  return { to: move };
}

/* ───────────── Texto que rueda al pasar ───────────── */
export const roll = (text) => h("span", { class: "roll", "data-t": text }, h("span", null, text));

/* ───────────── Etiqueta que se resuelve ───────────── */
const GLYPHS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789·—/";
export function scramble(el, text) {
  if (reducedMotion() || el.textContent === text) { el.textContent = text; return; }
  const total = 14;
  let tick = 0;
  clearInterval(el._scr);
  el._scr = setInterval(() => {
    tick++;
    const solved = Math.floor((tick / total) * text.length);
    el.textContent = [...text].map((ch, i) => (i < solved || ch === " " ? ch : GLYPHS[(Math.random() * GLYPHS.length) | 0])).join("");
    if (tick >= total || !el.isConnected) { clearInterval(el._scr); el.textContent = text; }
  }, 32);
}

/* ───────────── Sugerencias que rotan ───────────── */
export function rotator(texts, ms = 3800) {
  let i = 0;
  const el = h("span", { class: "rot", "aria-hidden": "true" }, h("span", null, texts[0]));
  el.current = () => texts[i];
  if (texts.length < 2) return el;
  const timer = setInterval(() => {
    if (!el.isConnected) { clearInterval(timer); return; }
    if (document.hidden || el.dataset.hold) return;
    i = (i + 1) % texts.length;
    const old = el.firstElementChild, next = h("span", { class: "in" }, texts[i]);
    if (reducedMotion()) { el.replaceChildren(next); next.classList.remove("in"); return; }
    old?.classList.add("out");
    el.append(next);
    requestAnimationFrame(() => requestAnimationFrame(() => next.classList.remove("in")));
    setTimeout(() => old?.remove(), 460);
  }, ms);
  return el;
}

/* ───────────── Reloj vivo ───────────── */
export function clock() {
  const fmt = () => { const d = new Date(); return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`; };
  const el = num(fmt(), "clock");
  const timer = setInterval(() => { if (!el.isConnected) { clearInterval(timer); return; } const v = fmt(); if (v !== el.dataset.v) el.set(v); }, 5000);
  return el;
}

/* ───────────── Sonido y vibración (opcionales) ───────────── */
let ac = null;
export function sfx(kind = "tick") {
  if (!state.settings.sounds) return;
  try {
    ac ||= new (window.AudioContext || window.webkitAudioContext)();
    const t = ac.currentTime, o = ac.createOscillator(), g = ac.createGain();
    const [f0, f1, dur] = { tick: [1500, 1500, 0.05], ok: [740, 1110, 0.12], undo: [620, 410, 0.12], open: [520, 660, 0.09] }[kind] || [1200, 1200, 0.05];
    o.type = "sine"; o.frequency.setValueAtTime(f0, t); o.frequency.exponentialRampToValueAtTime(f1, t + dur);
    g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(0.035, t + 0.006); g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    o.connect(g).connect(ac.destination); o.start(t); o.stop(t + dur + 0.02);
  } catch { /* sin audio: no pasa nada */ }
}
export const haptic = (ms = 8) => { try { navigator.vibrate?.(ms); } catch { /* no soportado */ } };

/* ───────────── Cursor con etiqueta ─────────────
   Aparece solo sobre lo que se puede tocar en el lienzo (y sobre [data-cursor]):
   un aro que dice qué va a pasar. */
let fieldLabel = null, domLabel = null, cx = 0, cy = 0, tx = 0, ty = 0, moving = false;
const cursorEl = document.getElementById("cursor");
function paintCursor() {
  const label = domLabel ?? fieldLabel;
  if (!cursorEl) return;
  const on = !!label && fine() && !reducedMotion();
  cursorEl.classList.toggle("on", on);
  document.documentElement.classList.toggle("cur-on", on && !domLabel);
  if (label) cursorEl.firstElementChild.textContent = label;
}
export function cursorLabel(label) { fieldLabel = label; paintCursor(); }
function cursorLoop() {
  cx += (tx - cx) * 0.24; cy += (ty - cy) * 0.24;
  cursorEl.style.translate = `${cx.toFixed(1)}px ${cy.toFixed(1)}px`;
  if (Math.abs(tx - cx) + Math.abs(ty - cy) > 0.3) requestAnimationFrame(cursorLoop); else moving = false;
}

/* ───────────── Arranque: imán, cursor y presión ───────────── */
export function initMicro() {
  let magnet = null;
  const release = () => { if (!magnet) return; magnet.classList.remove("pull"); magnet.style.removeProperty("--mx"); magnet.style.removeProperty("--my"); magnet = null; };
  addEventListener("pointermove", (e) => {
    if (e.pointerType !== "mouse") return;
    tx = e.clientX; ty = e.clientY;
    if (cursorEl && !moving) { moving = true; if (!cursorEl.classList.contains("on")) { cx = tx; cy = ty; } requestAnimationFrame(cursorLoop); }
    const t = e.target instanceof Element ? e.target : null;
    const d = t?.closest("[data-cursor]")?.dataset.cursor ?? null;
    if (d !== domLabel) { domLabel = d; paintCursor(); }
    if (reducedMotion()) return;
    const m = t?.closest("[data-magnet]") ?? null;
    if (m !== magnet) release();
    if (m && !m.disabled) {
      magnet = m;
      const r = m.getBoundingClientRect(), k = +m.dataset.magnet || 0.3;
      m.classList.add("pull");
      m.style.setProperty("--mx", `${((e.clientX - (r.left + r.width / 2)) * k).toFixed(1)}px`);
      m.style.setProperty("--my", `${((e.clientY - (r.top + r.height / 2)) * k).toFixed(1)}px`);
    }
  }, { passive: true });
  document.addEventListener("pointerleave", () => { release(); domLabel = null; fieldLabel = null; paintCursor(); });
  addEventListener("pointerdown", () => cursorEl?.classList.add("down"));
  addEventListener("pointerup", () => cursorEl?.classList.remove("down"));
}
