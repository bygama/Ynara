// Utilidades de interfaz: creación de nodos, íconos de marca, logo, orbe,
// toasts, diálogos y formato de fechas. Sin dependencias.

const SVG = "http://www.w3.org/2000/svg";

/** Hyperscript mínimo. `props`: class, dataset, on<Evento>, y atributos. */
export function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (k === "style" && typeof v === "object") {
      for (const [p, val] of Object.entries(v)) { if (val == null) continue; p.startsWith("--") ? el.style.setProperty(p, val) : (el.style[p] = val); }
    }
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === "value") el.value = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  append(el, kids);
  return el;
}

export function append(el, kids) {
  for (const k of kids.flat(Infinity)) {
    if (k == null || k === false) continue;
    el.append(k instanceof Node ? k : document.createTextNode(String(k)));
  }
  return el;
}

export function clear(el) {
  while (el.firstChild) el.firstChild.remove();
  return el;
}

// ── Iconografía (grilla 44×44, trazo uniforme; set de marca + utilitarios) ──
const ICONS = {
  // Set de marca (geometría de la guía de identidad)
  idea: [["c", 22, 19, 10], ["p", "M17 32h10M18.5 36h7"]],
  conexion: [["c", 12, 22, 5.5], ["c", 32, 22, 5.5], ["p", "M17.5 22h9"]],
  memoria: [["p", "M8 16 22 9 36 16 22 23Z"], ["p", "M8 23 22 30 36 23"], ["p", "M8 30 22 37 36 30"]],
  nota: [["r", 11, 8, 22, 28, 3], ["p", "M16 16h12M16 21h12M16 26h8"]],
  buscar: [["c", 19, 19, 9], ["p", "M26 26 35 35"]],
  dialogo: [["p", "M9 12h26a3 3 0 0 1 3 3v13a3 3 0 0 1-3 3H21l-7 6v-6h-5a3 3 0 0 1-3-3V15a3 3 0 0 1 3-3Z"]],
  recordatorio: [["c", 22, 23, 13], ["p", "M22 15v8l6 4"]],
  adaptacion: [["p", "M10 19a12 12 0 0 1 21-7"], ["p", "M25 5l7 7-7 4"], ["p", "M34 25a12 12 0 0 1-21 7"], ["p", "M19 39l-7-7 7-4"]],
  foco: [["r", 13, 13, 18, 18, 4, 45], ["c", 22, 22, 3.4]],
  red: [["c", 22, 11, 3.4], ["c", 11, 32, 3.4], ["c", 33, 32, 3.4], ["p", "M22 11 11 32M22 11 33 32M11 32h22"]],
  enviar: [["p", "M6 23 38 7 29 38 21 27Z"], ["p", "M38 7 21 27"]],
  detener: [["r", 14, 14, 16, 16, 3]],
  atras: [["p", "M26 12 15 22 26 32"]],
  cerrar: [["p", "M14 14 30 30M30 14 14 30"]],
  chevron: [["p", "M12 18 22 28 32 18"]],
  // Utilitarios nuevos, misma grilla y trazo
  mic: [["r", 16, 6, 12, 22, 6], ["p", "M9 21a13 13 0 0 0 26 0M22 34v6"]],
  micoff: [["r", 16, 6, 12, 22, 6], ["p", "M9 21a13 13 0 0 0 26 0M22 34v6M7 7l30 30"]],
  agenda: [["r", 8, 10, 28, 26, 4], ["p", "M8 18h28M16 6v8M28 6v8"]],
  persona: [["c", 22, 15, 7], ["p", "M9 37a13 13 0 0 1 26 0"]],
  mas: [["p", "M22 11v22M11 22h22"]],
  check: [["p", "M11 23 19 31 33 14"]],
  copiar: [["r", 15, 15, 20, 22, 4], ["p", "M11 28V11a4 4 0 0 1 4-4h14"]],
  editar: [["p", "M8 36l3-10L29 8l7 7-18 18-10 3Z"], ["p", "M25 12l7 7"]],
  borrar: [["p", "M9 13h26M17 13V8h10v5M13 13l2 24h14l2-24"]],
  deshacer: [["p", "M15 10 7 18l8 8"], ["p", "M7 18h18a10 10 0 0 1 0 20h-6"]],
  sol: [["c", 22, 22, 7], ["p", "M22 5v5M22 34v5M5 22h5M34 22h5M10 10l3.5 3.5M30.5 30.5 34 34M10 34l3.5-3.5M30.5 13.5 34 10"]],
  luna: [["p", "M34 26A14 14 0 0 1 18 10a14 14 0 1 0 16 16Z"]],
  teclado: [["r", 5, 12, 34, 20, 4], ["p", "M12 19h2M19 19h2M26 19h2M32 19h1M13 26h18"]],
  play: [["p", "M15 9v26l20-13Z"]],
  util: [["p", "M8 20h7v17H8zM15 21l6-13c3 0 5 2 4 6l-1 5h9c2 0 3 2 3 4l-3 11c-.5 2-2 3-4 3H15"]],
  noutil: [["p", "M8 7h7v17H8zM15 23l6 13c3 0 5-2 4-6l-1-5h9c2 0 3-2 3-4L33 10c-.5-2-2-3-4-3H15"]],
  ajustes: [["p", "M8 14h14M30 14h6M8 30h6M22 30h14"], ["c", 26, 14, 4], ["c", 18, 30, 4]],
  descargar: [["p", "M22 7v20M13 19l9 9 9-9M8 36h28"]],
  pausa: [["p", "M15 10v24M29 10v24"]],
  flecha: [["p", "M10 22h24M25 12l10 10-10 10"]],
};

