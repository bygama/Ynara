// Arranque. La cabecera y el campo inferior se construyen una sola vez y no se
// vuelven a tocar: al navegar solo entra el contenido.

import { app } from "./app.js";
import { state, update, subscribe, applyPrefs, setMode } from "./state.js";
import { MODES, MODE } from "./data.js";
import { initField, refreshField, setView, relayout } from "./field.js";
import { hoyView, agendaView, memoriaView, tuView, dia, rise } from "./views.js";
import { reducedMotion } from "./state.js";
import { chatView } from "./chat.js";
import { openVoice, voiceSupport } from "./voice.js";
import { openPalette } from "./palette.js";
import { h, icon, logo, sheet } from "./ui.js";

const root = document.getElementById("app");
const html = document.documentElement;
const NAV = [["hoy", "Hoy"], ["chat", "Chat"], ["agenda", "Agenda"], ["memoria", "Memoria"]];
const ROUTES = ["hoy", "chat", "agenda", "memoria", "tu"];

function route() {
  const [, name = "hoy", arg] = location.hash.split("/");
  return { name: ROUTES.includes(name) ? name : "hoy", arg };
}

const modeOption = (m, pressed, onClick) => h("button", { class: "opt", "aria-pressed": String(pressed), onClick },
  h("span", { class: "dia", style: { "--c": `var(--m-${m.id})` } }), h("span", null, h("b", null, m.label), h("span", null, m.desc)), h("span", { class: "check" }, icon("check")));

const ICON = { hoy: "foco", chat: "dialogo", agenda: "agenda", memoria: "memoria" };
const desktop = () => matchMedia("(min-width: 900px)").matches;

/** Selector de modo, pegado al campo de escritura. En escritorio es un menú que
    nace del botón; en móvil, una bandeja que sube desde abajo. */
function modeMenu(anchor) {
  if (!desktop()) {
    sheet({ eyebrow: "Ynara cambia de modo sola según lo que le pidas", title: "¿Cómo te acompaño?",
      body: (close) => h("div", { class: "opts" }, MODES.map((m) => modeOption(m, state.mode === m.id, () => { setMode(m.id); close(); }))) });
    return;
  }
  if (document.querySelector(".menu")) return;
  const items = MODES.map((m) => h("button", { type: "button", role: "menuitemradio", "aria-checked": String(state.mode === m.id), onClick: () => { close(); setMode(m.id); } },
    h("span", { class: "dot", style: { background: `var(--m-${m.id})` } }), h("span", null, h("b", null, m.label), h("span", null, m.desc))));
  const menu = h("div", { class: "menu", role: "menu", "aria-label": "Modo" }, items);
  function close() { menu.remove(); removeEventListener("pointerdown", out, true); removeEventListener("keydown", key, true); anchor.setAttribute("aria-expanded", "false"); }
  const out = (e) => { if (!menu.contains(e.target)) { close(); if (anchor.contains(e.target)) e.stopPropagation(); } };
  const key = (e) => {
    const i = items.indexOf(document.activeElement);
    if (e.key === "Escape") { e.preventDefault(); close(); anchor.focus(); }
    else if (e.key === "ArrowDown") { e.preventDefault(); items[(i + 1) % items.length].focus(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); items[(i - 1 + items.length) % items.length].focus(); }
  };
  anchor.after(menu);
  anchor.setAttribute("aria-expanded", "true");
  addEventListener("pointerdown", out, true); addEventListener("keydown", key, true);
  (items.find((x) => x.getAttribute("aria-checked") === "true") || items[0]).focus();
}

/** El selector de modo: vive en cada campo de escritura (el fijo y el del chat). */
function modePick() {
  const b = h("button", { type: "button", class: "mode-pick", "aria-haspopup": "menu", "aria-expanded": "false", onClick: () => modeMenu(b) },
    h("span", { class: "dot" }), h("span", { class: "mode-name" }), icon("chevron", 14));
  return b;
}
function paintMode() {
  for (const b of document.querySelectorAll(".mode-pick")) {
    b.querySelector(".mode-name").textContent = MODE[state.mode].label;
    b.setAttribute("aria-label", `Modo ${MODE[state.mode].label}. Cambiar`);
  }
}
// Cuando cambia el modo (lo elijas vos o lo cambie Ynara), una onda del color nuevo sale del selector.
addEventListener("ynara:modo", () => { paintMode(); shell?.ripple(); });

