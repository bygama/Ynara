# Auditoría dura: qué le falta a Ynara para ser un producto de mercado

> Entregable de trabajo del 2026-11-09.
>
> Versión pública. Los hallazgos de seguridad sin arreglar se reservaron: figuran
> con su ID y la marca *detalle reservado*, y se gestionan por el canal privado de
> `SECURITY.md`.
>
> **Base auditada:** `main` al 2026-11-09. **Complementa** a
> [`PENDIENTES-2026-11.md`](./PENDIENTES-2026-11.md) (deck, conexión, docs, CI):
> acá no se repite, se cita por ID (CON-xx, INF-xx, DECK-xx…).
>
> **Método:** cinco auditorías read-only en paralelo (seguridad/privacidad/legal,
> operación y escalabilidad, capa de IA, app web, mobile + admin + landing) y
> una segunda verificación manual de los hallazgos más graves contra el código.
> No se corrió el backend ni los modelos (falta `uv`; ver sección 7).

**Leyenda**

- **[V+]** verificado dos veces (agente y revisión manual). **[V]** verificado por
  el agente leyendo código. **[I]** inferido o estimado.
- **Esfuerzo:** S menos de 1 día · M 1 a 5 días · L más de 1 semana.
- Entre paréntesis, los IDs de origen de cada auditoría, para trazabilidad.
- Lo marcado **⚖** es materia legal: lo detecta la auditoría, lo cierra un abogado.

---

## 0. Veredicto

**Ynara hoy es una tesis sólida y no es un producto lanzable.** El núcleo técnico
(auth, cifrado, aislamiento por usuario, chat, memoria, contratos compartidos)
está por encima de lo esperable. Lo que falta es casi todo lo que rodea al
núcleo: no se puede cobrar, no se puede recuperar ni borrar una cuenta, no hay
base legal para tratar datos de salud mental, la seguridad del usuario en crisis
depende de tres líneas de prompt, el servidor es una PC hogareña, y la memoria
—el diferencial del producto— no aprende en tres de los cinco modos.

| Área | Estado | Bloqueantes | Lo que define el estado |
|---|---|---:|---|
| Cuenta e identidad | 🔴 | 6 | Sin recuperar contraseña, verificar email ni borrar cuenta |
| Legal y privacidad | 🔴 | 11 | Sin términos, sin consentimiento, promesas que el código no cumple |
| Seguridad del usuario (IA) | 🔴 | 5 | Crisis solo por prompt y solo en un modo; sin edad mínima |
| Cobro | 🔴 | 4 | Cero infraestructura de pago, en front y en back |
| Infraestructura y operación | 🔴 | 9 | PC hogareña, sin backups probados, observabilidad incompleta |
| Capacidad y confiabilidad del chat | 🔴 | 6 | Capacidad insuficiente para 1.000 usuarios [I] |
| Calidad de memoria e IA | 🔴 | 8 | Sin evals; consolidación ciega; `memory.add` confirma sin escribir |
| Seguridad web | 🟠 | 3 | Endurecimiento pendiente; *detalle reservado* |
| App web (producto) | 🟠 | 6 | Sesión que muere sin salida; recordatorios que nunca avisan |
| Mobile y tiendas | 🔴 | 5 | No pasaría revisión de App Store ni de Play (suma 6 de otras áreas) |
| Admin (operar usuarios) | 🔴 | 4 | Solo lectura y agregado: no se puede atender a un usuario |
| Landing y adquisición | 🟠 | 5 | Todos los CTA terminan en un `mailto:` |
| Núcleo backend (auth, cripto, aislamiento) | 🟢 | 0 | A nivel producción |

**Total: 72 bloqueantes consolidados.** Las cinco auditorías reportaron unos 110
antes de fusionar duplicados (por ejemplo, "borrar cuenta" apareció en cuatro).

**Capacidad estimada [I]:** capacidad insuficiente para 1.000 usuarios.

---

## 1. Bloqueantes

### 1.1 Cuenta e identidad

- [ ] **M-01 · No se puede borrar la cuenta.** Solo existe `PATCH /v1/users/me`.
      Las FK con `ON DELETE CASCADE` ya están: falta el endpoint y la UI. Apple y
      Google lo exigen dentro de la app. `apps/backend/app/api/v1/users.py:21`.
      **M** [V+] (SEC-01, WEB-03, STORE-01)
- [ ] **M-02 · No hay recuperación de contraseña.** Ni endpoint ni UI; en mobile
      la pantalla simula un envío que no ocurre. `apps/backend/app/api/v1/auth.py:209-459`,
      `apps/mobile/src/features/auth/ForgotPasswordScreen.tsx:58,62`. **L** [V+]
      (WEB-01, STORE-05, CON-11)
