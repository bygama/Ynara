# Sistema de Diseño de Ynara

Este archivo es la **fuente de verdad del sistema visual de Ynara**. El código se
actualiza para matchear este doc, no al revés — los tokens reales viven en
[`apps/web/src/app/globals.css`](./apps/web/src/app/globals.css) y este doc es la
especificación legible que ese CSS debe implementar. Si hay divergencia, se
corrige el código.

> **Aprobación**: los cambios a este archivo y al sistema visual se aprueban por
> los **CODEOWNERS** de `DESIGN.md` (`.github/CODEOWNERS`: @MateoGs013, @bygama,
> @querques20). Todo cambio entra por **PR** (rama → commits atómicos →
> `ynara-doctor` exit 0 → review → rebase merge), nunca por push directo a `main`.

> **Estado — v5 (2026-11), la dirección de la demo.** v5 adopta como sistema la
> dirección del **prototipo navegable** que vive en
> [`docs/prototypes/demo/`](./docs/prototypes/demo/): tres tamaños de texto, dos
> pesos, escala de espacios 4·8·16·24·40·64, una columna de 560 px, estructura
> de aplicación (no de sitio) y reglas de movimiento por transiciones. Las
> reglas operativas para diseñar con este sistema están en la skill
> [`ynara-interfaz`](./.claude/skills/ynara-interfaz/SKILL.md); si esa skill y
> este doc divergen, **gana este doc**. Los deltas exactos respecto a v4 están
> en el **§14**.

---

## 1. Esencia de marca

> **"Tecnología que se siente como pensar."** Ynara no es una IA ruidosa: es una
> **compañía cognitiva diaria**. Su universo visual es **claro, editorial y
> sereno** — «calma con alma», lo opuesto al cliché tecnológico (negro, neón,
> tablero lleno).

Tres ideas gobiernan el sistema:

| Idea | Forma | Significado |
|---|---|---|
| **Memoria** | Nodos y puntos de luz | Cada idea/recuerdo capturado. La unidad mínima. |
| **Conexión** | Vínculos e hilos | El tejido que une un pensamiento con el siguiente. |
| **Presencia** | El diamante | Donde está Ynara. El acento que ordena. |

Los **elementos propios** son los únicos adornos permitidos: el diamante, el
nodo, el vínculo y la bifurcación (la Y). El patrón de fondo es la «red de
memoria»: nodos y vínculos, ralos.

Lo que la UI **no es**: infantil, recargada, ruidosa, "Material Design
genérico", ni el look "AI/SaaS template" (gradiente de relleno, glassmorphism
porque sí, emojis como íconos, todo centrado).

Para el ADN conceptual ver [`IDENTITY.md`](./IDENTITY.md); para la voz,
[`docs/product/TONE-OF-VOICE.md`](./docs/product/TONE-OF-VOICE.md). La voz en
la interfaz: rioplatense, voseo, frases cortas, sin tics de chatbot, sin
emojis, sin meta-relato. «Listo, agendado mañana 19 hs.» Los vacíos invitan a
actuar.

---

## 2. Paleta

Identidad cromática: familia **azul → violeta** sobre dos superficies de marca,
**marfil** (claro) y **Noche** (oscuro). Nunca blanco puro de fondo, nunca
negro puro. No existen en la marca: ámbar, verde, jade.

### 2.1 Tema dual

El tema por defecto es **marfil**. Noche es co-protagonista, y la elección es
de la persona: claro, Noche o seguir al sistema
(`apps/web/src/stores/theme.ts` soporta `system`). El tema se aplica por clase en `<html>` (`html.theme-dark`).

**Superficies:**

| Rol | Marfil | Noche (`html.theme-dark`) |
|---|---|---|
| Lienzo (body) | `#FAF9F5` | `#242C3F` |
| Superficie (cards, hojas, inputs) | `#FFFFFF` | `#2B3346` |
| Suave (pills, zonas alternas) | `#F3F0EA` | `#313A52` |