/* ───────── Estructura fija ─────────
   Escritorio: barra lateral de aplicación y campo de escritura siempre a mano.
   Móvil: cabecera mínima y barra inferior con el diamante al centro; al tocarlo,
   la barra se convierte en el campo de escritura. */
let shell = null;
function buildShell() {
  const link = (id, label) => h("a", { href: `#/${id}`, dataset: { id } }, icon(ICON[id]), h("span", null, label));

  // Escritorio
  const railLinks = NAV.map(([id, label]) => link(id, label));
  const railNav = h("nav", { class: "rail-nav", "aria-label": "Principal" }, h("i", { class: "rail-ind", "aria-hidden": "true" }), railLinks);
  const brand = h("a", { class: "brand", href: "#/hoy", "aria-label": "Ynara, ir a Hoy" }, logo(26), h("span", null, "Ynara"));
  const meName = h("span"), meAv = h("span", { class: "avatar", "aria-hidden": "true" });
  const me = h("a", { class: "me", href: "#/tu" }, meAv, meName);
  const rail = h("aside", { class: "rail" }, brand, railNav,
    h("button", { class: "rail-search", onClick: openPalette }, icon("buscar"), h("span", null, "Buscar"), h("kbd", null, "Ctrl K")), me);

  // Móvil
  const topBrand = h("a", { class: "brand", href: "#/hoy", "aria-label": "Ynara, ir a Hoy" }, logo(24), h("span", null, "Ynara"));
  const topAv = h("a", { class: "avatar", href: "#/tu", "aria-label": "Tú" });
  const top = h("header", { class: "top" }, topBrand, topAv);
  const tabLinks = NAV.map(([id, label]) => link(id, label));
  const askBtn = h("button", { type: "button", class: "ask-btn", "aria-label": "Decile algo a Ynara" }, dia());
  const tabbar = h("nav", { class: "tabbar", "aria-label": "Principal" }, tabLinks[0], tabLinks[1], askBtn, tabLinks[2], tabLinks[3]);

  const main = h("main", { id: "main", tabindex: "-1" });

  // Campo de escritura: modo, texto, enviar y voz.
  const modeChip = modePick();
  const input = h("input", { type: "text", placeholder: "Decile algo a Ynara…", "aria-label": "Decile algo a Ynara", autocomplete: "off", enterkeyhint: "send" });
  const dock = h("form", { class: "dock", onSubmit: (e) => { e.preventDefault(); const v = input.value.trim(); if (!v) return; input.value = ""; dock.classList.remove("has-text"); bar.classList.remove("asking"); app.pending = v; app.go("#/chat/nuevo"); } },
    modeChip, input,
    h("button", { type: "submit", class: "send", "aria-label": "Enviar" }, icon("flecha", 18)),
    h("button", { type: "button", class: "talk", "aria-label": "Hablar con Ynara", onClick: () => openVoice() }, dia()));
  input.addEventListener("input", () => dock.classList.toggle("has-text", !!input.value.trim()));
  input.addEventListener("keydown", (e) => { if (e.key === "Escape") input.blur(); });
  const bar = h("div", { class: "bar" }, tabbar, dock);

  // Móvil: el diamante abre el campo; si queda vacío y pierde el foco, vuelve la barra.
  askBtn.addEventListener("click", () => {
    const composer = document.querySelector(".composer textarea");
    if (composer) { composer.focus(); return; }
    bar.classList.add("asking"); input.focus();
  });
  dock.addEventListener("focusout", () => setTimeout(() => {
    if (!dock.contains(document.activeElement) && !input.value.trim() && !document.querySelector("dialog[open]")) bar.classList.remove("asking");
  }, 60));

  // Imán: el diamante de voz se acerca al puntero cuando pasa cerca. Es el único elemento que lo hace.
  const talk = dock.lastChild;
  addEventListener("pointermove", (e) => {
    if (e.pointerType !== "mouse" || reducedMotion()) return;
    const r = talk.getBoundingClientRect(), dx = e.clientX - (r.left + r.width / 2), dy = e.clientY - (r.top + r.height / 2);
    const near = Math.hypot(dx, dy) < 90;
    talk.style.setProperty("--mx", near ? `${(dx * 0.22).toFixed(1)}px` : "0px");
    talk.style.setProperty("--my", near ? `${(dy * 0.22).toFixed(1)}px` : "0px");
  }, { passive: true });

  root.replaceChildren(rail, top, main, bar);
  relayout();

  let current = "";
  const place = (name) => {
    for (const l of [...railLinks, ...tabLinks]) l.dataset.id === name ? l.setAttribute("aria-current", "page") : l.removeAttribute("aria-current");
    const a = railLinks.find((l) => l.dataset.id === name);
    railNav.style.setProperty("--o", a ? 1 : 0);
    if (a) railNav.style.setProperty("--y", `${a.offsetTop}px`);
  };
  addEventListener("resize", () => place(current));
  document.fonts?.ready.then(() => place(current));
  return {
    main,
    relogo() { brand.firstChild.replaceWith(logo(26)); topBrand.firstChild.replaceWith(logo(24)); },
    /** Al cambiar de modo, una onda del color nuevo sale de donde se eligió. */
    ripple() {
      if (reducedMotion()) return;
      const dot = [...document.querySelectorAll(".mode-pick .dot")].find((d) => d.getClientRects().length && getComputedStyle(d).visibility !== "hidden");
      const r = (dot || askBtn).getBoundingClientRect(), x = r.left + r.width / 2, y = r.top + r.height / 2;
      const el = h("i", { class: "ripple", "aria-hidden": "true", style: { left: `${x}px`, top: `${y}px` } });
      document.body.append(el);
      el.addEventListener("animationend", () => el.remove());
    },
    sync(r) {
      current = r.name; place(r.name);
      for (const el of [me, topAv]) r.name === "tu" ? el.setAttribute("aria-current", "page") : el.removeAttribute("aria-current");
      const initial = state.user.name.charAt(0).toUpperCase();
      meAv.textContent = initial; topAv.textContent = initial; meName.textContent = state.user.name;
      paintMode();
      if (!dock.contains(document.activeElement)) bar.classList.remove("asking");
      html.classList.toggle("route-chat", r.name === "chat");
    },
  };
}

