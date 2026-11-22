# Ynara — demo navegable

Demo completa de la experiencia de Ynara con **datos de ejemplo** y **voz**. No
usa backend ni modelos: las respuestas siguen reglas fijas y todo corre en el
navegador. Es un proyecto aparte del monorepo, sin dependencias ni build.

## Cómo abrirla

```bash
python serve.py --abrir
```

Abre `http://localhost:3120`. Usá **Chrome o Edge** (Safari también funciona).
Hace falta servirla por `localhost`: abrir `index.html` con doble click no
carga los módulos ni habilita el micrófono.

Para la defensa: las tipografías de la guía (Space Grotesk y DM Sans) se bajan
de Google Fonts. Sin internet la demo funciona igual, con la tipografía del
sistema, pero pierde carácter: conviene tener conexión.

**Hora simulada:** `http://localhost:3120/?hora=10:15` hace que la demo crea que
son las 10:15 de hoy. Sirve para mostrar el día en curso (lo próximo, la cuenta
regresiva, el «ahora» en la órbita) a cualquier hora real.

## La idea: un solo espacio

Toda la app es un único lienzo vivo. Nada de lo que tiene nombre es decoración:

- **El orbe** es Ynara. Tocarlo abre la voz.
- **La órbita del día** es un dial de 24 horas alrededor del orbe: cada evento
  es un punto en su hora, con un arco que marca cuánto dura. El punto ámbar es
  el ahora.
- **La constelación** es tu memoria: cada estrella es un recuerdo real, agrupado
  en tres figuras (hechos, momentos, costumbres). Las líneas punteadas unen
  recuerdos que salieron de la misma conversación.

Navegar no cambia de página: mueve la cámara a otra región del espacio.

## Dos estilos sobre el mismo espacio

En **Tú → Apariencia → Estilo** se elige entre:

- **Minimal** (por defecto): fondo plano, una cosa importante por pantalla,
  texto en tamaño de lectura, sin índices ni mayúsculas espaciadas. En el
  lienzo quedan un anillo, el orbe y los puntos; los títulos de los eventos
  aparecen al señalarlos. Vive en `css/minimal.css`, como capa sobre `app.css`.
- **Editorial**: titulares y cifras grandes, filetes, índices numerados,
  mancheta y folio. Es lo que se describe a continuación.

Las tipografías, los colores, las interacciones y el contenido son los mismos.

## Dirección editorial

Sobre ese espacio, la interfaz se compone como una publicación, no como un
tablero de tarjetas:

- **Tipografía que manda, sin familias nuevas.** Titulares grandes en Space
  Grotesk, con una palabra en el color del modo; texto en DM Sans; metadatos
  en DM Sans chica, en mayúsculas espaciadas.
- **Filetes en lugar de cajas.** La estructura la dan líneas de 1 px, índices
  numerados (01, 02…) y columnas; el texto se apoya directo sobre el lienzo.
- **Mancheta y folio.** Arriba, el nombre, las secciones numeradas, la fecha y
  la hora. Abajo, el número de edición (el día del año) y la sección.
- **Cifras protagonistas.** La hora de lo próximo, el día de la agenda y la
  cantidad de recuerdos van en cuerpo grande.
- **El lienzo también es una lámina.** La órbita lleva marcas de registro y pie
  de figura, con la misma tipografía de la interfaz.

## Microinteracciones

Cada una responde a algo que hace la persona; ninguna es adorno suelto.

| Dónde | Qué pasa |
|---|---|
| Titulares | Entran palabra por palabra, detrás de una máscara. |
| Cifras | Ruedan dígito a dígito al cambiar (hora, día, contadores). |
| Navegación, semana, pestañas, selectores | Un único marcador se desliza con resorte hasta la opción elegida. |
| Listas | Al señalar una fila, las demás ceden; el texto se corre y aparece una flecha. |
| Lista ↔ lienzo | Señalar un evento o un recuerdo en la lista enciende su punto en el espacio, y al revés. |
| Órbita | El evento señalado se une al orbe con un radio; al tocarlo, una onda. |
| Constelación | Al señalar un recuerdo se encienden sus vínculos con otros de la misma conversación. |
| Cursor | Sobre el lienzo, un aro dice qué va a pasar: «Hablar», «Abrir», «Ver». |
| Pendientes | El círculo se llena, el tilde se dibuja y el texto se tacha de izquierda a derecha. |
| Botones principales y micrófono | Se dejan atraer por el puntero y vuelven con resorte. |
| Muelle | Las sugerencias rotan; con el campo vacío, **→** toma la que está a la vista. |
| Avisos | El filete inferior se consume: es el tiempo para deshacer. Se detiene al pasar el puntero. |
| Tarjetas de acción | Se estampan en la conversación; al deshacer, el título se tacha. |
| Copiar | El ícono pasa a un tilde y vuelve, sin aviso aparte. |
| Sonidos (opcional) | Un toque sutil al completar, guardar o deshacer. Se activan en Tú → Apariencia. |

Reglas que siguen: solo se animan `transform` y `opacity`, las entradas usan
*ease-out*, lo que la persona mueve usa resortes (`linear()`), la paleta de
comandos aparece sin coreografía porque se usa seguido y con teclado, y con
«menos movimiento» todo llega a su estado final sin animar.

## Qué mostrar