**Tinta:**

| Rol | Marfil | Noche |
|---|---|---|
| Títulos | `#1B2233` | `#FFFFFF` |
| Texto | `#242C3F` | `#F3F0EA` |
| Secundario | `rgb(36 44 63 / .70)` | `rgb(243 240 234 / .72)` |
| Terciario (solo decoración/íconos `aria-hidden`) | `rgb(36 44 63 / .45)` | `rgb(243 240 234 / .46)` |
| Desactivado | `rgb(36 44 63 / .18)` | `rgb(243 240 234 / .18)` |
| Filete | `rgb(36 44 63 / .10)` | `rgb(243 240 234 / .10)` |
| Filete marcado | `rgb(36 44 63 / .22)` | `rgb(243 240 234 / .20)` |

Jerarquía: de-enfatizar **bajando contraste** (secundario), no agrisando con un
gris ajeno a la marca. Todo texto real usa como mínimo el nivel secundario; el
terciario no alcanza AA y queda solo para decoración.

### 2.2 Un solo acento

**Azul `#2F5AA6`** (hover `#26498A`, press `#1F3C75`): acciones, foco y
enlaces. Plano, sin degradado. En Noche, el azul como **texto** se aclara a
`#A9B9E6` para que lea.

### 2.3 Colores de modo

Cada modo tiene **un** color. Los modos **solo tiñen el diamante y los nodos**
— nunca botones, fondos enteros ni cabeceras:

| Modo | Marfil | Noche (aclarado) |
|---|---|---|
| Productividad | `#2F5AA6` | `#7D9FD6` |
| Estudio | `#434A82` | `#8B9AD0` |
| Bienestar | `#8165A3` | `#B29ACB` |
| Vida | `#5C6FB3` | `#95A3D6` |
| Memoria | `#565F81` | `#A3ABC7` |

Los colores con significado por pantalla son **tres**: tinta + azul + el color
del modo activo. Nada más pide atención.

### 2.4 Stops oficiales de la guía 2026

Fuente única para el logo y el fondo vivo (no para fills de UI):
Azul `#2F5AA6` · Índigo `#434A82` · Azul violáceo `#5C6FB3` · Violeta
`#8165A3` · Celeste `#6E92CC` · Lavanda `#8B9AD0` · Lavanda oscuro `#565F81` ·
Noche `#242C3F` · Marfil `#F3F0EA`.

### 2.5 Disciplina de gradiente

La guía 2026 es textual: *"Sin gradientes decorativos: el color trabaja por
jerarquía, no por adorno."* Los degradados viven **solo** en: (1) el fondo vivo
(la red de memoria ambiental), (2) el logo, (3) el halo del diamante. Todo lo
demás — CTA, fills, bordes, texto, dots, tint de modo — es **color plano**. El
guard `apps/web/src/__tests__/gradient-guard.test.ts` lo hace cumplir.

### 2.6 Estados y utilitarios

Error `#C0392B` (Noche: `#FF8585`) · Overlay `rgb(36 44 63 / .40)` (Noche:
`rgb(15 19 30 / .55)`).

### 2.7 Contraste

WCAG 2.1 **AA** mínimo (AAA en títulos y CTA); APCA como guía en Noche. El
texto va sobre la superficie plana, no sobre el fondo vivo: el contraste se
mide contra el plano. Cualquier color que lleve texto encima se verifica
contra ese texto, no solo contra el fondo.

---

## 3. Tipografía

Dos familias, y nada más:

| Familia | Para | Peso |
|---|---|---|
| **Space Grotesk** | Títulos y marca | 600 |
| **DM Sans** | Todo lo demás | 400 y 600 |

**Tres tamaños por pantalla** (las hojas y la voz suman uno):

| Token | Valor | Uso |
|---|---|---|
| `--fs-title` | `clamp(2rem, 1.6rem + 2vw, 2.5rem)` | El título de la pantalla |
| `--fs-body` | `1rem` | Texto |
| `--fs-small` | `0.875rem` | Metadatos, un dato por fila |
| `--fs-sub` | `1.25rem` | Solo títulos de hoja y la frase de la voz |