- [ ] **M-03 · No existe proveedor de email.** Sin SMTP, Resend ni similar. Bloquea
      M-02, M-04, recibos de pago y aviso de brechas. **M** [V+] (OPS-10, WEB-31)
- [ ] **M-04 · El email no se verifica.** Falta el flujo de verificación al
      registrarse. **M** [V] (SEC-02, WEB-02)
- [ ] **M-05 · No se puede cambiar contraseña ni email.** `UserUpdate` solo acepta
      nombre, onboarding, huso y retención. `apps/backend/app/schemas/user.py:48-54`.
      **M** [V] (SEC-03, WEB-04)
- [ ] **M-06 · La sesión muere sin salida.** No hay refresh ni manejo de la sesión
      vencida en web ni mobile: cuando vence, el usuario queda adentro viendo
      errores. **M** [V] (WEB-05, MOB-05, CON-02)

### 1.2 Legal y privacidad ⚖

- [ ] **M-07 · No hay Términos ni Política de Privacidad publicados.** Solo
      borradores en `docs/compliance/compliance.pdf` con `[razón social]` sin
      completar. Sin páginas `/legal` en web, landing ni mobile. **M** [V]
      (LEGAL-03, WEB-32, STORE-02, GTM-04)
- [ ] **M-08 · El registro no pide aceptar términos ni guarda la versión aceptada.**
      `apps/backend/app/schemas/auth.py:33-52`. **S** [V+] (LEGAL-04, STORE-09)
- [ ] **M-09 · Sin consentimiento expreso para datos de salud.** El modo Bienestar
      guarda contenido emocional; es el requisito n.º 1 del propio informe de
      compliance (Ley 25.326, datos sensibles). No existe `consent_log`. **M** [V+]
      (PRIV-02, SAFE-05)
- [ ] **M-10 · Sin edad mínima ni dato de edad.** El onboarding no pide fecha de
      nacimiento y el público incluye estudiantes. **S–M** [V+] (PRIV-10, SAFE-04)
- [ ] **M-11 · Sin aviso de "es una IA, no un profesional de salud".** Ni en chat,
      ni en Bienestar, ni en onboarding. **S** [V] (LEGAL-05, WEB-33, STORE-08)
- [ ] **M-12 · "Borrar todo" no borra las conversaciones.** El wipe limpia solo las
      3 capas de memoria; quedan turnos, sesiones, eventos, tareas y recordatorios.
      La UI dice "Borrar para siempre". `apps/backend/app/services/memory.py:530-533`.
      **M** [V+] (PRIV-03)
- [ ] **M-13 · Endurecer el manejo de los datos del chat en el backend.** **S**
      Detalle reservado: se gestiona por el canal privado de `SECURITY.md`.
- [ ] **M-14 · El export no cumple el derecho de acceso.** Solo trae memoria;
      faltan perfil, conversaciones, eventos, tareas y recordatorios. En mobile se
      comparte como texto por la hoja del sistema. **M** [V] (PRIV-07, WEB-10, MOB-17)
- [ ] **M-15 · Base de datos sin inscribir ante la AAIP y transferencia
      internacional sin documentar** (Supabase en Brasil, Vercel y Sentry en
      EE.UU.). Faltan DPA y cláusulas. **S** [I] (LEGAL-01, PRIV-11)
- [ ] **M-16 · Promesas públicas que el código no cumple.** Riesgo de publicidad
      engañosa. **S** (copy) [V] (LEGAL-02, GTM-05, AI-09, PAY-05, DECK-07)
  - "Modelos propios / no dependemos de Google": son Gemma y Qwen stock, sin
    ajustar. `README.md:16,339`. [V+]
  - "Privada y on-prem / nada sale del perímetro": base en Supabase, web en Vercel.
  - "Tu memoria está cifrada": hay que ajustar el copy al alcance real del
    cifrado.
  - "Cita textual lo que recuerda — nunca inventa": garantía absoluta sobre un LLM.
  - "Lee tu energía y tus patrones de descanso / te frena antes del burnout": no
    hay sensores, solo un mood en el onboarding.
  - "Gratis para siempre" y los beneficios Premium: no hay planes ni límites.
  - `SECURITY.md`: promete pausa de memoria, export de conversaciones y controles
    operativos que todavía no existen.
- [ ] **M-17 · Lo necesario para cobrar suscripciones en Argentina:** botón de
      arrepentimiento y de baja, precio final, facturación. **M** [I] (LEGAL-06)

