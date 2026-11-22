// Conversación compartida por el chat y el modo voz: crea sesiones, envía
// turnos y dibuja las tarjetas de acción y las citas de memoria.

import { state, update, setMode } from "./state.js";
import { respond, undoCard } from "./brain.js";
import { MODE } from "./data.js";
import { h, icon, toast, uid, hhmm, dayDiff, cap, relDia, haceCuanto } from "./ui.js";
import { refreshField } from "./field.js";

export function activeChat() {
  return state.chats.find((c) => c.id === state.activeChat) || null;
}

export function newChat() {
  const chat = { id: uid(), title: "Nueva conversación", mode: state.mode, updated: Date.now(), messages: [] };
  update((s) => { s.chats.unshift(chat); s.activeChat = chat.id; }, { silent: true });
  return chat;
}

/** Envía un turno. Devuelve { chat, user, ai }; el mensaje `ai` trae `fresh` para animarlo una vez. */
export function sendTurn(text, via = "texto") {
  const chat = activeChat() || newChat();
  const user = { id: uid(), role: "user", text: text.trim(), via };
  const res = respond(text);
  const ai = { id: uid(), role: "ai", mode: res.mode, text: res.text, cards: res.cards, cites: res.cites, via };
  const prevMode = state.mode;
  update((s) => {
    const c = s.chats.find((x) => x.id === chat.id);
    c.messages.push(user, ai);
    c.updated = Date.now();
    c.mode = res.mode;
    if (c.title === "Nueva conversación") {
      const clean = text.trim().replace(/[¿?¡!.]/g, "");
      c.title = cap(clean.length > 40 ? `${clean.slice(0, 40).replace(/\s+\S*$/, "")}…` : clean);
    }
    s.chats.sort((a, b) => b.updated - a.updated);
  }, { silent: true });
  if (res.mode !== prevMode) {
    setMode(res.mode, { silent: true });
    refreshField();
    ai.switched = prevMode;
    // El cambio fue silencioso (sin re-render): se actualizan los chips de modo a mano.
    for (const b of document.querySelectorAll(".mode-chip b")) b.textContent = MODE[res.mode].label;
  }
  ai.fresh = true;
  return { chat, user, ai };
}

const KIND = {
  event: { label: "Evento creado", ico: "agenda", go: "#/agenda", goLabel: "Ver agenda" },
  reminder: { label: "Recordatorio", ico: "recordatorio", go: "#/hoy", goLabel: "Ver en Hoy" },
  task: { label: "Pendiente sumado", ico: "check", go: "#/hoy", goLabel: "Ver en Hoy" },
  memory: { label: "Nuevo recuerdo", ico: "memoria", go: "#/memoria", goLabel: "Ver memoria" },
};

/** Tarjeta de acción: dice qué se hizo, enlaza a la entidad y permite deshacer. */
export function renderCard(card, { compact = false, onNavigate } = {}) {
  if (card.kind === "help") {
    return h("div", { class: "help-card", role: "note" },
      h("b", null, "No estás solo"),
      h("p", null, "Si estás en peligro ahora, llamá al servicio de emergencias de tu zona o pedile a alguien de confianza que se quede con vos."),
      h("p", { class: "muted", style: { fontSize: "var(--fs-xs)" } }, "En el producto real, acá van las líneas de ayuda de tu país, verificadas con fuentes oficiales."));
  }
  if (card.kind === "agenda") {
    const day = new Date(card.day);
    let list = state.events.filter((e) => dayDiff(new Date(e.start), day) === 0).sort((a, b) => a.start - b.start);
    if (compact) {
      // En la pantalla de voz entra poco: lo que viene, o lo último del día.
      const upcoming = list.filter((e) => e.start + e.dur * 6e4 > Date.now());
      list = (upcoming.length ? upcoming : list.slice(-3)).slice(0, 3);
    }
    return h("div", { class: "act-list" }, list.map((e) =>
      h("div", { class: "tl-item", style: { "--c": `var(--m-${e.mode})` } },
        h("span", { class: "tl-time tnum" }, hhmm(new Date(e.start))),
        h("span", { class: "tl-dot" }),
        h("span", null, h("div", { class: "tl-title" }, e.title), e.place ? h("div", { class: "tl-sub" }, e.place) : null))));
  }
  const k = KIND[card.kind];
  const el = h("div", { class: `act${card.undone ? " undone" : ""}` },
    h("span", { class: "act-ico" }, icon(k.ico, 20)),
    h("div", null,
      h("div", { class: "act-kind" }, card.undone ? "Deshecho" : k.label),
      h("div", { class: "act-title" }, card.title),
      h("div", { class: "act-sub" }, card.sub)));
  if (!card.undone) {
    const btns = h("div", { class: "act-btns" });
    if (!compact) btns.append(h("a", { class: "btn btn-quiet btn-sm", href: k.go, onClick: () => onNavigate?.() }, k.goLabel));
    btns.append(h("button", { class: "btn btn-quiet btn-sm", onClick: () => {
      if (undoCard(card)) {
        card.undone = true;
        update(() => {}, { silent: true });
        el.replaceWith(renderCard(card, { compact, onNavigate }));
        toast("Deshecho.");
      }
    } }, icon("deshacer", 15), "Deshacer"));
    el.append(btns);
  }
  return el;
}

/** Chips con los recuerdos usados en la respuesta (origen visible). */
export function renderCites(ids, onOpen) {
  const found = (ids || []).map((id) => state.memories.find((m) => m.id === id)).filter(Boolean);
  if (!found.length) return null;
  // Plegado por defecto: una línea que dice que hubo memoria, y se abre para ver cuál.
  const list = h("div", { class: "why-list", hidden: true }, found.map((m) =>
    h("button", { class: "rel-row", onClick: () => onOpen(m) }, h("span", { class: "why" }, cap(haceCuanto(new Date(m.date)))), h("span", { class: "rel-text" }, m.text))));
  const btn = h("button", { class: "why-btn", "aria-expanded": "false", onClick: () => { const open = list.hidden; list.hidden = !open; btn.setAttribute("aria-expanded", String(open)); } },
    h("span", { class: "dia", "aria-hidden": "true" }), found.length === 1 ? "Usé un recuerdo" : `Usé ${found.length} recuerdos`);
  return h("div", { class: "why-wrap" }, btn, list);
}

export function modeNote(msg) {
  if (!msg.switched) return null;
  return h("div", { class: "mode-note" }, h("span", { class: "dot" }), `Pasé a modo ${MODE[msg.mode].label} para esto.`);
}

export const chatSubtitle = (c) => `${MODE[c.mode]?.label || ""} · ${cap(relDia(new Date(c.updated)))}`;