Reglas duras:

- **Jamás Space Grotesk en texto corrido.**
- **Dos pesos**: 400 y 600. Sin medium, sin bold extra, sin cambiar el peso en
  hover (salta el layout).
- La única letra por debajo de `--fs-small` es la de las pestañas de la barra
  inferior (11 px).
- Sin tamaños arbitrarios (`text-[N]`): todo sale de la escala.
- Títulos con `text-wrap: balance`, texto con `pretty`, cifras que cambian con
  `tabular-nums`. Comillas curvas, `…` real y espacio duro entre número y
  unidad.
- Nada de rótulos en mayúsculas espaciadas, ni rótulo chico arriba del título
  que repita la navegación (solo va si aporta un dato, como la fecha).
- El tamaño base lo elige la persona: `html.text-sm` 15px · `text-md` 16px ·
  `text-lg` 18px.

---

## 4. Espacio y medida

**Escala de espacios: 4 · 8 · 16 · 24 · 40 · 64. Nada intermedio.**

| Token | Valor |
|---|---|
| `--s1`…`--s6` | `4 · 8 · 16 · 24 · 40 · 64 px` |
| `--col` | **560 px** — una sola columna de contenido, 45–70 caracteres por línea |
| `--r` | `12px` (radio único de superficie) |
| `--r-pill` | `999px` |
| `--top-h` | `56px` — la cabecera |
| `--dock-h` | `52px` — la barra inferior (móvil) |

- Spacing no uniforme: más aire entre grupos distintos que entre elementos del
  mismo grupo. El espacio sobre un título es mayor que el que tiene debajo.
- Radios anidados concéntricos: externo = interno + relleno.
- **Elevación declarada una sola vez**: sombra en capas (`--shadow`) **o**
  borde, nunca los dos («tarjeta fantasma»). En Noche la separación viene más
  de la diferencia de superficie y el filete que de la sombra.
- Los **filetes** van solo entre filas de una misma lista; los bloques se
  separan con aire.

---

## 5. Densidad: límites que se pueden contar

Antes de dar por terminada una pantalla, contar. Si se pasa, sacar, no achicar.

| Qué | Límite |
|---|---|
| Columna de contenido | una sola, 560 px máx. |
| Tamaños de texto por pantalla | 3 (+ `--fs-sub` en hojas y voz) |
| Pesos | 2 (400 y 600) |
| Colores con significado | tinta + azul + color del modo |
| Acción principal | 1 por pantalla |
| Bloques por pantalla sin scroll | 5 como mucho (el título cuenta) |
| Niveles de revelado | 2: lo frecuente a la vista, el resto en una hoja |
| Fila de lista | marca + título + un dato. Lo demás va en la hoja |
| Zona de toque | 44 px en móvil, 24 px mínimo en escritorio, sin huecos muertos |

Prohibido: índices 01/02, relojes y contadores decorativos, dos columnas de
texto, tarjetas dentro de tarjetas, texto de ayuda permanente (va en el vacío o
en el primer uso), un mismo dato dos veces en pantalla.

---

## 6. Movimiento

### 6.1 Tokens

| Token | Valor | Uso |
|---|---|---|
| `--t-fast` | `150ms` | Hover y color |
| `--t` | `200ms` | Interacción |
| `--t-screen` | `300ms` | Entrada de pantalla (entre 250 y 350 según complejidad) |
| (hojas) | `500ms` | Hojas, con `--ease-sheet` |
| `--ease` | `cubic-bezier(.22, 1, .36, 1)` | Curva de salida, default |
| `--ease-sheet` | `cubic-bezier(.32, .72, 0, 1)` | Hojas |

Nada de utilidad pasa de 500 ms. Nunca `ease-in`. Resortes sin rebote, salvo el
final de un gesto.

### 6.2 Reglas