### 1.3 Seguridad del usuario en la IA

- [ ] **M-18 · El protocolo de crisis son tres líneas de prompt y solo en
      Bienestar.** Una ideación suicida en Vida, Estudio, Productividad o Memoria
      no tiene ninguna instrucción. No hay detector ni respuesta fija en código.
      `apps/backend/app/llm/prompts/bienestar.py:30-32`. **M** [V+] (SAFE-01, PRIV-01)
- [ ] **M-19 · El prompt no trae ningún recurso de ayuda.** Dice "líneas de ayuda
      locales" y el modelo tiene que inventarlas; compliance advierte que un
      número equivocado es peor que ninguno. Hay que cargar recursos por país
      verificados con fuente oficial, inyectados por código y visibles en la UI.
      **S** [V+] (SAFE-02)
- [ ] **M-20 · Instrucciones contradictorias.** El mismo prompt prohíbe decir
      "deberías ver un profesional" y ordena "te corrés" ante una crisis: retirarse
      en el peor momento. `bienestar.py:23-24,31`. **S** [V+] (SAFE-03)
- [ ] **M-21 · Sin canal para reportar una respuesta de la IA**, ni feedback por
      mensaje. Google lo pide para apps de IA generativa; además es la única señal
      de calidad posible. **M** [V] (AI-30, STORE-08, ADM-12)
- [ ] **M-22 · Faltan confirmaciones del usuario en acciones del agente sobre su
      memoria.** **M** [V] (AI-11, SEC-19)

### 1.4 Cobro

- [ ] **M-23 · No hay proveedor de pago, checkout, webhooks ni facturas.** Cero
      referencias a Mercado Pago, Stripe o suscripciones en el backend. **L** [V+]
      (PAY-01, STORE-03)
- [ ] **M-24 · El modelo `User` no tiene plan ni estado de suscripción.**
      `apps/backend/app/models/user.py:52-79`. **M** [V+] (PAY-02)
- [ ] **M-25 · El plan gratuito no tiene ningún límite.** No hay cuotas por plan.
      **M** [V]
      (PAY-03, SEC-20, SCALE-06)
- [ ] **M-26 · Paywall de maqueta.** "Plan gratis" hardcodeado en dos lugares,
      precio "$6.900/mes" sin moneda contra "USD 4–6" del deck, y un botón que
      responde "Premium es demo por ahora". Los planes no están especificados en
      ningún documento de producto. En mobile, vender Premium exige compras dentro
      de la app. **M** [V] (PAY-04/05/06/07/08)

### 1.5 Infraestructura y operación

- [ ] **M-27 · Punto único de falla total.** Todo el servicio depende de un solo
      equipo hogareño, sin redundancia. **L** [V] (OPS-06, OPS-07, WEB-42, MOB-02, AI-45)
- [ ] **M-28 · No hay camino reproducible a producción.** El compose de producción
      no refleja el despliegue real. Sin paso de migración en el deploy.
      **M** [V] (OPS-01, OPS-03, INF-01)
- [ ] **M-29 · Sin backups propios ni restore probado.** `backup-db.sh` no existe;
      el del runbook no cumple lo que promete `SECURITY.md`. **M** [V] (REL-15, SEC-27, INF-03)
- [ ] **M-30 · Falta la rotación y la custodia de la clave de cifrado.**
      **M** [V] (SEC-26, REL-18, OPS-04)
- [ ] **M-31 · Hallazgo de infraestructura reservado.** **S**
      Detalle reservado: se gestiona por el canal privado de `SECURITY.md`.
- [ ] **M-32 · Producción acepta embeddings falsos.** `EMBEDDING_BACKEND` es
      `fake` por defecto y el fail-fast de producción no lo valida: se escribirían
      vectores `sha256` en tablas sagradas, irrecuperables sin re-embeddear todo.
      `apps/backend/app/core/config.py:132`. **S** [V+] (OPS-19, MEMQ-03)
- [ ] **M-33 · Observabilidad del backend incompleta.** **M** [V]
      (OPS-14, OPS-16, REL-10)
- [ ] **M-34 · Hallazgo de infraestructura reservado.** **S**
      Detalle reservado: se gestiona por el canal privado de `SECURITY.md`.
- [ ] **M-35 · Sin observabilidad en los clientes.** Ni Sentry ni analítica en web
      ni mobile; las claves están en los `.env.example` pero no hay SDK. **M** [V]
      (WEB-27, MOB-08, GTM-08)