export function icon(name, size) {
  const svg = document.createElementNS(SVG, "svg");
  svg.setAttribute("viewBox", "0 0 44 44");
  svg.setAttribute("class", "icon");
  svg.setAttribute("aria-hidden", "true");
  if (size) { svg.style.width = `${size}px`; svg.style.height = `${size}px`; }
  for (const s of ICONS[name] || ICONS.foco) {
    let el;
    if (s[0] === "c") {
      el = document.createElementNS(SVG, "circle");
      el.setAttribute("cx", s[1]); el.setAttribute("cy", s[2]); el.setAttribute("r", s[3]);
    } else if (s[0] === "r") {
      el = document.createElementNS(SVG, "rect");
      el.setAttribute("x", s[1]); el.setAttribute("y", s[2]); el.setAttribute("width", s[3]); el.setAttribute("height", s[4]); el.setAttribute("rx", s[5]);
      if (s[6]) el.setAttribute("transform", `rotate(${s[6]} 22 22)`);
    } else {
      el = document.createElementNS(SVG, "path");
      el.setAttribute("d", s[1]);
    }
    svg.append(el);
  }
  return svg;
}

// ── Isotipo oficial (geometría 1:1 del artwork de marca) ──
const LOGO_MAIN = "M820.99,335.09c-49.27,52.6-62.12,60.65-106.09,109.14-50.19,55.36-66.07,80.85-74.08,94.62-6.88,11.82-23.51,41.74-36.59,81.69-13.87,42.4-18.58,80.28-19.97,111.23-.08,1.88-.16,3.74-.22,5.57-.76,22.58.15,41.17.22,54.77v101.74c0,11.14-9.03,20.16-20.16,20.16h-117.54c-11.14,0-20.17-9.02-20.17-20.16v-197.78c-.54-4.12-1.13-8.35-1.78-12.69-4.88-32.62-9.95-51.11-13.58-62.84-3.98-12.89-14.42-43.59-36.59-81.69-8.01-13.77-23.89-39.26-74.08-94.62-43.96-48.49-56.82-56.54-106.09-109.14-15.74-16.8-29.13-31.13-40.04-43.19-.01-.02-.03-.03-.05-.05-5.07-5.61-9.6-10.72-13.57-15.37-.03-.03-.05-.05-.07-.08-16.3-19.04-23.38-30.26-20.31-35.19.12-.19.25-.37.39-.55,0,0,.13-.17.28-.32,12.69-13.4,73.8,9.52,73.8,9.52,4.39,1.65,65.61,24.95,121.94,62.84,3.75,2.52,7.39,5.05,7.39,5.05,11.75,8.16,32.79,23.44,56.95,44.86.02.01.03.03.05.04,15.91,14.11,33.17,30.86,50.01,49.98.12.12.22.23.31.34.27.3.49.55.67.76.12.14.23.26.31.36.06.08.13.15.2.23.05.05.07.08.07.08.05.05.09.11.14.16,21.5,24.66,42.24,53.19,58.51,84.97h0c5.77,11.28,10.98,22.96,15.46,35.02.32.84.62,1.68.92,2.52.3-.85.61-1.69.93-2.52.25-.66.49-1.32.75-1.98,46.96-123.85,170.08-206.9,189.31-219.87,84.27-56.85,185.98-88.22,196.41-71.49,5.45,8.73-20.97,37.23-74.04,93.88Z";
let logoSeq = 0;

