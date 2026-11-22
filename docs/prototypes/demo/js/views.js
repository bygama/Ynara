// Pantallas: Hoy, Agenda, Memoria y Tú. Una columna, cinco bloques como mucho.
// A la vista va lo frecuente; el detalle de cada cosa vive en su hoja.

import { app } from "./app.js";
import { state, update, applyPrefs, reset, exportData } from "./state.js";
import { MODES, MODE } from "./data.js";
import { parseWhen, addEvent } from "./brain.js";
import { refreshField, pulse } from "./field.js";
import { spanishVoices, speak, voiceSupport } from "./voice.js";
import { haptic } from "./micro.js";
import {
  h, icon, toast, sheet, confirmModal, hhmm, dayDiff, startOfDay, cap, fechaLarga, relDia, cuando,
  haceCuanto, mesNombre, diaNombre, uid,
} from "./ui.js";

const pad2 = (n) => String(n).padStart(2, "0");
const SVG = "http://www.w3.org/2000/svg";
const s = (tag, attrs, ...kids) => { const el = document.createElementNS(SVG, tag); for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, v); el.append(...kids); return el; };

/** El diamante: la presencia de Ynara. */
export const dia = () => h("span", { class: "dia", "aria-hidden": "true" });
/** Titular que entra palabra por palabra, detrás de una máscara. Los nodos <em> llevan el acento. */
export function rise(parts) {
  const list = Array.isArray(parts) ? parts : [parts], out = [];
  let i = 0;
  for (const p of list) {
    const em = p instanceof Node, text = em ? p.textContent : p;
    for (const tok of text.split(/(\s+)/)) {
      if (!tok) continue;
      out.push(/^\s+$/.test(tok) ? " " : h("span", { class: "w", style: { "--wi": i++ } }, h("span", em ? { class: "em" } : null, tok)));
    }
  }
  return h("h1", { "aria-label": list.map((p) => (p instanceof Node ? p.textContent : p)).join("") }, out);
}
const head = (small, title) => h("div", { class: "head" }, small ? h("div", { class: "eyebrow" }, small) : null, rise(title));
const block = (title, ...kids) => h("section", { class: "block" }, title ? h("div", { class: "eyebrow" }, title) : null, kids);

function greeting(d) {
  const hr = d.getHours();
  return hr < 6 ? "Buenas noches" : hr < 13 ? "Buen día" : hr < 20 ? "Buenas tardes" : "Buenas noches";
}
function enCuanto(ts, now) {
  const m = Math.round((ts - now) / 6e4);
  if (m < 60) return `en ${Math.max(m, 1)} min`;
  const hs = Math.floor(m / 60), r = m % 60;
  return r ? `en ${hs} h ${r} min` : `en ${hs} h`;
}

/* ───────── El hilo: un día como vínculo con nodos ───────── */
function knot(e, now) {
  const end = e.start + e.dur * 6e4, t = now.getTime();
  const cls = end < t ? " past" : e.start <= t ? " live" : "";
  return h("button", { class: `knot${cls}`, style: { "--c": `var(--m-${e.mode})` }, dataset: { ev: e.id }, onClick: () => eventSheet(e) },
    h("time", null, hhmm(new Date(e.start))), h("i"),
    h("span", { class: "what" }, h("span", { style: { color: "var(--ink-deep)", fontSize: "var(--fs-body)" } }, e.title), e.place ? h("span", null, e.place) : null));
}
/** Los eventos del día en orden, con el diamante donde cae el ahora. */
function hilo(events, now, here) {
  const t = now.getTime(), rows = [];
  let placed = !here;
  for (const e of events) {
    if (!placed && e.start > t) { rows.push(here); placed = true; }
    rows.push(knot(e, now));
  }
  if (!placed) rows.push(here);
  rows.forEach((r, i) => r.style.setProperty("--i", i));
  return h("div", { class: "hilo" }, rows);
}