### 1.6 Capacidad y confiabilidad del chat

- [ ] **M-36 · Gestión de capacidad del chat bajo carga.** **M**
      Detalle reservado: se gestiona por el canal privado de `SECURITY.md`.
- [ ] **M-37 · Duración de los turnos del chat.** **S**
      Detalle reservado: se gestiona por el canal privado de `SECURITY.md`.
- [ ] **M-38 · Manejo de recursos de la base durante el chat.** **M**
      Detalle reservado: se gestiona por el canal privado de `SECURITY.md`.
- [ ] **M-39 · Si cae el servicio de embeddings, el chat responde 500.**
      `render_context_block` corre fuera del `try` que degrada; el modo "IA
      pausada" de ADR-027 no funciona como se describe.
      `apps/backend/app/llm/router.py:225`. **S** [V+] (REL-01, AI-46)
- [ ] **M-40 · La ventana de contexto presupuestada no coincide con la servida.**
      El config no coincide con lo medido en ADR-012 y el contexto puede
      truncarse. Tampoco se versiona la configuración del servidor de modelos.
      **S** [V+] (SCALE-03, AI-23)
- [ ] **M-41 · Los recordatorios se marcan como enviados sin entregarse.** El
      notifier es un noop y el scheduler pone `SENT` aunque no haya dispositivos.
      No hay push, Web Push ni email. Es la promesa central del producto.
      `apps/backend/app/workflows/reminder_dispatch.py:99-100`. **L** [V+]
      (REL-12, REL-13, UX-11, MOB-06, AI-31, CON-04, CON-05)

### 1.7 Calidad de la memoria y de la IA

- [ ] **M-42 · No existe ningún sistema de evaluación.** Sin datasets, golden sets
      ni regresión; el único test con modelo real es opcional y verifica que la
      respuesta no esté vacía. Sin esto no se puede medir nada de esta sección.
      **L** [V] (AI-01, AI-03)
- [ ] **M-43 · La consolidación de memoria es ciega.** El extractor recibe solo el
      turno, sin la memoria existente: no deduplica ni detecta contradicciones.
      "Vivo en Córdoba" y "me mudé a Rosario" coexisten.
      `apps/backend/app/llm/memory_engine.py:179-188`. **L** [V] (MEMQ-01)
- [ ] **M-44 · La memoria no aprende en tres de cinco modos.** Los turnos de
      Estudio, Bienestar y Vida nunca consolidan, y la memoria episódica solo se
      crea al cerrar sesión, cosa que ningún cliente hace: en uso real no se
      genera. `apps/backend/app/services/chat.py:340-342`. **M** [V]
      (MEMQ-02, SCALE-08, CON-06)
- [ ] **M-45 · `memory.add` confirma sin escribir.** Devuelve "Anotado en tu
      memoria." y la escritura depende de que otra llamada asíncrona decida
      guardarlo. `apps/backend/app/llm/tools/memory.py:205-221`. **M** [V+] (AI-13)
- [ ] **M-46 · Se pueden persistir hechos falsos.** El extractor no distingue
      hipótesis ni bromas, y el usuario no
      recibe aviso de qué se guardó. **M** [V] (MEMQ-06)
- [ ] **M-47 · Datos sensibles sin clasificar.** Solo se marca sensible lo
      episódico de Bienestar; salud contada en otros modos queda como hecho común
      con retención indefinida, y los resúmenes de Bienestar se inyectan en otros
      modos. **M** [V] (MEMQ-07, SAFE-08, PRIV-05)
- [ ] **M-48 · Licencias de los modelos sin inventariar.** El repo afirma Apache
      2.0 en PDFs de mayo y sobre otro modelo (26B, no el 12B vigente); los pesos
      son cuantizaciones de terceros sin hash fijado. Verificar con la licencia
      oficial de cada checkpoint. **S** ⚖ [V] (AI-40, AI-42, LEGAL-08)
- [ ] **M-49 · Nombres y tags de modelos inconsistentes.** El backend espera
      `gemma4` y `qwen`, pero el repo no trae el Modelfile que crea esos alias, y
      `LOCAL-DEV.md` manda a bajar otros modelos. Ollama sin versión fijada.
      **S** [V] (AI-43, AI-44)

### 1.8 Seguridad web

- [ ] **M-50 · Endurecer la seguridad de web, admin y landing.** **S**
      Detalle reservado: se gestiona por el canal privado de `SECURITY.md`.
- [ ] **M-51 · Hallazgo de seguridad web reservado.** **S**
      Detalle reservado: se gestiona por el canal privado de `SECURITY.md`.
