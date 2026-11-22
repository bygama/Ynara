// Voz de Ynara. Usa la Web Speech API del navegador: reconocimiento
// (SpeechRecognition) y síntesis (speechSynthesis). Diseño híbrido: pantalla de
// voz con subtítulos en vivo, y cada turno queda guardado en el chat.
// Si no hay micrófono o el navegador no reconoce voz, quedan dos caminos:
// escribir, o la demo guiada (un guion que se reproduce solo).

import { state } from "./state.js";
import { sendTurn, renderCard, activeChat, newChat } from "./convo.js";
import { GUIDED, MODE } from "./data.js";
import { app } from "./app.js";
import { h, icon, openDialog, plain, toast } from "./ui.js";
import { setOrb, setView } from "./field.js";

const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
const synth = window.speechSynthesis;

export const voiceSupport = () => ({ stt: !!SR, tts: !!synth });

export function spanishVoices() {
  if (!synth) return [];
  const rank = (v) => (/es-AR/i.test(v.lang) ? 0 : /es-(419|US|MX|UY|CL|CO)/i.test(v.lang) ? 1 : 2);
  return synth.getVoices().filter((v) => /^es/i.test(v.lang)).sort((a, b) => rank(a) - rank(b));
}
// Chrome carga las voces de forma asíncrona: al llegar, se refresca Tú.
synth?.addEventListener?.("voiceschanged", () => { if (location.hash.startsWith("#/tu")) app.rerender(); });
synth?.getVoices();

/** Dice un texto. `onWord(i)` avisa el avance para resaltar los subtítulos. */
export function speak(text, { onWord, onEnd } = {}) {
  const clean = plain(text).replace(/[«»]/g, "").replace(/\n+/g, ". ").replace(/\s+/g, " ").trim();
  const words = clean.split(" ");
  if (!synth || !synth.getVoices().length) {
    // Sin síntesis o sin voces instaladas: se simula el avance para que los
    // subtítulos igual acompañen, al ritmo de una lectura en voz alta.
    let i = 0, over = false;
    const id = setInterval(() => { onWord?.(i++); if (i >= words.length) { clearInterval(id); over = true; onEnd?.(); } }, 250 / state.settings.rate);
    return () => { clearInterval(id); if (!over) { over = true; onEnd?.(); } };
  }
  synth.cancel();
  const u = new SpeechSynthesisUtterance(clean);
  const voices = spanishVoices();
  const chosen = voices.find((v) => v.name === state.settings.voice) || voices[0];
  if (chosen) { u.voice = chosen; u.lang = chosen.lang; } else u.lang = "es-AR";
  u.rate = state.settings.rate;
  let done = false, boundary = false, idx = 0;
  const finish = () => { if (done) return; done = true; clearInterval(timer); onWord?.(words.length); onEnd?.(); };
  u.onboundary = (e) => {
    if (e.name && e.name !== "word") return;
    boundary = true;
    idx = clean.slice(0, e.charIndex).split(" ").length - 1;
    onWord?.(idx);
  };
  u.onend = finish;
  u.onerror = finish;
  // Algunas voces no emiten "boundary": avance estimado como respaldo.
  const timer = setInterval(() => { if (!boundary && idx < words.length) onWord?.(idx++); }, 230 / state.settings.rate);
  synth.speak(u);
  return () => { synth.cancel(); finish(); };
}

export function stopSpeaking() { synth?.cancel(); }

/** Nivel del micrófono (0–1) para que el orbe reaccione a la voz. */
async function micLevel(onLevel) {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
    const ac = new (window.AudioContext || window.webkitAudioContext)();
    const an = ac.createAnalyser();
    an.fftSize = 256;
    ac.createMediaStreamSource(stream).connect(an);
    const buf = new Uint8Array(an.frequencyBinCount);
    let raf = 0, smooth = 0;
    const tick = () => {
      an.getByteTimeDomainData(buf);
      let sum = 0;
      for (const v of buf) sum += (v - 128) ** 2;
      const rms = Math.sqrt(sum / buf.length) / 128;
      smooth += (Math.min(1, rms * 5) - smooth) * 0.35;
      onLevel(smooth);
      raf = requestAnimationFrame(tick);
    };
    tick();
    return () => { cancelAnimationFrame(raf); stream.getTracks().forEach((t) => t.stop()); ac.close(); };
  } catch { return null; }
}

const LABEL = { idle: "Tocá el diamante para hablar", listening: "Te escucho", thinking: "Pensando", speaking: "Hablando · tocá para interrumpir", guided: "Demo guiada" };

