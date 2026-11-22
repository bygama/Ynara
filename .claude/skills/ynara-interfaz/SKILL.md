---
name: ynara-interfaz
description: Reglas para diseñar o tocar cualquier pantalla de Ynara (demo o app). Usar SIEMPRE antes de escribir UI, CSS, animaciones o textos de interfaz de Ynara, y al revisar una pantalla existente. Triggers - "pantalla", "diseño", "demo", "interfaz", "minimalista", "animación", "micro interacción", "marca".
---

# Ynara · interfaz

Ynara es «tecnología que se siente como pensar»: una compañía cognitiva diaria.
Su mundo visual es **claro, editorial y sereno**, «calma con alma». Lo contrario
del cliché tecnológico (negro, neón, tablero lleno).

Fuentes de verdad (leerlas si hay duda, ganan sobre este archivo):
`DESIGN.md`, `IDENTITY.md`, `docs/product/TONE-OF-VOICE.md`,
`docs/brand/Ynara-Universo-de-Marca.html`, `apps/web/src/app/globals.css`.

## 1. Marca: lo que no se negocia

- **Tema por defecto: marfil.** Lienzo `#FAF9F5`, superficie `#FFFFFF`, suave `#F3F0EA`.
  Tinta Noche `#242C3F` (títulos `#1B2233`). Tema oscuro = Noche `#242C3F`, nunca negro.
- **Un solo acento: Azul `#2F5AA6`** (hover `#26498A`). Acciones, foco y enlaces. Plano, sin degradado.
- **Modos** (solo tiñen el diamante y los nodos, nunca botones ni fondos enteros):
  Productividad `#2F5AA6` · Estudio `#434A82` · Bienestar `#8165A3` · Vida `#5C6FB3` · Memoria `#565F81`.
- **No existen** en la marca: ámbar, verde, jade, negro puro, blanco puro de fondo.
- **Tipografías:** Space Grotesk (títulos, 600) y DM Sans (todo lo demás). Nunca otra familia.
  Nunca Space Grotesk en texto corrido.
- **Elementos propios** (los únicos adornos permitidos):
  **Diamante** = presencia de Ynara · **Nodo** = una idea o recuerdo · **Vínculo** = hilo entre dos nodos ·
  **Bifurcación** = la Y. El patrón de fondo es la «red de memoria»: nodos y vínculos, ralos.
- **Degradados** solo en: fondo vivo, logo, halo del diamante.
- **Voz:** rioplatense, voseo, frases cortas, sin tics de chatbot, sin emojis, sin meta-relato.
  «Listo, agendado mañana 19 hs.» Los vacíos invitan a actuar.

## 2. Densidad: límites que se pueden contar

Antes de dar por terminada una pantalla, contar. Si se pasa, sacar, no achicar.

| Qué | Límite |
|---|---|
| Columna de contenido | una sola, 560 px máx., 45–70 caracteres por línea |
| Tamaños de texto por pantalla | 3 (título, texto, chico). Las hojas y la voz suman uno: `--fs-sub` |
| Pesos | 2 (400 y 600) |
| Colores con significado | tinta + azul + color del modo |
| Acción principal | 1 por pantalla |
| Bloques por pantalla sin scroll | 5 como mucho (título cuenta) |
| Niveles de revelado | 2: lo frecuente a la vista, el resto en una hoja |
| Fila de lista | marca + título + un dato. Lo demás va en la hoja |
| Filetes | solo entre filas de una misma lista; los bloques se separan con aire |
| Espacios | 4 · 8 · 16 · 24 · 40 · 64, nada intermedio |
| Zona de toque | 44 px en móvil, 24 px mínimo en escritorio, sin huecos muertos entre filas |

Prohibido: índices 01/02, rótulos en mayúsculas espaciadas, relojes y contadores
decorativos, dos columnas de texto, tarjetas dentro de tarjetas, texto de ayuda
permanente (va en el vacío o en el primer uso), un mismo dato dos veces en pantalla.

## 3. Interacción