- [ ] **M-52 · Sentry puede enviar contenido del usuario.** El scrubbing es
      incompleto. Bloquea solo si se activa el DSN. **S** [V+] (PRIV-13)

### 1.9 App web

- [ ] **M-53 · Sin `/login`: el usuario que vuelve aterriza en "Crear cuenta".**
      **S** [V] (WEB-08)
- [ ] **M-54 · Historial de chat guardado solo en el dispositivo.** Sin renombrar, borrar ni
      buscar conversaciones; crece sin tope y se pierde al cambiar de dispositivo.
      **M** [V] (UX-01, UX-02, CON-06)
- [ ] **M-55 · Acciones del agente opacas.** Los chips no dicen qué se creó, no
      enlazan y no permiten deshacer; los recordatorios caen en "Acción ejecutada".
      **M** [V] (UX-05)
- [ ] **M-56 · Tareas sin alta, edición ni borrado.** Solo las crea el agente; ni
      la UI ni el backend tienen `POST`/`DELETE`. **M** [V] (UX-10)
- [ ] **M-57 · La URL de la API tiene un valor por defecto de desarrollo** en web y
      en mobile: un build mal configurado compila igual y apunta a un destino
      equivocado.
      **S** [V+] (WEB-41, MOB-01)
- [ ] **M-58 · Sin soporte, contacto ni ayuda**, aunque el paywall vende "Soporte
      prioritario". **S** [V] (WEB-29)

### 1.10 Mobile y tiendas

Además de M-01, M-02, M-07, M-21, M-41 y M-57, que también bloquean la revisión:

- [ ] **M-59 · Botones "Continuar con Google / Apple" que responden
      "próximamente".** Las tiendas suelen rechazar funciones placeholder.
      `apps/mobile/src/features/auth/WelcomeScreen.tsx:93-99`. **S** ocultar [V] (STORE-04)
- [ ] **M-60 · Hallazgo de sesión en móvil reservado.** **S**
      Detalle reservado: se gestiona por el canal privado de `SECURITY.md`.
- [ ] **M-61 · Sin inventario de datos para las etiquetas de privacidad** de App
      Store y Data Safety de Play. **S** [V] (STORE-07)
- [ ] **M-62 · Sin material de ficha ni cuenta demo para el revisor.** **M** [V]
      (STORE-11)
- [ ] **M-63 · Cuentas de desarrollador y titularidad.** El proyecto de Expo está
      en una cuenta personal; faltan Apple Developer y Play Console. **M**
      (calendario) [V] (MOB-20)

### 1.11 Admin

- [ ] **M-64 · No se puede buscar ni ver un usuario.** El panel es agregado y de
      solo lectura: no se puede responder "no puedo entrar". **M** [V] (ADM-01)
- [ ] **M-65 · No hay acciones de soporte sobre cuentas** ni circuito para
      pedidos de derechos. **M–L** [V] (ADM-02, ADM-03)
- [ ] **M-66 · Endurecer el acceso al admin.** **M**
      Detalle reservado: se gestiona por el canal privado de `SECURITY.md`.
- [ ] **M-67 · Sin vista de suscripciones ni pagos.** Depende de M-23. **L** [V] (ADM-04)

### 1.12 Landing y adquisición

- [ ] **M-68 · No hay captura de interesados.** Todos los CTA llevan a
      `#descargar`, cuyo único link es `mailto:hola@ynara.app`.
      `apps/landing/src/content/ynara.ts:193`. **S–M** [V+] (GTM-01)
- [ ] **M-69 · No hay enlace a la app web ni a las tiendas.** **S** [V] (GTM-02)
- [ ] **M-70 · Dominio y buzón sin configurar en el repo.** Sin deploy de la
      landing ni verificación de `ynara.app`. **S** [I] (GTM-03)
- [ ] **M-71 · Sin analítica ni consentimiento**: no se puede medir el embudo.
      **S** [V] (GTM-08)
- [ ] **M-72 · Fecha de lanzamiento vencida en el copy.** "MVP en el segundo
      semestre de 2026" y hoy es noviembre sin build de tienda. **S** [V] (GTM-05h)

---

## 2. Importantes (no bloquean lanzar, sí sostener)

### Seguridad y privacidad

- [ ] Endurecer el armado del contexto del modelo. **M** (SEC-18, AI-16)
      Detalle reservado: se gestiona por el canal privado de `SECURITY.md`.
- [ ] Completar los registros de auditoría. **S** (SEC-10, ADM-05, AI-12)
- [ ] Ampliar el audit y hacerlo visible para el usuario; la pausa de memoria no
      existe. **M** (PRIV-08, PRIV-09, MEMQ-08)
