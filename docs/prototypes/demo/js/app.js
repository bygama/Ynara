// Puente entre módulos: main.js completa estas funciones al arrancar, así las
// vistas pueden navegar o abrir la voz sin importarse entre sí.
export const app = {
  rerender() {},
  go(hash) { location.hash = hash; },
  openVoice() {},
  openPalette() {},
  /** Deja un texto pendiente para que el chat lo envíe al abrirse. */
  pending: null,
};
