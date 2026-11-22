// "Cerebro" de la demo: entiende la intención por reglas, ejecuta la acción
// sobre el estado y devuelve la respuesta con la voz de Ynara. No hay modelo:
// todo es determinista y local. Devuelve { mode, text, cards, cites }.

import { state, update } from "./state.js";
import { cuando, hhmm, relDia, haceCuanto, startOfDay, dayDiff, cap, uid, fechaLarga } from "./ui.js";

/** Minúsculas y sin tildes, conservando el largo (los índices coinciden con el original). */
export const norm = (s) => s.normalize("NFC").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();

const NUM = { una: 1, uno: 1, dos: 2, tres: 3, cuatro: 4, cinco: 5, seis: 6, siete: 7, ocho: 8, nueve: 9, diez: 10, once: 11, doce: 12 };
const DOW = ["domingo", "lunes", "martes", "miercoles", "jueves", "viernes", "sabado"];

/** Interpreta fecha y hora en español rioplatense. Devuelve los tramos usados para poder quitarlos del título. */
export function parseWhen(text, now = new Date()) {
  const n = norm(text);
  const used = [];
  const take = (m) => { if (m) used.push([m.index, m.index + m[0].length]); return m; };
  let hour = null, min = 0, part = null;

  const partM = take(n.match(/\b(?:de|a|por) la (manana|tarde|noche)\b/)) || take(n.match(/\b(al mediodia|a la madrugada)\b/));
  if (partM) part = partM[1];

  let m = take(n.match(/\b(?:a las?|tipo|para las?)?\s*(\d{1,2})[:.](\d{2})\s*(?:hs|h|horas)?\b/));
  if (m) { hour = +m[1]; min = +m[2]; }
  if (hour == null) {
    m = take(n.match(/\b(?:a las?|tipo|para las?)\s+(\d{1,2}|una|uno|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once|doce)(\s+y\s+(media|cuarto))?\s*(?:hs|h|horas)?\b/));
    if (m) { hour = NUM[m[1]] ?? +m[1]; if (m[3] === "media") min = 30; if (m[3] === "cuarto") min = 15; }
  }
  if (hour == null) {
    m = take(n.match(/\b(\d{1,2})\s*(?:hs|horas|h)\b/));
    if (m) hour = +m[1];
  }
  if (hour == null && part) hour = { manana: 9, tarde: 16, noche: 21, "al mediodia": 12, "a la madrugada": 6 }[part];
  else if (hour != null && hour < 12) {
    if (part === "tarde" || part === "noche") hour += 12;
    else if (!part && hour >= 1 && hour <= 7) hour += 12; // "a las 7" sin aclarar: se asume tarde y se confirma en 24 h
  }

  // El día: se quitan primero los "de la mañana" para no confundirlos con "mañana".
  const blanked = n.split("");
  for (const [a, b] of used) for (let i = a; i < b; i++) blanked[i] = " ";
  const rest = blanked.join("");
  let day = null;
  if ((m = take(rest.match(/\bpasado manana\b/)))) day = 2;
  else if ((m = take(rest.match(/\bmanana\b/)))) day = 1;
  else if ((m = take(rest.match(/\bhoy\b|\besta (tarde|noche)\b/)))) { day = 0; if (hour == null && m[1]) hour = m[1] === "tarde" ? 16 : 21; }
  else if ((m = take(rest.match(/\b(?:el |este |proximo )?(domingo|lunes|martes|miercoles|jueves|viernes|sabado)\b/)))) {
    day = (DOW.indexOf(m[1]) - now.getDay() + 7) % 7 || 7;
  } else if ((m = take(rest.match(/\bel (?:dia )?(\d{1,2})\b/)))) {
    const d = new Date(now.getFullYear(), now.getMonth(), +m[1]);
    if (d < startOfDay(now)) d.setMonth(d.getMonth() + 1);
    day = dayDiff(d, now);
  }

  if (day == null && hour == null) return { date: null, used, hasTime: false, hasDay: false };
  const date = new Date(now);
  date.setHours(hour ?? 9, min, 0, 0);
  if (day != null) date.setDate(now.getDate() + day);
  else if (date <= now) date.setDate(date.getDate() + 1);
  return { date, used, hasTime: hour != null, hasDay: day != null };
}

