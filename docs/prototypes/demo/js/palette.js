// Paleta de comandos (Ctrl/⌘ + K): saltar entre vistas, ejecutar acciones y
// buscar en la memoria sin tocar el mouse.

import { app } from "./app.js";
import { state, update, applyPrefs, setMode } from "./state.js";
import { MODES } from "./data.js";
import { memorySheet } from "./views.js";
import { refreshField } from "./field.js";
import { h, icon, openDialog } from "./ui.js";

export function openPalette() {
  if (document.querySelector("dialog.palette")) return;
  let sel = 0, items = [];
  const list = h("div", { class: "palette-list", role: "listbox" });
  const input = h("input", { type: "text", placeholder: "Buscar, ir a una vista o pedirle algo a Ynara…", "aria-label": "Paleta de comandos", autocomplete: "off" });
  let closeFn = () => {};

  const base = () => [
    { g: "Ir a", label: "Hoy", ico: "foco", run: () => app.go("#/hoy") },
    { g: "Ir a", label: "Chat", ico: "dialogo", run: () => app.go("#/chat") },
    { g: "Ir a", label: "Agenda", ico: "agenda", run: () => app.go("#/agenda") },
    { g: "Ir a", label: "Memoria", ico: "memoria", run: () => app.go("#/memoria") },
    { g: "Ir a", label: "Tú", ico: "persona", run: () => app.go("#/tu") },
    { g: "Acciones", label: "Hablar con Ynara", ico: "mic", hint: "Voz", run: () => app.openVoice() },
    { g: "Acciones", label: "Ver la demo guiada de voz", ico: "play", run: () => app.openVoice({ guided: true }) },
    { g: "Acciones", label: "Nueva conversación", ico: "mas", run: () => app.go("#/chat/nuevo") },
    { g: "Acciones", label: state.settings.theme === "dark" ? "Pasar a tema claro" : "Pasar a tema Noche", ico: state.settings.theme === "dark" ? "sol" : "luna", run: () => { update((s) => { s.settings.theme = s.settings.theme === "dark" ? "light" : "dark"; }); applyPrefs(); refreshField(); } },
    ...MODES.map((m) => ({ g: "Modo", label: `Modo ${m.label}`, ico: "adaptacion", hint: m.desc, run: () => { setMode(m.id); refreshField(); } })),
  ];

  function paint() {
    const q = input.value.trim().toLowerCase();
    items = base().filter((c) => !q || c.label.toLowerCase().includes(q));
    if (q.length > 1) {
      for (const m of state.memories.filter((x) => x.text.toLowerCase().includes(q)).slice(0, 4)) items.push({ g: "Memoria", label: m.text, ico: "memoria", run: () => memorySheet(m) });
      items.push({ g: "Ynara", label: `Preguntarle a Ynara: «${input.value.trim()}»`, ico: "enviar", run: () => { app.pending = input.value.trim(); app.go("#/chat/nuevo"); } });
    }
    sel = Math.min(sel, Math.max(items.length - 1, 0));
    list.replaceChildren();
    let group = "";
    items.forEach((c, i) => {
      if (c.g !== group) { group = c.g; list.append(h("div", { class: "eyebrow palette-group" }, group)); }
      list.append(h("button", { class: "cmd", role: "option", "aria-selected": String(i === sel), onClick: () => run(i), onMouseEnter: () => { sel = i; mark(); } },
        icon(c.ico), h("span", null, c.label), c.hint ? h("small", null, c.hint) : null));
    });
  }
  function mark() {
    list.querySelectorAll(".cmd").forEach((el, i) => { el.setAttribute("aria-selected", String(i === sel)); if (i === sel) el.scrollIntoView({ block: "nearest" }); });
  }
  function run(i) { const c = items[i]; if (!c) return; closeFn(); setTimeout(c.run, 30); }

  input.addEventListener("input", () => { sel = 0; paint(); });
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); sel = (sel + 1) % items.length; mark(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); sel = (sel - 1 + items.length) % items.length; mark(); }
    else if (e.key === "Enter") { e.preventDefault(); run(sel); }
  });

  openDialog("palette", (close) => {
    closeFn = close;
    paint();
    return h("div", { class: "palette-in" }, h("div", { class: "palette-input" }, icon("buscar"), input, h("kbd", null, "Esc")), list);
  });
  input.focus();
}