- [ ] Sin política de retención para turnos, sesiones, eventos, tareas y
      recordatorios. **M** (PRIV-12)
- [ ] Revisar qué datos del usuario terminan en los logs de acceso. **S** (PRIV-14, MOB-18)
- [ ] Revisar el almacenamiento local del historial en los clientes. **M**
      (MOB-13, PRIV-06)
- [ ] Endurecer el CI y la cadena de suministro (secretos, permisos y
      dependencias). **S** (SEC-17, SEC-22, SEC-23)
- [ ] Endurecer el build de la imagen de producción. **S** (SEC-24, SEC-25, OPS-02)
- [ ] Endurecer el registro y las credenciales. **S** (SEC-04, SEC-05)
      Detalle reservado: se gestiona por el canal privado de `SECURITY.md`.
- [ ] Revisar el contenido publicado del build web. **S** (SEC-16)
      Detalle reservado: se gestiona por el canal privado de `SECURITY.md`.
- [ ] Sin plan de notificación de brechas ni modelo de amenazas. **M** (SEC-28, SEC-29)

### Operación y confiabilidad

- [ ] Turnos sin idempotencia: un reintento duplica eventos y tareas. **M**
      (REL-03, UX-09, AI-21)
- [ ] SSE sin keep-alive: un proxy puede cortar turnos largos. **S** (REL-04)
- [ ] Sin endpoint de turnos por sesión para recuperar un stream cortado. **M** (REL-05)
- [ ] La consolidación compite con el chat por la GPU sin prioridad; los reintentos
      amplifican la carga. **M** (SCALE-04, SCALE-05)
- [ ] Consolidación at-most-once y con límite igual al presupuesto del modelo: se
      pierde sin rastro. **M** (REL-09, MEMQ-11, BE-05)
- [ ] Dispatch de recordatorios sin `SKIP LOCKED`: posibles envíos duplicados. **S** (REL-14)
- [ ] `/health/ready` no chequea modelo, embedder ni workers. **S** (OPS-15)
- [ ] Mejorar los logs del backend. **S** (OPS-13)
- [ ] Sin staging, sin versión mínima de cliente, sin feature flags, deploy que
      corta chats en vuelo. **M** (OPS-18, OPS-20, OPS-21)
- [ ] Pool y pooler de Supabase sin definir; Vercel Hobby no admite uso comercial.
      **S** (OPS-11, OPS-22)
- [ ] Recall del índice vectorial sin medir a 1.000 usuarios. **M** (SCALE-09, BE-02)
- [ ] Sin tests de carga ni e2e con modelo real. **M** (REL-20)

### IA y memoria

- [ ] Reranker en passthrough; sin umbral de relevancia; `importance` sin uso.
      **M** (MEMQ-04, MEMQ-05)
- [ ] Sin resumen de conversaciones largas; presupuesto de tokens que no cuenta
      los schemas de tools. **M** (AI-24, AI-25)
- [ ] El agente crea pero no corrige: sin tools para editar o cancelar. **M** (AI-15)
- [ ] El historial de Qwen no conserva lo que hizo en turnos previos. **M** (AI-20)
- [ ] JSON de tool mal formado degrada el turno entero; con `max_iterations` se ve
      un error pero las escrituras se guardan. **S** (AI-17, AI-18)
- [ ] Fechas sin zona horaria y "a las 7" ambiguo. **S** (AI-19)
- [ ] El modo lo elige el usuario; no hay clasificador ni cambio de modo, y los
      modos de Gemma pueden confirmar un "agendado" que no ocurrió. **M** (AI-27, AI-28)
- [ ] Vida no lee hechos del usuario; Productividad no lee preferencias. **S** (MEMQ-12)
- [ ] Sin límites para dominios regulados (medicación, legal, finanzas), sin
      filtro de salida, sin reglas contra la dependencia emocional. **M**
      (SAFE-06, SAFE-07, SAFE-10)
- [ ] Revisar el contenido del stream del modelo hacia el cliente. **S** (SAFE-09)
- [ ] Fine-tuning: nada en el repo; el plan asume otro modelo. **L** (AI-08, AI-10)

### Producto web

- [ ] Revisar el control de acceso de las rutas web; sin `global-error.tsx`. **M** (WEB-06, WEB-13)
- [ ] Markdown sin tablas, resaltado ni copiar código; sin copiar, regenerar ni
      editar mensajes. **M** (UX-03, UX-06)