function cut(text, used) {
  const chars = text.normalize("NFC").split("");
  for (const [a, b] of used) for (let i = a; i < b; i++) chars[i] = " ";
  return chars.join("").replace(/\s+/g, " ").trim();
}

function titleFrom(text, used, verbs) {
  let t = cut(text, used)
    .replace(/[¿?¡!.]+/g, " ")
    .replace(verbs, " ")
    .replace(W("por favor|porfa|che"), " ")
    .replace(/\s+/g, " ").trim()
    .replace(/^(que|de|un|una|el|la|los|las|con el|con la|al|para|a)\s+/i, "")
    .replace(/\s+(para|el|a|en|de|que)$/i, "").trim();
  return t ? cap(t) : "";
}

const segunda = (s) => s
  .replace(/\bmis\b/gi, "tus").replace(/\bmi\b/gi, "tu").replace(/\bme\b/gi, "te").replace(/\byo\b/gi, "vos")
  .replace(/\btengo\b/gi, "tenés").replace(/\bsoy\b/gi, "sos").replace(/\bestoy\b/gi, "estás")
  .replace(/\bprefiero\b/gi, "preferís").replace(/\bquiero\b/gi, "querés").replace(/\bvoy\b/gi, "vas");

const STOP = new Set("que te conte sobre sabes acordas recordas tenes dije para como cuando donde algo todo esto esta este unos unas pero porque desde hasta mas muy hay del las los una con por mio mia tuyo sobre acerca cosas cosa".split(" "));
const keywords = (n) => n.replace(/[^a-z0-9ñ ]/g, " ").split(/\s+/).filter((w) => w.length > 3 && !STOP.has(w));

function eventsOn(date) {
  return state.events.filter((e) => dayDiff(new Date(e.start), date) === 0).sort((a, b) => a.start - b.start);
}

// ── Acciones sobre el estado (todas devuelven una tarjeta con deshacer) ──
export function addEvent(title, date, dur = 60, mode = "productividad") {
  const ev = { id: uid(), title, start: date.getTime(), dur, mode };
  update((s) => { s.events.push(ev); }, { silent: true });
  return { kind: "event", ref: ev.id, title, sub: cap(cuando(date)) };
}
function addReminder(text, date) {
  const r = { id: uid(), text, when: date.getTime(), done: false };
  update((s) => { s.reminders.push(r); }, { silent: true });
  return { kind: "reminder", ref: r.id, title: text, sub: cap(cuando(date)) };
}
function addTask(title, date) {
  const t = { id: uid(), title, due: (date || new Date()).getTime(), done: false, mode: "productividad" };
  update((s) => { s.tasks.push(t); }, { silent: true });
  return { kind: "task", ref: t.id, title, sub: date ? `Para ${relDia(date)}` : "Para hoy" };
}
function addMemory(text, mode) {
  const m = { id: uid(), type: "hecho", mode, date: Date.now(), text, source: "Me lo dijiste en esta conversación.", chat: "Conversación actual", fresh: true };
  update((s) => { s.memories.unshift(m); }, { silent: true });
  dispatchEvent(new CustomEvent("ynara:recordado"));
  return { kind: "memory", ref: m.id, title: text, sub: "Guardado en tu memoria" };
}

/** Deshace la acción de una tarjeta. Devuelve true si había algo para deshacer. */
export function undoCard(card) {
  const coll = { event: "events", reminder: "reminders", task: "tasks", memory: "memories" }[card.kind];
  if (!coll) return false;
  let found = false;
  update((s) => { const before = s[coll].length; s[coll] = s[coll].filter((x) => x.id !== card.ref); found = s[coll].length < before; }, { silent: true });
  return found;
}