/* ───────── Bienvenida ───────── */
const onb = { step: 0, data: null };
function onboarding() {
  onb.data ||= { name: "", interests: new Set(state.user.interests) };
  const data = onb.data;
  const box = h("div", { class: "onb-card" });
  const finish = () => { onb.step = 0; onb.data = null; update((s) => { s.user.onboarded = true; s.user.name = data.name.trim() || s.user.name; s.user.interests = [...data.interests]; }); location.hash = "#/hoy"; };
  const go = (n) => { onb.step = n; paint(); };
  const actions = (next, label = "Seguir") => h("div", { class: "onb-actions" },
    onb.step ? h("button", { class: "btn btn-quiet", onClick: () => go(onb.step - 1) }, "Atrás") : h("button", { class: "btn btn-quiet", onClick: finish }, "Saltar"),
    h("button", { class: "btn btn-primary", onClick: next }, label));

  function paint() {
    const step = onb.step;
    const kids = [step === 0 ? logo(40) : h("div", { class: "onb-step" }, `${step} de 3`)];
    if (step === 0) {
      kids.push(rise(["Tecnología que se siente ", h("em", null, "como pensar.")]),
        h("p", { class: "muted" }, "Ynara es un asistente con memoria. Tu día, tus recuerdos y tu voz, en un solo lugar. Esta es una demo con datos de ejemplo."),
        actions(() => go(1), "Empezar"));
    } else if (step === 1) {
      const input = h("input", { class: "input", placeholder: "Tu nombre", value: data.name, maxlength: 24, "aria-label": "Tu nombre", onInput: (e) => { data.name = e.target.value; }, onKeydown: (e) => { if (e.key === "Enter") go(2); } });
      kids.push(rise("¿Cómo te llamo?"), input, actions(() => go(2)));
      setTimeout(() => input.focus(), 60);
    } else if (step === 2) {
      const opts = h("div", { class: "opts" }, MODES.map((m) => {
        const b = modeOption(m, data.interests.has(m.id), () => {
          data.interests.has(m.id) ? data.interests.delete(m.id) : data.interests.add(m.id);
          b.setAttribute("aria-pressed", String(data.interests.has(m.id)));
        });
        return b;
      }));
      kids.push(rise("¿Para qué me vas a usar?"), opts, actions(() => go(3)));
    } else {
      kids.push(rise("También podés hablarme."),
        h("p", { class: "muted" }, voiceSupport().stt
          ? "Tocá el diamante en cualquier pantalla. Te pido permiso la primera vez, y lo que digamos queda en el chat."
          : "En este navegador no puedo escucharte, pero podés escribir o ver la demo guiada de voz."),
        h("p", { class: "note" }, "Soy una inteligencia artificial. No soy una persona ni reemplazo a un profesional."),
        actions(finish, "Entrar"));
    }
    box.replaceChildren(...kids);
  }
  paint();
  return h("div", { class: "onb" }, box);
}