- [ ] Agenda: recurrencia sin renderizar, formulario incompleto, corte en 100
      eventos, huso horario no editable, sin calendarios externos. **M–L**
      (UX-12, UX-13, UX-14, UX-15, UX-17)
- [ ] Check-in que descarta lo cargado; banner offline que promete lo que no hay.
      **S–M** (UX-18, UX-19)
- [ ] Sin favicon, íconos, manifest, Open Graph ni `robots`. **M** (WEB-18, WEB-19)
- [ ] Cobertura de tests baja en agenda, auth y chat; e2e sin flujos de negocio.
      **M–L** (WEB-35 a WEB-38)

### Mobile, admin y landing

- [ ] Ajustes de accesibilidad que no hacen nada; accesibilidad parcial. **M**
      (MOB-04, MOB-15)
- [ ] Sin OTA, sin deep links, sin manejo de offline, sin CI ni tests de
      componentes. **M** (MOB-07, MOB-09, MOB-10, MOB-T1 a T4)
- [ ] `app.json` sin privacy manifest ni clave de cifrado; íconos a rehacer;
      permiso de notificaciones declarado sin uso. **S** (STORE-06, STORE-10, STORE-12)
- [ ] Perfil completo en SecureStore; rendimiento del fondo animado sin medir.
      **S–M** (MOB-12, MOB-16)
- [ ] Admin sin estado de colas ni workers, sin costos ni latencias; gestión de
      admins pendiente. **M–L** (ADM-06, ADM-07, ADM-09, ADM-10)
- [ ] Landing sin FAQ, prueba social ni equipo; precio como rango; sin press kit.
      **M** (GTM-06, GTM-07, GTM-13)

---

## 3. Mejoras (después del lanzamiento)

- [ ] Adjuntos y PDF para Estudio, voz, búsqueda web, integraciones, citar la
      memoria usada, multi-idioma. (AI-32 a AI-38, UX-04)
- [ ] 2FA para usuarios, login social, passkeys, bloqueo biométrico en mobile.
      (SEC-08, WEB-09, MOB-11)
- [ ] i18n, sistema único de toasts, carga diferida, onboarding educativo.
      (WEB-16, WEB-20, WEB-25, WEB-30)
- [ ] Decay en memoria semántica, catálogo de keys procedurales. (MEMQ-09)
- [ ] Limpieza de código y datos de desarrollo. (SEC-09, WEB-39, WEB-40)

---

## 4. Lo que ya está a nivel producción

No tocar sin motivo:

- **Auth del backend:** bcrypt, login en tiempo constante, refresh de un solo uso
  con detección de reutilización, rechazo de secretos débiles en producción.
- **Aislamiento por usuario:** todos los stores filtran por el `user_id` del JWT;
  recurso ajeno da 404 sin pista. Sin IDOR en ningún router.
- **Cifrado:** cifrado por usuario (AES-256-GCM con HKDF) de la memoria.
- **Admin sin acceso a contenido:** agregados, sin texto ni IDs de usuario.
- **Cliente del modelo:** reintentos, circuit breaker, degradación sin propagar
  errores; rollback de escrituras en turnos degradados.
- **Tool loop defensivo:** topes de iteraciones, llamadas y tamaño; errores
  estructurados; `user_id` nunca como argumento.
- **Chat en los clientes:** streaming con cancelación, estado "IA pausada"
  honesto, markdown sanitizado, código compartido web/mobile.
- **Controles de memoria ya existentes:** ver, editar, borrar, wipe con preview y
  palabra de confirmación, retención configurable.
- **Higiene:** TypeScript estricto, contratos Zod compartidos, mocks bloqueados
  en producción, historial de git sin secretos, contenedor no-root, CI de backend
  con cobertura mínima del 85 %.

---

## 5. Hoja de ruta sugerida

Las fases son secuenciales; dentro de cada una, el trabajo se puede repartir.

### Fase 0 — Decisiones (días, sin código)

- [ ] ¿Lanzamiento gratuito primero o con cobro desde el día uno? Define si M-23 a
      M-26 y M-17 entran en el alcance.
- [ ] ¿El modo Bienestar entra en la v1? Sacarlo elimina M-09, M-18 a M-20 y M-47
      de la ruta crítica.
- [ ] ¿Web primero o web y mobile juntos? Mobile suma M-59 a M-63 y las tiendas.
- [ ] Titular legal (persona o sociedad) y abogado para M-07 a M-17.
- [ ] Dónde corre el backend (ver arquitectura abajo).

### Fase 1 — Lo que no se puede lanzar sin tener