/** Regex de palabras completas que entiende tildes ( de JS no las reconoce). */
const W = (alts) => new RegExp(`(?<![\\p{L}\\d])(?:${alts})(?![\\p{L}\\d])`, "giu");

const pick = (arr) => arr[Math.floor(Math.random() * arr.length)];

/** Punto de entrada: texto del usuario → respuesta de Ynara. */
export function respond(text) {
  const n = norm(text);
  const now = new Date();
  const name = state.user.name;
  const R = (mode, body, extra = {}) => ({ mode, text: body, cards: [], cites: [], ...extra });

  // 1. Señales de crisis: respuesta fija, nunca generada. Vale en cualquier modo.
  if (/(suicid|matarme|quitarme la vida|no quiero vivir|hacerme dano|lastimarme|autolesion|no vale la pena vivir|desaparecer para siempre)/.test(n)) {
    return R("bienestar", "Gracias por decírmelo. Lo que estás sintiendo importa y no tenés que atravesarlo en soledad.\n\nSoy una inteligencia artificial y esto me excede: necesitás a una persona. Me quedo acá mientras buscás a alguien.", { cards: [{ kind: "help" }] });
  }

  // 2. Recordar: cita textual, con origen.
  if (/(que te conte|que sabes|te acordas|que recordas|que tenes (de|sobre|guardado)|que te dije|que anotaste)/.test(n)) {
    const kw = keywords(n);
    let found = state.memories.filter((m) => kw.some((k) => norm(m.text + " " + m.chat).includes(k.replace(/s$/, ""))));
    if (!kw.length || /\b(de|sobre) mi\s*[?.!]*$/.test(n)) found = state.memories.slice(0, 3);
    if (!found.length) return R("memoria", "No tengo nada sobre eso. Si me lo contás, lo guardo.");
    found = found.slice(0, 3);
    const body = found.map((m) => `> ${m.text}\n${cap(haceCuanto(new Date(m.date)))}, en «${m.chat}».`).join("\n\n");
    return R("memoria", `${found.length === 1 ? "Esto es lo que tengo:" : "Tengo esto:"}\n\n${body}`, { cites: found.map((m) => m.id) });
  }

  // 3. Guardar un recuerdo explícito.
  let m = n.match(/\b(acordate|recorda|anota|guarda|tene en cuenta|no te olvides)(?: de)? que\s+/);
  if (m && !/recordame|acordame|avisame/.test(n)) {
    if (state.settings.memoryPaused) return R("memoria", "Tenés la memoria en pausa, así que no lo guardé. La reactivás desde Memoria.");
    const raw = text.normalize("NFC").slice(m.index + m[0].length).replace(/[.!]+$/, "").trim();
    const fact = cap(segunda(raw)) + ".";
    return R("memoria", "Anotado. Lo vas a ver en tu memoria y lo podés borrar cuando quieras.", { cards: [addMemory(fact, state.mode)] });
  }

  // 4. Recordatorio.
  if (/(recordame|acordame|avisame|haceme acordar|poneme un recordatorio|recordatorio)/.test(n)) {
    const w = parseWhen(text, now);
    const title = titleFrom(text, w.used, W("recordame|acordame|avisame|haceme acordar|poneme un recordatorio|recordatorio")) || "Recordatorio";
    const date = w.date || new Date(now.getTime() + 36e5);
    const note = w.date ? "" : " No me dijiste cuándo, así que te aviso en una hora.";
    return R("productividad", `Listo, te aviso ${cuando(date)}.${note}`, { cards: [addReminder(title, date)] });
  }

  // 5. Consultar la agenda o el día.
  if (/(que tengo|como viene|mi dia|mi agenda|tengo algo|que hay (hoy|manana)|resumime el dia|como esta mi)/.test(n)) {
    const w = parseWhen(text, now);
    const day = w.hasDay && w.date ? w.date : now;
    const list = eventsOn(day);
    const isToday = dayDiff(day, now) === 0;
    if (!list.length) return R("productividad", `${cap(relDia(day))} no tenés nada agendado.`);
    const pending = isToday ? list.filter((e) => e.start + e.dur * 6e4 > now.getTime()) : list;
    const top = state.tasks.find((t) => t.top && !t.done);
    let body;
    if (isToday && !pending.length) body = "Por hoy ya cerraste todo lo agendado.";
    else {
      const next = pending[0];
      body = `${cap(relDia(day))} ${isToday ? (pending.length === 1 ? "te queda" : "te quedan") : "tenés"} **${pending.length === 1 ? "una cosa" : pending.length + " cosas"}**. Lo próximo es ${next.title.toLowerCase()} a las ${hhmm(new Date(next.start))}.`;
      if (isToday && top) body += ` Tu prioridad: ${top.title.toLowerCase()}.`;
    }
    return R("productividad", body, { cards: [{ kind: "agenda", day: startOfDay(day).getTime() }] });
  }

  // 6. Agendar un evento.
  if (/(agend|reunion|turno|cita con|reserv|program|bloquea|bloqueame)/.test(n)) {
    const w = parseWhen(text, now);
    const title = titleFrom(text, w.used, W("agend[aá](?:me|r|te)?|agreg[aá](?:me)?|anot[aá](?:me)?|program[aá](?:me)?|pon[eé](?:me)?|cre[aá](?:me)?|reserv[aá](?:me)?|bloque[aá](?:me)?|necesito|quiero|tengo|en (?:la|mi) agenda|en el calendario")) || "Evento";
    const date = w.date || new Date(startOfDay(now).getTime() + 864e5 + 9 * 36e5);
    let note = "";
    if (!w.date) note = " No me dijiste cuándo: lo dejé mañana a las 9 y lo podés mover.";
    else if (!w.hasTime) note = " No me dijiste la hora, así que lo dejé a las 9.";
    const clash = state.events.find((e) => Math.abs(e.start - date.getTime()) < 30 * 6e4);
    if (clash && norm(clash.title) === norm(title)) return R("productividad", `Eso ya lo tenías agendado ${cuando(new Date(clash.start))}. No lo dupliqué.`);
    if (clash) note += ` Ojo: se pisa con ${clash.title.toLowerCase()}.`;
    const mode = /(estudi|parcial|clase|facu)/.test(n) ? "estudio" : /(futbol|cumple|cena|amig|cine)/.test(n) ? "vida" : "productividad";
    return R("productividad", `Listo, agendado ${cuando(date)}.${note}`, { cards: [addEvent(title, date, 60, mode)] });
  }

  // 7. Tarea o pendiente.
  if (/(tarea|pendiente|tengo que|me falta|no me puedo olvidar de)/.test(n)) {
    const w = parseWhen(text, now);
    const title = titleFrom(text, w.used, W("anot[aá](?:me)?|agreg[aá](?:me)?|sum[aá](?:me)?|como|una|la|tarea|pendiente|tengo que|me falta|no me puedo olvidar de")) || "Pendiente";
    return R("productividad", "Sumado a tus pendientes.", { cards: [addTask(title, w.date)] });
  }

  // 8. Estudio.
  if (/(estudi|parcial|examen|explic|repas|plan de estudio|unidad|tesis|no entiendo)/.test(n)) {
    if (/(desvio|varianza|media\b|mediana|estadistic.*(que es|explic))/.test(n) || /explic/.test(n) && /estadistic/.test(n)) {
      return R("estudio", "Arranquemos por lo que ya sabés: la **media** es el promedio. El **desvío estándar** te dice cuánto se alejan los datos de ese promedio.\n\nUn ejemplo: dos cursos con promedio 7. En uno todos sacaron entre 6 y 8; en el otro hay notas de 2 y de 10. La media es la misma, pero el desvío del segundo es mucho mayor.\n\nProbá vos ahora: si todos sacaran exactamente 7, ¿cuánto daría el desvío?");
    }
    if (/tesis/.test(n)) {
      return R("estudio", "Venías con un plan de tres bloques por semana, arrancando por el capítulo 3. ¿Cómo te fue con el primero? Si se trabó, lo achicamos a 25 minutos.", { cites: ["m2", "m3"] });
    }
    return R("estudio", "Te armo el repaso con lo que sé: el parcial entra las unidades 2, 3 y 4, y rendís mejor a la mañana.\n- **Mañana, 8:00** — unidad 2, 45 minutos y 10 de pausa\n- **Pasado, 8:00** — unidad 3, la más larga: dos bloques\n- **Dos días antes** — unidad 4 y ejercicios mezclados\n- **El día previo** — solo repaso, nada nuevo\n\nSi querés, te lo paso a la agenda.", { cites: ["m10", "m4"] });
  }

  // 9. Bienestar: frases cortas, preguntas abiertas, sin consejos.
  if (/(cansad|quemad|agotad|ansios|ansiedad|estresad|estres|triste|angusti|mal dia|no doy mas|abrumad|no puedo mas|me siento (mal|solo|sola)|bajon|desbordad)/.test(n)) {
    if (/(quemad|cansad|agotad|no doy mas|dorm)/.test(n)) {
      return R("bienestar", `Se nota, ${name}. La semana pasada me dijiste que venías durmiendo poco.\n\n¿Qué es lo que más te está pesando hoy?`, { cites: ["m9"] });
    }
    return R("bienestar", pick(["Te escucho. No hace falta ordenarlo: contámelo como salga.", "Acá estoy. ¿Qué pasó?", "Suena pesado. ¿Querés contarme qué fue lo que lo disparó?"]));
  }

  // 10. Vida.
  if (/(recomend|serie|pelicula|peli\b|comer|cenar|finde|fin de semana|regalo|musica|salir|plan para)/.test(n)) {
    if (/regalo/.test(n)) return R("vida", "Para Sofi iría por algo que use: unos auriculares o una entrada para un recital. Si me contás qué le gusta últimamente, afino.", { cites: ["m6"] });
    if (/(comer|cenar|pizza|restaurant)/.test(n)) return R("vida", "Hoy jugás al fútbol, así que después del partido: pizza a la piedra cerca de la cancha. Es viernes de amigos, no hace falta reservar.", { cites: ["m7"] });
    if (/(serie|peli|pelicula)/.test(n)) return R("vida", "Para el finde, algo corto y que no te pida mucha cabeza: una miniserie de seis capítulos, de suspenso. La terminás el domingo y el lunes no te quedás enganchado.");
    return R("vida", "El sábado a la mañana tenés el bloque de la tesis; a la tarde quedás libre. Yo saldría a caminar y dejaría la noche para el cumple de Sofi.", { cites: ["m6"] });
  }

  // 11. Conversación básica.
  if (/^(hola|buenas|buen dia|buenas tardes|buenas noches|que tal|hey|ey)\b/.test(n)) return R(state.mode, `Hola, ${name}. ¿En qué andás?`);
  if (/(gracias|genial|barbaro|joya|dale)\s*[.!]*$/.test(n) && n.length < 30) return R(state.mode, pick(["Dale.", "Listo.", "Para eso estoy."]));
  if (/(quien sos|que sos|que podes hacer|como funcion|ayuda|que modelo|sos una ia|sos humana)/.test(n)) {
    return R(state.mode, "Soy Ynara, una inteligencia artificial. No soy una persona ni reemplazo a un profesional.\n\nPuedo:\n- **Agendar** y avisarte: «agendame dentista mañana a las 7»\n- **Recordar** lo que me contás, y mostrarte de dónde lo saqué\n- **Ayudarte a estudiar** con planes y explicaciones\n- **Acompañarte** cuando el día viene pesado\n\nEsta es una demo con datos de ejemplo.");
  }
  if (/(que hora|que dia es|fecha de hoy)/.test(n)) return R(state.mode, `Son las ${hhmm(now)}. ${fechaLarga(now)}.`);

  // 12. Honestidad antes que inventar.
  return R(state.mode, "Eso todavía no lo sé hacer en esta demo. Probá pedirme que agende algo, que te cuente cómo viene tu día o qué recuerdo de un tema.");
}
