// Chat: campo de texto primero, respuesta editorial sin burbuja, streaming,
// tarjetas de acción dentro de la conversación y origen visible de la memoria.

import { app } from "./app.js";
import { state, update, reducedMotion } from "./state.js";
import { MODE, PROMPTS } from "./data.js";
import { sendTurn, renderCard, renderCites, modeNote, newChat, chatSubtitle } from "./convo.js";
import { memorySheet } from "./views.js";
import { speak, stopSpeaking } from "./voice.js";
import { h, icon, orb, rich, plain, toast, sheet, confirmModal, flash } from "./ui.js";
import { setOrb } from "./field.js";
import { split, sfx, haptic } from "./micro.js";

let streaming = null; // { stop() } mientras Ynara "escribe"

function aiMessage(msg, thread) {
  const body = h("div", { style: { display: "grid", gap: "12px" } });
  const el = h("article", { class: "msg ai", "aria-label": "Ynara" }, orb(30), body);

  const finish = () => {
    body.replaceChildren(
      modeNote(msg) || "",
      rich(msg.text),
      ...(msg.cards || []).map((c) => renderCard(c)),
      renderCites(msg.cites, memorySheet) || "",
      tools(msg));
    if (msg.via === "voz") body.prepend(h("span", { class: "via" }, icon("mic"), "Dicho por voz"));
  };

  if (msg.fresh && !reducedMotion()) {
    // Streaming simulado: pensar un instante y después palabra por palabra.
    const orbEl = el.querySelector(".orb");
    orbEl.classList.add("thinking");
    const p = h("p", { class: "caret", "aria-live": "off" });
    body.append(h("div", { class: "prose" }, p));
    const words = plain(msg.text).split(/(\s+)/);
    let i = 0, timer = 0, stopped = false;
    const done = () => {
      if (stopped) return;
      stopped = true; clearTimeout(timer); streaming = null; setOrb({ state: "idle" });
      delete msg.fresh; update(() => {}, { silent: true });
      orbEl.classList.remove("thinking");
      finish();
      el.setAttribute("aria-live", "polite");
      follow(thread);
      refreshComposer();
      if (state.settings.speakReplies) speak(msg.text);
    };
    const step = () => {
      if (stopped) return;
      orbEl.classList.remove("thinking");
      p.textContent += words[i++] ?? "";
      follow(thread);
      if (i >= words.length) done(); else timer = setTimeout(step, 14 + Math.random() * 30);
    };
    timer = setTimeout(step, 520);
    streaming = { stop: done };
    setOrb({ state: "thinking" });
  } else {
    delete msg.fresh;
    finish();
  }
  return el;
}

function tools(msg) {
  const fb = (val, ico, label) => h("button", { class: "icon-btn sm", "aria-label": label, title: label, "aria-pressed": String(msg.feedback === val), onClick: (e) => {
    msg.feedback = msg.feedback === val ? null : val;
    update(() => {}, { silent: true });
    for (const b of e.currentTarget.parentElement.querySelectorAll("[data-fb]")) b.setAttribute("aria-pressed", String(b.dataset.fb === msg.feedback));
    haptic(); sfx("tick");
    if (msg.feedback) toast(val === "up" ? "Gracias, me sirve saberlo." : "Gracias. Lo tomo para mejorar.");
  }, dataset: { fb: val } }, icon(ico, 17));
  return h("div", { class: "msg-tools" },
    h("button", { class: "icon-btn sm", "aria-label": "Copiar", title: "Copiar", onClick: (e) => { const b = e.currentTarget; navigator.clipboard?.writeText(plain(msg.text)).then(() => { flash(b); sfx("ok"); }, () => toast("No pude copiar.")); } }, icon("copiar", 17)),
    h("button", { class: "icon-btn sm", "aria-label": "Escuchar", title: "Escuchar", onClick: () => { stopSpeaking(); speak(msg.text); } }, icon("play", 17)),
    fb("up", "util", "Me sirvió"),
    fb("down", "noutil", "No me sirvió"));
}

function userMessage(msg) {
  return h("article", { class: "msg user", "aria-label": "Vos" },
    h("div", { class: "bubble" }, msg.text),
    msg.via === "voz" ? h("span", { class: "via" }, icon("mic"), "Por voz") : null);
}

let pinned = true;
function follow(thread) { if (pinned) thread.scrollTop = thread.scrollHeight; }

let refreshComposer = () => {};

function historySheet() {
  sheet({
    eyebrow: "Chat", title: "Conversaciones",
    body: (close) => state.chats.length
      ? h("div", { class: "rows" }, state.chats.map((c) =>
          h("div", { class: "row" },
            h("button", { class: "row-main", style: { textAlign: "left" }, onClick: () => { close(); app.go(`#/chat/${c.id}`); } },
              h("div", { class: "row-title" }, c.title), h("div", { class: "row-sub" }, chatSubtitle(c))),
            h("button", { class: "icon-btn sm", "aria-label": `Borrar ${c.title}`, onClick: () => confirmModal({ title: "¿Borrar esta conversación?", text: "Los recuerdos que salieron de ella se conservan.", confirm: "Borrar", danger: true, onConfirm: () => {
              update((s) => { s.chats = s.chats.filter((x) => x.id !== c.id); if (s.activeChat === c.id) s.activeChat = null; }, { silent: true });
              close(); app.go("#/chat"); app.rerender();
            } }) }, icon("borrar", 16)))))
      : h("div", { class: "empty" }, h("b", null, "Sin conversaciones"), "Empezá una y va a aparecer acá."),
  });
}

