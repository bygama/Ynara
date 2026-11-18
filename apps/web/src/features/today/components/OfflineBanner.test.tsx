import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { OfflineBanner } from "./OfflineBanner";

function setOnline(value: boolean) {
  Object.defineProperty(navigator, "onLine", { configurable: true, value });
}

afterEach(() => setOnline(true));

describe("OfflineBanner", () => {
  it("no muestra nada cuando hay conexión", () => {
    setOnline(true);
    render(<OfflineBanner />);
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("avisa sin conexión que no se puede responder ni guardar", () => {
    setOnline(false);
    render(<OfflineBanner />);
    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(
      screen.getByText("Sin conexión · no podemos responder ni guardar cambios"),
    ).toBeInTheDocument();
  });
});