export function openVoice({ guided = false } = {}) {
  if (document.querySelector("dialog.voice")) return;
  if (!activeChat()) newChat();

  let st = "idle", rec = null, stopMic = null, stopSpeak = null, closed = false, guidedRun = false, level = 0, fake = 0;
  const support = voiceSupport();

  // El orbe de la voz es el del lienzo: la cámara entra en él. Este botón es
  // solo su zona de toque, transparente, encima.
  const orbEl = h("button", { class: "voice-orb", "aria-label": "Hablar o interrumpir" });
  const stateEl = h("div", { class: "voice-state", role: "status" });
  const capUser = h("p", { class: "cap-user" });
  const capAi = h("p", { class: "cap-ai" });
  const cards = h("div", { class: "voice-cards" });
  const hint = h("p", { class: "voice-hint" });
  const mainBtn = h("button", { class: "vbtn main", "aria-label": "Micrófono" }, icon("mic"));
  const typeInput = h("input", { class: "input", type: "text", placeholder: "Escribí lo que querés decir…", "aria-label": "Escribir en lugar de hablar", autocomplete: "off" });
  const typeForm = h("form", { class: "voice-type", hidden: true, onSubmit: (e) => { e.preventDefault(); const v = typeInput.value.trim(); if (!v) return; typeInput.value = ""; turn(v); } },
    typeInput, h("button", { class: "btn btn-primary" }, "Enviar"));

  function setState(next) {
    st = next;
    setOrb({ state: next });
    stateEl.replaceChildren(h("span", { class: "dot" }), guidedRun && next !== "speaking" && next !== "thinking" ? LABEL.guided : LABEL[next]);
    mainBtn.dataset.on = String(next === "listening");
    mainBtn.replaceChildren(icon(next === "listening" ? "detener" : "mic"));
    mainBtn.setAttribute("aria-label", next === "listening" ? "Dejar de escuchar" : "Hablar");
  }

  // El orbe late con el micrófono; al hablar Ynara, con un pulso sintético.
  let raf = 0;
  const frame = (t) => {
    if (st === "speaking") fake = 0.35 + 0.3 * Math.abs(Math.sin(t / 130)) * (0.6 + 0.4 * Math.sin(t / 47));
    else if (st === "listening" && !stopMic) fake = 0.15 + 0.1 * Math.abs(Math.sin(t / 420));
    else fake = 0;
    setOrb({ level: st === "listening" && stopMic ? level : fake });
    raf = requestAnimationFrame(frame);
  };
  raf = requestAnimationFrame(frame);

  function listen() {
    if (closed || guidedRun) return;
    stopSpeak?.(); stopSpeak = null;
    if (!SR) { showTyping("Este navegador no reconoce voz. Escribí o probá la demo guiada."); return; }
    try { rec?.abort(); } catch { /* ya estaba detenido */ }
    rec = new SR();
    rec.lang = "es-AR";
    rec.interimResults = true;
    rec.continuous = false;
    let finalText = "";
    rec.onstart = () => { setState("listening"); capUser.textContent = ""; capUser.classList.add("interim"); };
    rec.onresult = (e) => {
      let interim = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        if (e.results[i].isFinal) finalText += e.results[i][0].transcript;
        else interim += e.results[i][0].transcript;
      }
      capUser.textContent = finalText || interim;
    };
    rec.onerror = (e) => {
      if (e.error === "not-allowed" || e.error === "service-not-allowed") showTyping("No tengo permiso para usar el micrófono. Podés habilitarlo en el navegador, escribir o ver la demo guiada.");
      else if (e.error === "network") showTyping("El reconocimiento de voz necesita conexión. Escribí o probá la demo guiada.");
    };
    rec.onend = () => {
      if (closed) return;
      const said = finalText.trim();
      if (said) turn(said);
      else if (st === "listening") setState("idle");
    };
    try { rec.start(); } catch { setState("idle"); }
    if (!stopMic) micLevel((v) => { level = v; }).then((stop) => { if (closed) stop?.(); else stopMic = stop; });
  }

  function stopListening() { try { rec?.stop(); } catch { /* nada que detener */ } }

  function showTyping(message) {
    setState("idle");
    typeForm.hidden = false;
    if (message) hint.textContent = message;
    typeInput.focus();
  }

  /** Un turno completo: lo que dijo el usuario → respuesta hablada con subtítulos. */
  function turn(text) {
    return new Promise((resolve) => {
      capUser.classList.remove("interim");
      capUser.textContent = `«${text}»`;
      capAi.replaceChildren();
      cards.replaceChildren();
      setState("thinking");
      setTimeout(() => {
        if (closed) return resolve();
        const { ai } = sendTurn(text, "voz");
        delete ai.fresh;
        const words = plain(ai.text).replace(/\n+/g, " ").split(/\s+/).filter(Boolean);
        const spans = words.map((w) => h("span", { class: "w" }, `${w} `));
        capAi.replaceChildren(...spans);
        for (const c of ai.cards || []) cards.append(renderCard(c, { compact: true }));
        hint.textContent = ai.switched ? `Pasé a modo ${MODE[ai.mode].label}.` : "";
        setState("speaking");
        stopSpeak = speak(ai.text, {
          onWord: (i) => { for (let k = 0; k < spans.length; k++) spans[k].classList.toggle("said", k <= i); },
          onEnd: () => {
            stopSpeak = null;
            if (closed) return resolve();
            setState("idle");
            resolve();
            if (!guidedRun && state.settings.handsFree && SR && typeForm.hidden) setTimeout(listen, 350);
          },
        });
      }, 620);
    });
  }

  async function runGuided() {
    if (guidedRun) return;
    guidedRun = true;
    stopListening();
    typeForm.hidden = true;
    hint.textContent = "Demo guiada: Ynara responde a un guion fijo. Tocá el diamante para cortarla.";
    for (const line of GUIDED) {
      if (closed || !guidedRun) break;
      setState("listening");
      capAi.replaceChildren(); cards.replaceChildren();
      capUser.classList.add("interim");
      capUser.textContent = "";
      for (const ch of line) { if (closed || !guidedRun) break; capUser.textContent += ch; await sleep(28); }
      await sleep(450);
      if (closed || !guidedRun) break;
      await turn(line);
      await sleep(900);
    }
    guidedRun = false;
    if (!closed) { setState("idle"); hint.textContent = "Fin de la demo guiada. Todo quedó guardado en el chat."; }
  }

  function interrupt() {
    if (guidedRun) { guidedRun = false; stopSpeak?.(); stopSpeak = null; setState("idle"); hint.textContent = "Demo guiada detenida."; return; }
    if (st === "speaking") { orbEl.classList.remove("cut"); void orbEl.offsetWidth; orbEl.classList.add("cut"); stopSpeak?.(); stopSpeak = null; listen(); }
    else if (st === "listening") stopListening();
    else if (st === "idle") listen();
  }
  orbEl.addEventListener("click", interrupt);
  mainBtn.addEventListener("click", interrupt);

  const dlg = openDialog("voice", (close) =>
    h("div", { class: "voice-in" },
      h("div", { class: "voice-top" },
        h("button", { class: "btn btn-ghost btn-sm", onClick: () => { close(); app.go(`#/chat/${state.activeChat}`); } }, icon("dialogo", 16), "Ver el chat"),
        h("button", { class: "btn btn-ghost btn-sm", onClick: runGuided }, icon("play", 16), "Demo guiada"),
        h("button", { class: "icon-btn", "aria-label": "Cerrar la voz", onClick: close }, icon("cerrar"))),
      h("div", { class: "voice-stage" }, orbEl, h("div", { class: "voice-copy" }, stateEl, h("div", { class: "captions", "aria-live": "polite" }, capUser, capAi), cards)),
      h("div", { class: "voice-bottom" },
        typeForm,
        h("div", { class: "voice-ctrls" },
          h("button", { class: "vbtn", "aria-label": "Escribir en lugar de hablar", onClick: () => { stopListening(); typeForm.hidden = !typeForm.hidden; if (!typeForm.hidden) typeInput.focus(); } }, icon("teclado")),
          mainBtn,
          h("button", { class: "vbtn end", "aria-label": "Terminar", onClick: close }, icon("cerrar"))),
        hint)));

  document.documentElement.classList.add("voice-open");
  setView("voice");

  dlg.addEventListener("close", () => {
    closed = true; guidedRun = false;
    document.documentElement.classList.remove("voice-open");
    setOrb({ state: "idle", level: 0 });
    cancelAnimationFrame(raf);
    try { rec?.abort(); } catch { /* ya estaba detenido */ }
    stopSpeak?.(); stopSpeaking(); stopMic?.();
    app.rerender();
  });
  dlg.addEventListener("keydown", (e) => {
    if (e.code === "Space" && e.target.tagName !== "INPUT") { e.preventDefault(); interrupt(); }
  });

  setState("idle");
  if (!support.tts) toast("Este navegador no tiene voz: vas a ver los subtítulos.");
  if (guided) runGuided();
  else if (SR) { hint.textContent = "Hablá natural. Barra espaciadora o un toque en el diamante para hablar o interrumpir."; listen(); }
  else showTyping("Este navegador no reconoce voz. Escribí o probá la demo guiada.");
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
