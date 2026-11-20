import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AiProviders } from "@/features/system/components/AiProviders";
import type { SystemAiT } from "@/features/system/schemas";
import { systemFixture } from "@/fixtures/system";

/**
 * Render de la sección "Proveedores de IA" de System Health con el fixture, que
 * cubre los tres estados: el principal OK, el respaldo caído y los embeddings sin
 * configurar (backend fake). Verifica lo que el operador lee y que nunca se pinte
 * una URL.
 */

function providerItems(): [HTMLElement, HTMLElement] {
  const [principal, respaldo, ...rest] = screen.getAllByRole("listitem");
  if (!principal || !respaldo || rest.length > 0) {
    throw new Error("se esperaban exactamente dos proveedores de LLM");
  }
  return [principal, respaldo];
}

describe("AiProviders", () => {
  it("muestra cada proveedor con su rol, estado, host, modo y modelos por alias", () => {
    render(<AiProviders ai={systemFixture().ai} />);

    expect(screen.getByRole("heading", { name: "Proveedores de IA" })).toBeInTheDocument();
    const [principal, respaldo] = providerItems();

    const p = within(principal);
    expect(p.getByText("Principal")).toBeInTheDocument();
    expect(p.getByText("principal")).toBeInTheDocument();
    expect(p.getByText("Operativo")).toBeInTheDocument();
    expect(p.getByText("api.example.com")).toBeInTheDocument();
    expect(p.getByText("api")).toBeInTheDocument();
    expect(p.getByText("agente")).toBeInTheDocument();
    expect(p.getByText("proveedor/modelo-agente")).toBeInTheDocument();
    expect(p.getByText("conversacional")).toBeInTheDocument();
    expect(p.getByText("proveedor/modelo-chat")).toBeInTheDocument();

    const r = within(respaldo);
    expect(r.getByText("Respaldo")).toBeInTheDocument();
    expect(r.getByText("Caído")).toBeInTheDocument();
    expect(r.getByText("respaldo.example.com")).toBeInTheDocument();
  });

  it("muestra los embeddings con modelo, dimensión, modo y su estado no en uso", () => {
    render(<AiProviders ai={systemFixture().ai} />);

    expect(screen.getByText("BAAI/bge-m3")).toBeInTheDocument();
    expect(screen.getByText("1024")).toBeInTheDocument();
    expect(screen.getByText("fake")).toBeInTheDocument();
    expect(screen.getByText("No en uso")).toBeInTheDocument();
    expect(screen.getByText("Sin URL configurada")).toBeInTheDocument();
  });

  it("sin proveedores de LLM muestra el próximo paso según el modo", () => {
    const ai: SystemAiT = { ...systemFixture().ai, llm: { backend: "fake", providers: [] } };
    render(<AiProviders ai={ai} />);

    expect(screen.getByText("Sin proveedores de LLM configurados.")).toBeInTheDocument();
    expect(
      screen.getByText(/Modo fake: el chat corre con el cliente determinista/),
    ).toBeInTheDocument();
    expect(screen.queryAllByRole("listitem")).toHaveLength(0);
  });

  it("nombra a los respaldos por orden cuando hay más de uno", () => {
    const base = systemFixture().ai;
    const [principal, respaldo] = base.llm.providers;
    if (!principal || !respaldo) throw new Error("el fixture trae dos proveedores");
    const ai: SystemAiT = {
      ...base,
      llm: {
        backend: "api",
        providers: [principal, respaldo, { ...respaldo, name: "tercero", host: "c.example.com" }],
      },
    };
    render(<AiProviders ai={ai} />);

    expect(screen.getByText("Respaldo 1")).toBeInTheDocument();
    expect(screen.getByText("Respaldo 2")).toBeInTheDocument();
  });

  it("nunca pinta una URL completa", () => {
    const { container } = render(<AiProviders ai={systemFixture().ai} />);

    expect(container.textContent).not.toContain("://");
  });
});
