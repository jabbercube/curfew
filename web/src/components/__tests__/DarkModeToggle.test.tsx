import { describe, expect, it, beforeEach } from "vitest";
import userEvent from "@testing-library/user-event";
import { render, screen } from "@testing-library/react";
import { DarkModeToggle } from "@/components/DarkModeToggle";

beforeEach(() => {
  window.localStorage.removeItem("curfew:theme");
  document.documentElement.classList.remove("dark");
});

describe("DarkModeToggle", () => {
  it("toggles the dark class on <html> and persists to localStorage", async () => {
    const user = userEvent.setup();
    render(<DarkModeToggle />);
    expect(document.documentElement.classList.contains("dark")).toBe(false);

    await user.click(screen.getByRole("button"));
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    expect(window.localStorage.getItem("curfew:theme")).toBe("dark");

    await user.click(screen.getByRole("button"));
    expect(document.documentElement.classList.contains("dark")).toBe(false);
    expect(window.localStorage.getItem("curfew:theme")).toBe("light");
  });
});