- **Respuesta en menos de 100 ms**: estado apretado al bajar el dedo (`scale(.97)`), cambio optimista, deshacer en el aviso.
- **Duraciones:** 150 ms hover y color · 200 ms interacción · 250–350 ms entrada de pantalla · 500 ms hojas (`cubic-bezier(.32,.72,0,1)`). Nada de utilidad pasa de 500 ms.
- **Curva:** `cubic-bezier(.22,1,.36,1)` (salida). Nunca `ease-in`. Resortes sin rebote, salvo el final de un gesto.
- **El movimiento es solo `transform` y `opacity`.** Nunca `transition: all`, ni ancho, alto o posición. El color puede cambiar con una transición de 150 ms.
- **Transiciones, no keyframes**, para que todo sea interrumpible.
- **No se anima:** lo que se dispara con teclado, la paleta de comandos, agregar o tachar en una lista más allá del propio tilde, el cambio de tema, lo que se usa decenas de veces por día.
- **Una sola cosa se mueve por interacción.**
- **Cambiar de pantalla:** la cabecera y el campo inferior no se reconstruyen ni se funden; solo entra el contenido (fundido + 8 px, 250 ms, escalonado de 40 ms, 6 elementos como mucho).
- **Entradas** desde escala ≥ .96 y con origen en lo que las disparó. Nunca desde 0.
- **Nada de saltos de layout**: no cambiar el peso en hover, reservar el alto de lo que va a aparecer.
- Hover solo dentro de `@media (hover: hover)`. Foco visible con anillo doble. `prefers-reduced-motion` cambia movimiento por un fundido de 200 ms.
- **El deleite se gasta en momentos raros**: la primera vez, guardar un recuerdo, abrir la voz. El camino diario es silencioso.

## 4. Cómo se nota la marca con tan poco

Un solo elemento firma, usado siempre igual: **el diamante**.
Está donde está Ynara: marca el «ahora» en el hilo del día, es el botón de voz,
late cuando piensa y es lo único que lleva el color del modo. Todo lo demás calla.

Segundo plano, casi invisible: la **red de memoria** (nodos y vínculos) como fondo
que reacciona apenas al cursor. En Memoria pasa al frente: cada nodo es un recuerdo.

El **hilo**: toda secuencia en el tiempo (el día, la agenda) se dibuja como un
vínculo vertical con nodos. Es la gramática de la marca aplicada a los datos.

## 5. Antes de entregar

1. Contar contra la tabla de densidad en 390 px y en 1440 px.
2. Sacar una cosa más («mirarse al espejo y quitarse un accesorio»).
3. Recorrer con teclado. Probar tema oscuro y movimiento reducido.
4. Pasar la pantalla por el agente `ynara-critico` (revisión separada de la autoría).

## 6. Pulido fino (pasada final, sí o no)

Sintetizado de las skills públicas de Jakub Krehel (make-interfaces-feel-better),
Emil Kowalski (emil-design-eng), Paul Bakaus (impeccable), Vercel (Web Interface
Guidelines) y Raphael Salaja (userinterface-wiki).

1. ¿La elevación se declara una sola vez? Sombra en capas **o** borde, nunca los dos («tarjeta fantasma»).
2. ¿Los radios anidados son concéntricos (externo = interno + relleno)?
3. ¿Los títulos usan `text-wrap: balance` y el texto `pretty`? ¿Las cifras que cambian, `tabular-nums`?
4. ¿El espacio sobre un título es mayor que el que tiene debajo?
5. ¿Cero `transition: all`? ¿Nada entra desde `scale(0)` (mínimo .95 + opacidad)?
6. ¿Las salidas son más rápidas que las entradas? ¿Lo que aparece nace desde lo que lo disparó?
7. ¿Lo que se usa 100 veces por día no anima, y lo raro sí puede deleitar? (filtro de frecuencia)
8. ¿El hover está detrás de `@media (hover: hover) and (pointer: fine)`?
9. ¿Con movimiento reducido se quitan traslación y escala pero quedan los fundidos?
10. ¿Cada control tiene foco visible y zona de toque de 44 px (o 40 en escritorio), sin solaparse?
11. ¿Están diseñados vacío, cargando, error y deshabilitado? ¿El texto muy largo no rompe nada (`min-width: 0`)?
12. ¿Las hojas usan `overscroll-behavior: contain` y los botones `touch-action: manipulation`?
13. ¿El navegador también está tematizado: selección, cursor de texto, barras de scroll, `theme-color`?
14. ¿Se usa `…` y comillas tipográficas, y espacio duro entre número y unidad?
15. ¿Se revisó alguna animación al 10 % de velocidad?

## 7. Vicios que delatan una interfaz genérica (prohibidos)

- Rótulo chico arriba del título cuando solo repite la navegación. Solo va si aporta un dato (la fecha).
- Una palabra acentuada en cada titular. En Ynara el acento queda para el nombre de la persona y el lema.
- Índices 01/02/03, cifra gigante con rótulo, grillas de tarjetas idénticas, tarjetas dentro de tarjetas.
- Texto en degradado, vidrio decorativo, borde lateral de color, monoespaciada como disfraz técnico, emojis como íconos.
- Puntos de estado que laten, cursor que parpadea de adorno, rebote en acciones de utilidad.
- El mismo «aparece desde abajo» en cuatro o más componentes; escalonado en más de una lista por pantalla.
- Flechas pegadas a los enlaces.