/** `variant`: "auto" (color en claro, marfil en Noche) | "color" | "mono". */
export function logo(size = 28, variant = "auto") {
  const id = `lg${++logoSeq}`;
  const svg = document.createElementNS(SVG, "svg");
  svg.setAttribute("viewBox", "0 0 1012.54 1009.81");
  svg.setAttribute("width", size); svg.setAttribute("height", size);
  svg.setAttribute("role", "img"); svg.setAttribute("aria-label", "Ynara");
  const dark = document.documentElement.classList.contains("theme-dark");
  const mono = variant === "mono" || (variant === "auto" && dark);
  const body = mono
    ? `<rect fill="currentColor" x="422.72" y="141.9" width="169.7" height="168.93" rx="17.08" transform="translate(308.73 -292.6) rotate(45)"/><path fill="currentColor" d="${LOGO_MAIN}"/>`
    : `<defs><linearGradient id="${id}p" x1="394.92" y1="226.37" x2="620.21" y2="226.37" gradientTransform="translate(-11.4 425.21) rotate(-45)" gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="#8265a3"/><stop offset="1" stop-color="#692d87"/></linearGradient><linearGradient id="${id}b" x1="119.53" y1="575.07" x2="895.73" y2="575.07" gradientUnits="userSpaceOnUse"><stop offset=".5" stop-color="#305ba6"/><stop offset=".85" stop-color="#242552"/></linearGradient></defs><rect fill="url(#${id}p)" x="422.72" y="141.9" width="169.7" height="168.93" rx="17.08" transform="translate(308.73 -292.6) rotate(45)"/><path fill="url(#${id}b)" d="${LOGO_MAIN}"/>`;
  svg.innerHTML = body; // constante propia, sin datos externos
  if (mono) svg.style.color = "var(--marfil)";
  return svg;
}

export function orb(size, thinking) {
  const el = h("span", { class: `orb${thinking ? " thinking" : ""}`, "aria-hidden": "true" }, h("i"), h("i"), h("b"));
  if (size) el.style.setProperty("--size", `${size}px`);
  return el;
}

// ── Toasts ──
export function toast(text, action) {
  const root = document.getElementById("toasts");
  const life = action ? 6000 : 3200;
  const el = h("div", { class: "toast", role: "status" }, h("span", null, text));
  if (action) el.append(h("button", { onClick: () => { action.run(); close(); } }, action.label));
  // El filete inferior muestra cuánto queda; al pasar el puntero, el tiempo se detiene.
  if (action) el.append(h("i", { class: "life", style: { "--life": `${life}ms` } }));
  let done = false, timer = 0, left = life, since = Date.now();
  function close() {
    if (done) return;
    done = true;
    el.classList.add("out");
    setTimeout(() => el.remove(), 220);
  }
  const run = () => { since = Date.now(); timer = setTimeout(close, left); };
  el.addEventListener("pointerenter", () => { clearTimeout(timer); left = Math.max(600, left - (Date.now() - since)); });
  el.addEventListener("pointerleave", run);
  // Solo queda a la vista el último aviso.
  for (const old of root.children) old.classList.add("out"), setTimeout(() => old.remove(), 220);
  root.append(el);
  run();
}

/** Confirmación en el lugar: el ícono del botón pasa a un check y vuelve. */
export function flash(btn, name, size = 17) {
  if (btn._flash) return;
  const prev = [...btn.childNodes];
  btn._flash = true;
  btn.classList.add("done");
  btn.replaceChildren(icon("check", size));
  setTimeout(() => { btn.classList.remove("done"); btn.replaceChildren(...prev); btn._flash = false; }, 1400);
}

