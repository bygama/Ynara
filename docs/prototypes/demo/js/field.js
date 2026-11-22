// Fondo vivo: la «red de memoria» de la guía de marca (nodos y vínculos).
// En las pantallas de lectura vive rala en los márgenes. En la voz y en la
// bienvenida ocupa todo el espacio. No lleva información ni captura clics.
//  · el cursor enciende los vínculos cercanos
//  · un pulso la recorre cuando cambia algo (pantalla, modo, Ynara piensa)
//  · en la voz, ondas que salen del diamante al ritmo de lo que se dice

import { reducedMotion } from "./state.js";

let cv, ctx, W = 0, H = 0, nodes = [], links = [], raf = 0, view = "hoy";
let rgb = "47 90 166", alpha = 0.16, mx = -9999, my = -9999;
let level = 0, pulseAt = -1e9, pulseX = 0, pulseY = 0;

const full = () => view === "voice" || view === "intro";
/** Centro horizontal del contenido: en escritorio, lo que queda a la derecha de la barra lateral. */
let rail = 0;
const mid = () => (full() ? W / 2 : (W + rail) / 2);
/** Centro del diamante de la voz, o de la tarjeta de bienvenida. */
const heart = () => (view === "voice" ? { x: mid(), y: H * 0.46, r: Math.min(270, W * 0.6) } : { x: mid(), y: H / 2, r: Math.min(300, W * 0.42) });

function build() {
  nodes = []; links = [];
  if (W < 900 && !full()) return;                        // angosto: fondo liso, salvo voz y bienvenida
  const cell = W < 700 ? 110 : 150, band = 340, c = heart();
  let seed = 7;
  const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
  for (let y = cell / 2; y < H + cell; y += cell) {
    for (let x = cell / 2; x < W + cell; x += cell) {
      const nx = x + (rnd() - 0.5) * cell * 0.8, ny = y + (rnd() - 0.5) * cell * 0.8, dia = rnd() < 0.12;
      // Qué tan cerca del contenido está: ahí la red se disuelve.
      const gap = full() ? Math.hypot(nx - c.x, ny - c.y) - c.r : nx < rail ? -1 : Math.abs(nx - mid()) - band;
      if (gap < 0) continue;
      nodes.push({ x: nx, y: ny, ph: rnd() * 6.28, dia, edge: Math.min(1, gap / 220) });
    }
  }
  const seen = new Set();
  nodes.forEach((a, i) => {
    nodes.map((b, j) => ({ j, d: Math.hypot(a.x - b.x, a.y - b.y) })).filter((o) => o.j !== i && o.d < cell * 1.55).sort((p, q) => p.d - q.d).slice(0, 2)
      .forEach(({ j }) => { const k = i < j ? `${i}-${j}` : `${j}-${i}`; if (!seen.has(k)) { seen.add(k); links.push([i, j]); } });
  });
}

function draw(t) {
  ctx.clearRect(0, 0, W, H);
  const still = reducedMotion(), c = heart(), voice = view === "voice";
  const age = t - pulseAt, front = age * 0.9, fade = Math.max(0, 1 - age / 1700);
  // Cuánto se enciende un punto: cursor + pulso que pasa + ondas de la voz.
  const glow = (x, y) => {
    let g = Math.max(0, 1 - Math.hypot(x - mx, y - my) / 200);
    if (fade > 0) g += Math.max(0, 1 - Math.abs(Math.hypot(x - pulseX, y - pulseY) - front) / 110) * fade * 0.9;
    if (voice && level > 0.01) g += level * 0.9 * (0.5 + 0.5 * Math.sin(Math.hypot(x - c.x, y - c.y) * 0.028 - t * 0.007));
    return Math.min(1.4, g);
  };
  const pos = nodes.map((n) => still ? n : { ...n, x: n.x + Math.sin(t * 0.00022 + n.ph) * 7, y: n.y + Math.cos(t * 0.00018 + n.ph) * 7 });
  ctx.lineWidth = 1;
  for (const [i, j] of links) {
    const a = pos[i], b = pos[j], p = glow((a.x + b.x) / 2, (a.y + b.y) / 2);
    ctx.strokeStyle = `rgb(${rgb} / ${(alpha * 0.8 + p * 0.4) * Math.min(a.edge, b.edge)})`;
    ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
  }
  for (const n of pos) {
    const p = glow(n.x, n.y), r = (n.dia ? 3.4 : 2.2) + p * 1.8;
    ctx.fillStyle = `rgb(${rgb} / ${(alpha * 2 + p * 0.5) * n.edge})`;
    ctx.beginPath();
    if (n.dia) { ctx.moveTo(n.x, n.y - r); ctx.lineTo(n.x + r, n.y); ctx.lineTo(n.x, n.y + r); ctx.lineTo(n.x - r, n.y); }
    else ctx.arc(n.x, n.y, r, 0, 6.2832);
    ctx.fill();
  }
}

function loop(t) { draw(t); raf = reducedMotion() || document.hidden || !nodes.length ? 0 : requestAnimationFrame(loop); }
function start() { cancelAnimationFrame(raf); raf = requestAnimationFrame(loop); }

function resize() {
  const dpr = Math.min(2, devicePixelRatio || 1);
  W = innerWidth; H = innerHeight;
  rail = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--rail")) || 0;
  cv.width = W * dpr; cv.height = H * dpr;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  build(); start();
}

export function refreshField() {
  if (!cv) return;
  const cs = getComputedStyle(document.documentElement);
  rgb = cs.getPropertyValue("--net").trim() || rgb;
  alpha = parseFloat(cs.getPropertyValue("--net-a")) || alpha;
  start();
}

/** Un pulso que recorre la red desde un punto (por defecto, el centro). */
export function pulse(x = mid(), y = H * 0.4) {
  if (reducedMotion()) return;
  pulseAt = performance.now(); pulseX = x; pulseY = y;
}

export function initField() {
  cv = document.getElementById("field");
  ctx = cv.getContext("2d");
  addEventListener("resize", resize);
  addEventListener("pointermove", (e) => { if (e.pointerType === "mouse") { mx = e.clientX; my = e.clientY; } }, { passive: true });
  document.addEventListener("pointerleave", () => { mx = my = -9999; });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) start(); });
  resize(); refreshField();
}

/** Estado del diamante de la voz y del chat: lo leen los estilos y la red. */
let orbEl = null;
export function setOrb(next) {
  if (next.state) {
    if (next.state === "thinking" && document.documentElement.dataset.orb !== "thinking") pulse(...(view === "voice" ? [W / 2, H * 0.36] : []));
    document.documentElement.dataset.orb = next.state;
  }
  if (next.level != null) {
    level = next.level;
    if (!orbEl?.isConnected) orbEl = document.querySelector(".voice-orb");
    orbEl?.style.setProperty("--level", next.level.toFixed(3));
  }
}
export function relayout() { if (cv) resize(); }
export function setView(name) {
  if (name === view) return;
  const was = full();
  view = name;
  if (!cv) return;
  if (was !== full() || full()) { build(); start(); if (full()) pulse(); }   // la red cambia de forma al entrar o salir de la voz
}
export const getView = () => view;