- [ ] Email transaccional → recuperar contraseña, verificar email, cambiar
      credenciales (M-02 a M-05).
- [ ] Borrado de cuenta y wipe completo (M-01, M-12, M-14).
- [ ] Sesión con refresh y 401 en todos los clientes (M-06; M-60, detalle reservado).
- [ ] Documentos legales, aceptación con versión, consentimiento, edad (M-07 a M-11).
- [ ] Guardrail de crisis en código con recursos verificados (M-18 a M-20).
- [ ] Correcciones cortas de alto impacto: M-32, M-39, M-40, M-52, M-57, y
      M-13, M-31, M-50 y M-51 (detalle reservado).
- [ ] Reescribir el copy público contra lo que existe (M-16, M-72).

### Fase 2 — Que funcione con usuarios reales

- [ ] Backend fuera de la PC; la PC solo para inferencia (M-27, M-28).
- [ ] Capacidad del chat bajo carga (M-36 a M-38, detalle reservado).
- [ ] Backups cifrados con restore probado y custodia de la master key (M-29, M-30).
- [ ] Observabilidad en backend y clientes (M-33, M-35).
- [ ] Recordatorios que lleguen: push y email (M-41).
- [ ] Sistema de evaluación con modelo real, y sobre él arreglar la memoria
      (M-42 a M-47).
- [ ] Admin operable (M-64, M-65; M-66, detalle reservado).

### Fase 3 — Cobrar

- [ ] Planes especificados, cuotas por plan, proveedor de pago, suscripción en el
      modelo, UI de facturación y baja (M-23 a M-26, M-17, M-67).

### Fase 4 — Tiendas y adquisición

- [ ] Requisitos de tienda y ficha (M-59, M-61 a M-63).
- [ ] Landing con captura, enlaces reales y medición (M-68 a M-71).

### Arquitectura mínima para 100 usuarios pagos [I]

1. Backend y servicios de soporte en una VPS chica con TLS y dominio propio,
   independientes de la PC.
2. La PC queda solo para inferencia, alcanzada por red privada, con Ollama
   configurado y versionado.
3. Gestión de capacidad con respuesta honesta cuando la GPU está ocupada.
4. Supabase con recuperación a un punto en el tiempo y pooler de transacciones.

**Costo estimado [I]:** entre USD 90 y 235 por mes, contra ingresos de USD 400 a
600 con 100 usuarios a USD 4–6. A 1.000 usuarios hace falta una GPU de 24 GB o
más con vLLM (USD 500 a 900 por mes en la nube).

---

## 6. Experimentos que solo se resuelven corriendo los modelos

- [ ] ¿Deriva en crisis sin inventar números? 60 a 100 mensajes × 5 modos × 3
      semillas, con rúbrica.
- [ ] Exactitud de tool-calling de Qwen: unas 100 intenciones rioplatenses con
      fechas relativas.
- [ ] Calidad de la extracción de memoria: 200 turnos etiquetados.
- [ ] Recall del retrieval con bge-m3 real, con y sin reranker.
- [ ] Truncado de contexto en conversaciones largas.
- [ ] Confirmaciones falsas en modos sin tools: 30 pedidos de "agendame…".
- [ ] Latencia p50/p95 y concurrencia con 1, 2 y 4 usuarios simultáneos.

---

## 7. Lo que esta auditoría no pudo verificar

- **Ejecución:** no se corrió `pytest`, `ruff`, los modelos ni la app contra el
  backend real. Falta `uv` en esta máquina (CI-11).
- **Capacidad:** todos los números de usuarios y latencia son estimaciones con
  supuestos, no mediciones.
- **Servicios externos:** plan, región y pooler de Supabase; configuración de
  Vercel y de la exposición de red del backend; cuentas de Apple, Google y EAS;
  registro del dominio y del buzón.
- **GitHub:** protección de ramas, secret scanning, reporte privado de
  vulnerabilidades.
- **Dependencias:** CVEs de npm y pip (requieren red).
- **Legal:** todo lo marcado ⚖, las referencias a normativa argentina y las
  políticas de las tiendas salen del conocimiento general de la auditoría; hay
  que contrastarlas con un abogado y con las guías oficiales vigentes.
- **Licencias:** los textos vigentes de Gemma, Qwen y bge-m3 para uso comercial.
- **Recursos de crisis:** ningún número de línea de ayuda fue verificado; deben
  confirmarse con fuente oficial antes de usarse.
- **IDs sin descripción en la auditoría de junio:** CORE-001/002/003, AR-05,
  ADMIN-001, SVC-06.