/* ───────── Hoy ───────── */
export function hoyView() {
  const now = new Date();
  const todays = state.events.filter((e) => dayDiff(new Date(e.start), now) === 0).sort((a, b) => a.start - b.start);
  const next = todays.find((e) => e.start > now.getTime());
  const tasks = state.tasks.filter((t) => dayDiff(new Date(t.due), now) <= 0 || t.top).sort((a, b) => a.done - b.done || (b.top ? 1 : 0) - (a.top ? 1 : 0));

  // El ahora: una sola frase de Ynara. Si tiene algo para proponer, va acá.
  const n = state.nudge?.state === "open" ? state.nudge : null;
  let say, acts = null;
  if (n) {
    const early = now.getHours() < 10, start = new Date(now);
    if (early) start.setHours(now.getHours() + 1, 0, 0, 0); else { start.setDate(start.getDate() + 1); start.setHours(8, 30, 0, 0); }
    say = early ? n.text : "Mañana a las 10 entregás el informe mensual. ¿Te bloqueo un rato a primera hora para cerrarlo tranquilo?";
    acts = h("div", { class: "acts" },
      h("button", { class: "link-btn", onClick: () => {
        const card = addEvent(early ? "Foco: cerrar la propuesta" : "Foco: cerrar el informe", start, 90);
        update((st) => { st.nudge.state = "done"; });
        toast(`Bloqueado ${cuando(start)}.`, { label: "Deshacer", run: () => update((st) => { st.events = st.events.filter((e) => e.id !== card.ref); st.nudge.state = "open"; }) });
      } }, n.yes),
      h("button", { class: "link-btn", style: { color: "var(--ink-soft)" }, onClick: () => update((st) => { st.nudge.state = "dismissed"; }) }, n.no));
  } else {
    say = next ? `Lo próximo es ${next.title}, ${enCuanto(next.start, now.getTime())}.` : todays.length ? "Ya pasó todo lo de hoy." : "Hoy no tenés nada agendado.";
  }
  const here = h("div", { class: "knot now" }, h("time", null, "Ahora"), dia(), h("div", { class: "what" }, h("p", null, say), acts));

  const allDone = () => tasks.length > 0 && tasks.every((x) => state.tasks.find((y) => y.id === x.id)?.done);
  const fin = h("p", { class: "fin", hidden: !allDone() }, "Eso es todo por hoy.");
  const addTask = h("input", { type: "text", placeholder: "Sumar un pendiente", "aria-label": "Sumar un pendiente", autocomplete: "off" });
  const taskRow = (t) => {
    const b = h("button", { class: "task", "aria-pressed": String(t.done), onClick: () => {
      const on = b.getAttribute("aria-pressed") !== "true";
      b.setAttribute("aria-pressed", String(on));
      update((st) => { st.tasks.find((y) => y.id === t.id).done = on; }, { silent: true });
      haptic();
      fin.hidden = !allDone();
      if (on && tasks.every((x) => state.tasks.find((y) => y.id === x.id)?.done)) {
        const d = document.querySelector(".knot.now .dia");
        if (d) { d.classList.remove("yay"); void d.offsetWidth; d.classList.add("yay"); }
        pulse(); toast("Cerraste todo lo de hoy.");
      }
    } }, h("span", { class: "check" }, icon("check")), h("span", { class: "row-title" }, t.title));
    return b;
  };

  return h("div", { class: "col" },
    head(cap(fechaLarga(now)), [`${greeting(now)}, `, h("em", null, `${state.user.name}.`)]),
    hilo(todays, now, here),
    block("Pendientes",
      h("div", { class: "rows" }, tasks.slice(0, 5).map(taskRow)),
      tasks.length > 5 ? h("p", { class: "row-sub" }, `Y ${tasks.length - 5} más para otro momento.`) : null,
      h("form", { class: "add", onSubmit: (e) => { e.preventDefault(); const v = addTask.value.trim(); if (!v) return; update((st) => { st.tasks.push({ id: uid(), title: cap(v), due: Date.now(), done: false, mode: "productividad" }); }); } },
        icon("mas"), addTask)),
    fin);
}

