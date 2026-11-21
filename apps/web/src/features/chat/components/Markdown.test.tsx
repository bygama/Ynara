import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { CHAT_MARKDOWN_SCHEMA, Markdown } from "./Markdown";

describe("Markdown", () => {
  it("renderiza texto enriquecido y enlaces HTTPS con rel seguro", () => {
    render(
      <Markdown>{"Texto con **negrita**, `codigo` y [enlace](https://ynara.app/docs)."}</Markdown>,
    );

    expect(screen.getByText("negrita").tagName).toBe("STRONG");
    expect(screen.getByText("codigo").tagName).toBe("CODE");
    const link = screen.getByRole("link", { name: "enlace" });
    expect(link).toHaveAttribute("href", "https://ynara.app/docs");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(link).toHaveAttribute("target", "_blank");
  });

  it("omite elementos de imagen en markdown", () => {
    const { container } = render(
      <Markdown>{"Hola ![foto](https://example.com/pixel.png) fin"}</Markdown>,
    );

    expect(container.querySelector("img")).toBeNull();
    expect(CHAT_MARKDOWN_SCHEMA.tagNames).not.toContain("img");
  });

  it("descarta esquemas no permitidos en enlaces", () => {
    const { container } = render(<Markdown>{"[abrir](javascript:alert(1))"}</Markdown>);

    const link = container.querySelector("a");
    expect(link?.getAttribute("href")).toBeFalsy();
  });
});