- **Solo `transform` y `opacity`.** Nunca `transition: all`, ni ancho, alto o
  posición. El color puede transicionar en 150 ms.
- **Transiciones, no keyframes**, para que todo sea interrumpible.
- **Respuesta en menos de 100 ms**: estado apretado al bajar el dedo
  (`scale(.97)`), cambio optimista, deshacer en el aviso.
- **Una sola cosa se mueve por interacción.**
- **Cambiar de pantalla**: la cabecera y el campo inferior no se reconstruyen
  ni se funden; solo entra el contenido (fundido + 8 px, 250 ms, escalonado de
  40 ms, 6 elementos como mucho). Escalonado en una sola lista por pantalla.
- **Entradas** desde escala ≥ .96, con origen en lo que las disparó. Nunca
  desde 0. Las salidas son más rápidas que las entradas.
- **No se anima**: lo que se dispara con teclado, la paleta de comandos,
  agregar o tachar en una lista (más allá del propio tilde), el cambio de
  tema, lo que se usa decenas de veces por día.
- **Nada de saltos de layout**: reservar el alto de lo que va a aparecer.
- Hover solo dentro de `@media (hover: hover) and (pointer: fine)`.
- `prefers-reduced-motion` (y el override del store) cambia traslación y
  escala por un fundido de 200 ms; el fondo vivo dibuja un solo frame.
- **El deleite se gasta en momentos raros**: la primera vez, guardar un
  recuerdo, abrir la voz. El camino diario es silencioso.

---

## 7. Estructura de aplicación (no de sitio)

Ynara es una aplicación integral: escritorio y móvil se ven bien los dos, con
diferencias claras. **El corte móvil/escritorio es un único umbral: 900 px**,
definido como token y usado por navegación, hojas y Agenda (reemplaza los
640/768/1024 que convivían).

| | Móvil (< 900 px) | Escritorio (≥ 900 px) |
|---|---|---|
| Navegación | Barra inferior flotante: **Hoy, Chat, ◆, Agenda, Memoria** | Barra lateral de 232 px con ícono y nombre, mismo orden |
| Diamante | Al centro de la barra: al tocarlo, la barra se vuelve campo de escritura | Botón de voz dentro del campo, siempre visible |
| Campo de escritura | Aparece al pedirlo | Fijo abajo, centrado en el contenido |
| **Vos** (cuenta) | Avatar en la cabecera | Fila con nombre al pie de la barra lateral |
| Buscar | Se busca escribiéndole a Ynara | Fila «Buscar» con atajo en la barra lateral |
| Modo | Bandeja que sube desde abajo | Menú que nace del selector, dentro del campo |
| Hojas | Suben desde abajo | Entran por la derecha |

- El orden de las pestañas es **Hoy · Chat · Agenda · Memoria** (+ **Vos**
  como destino de cuenta), definido una sola vez y compartido entre web y
  móvil.
- La sección personal se llama **«Vos»** — coherente con el voseo de la marca.
- **Nunca** una fila de palabras arriba como navegación: eso es un sitio web.
- **El modo lo elige la persona**, donde se escribe (como el selector de
  modelo de los asistentes actuales), no en la cabecera. Al cambiarlo, una
  onda del color nuevo; sin meta-relato.
- El campo de escritura es global: «Decile algo a Ynara…» disponible en todas
  las pantallas; al enviar abre un chat con ese texto.
- Hojas con `overscroll-behavior: contain`; botones con
  `touch-action: manipulation`. El navegador también se tematiza: selección,
  cursor de texto, barras de scroll y `theme-color` por tema.

---

## 8. Pantallas

- **Hoy — el día es finito.** Saludo, lo próximo, como mucho **cinco**
  pendientes, y cierra con una línea. No es un feed.
- **Chat — editorial, no mensajería.** La respuesta sin burbuja, a medida de
  lectura; lo de la persona en un contenedor liviano. Streaming interrumpible.
  El chat se queda pegado abajo; el botón para bajar aparece solo si la
  persona subió. Región `aria-live="polite"` sobre el mensaje en curso.
