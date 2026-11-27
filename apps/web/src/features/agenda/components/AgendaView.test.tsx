import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Agenda sin eventos: lo que se prueba es qué día muestra y si «Hoy» está activo.
vi.mock("../api", () => ({
  useEvents: () => ({
    data: [],
    isPending: false,
    isError: false,
    isFetching: false,
    refetch: vi.fn(),
  }),
  useCreateEvent: () => ({ mutateAsync: vi.fn(), isPending: false }),
  usePatchEvent: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useDeleteEvent: () => ({ mutateAsync: vi.fn(), isPending: false }),
  usePatchEventById: () => ({ mutate: vi.fn() }),
}));

// Mocks de infraestructura de UI (canvas / GSAP / animaciones).
vi.mock("@/components/ui/LivingField", () => ({
  LivingField: () => null,
}));
vi.mock("@/components/ui/HeroReveal", () => ({
  HeroReveal: ({ children, className }: { children: React.ReactNode; className?: string }) => (
    <div className={className}>{children}</div>
  ),
}));
vi.mock("@/hooks/useActiveMode", () => ({
  useActiveMode: () => "productividad" as const,
}));

const { AgendaView } = await import("./AgendaView");

// Fechas con el constructor local: no depende de la TZ del runner. Jueves 7 de
// mayo de 2026.
const at = (d: number, h: number, min = 0, s = 0) => new Date(2026, 4, d, h, min, s);

function renderDayView() {
  const view = render(<AgendaView />);
  fireEvent.click(screen.getByRole("radio", { name: "Día" }));
  const period = () => view.container.querySelector("header p")?.textContent ?? "";
  const todayButton = () => screen.getByRole("button", { name: "Hoy" });
  return { period, todayButton };
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("AgendaView con la pestaña abierta pasada la medianoche", () => {
  it("si mostraba hoy, pasa al día nuevo y «Hoy» sigue desactivado", () => {
    vi.setSystemTime(at(7, 23, 59, 30));
    const { period, todayButton } = renderDayView();
    expect(period()).toMatch(/7 de mayo/);
    expect(todayButton()).toBeDisabled();

    act(() => {
      vi.advanceTimersByTime(30_000);
    });
    expect(period()).toMatch(/8 de mayo/);
    expect(todayButton()).toBeDisabled();
  });

  it("si la persona había ido a otro día, lo deja y activa «Hoy»", () => {
    vi.setSystemTime(at(7, 23, 59, 30));
    const { period, todayButton } = renderDayView();
    fireEvent.click(screen.getByRole("button", { name: "Día anterior" }));
    expect(period()).toMatch(/6 de mayo/);

    act(() => {
      vi.advanceTimersByTime(30_000);
    });
    expect(period()).toMatch(/6 de mayo/);
    expect(todayButton()).toBeEnabled();
  });

  it("«Hoy» lleva al día de `now` aunque el reloj no se haya enterado del cambio", () => {
    vi.setSystemTime(at(7, 23, 59, 30));
    const { period, todayButton } = renderDayView();
    fireEvent.click(screen.getByRole("button", { name: "Día anterior" }));

    // Pasó la medianoche pero el timer no corrió (máquina suspendida).
    vi.setSystemTime(at(8, 9));
    fireEvent.click(todayButton());
    expect(period()).toMatch(/7 de mayo/);
    expect(todayButton()).toBeDisabled();
  });
});
