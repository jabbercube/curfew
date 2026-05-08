import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { DeleteConfirmDialog } from "@/components/DeleteConfirmDialog";

describe("DeleteConfirmDialog", () => {
  it("renders title + description; Confirm fires onConfirm; Cancel closes", async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    const onOpenChange = vi.fn();
    render(
      <DeleteConfirmDialog
        open
        onOpenChange={onOpenChange}
        title="Delete kid1?"
        description="permanently removes the user"
        onConfirm={onConfirm}
      />,
    );
    expect(screen.getByText("Delete kid1?")).toBeInTheDocument();
    expect(screen.getByText("permanently removes the user")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /^Delete$/i }));
    expect(onConfirm).toHaveBeenCalledOnce();

    await user.click(screen.getByRole("button", { name: /Cancel/i }));
    // Cancel closes via DialogClose -> onOpenChange(false). Radix dispatches.
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("disables both buttons while pending", () => {
    render(
      <DeleteConfirmDialog
        open
        onOpenChange={() => {}}
        title="x"
        description="y"
        pending
        onConfirm={() => {}}
      />,
    );
    expect(screen.getByRole("button", { name: /Cancel/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Deleting/i })).toBeDisabled();
  });
});