export function chatView(id) {
  streaming?.stop();
  if (id === "nuevo") { const c = newChat(); history.replaceState(null, "", `#/chat/${c.id}`); id = c.id; }
  let chat = id ? state.chats.find((c) => c.id === id) : null;
  if (id && !chat) { history.replaceState(null, "", "#/chat"); }
  if (chat && state.activeChat !== chat.id) update((s) => { s.activeChat = chat.id; }, { silent: true });
  pinned = true;

  const thread = h("div", { class: "thread", tabindex: "0", role: "log", "aria-label": "Conversación" });
  thread.addEventListener("scroll", () => { pinned = thread.scrollHeight - thread.scrollTop - thread.clientHeight < 80; });

  const ta = h("textarea", { rows: 1, placeholder: "Escribile a Ynara…", "aria-label": "Mensaje", maxlength: 4000 });
  const sendBtn = h("button", { class: "send", "data-magnet": "0.3", "aria-label": "Enviar", disabled: true }, icon("flecha", 18));
  const autosize = () => { ta.style.height = "auto"; ta.style.height = `${Math.min(ta.scrollHeight, 180)}px`; };
  refreshComposer = () => {
    const busy = !!streaming;
    sendBtn.replaceChildren(icon(busy ? "detener" : "flecha", 18));
    sendBtn.setAttribute("aria-label", busy ? "Detener" : "Enviar");
    sendBtn.disabled = !busy && !ta.value.trim();
  };

  function paintEmpty() {
    const recents = state.chats.filter((c) => c.messages.length).slice(0, 3);
    thread.replaceChildren(h("div", { class: "chat-empty rise" },
      h("div", { class: "page-head" }, h("div", { class: "eyebrow" }, "Chat"), h("h1", { class: "display" }, split("¿De qué ", ["em", "hablamos?"])), h("p", { class: "lede" }, "Escribí, hablá o arrancá con una de estas.")),
      h("div", { class: "prompt-grid" }, PROMPTS.map((p) =>
        h("button", { class: "prompt", onClick: () => submit(p.text) },
          h("small", null, h("span", { class: "dot", style: { background: `var(--m-${p.mode})` } }), p.label), h("span", null, p.text), h("span", { class: "arrow", "aria-hidden": "true" }, icon("flecha", 16))))),
      recents.length ? h("div", { class: "section" }, h("div", { class: "eyebrow" }, "Seguir una conversación"),
        h("div", { class: "rows" }, recents.map((c) => h("a", { class: "row", href: `#/chat/${c.id}` },
          h("span", { class: "row-main" }, h("span", { class: "row-title" }, c.title), h("div", { class: "row-sub" }, chatSubtitle(c))), h("span", { class: "arrow", "aria-hidden": "true" }, icon("flecha", 16)))))) : null));
  }

  function paintThread() {
    thread.replaceChildren(...chat.messages.map((m) => (m.role === "user" ? userMessage(m) : aiMessage(m, thread))));
    requestAnimationFrame(() => { thread.scrollTop = thread.scrollHeight; });
  }

  function submit(text) {
    const v = (text ?? ta.value).trim();
    if (!v || streaming) return;
    ta.value = ""; autosize();
    const firstTurn = !chat || !chat.messages.length;
    const { chat: c, user, ai } = sendTurn(v);
    sfx("tick");
    if (firstTurn) {
      chat = state.chats.find((x) => x.id === c.id);
      history.replaceState(null, "", `#/chat/${chat.id}`);
      thread.replaceChildren();
      title.textContent = chat.title;
    }
    pinned = true;
    thread.append(userMessage(user), aiMessage(ai, thread));
    modeChip.replaceChildren(h("span", { class: "dot" }), h("b", null, MODE[state.mode].label));
    follow(thread);
    refreshComposer();
  }

  ta.addEventListener("input", () => { autosize(); refreshComposer(); });
  ta.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); submit(); } });
  sendBtn.addEventListener("click", () => { if (streaming) streaming.stop(); else submit(); });

  const title = h("h1", null, chat?.messages.length ? chat.title : "");
  const modeChip = h("span", { class: "mode-chip" }, h("span", { class: "dot" }), h("b", null, MODE[state.mode].label));

  if (chat?.messages.length) paintThread(); else paintEmpty();

  const view = h("div", { class: "chat-wrap" }, h("div", { class: "chat" },
    h("header", { class: "chat-head" },
      title, modeChip,
      h("button", { class: "icon-btn", "aria-label": "Conversaciones", title: "Conversaciones", onClick: historySheet }, icon("nota")),
      h("a", { class: "icon-btn", href: "#/chat/nuevo", "aria-label": "Nueva conversación", title: "Nueva conversación" }, icon("mas"))),
    thread,
    h("div", { class: "composer-wrap" },
      h("div", { class: "composer" }, ta,
        h("button", { class: "icon-btn", "aria-label": "Hablar con Ynara", title: "Hablar", onClick: () => app.openVoice() }, icon("mic")),
        sendBtn),
      h("div", { class: "composer-hint" }, h("span", null, "Enter envía · Shift + Enter salto de línea"), h("span", null, "Ynara es una IA. Demo con datos de ejemplo.")))));

  // Texto que vino desde Hoy o la paleta: se envía apenas se monta.
  if (app.pending) { const p = app.pending; app.pending = null; setTimeout(() => submit(p), 60); }
  else if (matchMedia("(min-width: 861px)").matches) setTimeout(() => ta.focus(), 80);
  return view;
}