// ── Diálogos (nativos: foco atrapado, Escape y backdrop gratis) ──
export function openDialog(cls, build) {
  const dlg = h("dialog", { class: cls });
  const close = () => dlg.close();
  dlg.append(build(close));
  dlg.addEventListener("click", (e) => { if (e.target === dlg) close(); });
  dlg.addEventListener("close", () => setTimeout(() => dlg.remove(), 320));
  document.body.append(dlg);
  dlg.showModal();
  return dlg;
}

export function sheet({ title, eyebrow, body, foot }) {
  return openDialog("sheet", (close) =>
    h("div", { class: "sheet-in" },
      h("div", { class: "sheet-head" },
        h("div", null, eyebrow ? h("div", { class: "eyebrow" }, eyebrow) : null, h("h2", null, title)),
        h("button", { class: "icon-btn", "aria-label": "Cerrar", onClick: close }, icon("cerrar"))),
      h("div", { class: "sheet-body" }, body(close)),
      foot ? h("div", { class: "sheet-foot" }, foot(close)) : null));
}

export function confirmModal({ title, text, confirm, danger, onConfirm }) {
  return openDialog("modal", (close) =>
    h("div", { class: "sheet-in" },
      h("div", { class: "sheet-body", style: { paddingTop: "22px" } }, h("h2", null, title), h("p", { class: "muted" }, text)),
      h("div", { class: "sheet-foot" },
        h("button", { class: "btn btn-ghost", onClick: close }, "Cancelar"),
        h("button", { class: `btn ${danger ? "btn-danger" : "btn-primary"}`, onClick: () => { onConfirm(); close(); } }, confirm))));
}

// ── Texto enriquecido mínimo y seguro (sin innerHTML) ──
// Soporta **negrita**, listas con "- " y citas con "> ".
export function rich(text) {
  const root = h("div", { class: "prose" });
  let list = null;
  for (const raw of text.split("\n")) {
    const line = raw.trimEnd();
    if (!line.trim()) { list = null; continue; }
    if (line.startsWith("- ")) {
      if (!list) { list = h("ul"); root.append(list); }
      list.append(h("li", null, h("span", null, inline(line.slice(2)))));
      continue;
    }
    list = null;
    if (line.startsWith("> ")) root.append(h("blockquote", null, inline(line.slice(2))));
    else root.append(h("p", null, inline(line)));
  }
  return root;
}
function inline(s) {
  return s.split(/(\*\*[^*]+\*\*)/g).map((part) => (part.startsWith("**") && part.endsWith("**") ? h("strong", null, part.slice(2, -2)) : part));
}
export const plain = (s) => s.replace(/\*\*/g, "").replace(/^[->] /gm, "");

// ── Fechas ──
const DIAS = ["domingo", "lunes", "martes", "miércoles", "jueves", "viernes", "sábado"];
const MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"];
export const startOfDay = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
export const dayDiff = (a, b) => Math.round((startOfDay(a) - startOfDay(b)) / 864e5);
export const hhmm = (d) => `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
export const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
export const diaNombre = (d) => DIAS[d.getDay()];
export const mesNombre = (d) => MESES[d.getMonth()];
export const fechaLarga = (d) => `${cap(DIAS[d.getDay()])} ${d.getDate()} de ${MESES[d.getMonth()]}`;
export function relDia(d, now = new Date()) {
  const n = dayDiff(d, now);
  if (n === 0) return "hoy";
  if (n === 1) return "mañana";
  if (n === -1) return "ayer";
  if (n > 1 && n < 7) return `el ${DIAS[d.getDay()]}`;
  return `el ${d.getDate()} de ${MESES[d.getMonth()]}`;
}
/** "mañana 19 hs" / "hoy 9:30 hs" — siempre en 24 h para no dejar ambigüedad. */
export function cuando(d, now = new Date()) {
  const m = d.getMinutes();
  return `${relDia(d, now)} ${d.getHours()}${m ? `:${String(m).padStart(2, "0")}` : ""} hs`;
}
export function haceCuanto(d, now = new Date()) {
  const n = dayDiff(now, d);
  if (n <= 0) return "hoy";
  if (n === 1) return "ayer";
  if (n < 7) return `hace ${n} días`;
  if (n < 14) return "la semana pasada";
  if (n < 31) return `hace ${Math.round(n / 7)} semanas`;
  return `en ${MESES[d.getMonth()]}`;
}
export const uid = () => Math.random().toString(36).slice(2, 10);