## 8. Interfaz de un asistente con memoria

Aprendido de ChatGPT, Claude, Granola, Family, Gemini y los componentes de IA de Vercel.

- **La memoria se muestra cuando se usa.** Si una respuesta usó recuerdos, lo dice en una línea que se abre («Usé 2 recuerdos»), con fecha y acceso a corregir u olvidar. Nunca una fila de chips en cada mensaje.
- **Autoría visible.** Lo que la persona dijo va en tinta plena y nodo lleno; lo que Ynara infirió, en tinta suave y nodo hueco, hasta que la persona lo confirma.
- **El día es finito.** Hoy muestra como mucho cinco pendientes y cierra con una línea. No es un feed.
- **Razonamiento y herramientas:** plegados por defecto; una tarjeta por acción, con su estado y deshacer.
- **La voz tiene cinco estados** y cada uno mueve una sola propiedad: en espera, escuchando, pensando, hablando e interrumpida.
- **Guardar algo es un momento raro:** la primera vez de la sesión, el dato viaja como diamante hasta Memoria. Después, silencio.
- El chat se queda pegado abajo; el botón para bajar aparece solo si la persona subió.

## 9. Ideas en cola (no implementadas)

Hoy en tres horizontes (ahora, después, más adelante) · voz dentro del hilo del chat con puntos clave ·
confirmación en una bandeja que cambia de alto por paso · hilo de luz en el borde mientras escucha ·
el botón «Recordar» que se transforma en «Recordado» · recorrer el tiempo en la red de memoria.

## 10. Estructura de aplicación (no de sitio)

Ynara es una aplicación integral: escritorio y móvil se ven bien los dos, con diferencias claras.

| | Móvil (< 900 px) | Escritorio (≥ 900 px) |
|---|---|---|
| Navegación | Barra inferior flotante: Hoy, Chat, ◆, Agenda, Memoria | Barra lateral de 232 px con ícono y nombre |
| Diamante | Al centro de la barra: al tocarlo, la barra se vuelve campo de escritura | Botón de voz dentro del campo, siempre visible |
| Campo de escritura | Aparece al pedirlo | Fijo abajo, centrado en el contenido |
| Cuenta | Avatar en la cabecera | Fila con nombre al pie de la barra lateral |
| Buscar | Ctrl K no existe: se busca escribiéndole a Ynara | Fila «Buscar» con atajo en la barra lateral |
| Modo | Bandeja que sube desde abajo | Menú que nace del selector, dentro del campo |
| Hojas | Suben desde abajo | Entran por la derecha |

- Nunca una fila de palabras arriba como navegación: eso es un sitio web.
- El modo se elige donde se escribe (como el selector de modelo de los asistentes actuales), no en la cabecera.
- La única letra por debajo de `--fs-small` permitida es la de las pestañas de la barra inferior (11 px).

## 11. Skills de terceros instaladas en el proyecto

Están en `.claude/skills/` y se usan junto con esta (la marca de Ynara gana si hay conflicto):

- **make-interfaces-feel-better** (Jakub Krehel, MIT): micro-pulido con valores exactos. Usarla en cada pasada final;
  su formato de revisión (tabla Antes / Después / Por qué) es el que se le pide al agente `ynara-critico`.
- **impeccable** (Paul Bakaus, Apache 2.0): guías por tarea en `reference/` (`polish`, `audit`, `critique`, `distill`,
  `animate`, `delight`, `adapt`). Leer `reference/craft-floor.md` antes de tocar interfaz. Ynara es modo **Operate**:
  la ambición se nota en la precisión, no en el espectáculo.
  Su lanzador (`scripts/impeccable`) descarga y ejecuta un binario: el agente no lo corre; lo ejecuta la persona si quiere
  el detector automático. Sin lanzador, se lee `DESIGN.md` del monorepo directamente.

Valores que se adoptaron de ellas: presión `scale(0.96)`; trazo de ícono 1.5 px junto a texto regular; contorno por
defecto y relleno en la sección activa; ícono que aparece con escala .25, opacidad y desenfoque de 4 px; sombra en
tres capas con desplazamiento; nada de rótulo sobre un título (el dato va debajo); el desplazamiento al entrar se
reserva para los momentos con autoría y el resto entra con un fundido.