- **Agenda — la tira de siete días con el hilo, y el detalle de día.** Toda
  secuencia en el tiempo se dibuja como un vínculo vertical con nodos (el
  hilo). Alta en lenguaje natural («Dentista mañana 19 hs»). No hay vistas de
  Semana ni Mes en grilla, ni Lista.
- **Memoria — la red al frente.** El fondo de nodos pasa a ser contenido: cada
  nodo es un recuerdo real. Tiene vista de lista.
- **Vos — cuenta y preferencias.** Tema (claro / Noche / sistema), tamaño de
  texto, movimiento, voz, pausa de memoria, exportar y borrar todo.

---

## 9. El diamante y la red (la firma)

Un solo elemento firma, usado siempre igual: **el diamante**. Está donde está
Ynara — marca el «ahora» en el hilo del día, es el botón de voz, late cuando
piensa y, **junto con los nodos, es lo único que lleva el color del modo**.
Todo lo demás calla.

Segundo plano, casi invisible: la **red de memoria** (nodos y vínculos) como
fondo que reacciona apenas al cursor. Reglas del fondo vivo: detrás del
contenido (`aria-hidden`, sin eventos), baja opacidad, pausa cuando la pestaña
no está visible, un solo frame bajo `prefers-reduced-motion`, GPU-friendly.

**Autoría visible**: lo que la persona dijo va en tinta plena y nodo lleno; lo
que Ynara infirió, en tinta suave y nodo hueco, hasta que la persona lo
confirma.

---

## 10. Iconografía

Set propio con el ADN de la marca: **trazo uniforme de 1.5 px** junto a texto
regular, con el diamante como acento. Contorno por defecto, relleno en la
sección activa. Fallback Lucide solo para utilitarios que el set no cubra,
mismo grosor. ❌ Nunca emojis ni flechas como íconos; sin flechas pegadas a los
enlaces.

---

## 11. Logo

### 11.1 Uso correcto

- **El diamante del isotipo, en la versión a color, es violeta** (decisión
  LOGO-01). El juego único de logo (isotipo, horizontal y vertical; color, una
  tinta y negativo) se construye sobre esa base.
- **Variante por fondo**: a color sobre claro/neutro; **mono-light**
  (`#F3F0EA`) sobre Noche o fondos de marca; **mono-dark** (`#242C3F`) sobre
  claro cuando se quiere mono; avatar (cuadrado redondeado) solo como
  app-icon, nunca inline en un lockup.
- **Lockup símbolo + wordmark** con baseline compartida (los pies de la Y caen
  sobre la baseline del texto), nunca centrado a mano.
- **Clear-space**: aire mínimo alrededor ≥ la altura del símbolo.
- **Contraste**: jamás el símbolo a color sobre Noche — ahí va mono-light.

---

## 12. Interfaz de un asistente con memoria

- **La memoria se muestra cuando se usa.** Si una respuesta usó recuerdos, lo
  dice en una línea que se abre («Usé 2 recuerdos»), con fecha y acceso a
  corregir u olvidar. Nunca una fila de chips en cada mensaje.
- **Razonamiento y herramientas** plegados por defecto; una tarjeta por
  acción, con su estado y deshacer.
- **La voz tiene cinco estados** y cada uno mueve una sola propiedad: en
  espera, escuchando, pensando, hablando e interrumpida.
- **Guardar algo es un momento raro**: la primera vez de la sesión, el dato
  viaja como diamante hasta Memoria. Después, silencio.
- Siempre diseñados: vacío, cargando, error y deshabilitado. El texto muy
  largo no rompe nada (`min-width: 0`).

---

## 13. Anti-patterns

❌ hex hardcodeado (siempre token) · ❌ Space Grotesk en body · ❌ animar
`width/height/top/left` · ❌ `transition: all` · ❌ `outline: none` sin
reemplazo · ❌ sombra + borde a la vez · ❌ disabled sin explicación · ❌ toast
para error de form (va inline) · ❌ loops decorativos infinitos en la UI.