| Pantalla | Qué tiene |
|---|---|
| Bienvenida | Cuatro pasos sobre el espacio: marca, nombre, modos y voz. |
| Hoy | La portada del día: titular, la órbita (cada punto se toca), lo próximo con cuenta regresiva, una sola anticipación y las prioridades como índice. |
| Chat | Una columna de texto entre dos filetes, con el orbe a un lado. Lo tuyo alineado a la derecha y sin burbuja, la respuesta sin burbuja, tarjetas de acción con «Deshacer», origen de cada recuerdo. |
| Voz | La cámara entra al orbe, que se deforma con la voz. Subtítulos palabra por palabra, interrupción y manos libres. Cada turno queda en el chat. |
| Agenda | La órbita en grande del día elegido, semana, alta en lenguaje natural, edición y borrado. |
| Memoria | La constelación: arrastrar para recorrer, rueda para acercarse, tocar una estrella para ver de dónde salió. Tiene vista de lista. |
| Tú | Escenario (Noche o Marfil), intensidad, texto, movimiento, voz, pausa de memoria, exportar y borrar todo. |

Atajos: **Ctrl/⌘ + K** abre la paleta de comandos; en la pantalla de voz, la
**barra espaciadora** habla o interrumpe.

## Frases que entiende

- «¿Cómo viene mi día?» · «¿Qué tengo mañana?»
- «Agendame dentista mañana a las 7 de la tarde»
- «Recordame llamar a mamá a las 6»
- «Tengo que comprar pan»
- «Acordate que la defensa de la tesis es en diciembre»
- «¿Qué te conté sobre la tesis?»
- «Armame un plan de estudio para el parcial»
- «Estoy medio quemado hoy»
- «Recomendame una serie para el finde»

Lo que no entiende lo dice: no inventa.

## La voz

Usa la Web Speech API del navegador: reconocimiento (`SpeechRecognition`) y
síntesis (`speechSynthesis`).

- **Con micrófono:** tocá «Hablar con Ynara» y aceptá el permiso.
- **Sin micrófono o sin permiso:** botón del teclado para escribir.
- **Demo guiada:** botón «Demo guiada» en la pantalla de voz (o desde la paleta).
  Reproduce un guion de cinco turnos sin necesidad de micrófono. Es el camino
  seguro para mostrar en vivo.

En Chrome el reconocimiento puede enviar el audio a los servidores del
fabricante. La demo lo aclara en **Tú → Voz**; en el producto, el
reconocimiento correría en infraestructura propia.

## Estructura

```
index.html        Entrada. Aplica el tema antes del primer frame.
serve.py          Servidor local sin caché.
css/tokens.css    Identidad evolucionada: paleta, tipografías, filetes y resortes.
css/app.css       Componentes y pantallas (dirección editorial).
css/minimal.css   Capa del estilo minimalista (se activa con la clase «minimal»).
js/main.js        Mancheta, folio, muelle, ruteo, cámara por ruta y bienvenida.
js/micro.js       Microinteracciones: titulares, cifras, marcador, cursor, imán, sonidos.
js/state.js       Estado único, guardado en localStorage.
js/data.js        Datos de ejemplo (fechas relativas a hoy).
js/brain.js       Intención por reglas + fechas en español rioplatense.
js/convo.js       Turnos, tarjetas de acción y citas de memoria.
js/chat.js        Pantalla de chat.
js/voice.js       Reconocimiento, síntesis y pantalla de voz.
js/views.js       Hoy, Agenda, Memoria y Tú.
js/palette.js     Paleta de comandos.
js/field.js       El espacio: orbe, órbita del día, constelación y cámara.
js/ui.js          Nodos, íconos, logo, diálogos, toasts y fechas.
```

Para reiniciar la demo: **Tú → Borrar todo**.

## Identidad: qué se conserva y qué evoluciona

Se conserva el isotipo, el nombre, los íconos de la guía y la familia
azul → violeta. Evoluciona:

- **Noche profunda** como escenario por defecto; **Marfil** como tema de día.
- **Filetes y columnas** en lugar de tarjetas: el texto vive sobre el espacio.
- **Las tipografías no cambian:** Space Grotesk y DM Sans, las de la guía. Lo
  que cambia es la escala (titulares y cifras mucho más grandes) y el uso de
  mayúsculas espaciadas para los metadatos.
- **Grano de papel** muy leve sobre toda la superficie.
- **Alba**, un acento cálido nuevo, reservado al ahora y a lo recién guardado.
- **El diamante** del isotipo como forma de todo: el orbe, los eventos, los
  hechos, las viñetas.

## Accesibilidad

Todo lo que se dibuja en el lienzo tiene su versión en texto: la órbita se
lista para lectores de pantalla y en pantallas chicas, y la memoria tiene
vista de lista. Con «menos movimiento» la cámara salta sin animar y el espacio
queda quieto. Los controles que se animan en el lugar (pendientes, selectores,
interruptores) no reconstruyen la pantalla, así que el foco del teclado no se
pierde al usarlos.

## Referencias de la investigación

- Emil Kowalski, *7 practical animation tips* y *Great animations*
  (emilkowal.ski): duraciones cortas, *ease-out*, presión a `scale(.97)`.
- Rauno Freiberg, *Invisible details of interaction design* (rauno.me).
- Vercel, *Web Interface Guidelines*: animar solo `transform`/`opacity`,
  animaciones interrumpibles, foco visible.
- Josh W. Comeau y Chrome for Developers sobre `linear()` para resortes;
  curvas de Open Props.
- Awwwards, ganadores con honor en tipografía (2024–2026), como referencia
  de composición editorial sobre fondos generativos.