/* ───────── Render ───────── */
function focusKey(el) {
  const tag = el.tagName.toLowerCase();
  const label = el.getAttribute("aria-label");
  if (label) return `${tag}[aria-label="${label.replace(/"/g, '\\"')}"]`;
  if (el.dataset.ev) return `${tag}[data-ev="${CSS.escape(el.dataset.ev)}"]`;
  if (el.dataset.mem) return `${tag}[data-mem="${CSS.escape(el.dataset.mem)}"]`;
  return null;
}

let lastRoute = "", lastTheme = "", lastMode = state.mode, enterTimer = 0;
function render() {
  applyPrefs();
  const theme = html.classList.contains("theme-dark") ? "dark" : "light";
  if (theme !== lastTheme) { lastTheme = theme; shell?.relogo(); refreshField(); }   // el logo cambia de variante con el tema
  const r = route();
  if (!state.user.onboarded) { shell = null; lastRoute = ""; setView("intro"); root.replaceChildren(onboarding()); return; }
  setView(r.name);
  shell ||= buildShell();
  const changed = r.name !== lastRoute;
  lastRoute = r.name;
  shell.sync(r);

  const view = { hoy: hoyView, agenda: agendaView, memoria: memoriaView, tu: tuView }[r.name];
  const main = shell.main, a = document.activeElement;
  const keep = !changed && a && main.contains(a) ? focusKey(a) : null;
  const y = document.scrollingElement.scrollTop;
  main.classList.remove("enter");
  main.replaceChildren(r.name === "chat" ? chatView(r.arg) : view());
  if (r.name === "chat") { main.querySelector(".composer")?.prepend(modePick()); paintMode(); }
  if (changed) {
    for (const d of document.querySelectorAll("dialog[open]:not(.voice)")) d.close();
    void main.offsetWidth; main.classList.add("enter");
    clearTimeout(enterTimer); enterTimer = setTimeout(() => main.classList.remove("enter"), 1100);
    scrollTo(0, 0);
  } else {
    document.scrollingElement.scrollTop = y;
    if (keep) { try { main.querySelector(keep)?.focus({ preventScroll: true }); } catch { /* selector no válido */ } }
  }
}

app.rerender = render;
app.openVoice = openVoice;
app.openPalette = openPalette;

subscribe(render);
addEventListener("hashchange", render);
addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); if (state.user.onboarded) openPalette(); }
});

// La primera vez que Ynara guarda algo en la sesión, el dato viaja como diamante hasta Memoria.
let flown = false;
addEventListener("ynara:recordado", () => {
  if (flown || reducedMotion()) return;
  flown = true;
  const before = document.querySelectorAll(".act").length;
  let tries = 0;
  const wait = setInterval(() => {
    const cards = document.querySelectorAll(".act"), to = document.querySelector('.nav a[data-id="memoria"]');
    if (++tries > 20) clearInterval(wait);
    if (cards.length <= before || !to) return;
    clearInterval(wait);
    const a = cards[cards.length - 1].getBoundingClientRect(), b = to.getBoundingClientRect();
    const x = a.left + 30, y = a.top + a.height / 2;
    const d = h("i", { class: "fly", "aria-hidden": "true", style: { left: `${x}px`, top: `${y}px` } });
    document.body.append(d);
    requestAnimationFrame(() => requestAnimationFrame(() => {
      d.style.transform = `translate(${b.left + b.width / 2 - x}px, ${b.top + b.height / 2 - y}px) rotate(225deg) scale(0.5)`;
      d.style.opacity = "0";
    }));
    setTimeout(() => { d.remove(); to.classList.add("bump"); setTimeout(() => to.classList.remove("bump"), 500); }, 720);
  }, 300);
});

initField();
render();