Vicios que delatan una interfaz genérica (prohibidos): rótulo chico que repite
la navegación · una palabra acentuada en cada titular (el acento queda para el
nombre de la persona y el lema) · índices 01/02/03 · cifra gigante con rótulo ·
grillas de tarjetas idénticas · texto en degradado · vidrio decorativo · borde
lateral de color · monoespaciada como disfraz técnico · puntos de estado que
laten · cursor que parpadea de adorno · el mismo «aparece desde abajo» en
cuatro o más componentes.

---

## 14. Migración v4 → v5 (deltas)

| Área | v4 | v5 (la demo) | Tipo |
|---|---|---|---|
| Fuente del sistema | mockup Vite + React | **prototipo navegable** (`docs/prototypes/demo/`) | reemplaza |
| Escala tipográfica | 6+ tamaños fluidos, 5 pesos | **3 tamaños + `--fs-sub`, 2 pesos (400/600)** | simplifica |
| Espacios | escala base 4 de Tailwind | **4·8·16·24·40·64, nada intermedio** | acota |
| Medida | 60–70ch en chat | **columna única de 560 px en toda la app** | generaliza |
| Breakpoints | `sm:640 md:768 lg:1024` | **un umbral: 900 px** | unifica |
| Radius | 4 tokens (8/12/16/20) | **uno: 12 px (+ pill)** | acota |
| Motion | 6 tokens de duración | **150/200/250–350/500 + 2 curvas; transiciones, no keyframes** | simplifica |
| Tema | marfil + Noche, sin seguir al OS | marfil + Noche + **sistema** (elección de la persona) | corrige |
| Colores de modo | base + fill por modo, tints en chips | **un color por modo, solo en diamante y nodos** | acota |
| Navegación | sin orden definido | **Hoy · Chat · Agenda · Memoria + Vos, orden compartido** | define |
| Sección personal | «Tú» | **«Vos»** | renombra |
| Agenda | día/semana/mes/lista | **tira de 7 días + detalle de día** | reemplaza |
| Logo | stops del SVG, lockup | igual + **diamante violeta en la versión a color** (LOGO-01) | define |
| Densidad | — | **tabla de límites contables (§5)** | agrega |
| Fondo vivo | `LivingField` (§2 de v4) | se conserva como «red de memoria», con las mismas reglas de performance | conserva |
| Disciplina de gradiente | canvas + logo + orbe | igual (fondo vivo + logo + halo del diamante) | conserva |
| a11y | AA, reduced-motion, focus visible | igual + checklist de pulido (§6.2, §5) | conserva |

Lo que v5 **no toca**: el backend, la memoria, la config de modos
(`ynara.config.json`), ni ninguna regla del `AGENTS.md`. La implementación en
`apps/web` / `apps/mobile` se hace por PRs propios (tokens → componentes →
pantallas), cada uno contra este doc.

---

## 15. Referencias

- [`docs/prototypes/demo/`](./docs/prototypes/demo/) — el prototipo navegable,
  fuente de esta versión (`css/tokens.css` es el espejo de los §2–§6).
- [`.claude/skills/ynara-interfaz/SKILL.md`](./.claude/skills/ynara-interfaz/SKILL.md)
  — reglas operativas para diseñar con este sistema (este doc gana).
- [`apps/web/src/app/globals.css`](./apps/web/src/app/globals.css) — tokens
  reales de la app web.
- [`docs/brand/Ynara-Universo-de-Marca.html`](./docs/brand/Ynara-Universo-de-Marca.html)
  — guía de identidad visual 2026 (paleta, sistema gráfico, logo).
- [`IDENTITY.md`](./IDENTITY.md) — ADN de marca.
- [`docs/product/TONE-OF-VOICE.md`](./docs/product/TONE-OF-VOICE.md) — voz por
  modo.