/* ───────── Agenda ───────── */
let agendaDay = 0, agendaDir = 0;

export function eventSheet(ev) {
  const isNew = !ev.id;
  const d = new Date(ev.start);
  const local = `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}T${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
  const title = h("input", { class: "input", id: "ev-t", value: ev.title || "", placeholder: "Título" });
  const when = h("input", { class: "input", id: "ev-w", type: "datetime-local", value: local });
  const dur = h("select", { class: "input", id: "ev-d" }, [15, 30, 45, 60, 90, 120, 180].map((m) => h("option", { value: m, selected: m === (ev.dur || 60) }, m < 60 ? `${m} min` : `${m / 60} h`)));
  const place = h("input", { class: "input", id: "ev-p", value: ev.place || "", placeholder: "Opcional" });
  let mode = ev.mode || "productividad";
  const modes = h("div", { class: "chips" });
  const paint = () => { modes.replaceChildren(...MODES.slice(0, 4).map((m) => h("button", { type: "button", class: "chip", "aria-pressed": String(mode === m.id), onClick: () => { mode = m.id; paint(); } }, m.label))); };
  paint();
  return sheet({
    eyebrow: isNew ? "Agenda" : cap(cuando(d)),
    title: isNew ? "Nuevo evento" : "Editar evento",
    body: () => [
      h("div", { class: "field" }, h("label", { for: "ev-t" }, "Título"), title),
      h("div", { class: "field" }, h("label", { for: "ev-w" }, "Cuándo"), when),
      h("div", { class: "field" }, h("label", { for: "ev-d" }, "Duración"), dur),
      h("div", { class: "field" }, h("label", { for: "ev-p" }, "Lugar"), place),
      h("div", { class: "field" }, h("label", null, "Modo"), modes),
    ],
    foot: (close) => [
      isNew ? null : h("button", { class: "btn btn-quiet", style: { marginRight: "auto", color: "var(--error)" }, onClick: () => {
        const copy = { ...ev };
        update((st) => { st.events = st.events.filter((e) => e.id !== ev.id); });
        close();
        toast("Evento borrado.", { label: "Deshacer", run: () => update((st) => { st.events.push(copy); }) });
      } }, "Borrar"),
      h("button", { class: "btn btn-ghost", onClick: close }, "Cancelar"),
      h("button", { class: "btn btn-primary", onClick: () => {
        const start = new Date(when.value).getTime();
        if (!title.value.trim() || Number.isNaN(start)) { title.focus(); return; }
        const data = { title: cap(title.value.trim()), start, dur: +dur.value, place: place.value.trim() || undefined, mode };
        update((st) => { if (isNew) st.events.push({ id: uid(), ...data }); else Object.assign(st.events.find((e) => e.id === ev.id), data); });
        close();
        toast(isNew ? "Evento creado." : "Cambios guardados.");
      } }, "Guardar"),
    ],
  });
}

export function agendaView() {
  const now = new Date();
  const days = Array.from({ length: 7 }, (_, i) => { const d = startOfDay(now); d.setDate(d.getDate() + i); return d; });
  const on = (d) => state.events.filter((e) => dayDiff(new Date(e.start), d) === 0).sort((a, b) => a.start - b.start);
  const sel = days[agendaDay], list = on(sel);

  const quick = h("input", { type: "text", placeholder: "Dentista mañana 19 hs", "aria-label": "Agregar un evento escribiendo", autocomplete: "off" });
  const quickForm = h("form", { class: "add", onSubmit: (e) => {
    e.preventDefault();
    const v = quick.value.trim(); if (!v) return;
    const w = parseWhen(v, now);
    if (!w.date) { toast("Decime cuándo: «mañana 19 hs»."); return; }
    const chars = v.split(""); for (const [a, b] of w.used) for (let i = a; i < b; i++) chars[i] = " ";
    const title = cap(chars.join("").replace(/\s+/g, " ").trim().replace(/\s+(a|el|para|en)$/i, "")) || "Evento";
    const card = addEvent(title, w.date);
    agendaDay = Math.max(0, Math.min(6, dayDiff(w.date, now)));
    app.rerender();
    toast(`Agendado ${cuando(w.date)}.`, { label: "Deshacer", run: () => update((st) => { st.events = st.events.filter((x) => x.id !== card.ref); }) });
  } }, icon("mas"), quick);

  const week = h("div", { class: "week", role: "group", "aria-label": "Próximos siete días" }, days.map((d, i) =>
    h("button", { class: `day${on(d).length ? " has" : ""}`, "aria-pressed": String(i === agendaDay), "aria-label": fechaLarga(d), onClick: () => { if (i === agendaDay) return; agendaDir = i > agendaDay ? 1 : -1; agendaDay = i; app.rerender(); } },
      h("span", null, diaNombre(d).slice(0, 3)), h("b", null, String(d.getDate())), h("em"))));

  const here = agendaDay === 0 ? h("div", { class: "knot now" }, h("time", null, "Ahora"), dia(), h("div", { class: "what" })) : null;
  const day = block(null,
    list.length || here ? hilo(list, now, here) : null,
    list.length ? null : h("div", { class: "empty" }, h("b", null, `Nada agendado ${relDia(sel, now)}`), "Escribilo acá abajo y lo sumo."),
    quickForm);
  if (agendaDir) day.classList.add(agendaDir > 0 ? "from-r" : "from-l");
  agendaDir = 0;
  return h("div", { class: "col" },
    head(`${cap(mesNombre(sel))} ${sel.getFullYear()}`, `${cap(diaNombre(sel))} ${sel.getDate()}`),
    week, day);
}

/* ───────── Memoria ───────── */
let memFilter = "todo";
const TYPES = { hecho: "Hecho", momento: "Momento", costumbre: "Costumbre" };

export function memorySheet(m) {
  let editing = false;
  return sheet({
    eyebrow: `${TYPES[m.type]} · ${haceCuanto(new Date(m.date))}`,
    title: "Recuerdo",
    body: () => {
      const wrap = h("div", { style: { display: "grid", gap: "24px" } });
      const paint = () => {
        const area = h("textarea", { class: "textarea", rows: 3, "aria-label": "Texto del recuerdo" }, m.text);
        wrap.replaceChildren(
          editing ? area : h("p", { class: "quote" }, `«${m.text}»`),
          h("div", { class: "source" }, h("div", { class: "eyebrow" }, "De dónde salió"), h("span", null, m.source), h("span", { class: "muted small" }, `Conversación: ${m.chat}${m.sensitive ? " · Sensible" : ""}`)),
          editing
            ? h("div", { style: { display: "flex", gap: "8px" } },
                h("button", { class: "btn btn-primary", onClick: () => { const v = area.value.trim(); if (v) { update((st) => { st.memories.find((x) => x.id === m.id).text = v; }); m.text = v; } editing = false; paint(); toast("Recuerdo actualizado."); } }, "Guardar"),
                h("button", { class: "btn btn-ghost", onClick: () => { editing = false; paint(); } }, "Cancelar"))
            : "");
        if (editing) area.focus();
      };
      wrap._edit = () => { editing = true; paint(); };
      paint();
      return wrap;
    },
    foot: (close) => [
      h("button", { class: "btn btn-quiet", style: { marginRight: "auto", color: "var(--error)" }, onClick: () => {
        const copy = { ...m }, idx = state.memories.findIndex((x) => x.id === m.id);
        update((st) => { st.memories = st.memories.filter((x) => x.id !== m.id); });
        close();
        toast("Olvidado.", { label: "Deshacer", run: () => update((st) => { st.memories.splice(Math.max(idx, 0), 0, copy); }) });
      } }, "Olvidar"),
      h("button", { class: "btn btn-ghost", onClick: (e) => e.currentTarget.closest("dialog").querySelector(".sheet-body > div")._edit() }, "Corregir"),
      h("button", { class: "btn btn-primary", onClick: close }, "Listo"),
    ],
  });
}

/* La memoria no es una lista: es una red que se lee. Dos recuerdos se vinculan
   si hablan de lo mismo o salieron de la misma charla, y cada vínculo dice por
   qué. Cada isla lleva el nombre de su tema. Elegir un punto lo vuelve diamante
   y muestra qué guarda, de dónde salió y con qué se conecta. */
const TOPICS = [["Tesis", /tesis/i], ["Estudio", /estudi|parcial|estad[ií]stica|rend[ií]s/i], ["Trabajo", /agencia|cliente|entrega|trabaj/i], ["Descanso", /dorm|quemad|pantallas|de noche|a la mañana/i], ["Tu gente", /hermana|amig|f[uú]tbol|cumple/i]];
function memoryLinks(items) {
  const out = [];
  for (let i = 0; i < items.length; i++) for (let j = i + 1; j < items.length; j++) {
    const x = items[i], y = items[j];
    const why = TOPICS.find(([, t]) => t.test(x.text) && t.test(y.text))?.[0] ?? (x.chat === y.chat ? "Misma charla" : null);
    if (why) out.push([x.id, y.id, why]);
  }
  return out;
}
/** Ubica los nodos: los vinculados se acercan, el resto se separa. Siempre da el mismo dibujo. */
function layout(items, links, W, H, rest, push) {
  const P = items.map((m, i) => { const t = (i / items.length) * 6.2832; return { id: m.id, x: W / 2 + Math.cos(t) * W * 0.3, y: H / 2 + Math.sin(t) * H * 0.3 }; });
  const by = new Map(P.map((q) => [q.id, q]));
  for (let it = 0; it < 300; it++) {
    for (const q of P) { q.fx = (W / 2 - q.x) * 0.009; q.fy = (H / 2 - q.y) * 0.016; }
    for (let i = 0; i < P.length; i++) for (let j = i + 1; j < P.length; j++) {
      const u = P[i], v = P[j], dx = u.x - v.x, dy = u.y - v.y, d = Math.max(14, Math.hypot(dx, dy)), f = push / (d * d);
      u.fx += (dx / d) * f; u.fy += (dy / d) * f; v.fx -= (dx / d) * f; v.fy -= (dy / d) * f;
    }
    for (const [m, n] of links) {
      const u = by.get(m), v = by.get(n), dx = v.x - u.x, dy = v.y - u.y, d = Math.hypot(dx, dy) || 1, f = (d - rest) * 0.02;
      u.fx += (dx / d) * f; u.fy += (dy / d) * f; v.fx -= (dx / d) * f; v.fy -= (dy / d) * f;
    }
    for (const q of P) { q.x = Math.min(W - 30, Math.max(30, q.x + q.fx)); q.y = Math.min(H - 26, Math.max(44, q.y + q.fy)); }
  }
  return by;
}
/** Islas de la red (recuerdos conectados entre sí) con el tema que más se repite en cada una. */
function islands(items, links) {
  const root = new Map(items.map((m) => [m.id, m.id]));
  const find = (id) => { while (root.get(id) !== id) id = root.get(id); return id; };
  for (const [x, y] of links) root.set(find(x), find(y));
  const out = new Map();
  for (const m of items) { const r = find(m.id); if (!out.has(r)) out.set(r, { ids: [], why: {} }); out.get(r).ids.push(m.id); }
  for (const [x, , why] of links) if (why !== "Misma charla") { const g = out.get(find(x)); g.why[why] = (g.why[why] || 0) + 1; }
  return [...out.values()].filter((g) => g.ids.length > 1).map((g) => ({ ids: g.ids, name: Object.entries(g.why).sort((p, q) => q[1] - p[1])[0]?.[0] }));
}

let memSel = null;
export function memoriaView() {
  const all = [...state.memories].sort((x, y) => y.date - x.date);
  const title = head(state.settings.memoryPaused ? "En pausa: no guardo nada nuevo" : null, "Lo que recuerdo de vos");
  // Autoría: lo que dijiste vos va lleno; lo que Ynara infirió queda hueco hasta que lo confirmes.
  const guessed = (m) => /^(Lo fui notando|Resumen)/.test(m.source);
  const inferred = (m) => !m.confirmed && guessed(m);
  const label = (m) => `${inferred(m) ? "Lo inferí · " : guessed(m) ? "Confirmado · " : ""}${inferred(m) || guessed(m) ? haceCuanto(new Date(m.date)) : cap(haceCuanto(new Date(m.date)))}${m.sensitive ? " · Sensible" : ""}`;
  if (!all.length) return h("div", { class: "col" }, title, h("div", { class: "empty" }, h("b", null, "Todavía no hay nada acá"), "Lo que me cuentes va a ir apareciendo."));

  const narrow = matchMedia("(max-width: 640px)").matches, W = narrow ? 340 : 560, H = narrow ? 300 : 320;
  const links = memoryLinks(all), pos = layout(all, links, W, H, narrow ? 78 : 118, narrow ? 3400 : 8500);
  const near = (id) => links.filter((l) => l[0] === id || l[1] === id).map((l) => ({ m: all.find((m) => m.id === (l[0] === id ? l[1] : l[0])), why: l[2] }));
  if (!all.some((m) => m.id === memSel)) memSel = all[0].id;

  const net = s("svg", { class: "red has-sel", viewBox: `0 0 ${W} ${H}`, role: "group", "aria-label": "Red de recuerdos. Elegí un punto para leerlo." });
  links.forEach(([m, n], i) => { const u = pos.get(m), v = pos.get(n); net.append(s("line", { x1: u.x, y1: u.y, x2: v.x, y2: v.y, "data-a": m, "data-b": n, pathLength: 1, style: `--i: ${i}` })); });
  for (const g of islands(all, links)) {
    if (!g.name) continue;
    const pts = g.ids.map((id) => pos.get(id)), x = pts.reduce((t, q) => t + q.x, 0) / pts.length, y = Math.min(...pts.map((q) => q.y)) - 20;
    const label = s("text", { class: "isla", x: Math.min(W - 40, Math.max(40, x)), y: Math.max(14, y), "text-anchor": "middle", "data-ids": g.ids.join(" ") });
    label.textContent = g.name;
    net.append(label);
  }
  all.forEach((m, i) => {
    const { x, y } = pos.get(m.id), label = s("title"); label.textContent = m.text;
    const g = s("g", { tabindex: "0", role: "button", "aria-label": m.text, "data-mem": m.id, "data-type": m.type, class: `n${inferred(m) ? " inf" : ""}`, style: `--c: var(--m-${m.mode}); --i: ${i}` },
      label, s("circle", { class: "hit", cx: x, cy: y, r: 22 }), s("circle", { class: "halo", cx: x, cy: y, r: 15 }), s("rect", { class: "p", x: x - 5, y: y - 5, width: 10, height: 10 }));
    g.addEventListener("click", () => select(m.id));
    g.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select(m.id); } });
    net.append(g);
  });

  const card = h("div", { class: "mem-card", "aria-live": "polite" });
  function select(id, quiet) {
    memSel = id;
    const m = all.find((x) => x.id === id), rel = near(id), ids = new Set(rel.map((r) => r.m.id));
    for (const n of net.querySelectorAll(".n")) { n.classList.toggle("sel", n.dataset.mem === id); n.classList.toggle("rel", ids.has(n.dataset.mem)); n.setAttribute("aria-pressed", String(n.dataset.mem === id)); }
    for (const l of net.querySelectorAll("line")) l.classList.toggle("on", l.dataset.a === id || l.dataset.b === id);
    for (const t of net.querySelectorAll(".isla")) t.classList.toggle("on", t.dataset.ids.split(" ").includes(id));
    card.replaceChildren(...[
      h("div", { class: "eyebrow" }, label(m)),
      h("p", { class: `quote${inferred(m) ? " inf" : ""}` }, `«${m.text}»`),
      h("p", { class: "row-sub" }, m.source),
      rel.length ? h("div", { class: "rel" }, rel.map((r) =>
        h("button", { class: "rel-row", onClick: () => select(r.m.id) }, h("span", { class: "why" }, r.why), h("span", { class: "rel-text" }, r.m.text)))) : null,
      h("div", { class: "acts" },
        h("button", { class: "link-btn", style: { color: "var(--ink-soft)" }, onClick: () => memorySheet(m) }, "Corregir u olvidar"),
        inferred(m) ? h("button", { class: "link-btn", onClick: (e) => {
          const mark = (on) => {
            m.confirmed = on; update(() => {}, { silent: true });
            net.querySelector(`.n[data-mem="${m.id}"]`)?.classList.toggle("inf", !on);
            if (memSel !== m.id) return;
            card.querySelector(".quote")?.classList.toggle("inf", !on);
            card.querySelector(".eyebrow").textContent = label(m);
            e.target.style.visibility = on ? "hidden" : "";
          };
          mark(true); haptic();
          toast("Confirmado.", { label: "Deshacer", run: () => mark(false) });
        } }, "Es así") : null),
    ].filter(Boolean));
    if (!quiet) { card.classList.remove("swap"); void card.offsetWidth; card.classList.add("swap"); }
  }

  // Los tipos no cambian de pantalla: atenúan lo que no corresponde.
  const paint = () => { for (const n of net.querySelectorAll(".n")) n.classList.toggle("off", memFilter !== "todo" && n.dataset.type !== memFilter); };
  const tabBtns = [["todo", "Todo"], ["hecho", "Hechos"], ["momento", "Momentos"], ["costumbre", "Costumbres"]].map(([id, label]) =>
    h("button", { "aria-pressed": String(memFilter === id), onClick: (e) => { memFilter = id; for (const t of tabBtns) t.setAttribute("aria-pressed", String(t === e.currentTarget)); paint(); } }, label));
  paint();
  select(memSel);

  return h("div", { class: "col" }, title,
    h("div", { class: "red-wrap" }, h("div", { class: "tabs", role: "group", "aria-label": "Resaltar por tipo" }, tabBtns), net),
    card);
}

/* ───────── Tú ───────── */
const setRow = (title, sub, control) => h("div", { class: "set" }, h("div", null, h("div", { class: "row-title" }, title), sub ? h("div", { class: "row-sub" }, sub) : null), control);
function seg(options, value, onPick) {
  const btns = options.map(([id, label]) => h("button", { "aria-pressed": String(value === id), onClick: (e) => { for (const b of btns) b.setAttribute("aria-pressed", String(b === e.currentTarget)); onPick(id); } }, label));
  return h("div", { class: "seg", role: "group" }, btns);
}
function toggle(checked, label, onChange) {
  const b = h("button", { class: "switch", role: "switch", "aria-checked": String(checked), "aria-label": label, onClick: () => { const v = b.getAttribute("aria-checked") !== "true"; b.setAttribute("aria-checked", String(v)); haptic(); onChange(v); } });
  return b;
}
/** Guarda una preferencia sin reconstruir la pantalla: el control ya cambió en el lugar. */
function setPref(key, value, rebuild) {
  update((st) => { st.settings[key] = value; }, { silent: !rebuild });
  if (!rebuild) { applyPrefs(); refreshField(); }
}

export function tuView() {
  const st = state.settings;
  const name = h("input", { class: "input", value: state.user.name, "aria-label": "Tu nombre", maxlength: 24, onChange: (e) => { const v = e.target.value.trim(); if (v) update((x) => { x.user.name = v; }); } });
  const voices = spanishVoices(), support = voiceSupport();
  const rate = h("div", { class: "row-sub tnum" }, `${st.rate.toFixed(2)}×`);

  return h("div", { class: "col" },
    head("Demo con datos de ejemplo", `${state.user.name}`),
    block("Cuenta", h("div", { class: "rows" },
      setRow("Nombre", null, name))),
    block("Apariencia", h("div", { class: "rows" },
      setRow("Tema", null, seg([["light", "Marfil"], ["dark", "Noche"], ["system", "Sistema"]], st.theme, (v) => setPref("theme", v, true))),
      setRow("Texto", null, seg([["sm", "Chico"], ["md", "Normal"], ["lg", "Grande"]], st.textSize, (v) => setPref("textSize", v))),
      setRow("Movimiento", null, seg([["auto", "Auto"], ["on", "Con"], ["off", "Sin"]], st.motion, (v) => setPref("motion", v))))),
    block("Voz", h("div", { class: "rows" },
      voices.length ? setRow("Voz de Ynara", null, h("select", { class: "input", "aria-label": "Voz de Ynara", onChange: (e) => setPref("voice", e.target.value) },
        h("option", { value: "" }, "Automática"), voices.map((v) => h("option", { value: v.name, selected: v.name === st.voice }, v.name.replace(/Microsoft |Google /, "")))) ) : null,
      h("div", { class: "set" }, h("div", null, h("div", { class: "row-title" }, "Velocidad"), rate),
        h("input", { type: "range", min: 0.8, max: 1.3, step: 0.05, value: st.rate, "aria-label": "Velocidad de la voz", onInput: (e) => { rate.textContent = `${(+e.target.value).toFixed(2)}×`; }, onChange: (e) => setPref("rate", +e.target.value) })),
      setRow("Manos libres", "Después de responder, vuelvo a escuchar.", toggle(st.handsFree, "Manos libres", (v) => setPref("handsFree", v))),
      setRow("Leer las respuestas del chat", null, toggle(st.speakReplies, "Leer las respuestas del chat", (v) => setPref("speakReplies", v))),
      support.tts ? setRow("Probar la voz", null, h("button", { class: "btn btn-ghost btn-sm", onClick: () => speak(`Hola, ${state.user.name}. Así sueno.`) }, "Escuchar")) : null)),
    block("Tus datos", h("div", { class: "rows" },
      setRow("Pausar la memoria", "Mientras esté en pausa, no guardo nada nuevo.", toggle(st.memoryPaused, "Pausar la memoria", (v) => setPref("memoryPaused", v))),
      setRow("Exportar", "Todo, en un archivo JSON.", h("button", { class: "btn btn-ghost btn-sm", onClick: () => { exportData(); toast("Descarga lista."); } }, "Exportar")),
      setRow("Borrar todo", "Reinicia la demo.", h("button", { class: "btn btn-danger btn-sm", onClick: () => confirmModal({ title: "¿Borrar todo?", text: "Se borran conversaciones, agenda, pendientes y memoria de esta demo. No se puede deshacer.", confirm: "Borrar todo", danger: true, onConfirm: reset }) }, "Borrar"))),
      h("p", { class: "note", style: { marginTop: "16px" } }, "Ynara es una inteligencia artificial: no es una persona ni reemplaza a un profesional. En esta demo nada sale de tu navegador, salvo el audio si usás el micrófono.")));
}
