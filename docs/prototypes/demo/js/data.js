// Datos fijos de la demo. Las fechas son relativas a hoy para que la demo
// siempre se vea actual. Nada de esto sale del navegador.

export const MODES = [
  { id: "productividad", label: "Productividad", desc: "Agendar, recordar y cerrar pendientes", color: "#2f5aa6" },
  { id: "estudio", label: "Estudio", desc: "Explicar, planificar y repasar", color: "#434a82" },
  { id: "bienestar", label: "Bienestar", desc: "Bajar un cambio y pensar en voz alta", color: "#8165a3" },
  { id: "vida", label: "Vida", desc: "Charla cotidiana y recomendaciones", color: "#5c6fb3" },
  { id: "memoria", label: "Memoria", desc: "Lo que Ynara recuerda de vos", color: "#565f81" },
];
export const MODE = Object.fromEntries(MODES.map((m) => [m.id, m]));

/** Fecha relativa: `at(1, "19:00")` = mañana a las 19. */
export function at(days, time = "09:00") {
  const [hh, mm] = time.split(":").map(Number);
  const d = new Date();
  d.setDate(d.getDate() + days);
  d.setHours(hh, mm, 0, 0);
  return d.getTime();
}

export function seed() {
  return {
    user: { name: "Mateo", onboarded: false, interests: ["productividad", "estudio", "memoria"] },
    settings: { v5: true, theme: "light", field: "media", textSize: "md", motion: "auto", voice: "", rate: 1, handsFree: true, speakReplies: false, memoryPaused: false, sounds: false, style: "minimal" },
    mode: "productividad",
    events: [
      { id: "e1", title: "Standup del equipo", start: at(0, "09:30"), dur: 30, mode: "productividad", place: "Meet" },
      { id: "e2", title: "Reunión con el cliente: propuesta", start: at(0, "11:00"), dur: 60, mode: "productividad", place: "Oficina" },
      { id: "e3", title: "Bloque de estudio: Estadística", start: at(0, "15:00"), dur: 45, mode: "estudio" },
      { id: "e4", title: "Fútbol con los pibes", start: at(0, "19:30"), dur: 90, mode: "vida", place: "Cancha de Palermo" },
      { id: "e5", title: "Entrega del informe mensual", start: at(1, "10:00"), dur: 30, mode: "productividad" },
      { id: "e6", title: "Clase de Estadística", start: at(2, "18:00"), dur: 120, mode: "estudio", place: "Aula 204" },
      { id: "e7", title: "Cumple de Sofi", start: at(3, "21:00"), dur: 180, mode: "vida" },
      { id: "e8", title: "Parcial de Estadística", start: at(5, "14:00"), dur: 120, mode: "estudio", place: "Aula Magna" },
    ],
    tasks: [
      { id: "t1", title: "Cerrar la propuesta del cliente", due: at(0, "11:00"), done: false, mode: "productividad", top: true },
      { id: "t2", title: "Repasar la unidad 3 de Estadística", due: at(0, "15:00"), done: false, mode: "estudio" },
      { id: "t3", title: "Responder el mail de la facultad", due: at(0, "18:00"), done: true, mode: "productividad" },
      { id: "t4", title: "Comprar el regalo de Sofi", due: at(2, "12:00"), done: false, mode: "vida" },
    ],
    reminders: [
      { id: "r1", text: "Llevar la notebook cargada a la reunión", when: at(0, "10:30"), done: false },
      { id: "r2", text: "Pagar la cuota del gimnasio", when: at(1, "09:00"), done: false },
    ],
    memories: [
      { id: "m1", type: "hecho", mode: "productividad", date: at(-34), text: "Trabajás como diseñador en una agencia y estudiás de noche.", source: "Me contaste que salís de la agencia a las 18 y cursás tres veces por semana.", chat: "Primera charla" },
      { id: "m2", type: "hecho", mode: "estudio", date: at(-12), text: "Estás escribiendo la tesis y decidiste arrancar por el capítulo 3.", source: "Dijiste: «arranco por el capítulo 3, que es el que tengo más claro».", chat: "Plan para la tesis" },
      { id: "m3", type: "momento", mode: "estudio", date: at(-12), text: "La tesis te tenía trabado. Armamos un plan de tres bloques por semana.", source: "Resumen de la conversación «Plan para la tesis».", chat: "Plan para la tesis" },
      { id: "m4", type: "costumbre", mode: "estudio", date: at(-20), text: "Rendís mejor a la mañana. Después de las 22 no estudiás.", source: "Lo fui notando: las tres veces que estudiaste de noche lo dejaste a la media hora.", chat: "Semana de parciales" },
      { id: "m5", type: "costumbre", mode: "productividad", date: at(-28), text: "Preferís respuestas cortas y directas.", source: "Me pediste: «decímelo corto, sin vueltas».", chat: "Primera charla" },
      { id: "m6", type: "hecho", mode: "vida", date: at(-25), text: "Tu hermana se llama Sofi y cumple años esta semana.", source: "Me pediste que te avisara para comprarle un regalo.", chat: "Cosas de la semana" },
      { id: "m7", type: "costumbre", mode: "vida", date: at(-18), text: "Los viernes jugás al fútbol con tus amigos.", source: "Lo agendaste cuatro viernes seguidos.", chat: "Cosas de la semana" },
      { id: "m8", type: "momento", mode: "productividad", date: at(-7), text: "Cerraste la entrega del cliente dos días antes de la fecha.", source: "Resumen de la conversación «Entrega del cliente».", chat: "Entrega del cliente" },
      { id: "m9", type: "momento", mode: "bienestar", date: at(-4), sensitive: true, text: "Venías durmiendo poco y te sentías quemado. Quedamos en cortar pantallas a las 23.", source: "Resumen de una charla en modo Bienestar. Marcado como sensible: se borra solo a los 180 días.", chat: "Semana pesada" },
      { id: "m10", type: "hecho", mode: "estudio", date: at(-3), text: "El parcial de Estadística cubre las unidades 2, 3 y 4.", source: "Me lo dictaste después de la clase.", chat: "Semana de parciales" },
    ],
    chats: [
      {
        id: "c1", title: "Plan para la tesis", mode: "estudio", updated: at(-12, "20:10"),
        messages: [
          { id: "x1", role: "user", text: "La tesis me tiene trabado, no sé por dónde arrancar." },
          { id: "x2", role: "ai", mode: "estudio", text: "Arranquemos por lo que ya tenés claro. ¿Qué capítulo podrías explicarme hoy sin mirar apuntes?" },
          { id: "x3", role: "user", text: "El 3, el del relevamiento." },
          { id: "x4", role: "ai", mode: "estudio", text: "Entonces empezamos por el 3. Te propongo tres bloques por semana:\n- **Lunes** 45 minutos: estructura y títulos\n- **Miércoles** 45 minutos: primer borrador de una sección\n- **Sábado** a la mañana: revisar y cerrar\n\nProbá el primero mañana y me contás cómo te fue.", cites: ["m2"] },
        ],
      },
      {
        id: "c2", title: "Semana de parciales", mode: "estudio", updated: at(-3, "19:30"),
        messages: [
          { id: "y1", role: "user", text: "El parcial de Estadística entra unidades 2, 3 y 4." },
          { id: "y2", role: "ai", mode: "estudio", text: "Anotado. Como rendís mejor a la mañana, te conviene repasar la 3 antes de ir a la agencia.", cites: ["m4", "m10"] },
        ],
      },
    ],
    activeChat: null,
    nudge: { id: "n1", kind: "Anticipación", text: "Tenés 90 minutos libres antes de la reunión de las 11. ¿Bloqueo ese rato para cerrar la propuesta?", yes: "Sí, bloquealo", no: "Ahora no", state: "open" },
    suggestions: [
      { id: "s1", mode: "estudio", text: "Armar el repaso de la unidad 3 para hoy", prompt: "Armame un plan de estudio para la unidad 3 de Estadística" },
      { id: "s2", mode: "vida", text: "Elegir el regalo de Sofi antes del jueves", prompt: "Recomendame un regalo para mi hermana Sofi" },
      { id: "s3", mode: "memoria", text: "Ver qué recordás de la tesis", prompt: "¿Qué te conté sobre la tesis?" },
    ],
  };
}

export const PROMPTS = [
  { mode: "productividad", label: "Organizar", text: "¿Cómo viene mi día?" },
  { mode: "productividad", label: "Agendar", text: "Agendame dentista mañana a las 7 de la tarde" },
  { mode: "estudio", label: "Estudiar", text: "Armame un plan de estudio para el parcial" },
  { mode: "memoria", label: "Recordar", text: "¿Qué te conté sobre la tesis?" },
  { mode: "bienestar", label: "Bajar un cambio", text: "Estoy medio quemado hoy" },
  { mode: "vida", label: "Charlar", text: "Recomendame una serie para el finde" },
];

/** Guion de la demo guiada del modo voz (para mostrar sin micrófono). */
export const GUIDED = [
  "¿Cómo viene mi día?",
  "Agendame dentista mañana a las siete de la tarde",
  "Acordate que la defensa de la tesis es en diciembre",
  "¿Qué te conté sobre la tesis?",
  "Estoy medio quemado hoy",
];
