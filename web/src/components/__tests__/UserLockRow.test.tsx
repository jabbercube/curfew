import { describe, expect, it } from "vitest";
import userEvent from "@testing-library/user-event";
import { screen, waitFor } from "@testing-library/react";
import { UserLockRow } from "@/components/UserLockRow";
import { renderWithQuery } from "@/test-utils/render";
import { http, HttpResponse, server } from "@/test-utils/server";
import type { User } from "@/users/queries";

const kid: User = { id: 1, username: "kid1", role: "member", managed: true };
const guest: User = { id: 2, username: "guest", role: "member", managed: false };

describe("UserLockRow", () => {
  it("shows unlocked + Lock action when not locked", () => {
    renderWithQuery(
      <UserLockRow user={kid} status={{ locked: false, reasons: [] }} isLoading={false} />,
    );
    expect(screen.getByText("kid1")).toBeInTheDocument();
    expect(screen.getByText("unlocked")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /lock/i })).toBeInTheDocument();
  });

  it("shows locked badge + reasons when locked", () => {
    renderWithQuery(
      <UserLockRow
        user={kid}
        status={{ locked: true, reasons: [{ kind: "manual_lock" }] }}
        isLoading={false}
      />,
    );
    expect(screen.getByText("locked")).toBeInTheDocument();
    expect(screen.getByText("manual_lock")).toBeInTheDocument();
  });

  it("greys out unmanaged users with no toggle", () => {
    renderWithQuery(<UserLockRow user={guest} status={undefined} isLoading={false} />);
    expect(screen.getByText("guest")).toBeInTheDocument();
    expect(screen.getByText(/unmanaged/)).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("calls POST .../lock when Lock clicked", async () => {
    let calledPath: string | null = null;
    server.use(
      http.post("/v1/users/:user/lock", ({ request }) => {
        calledPath = new URL(request.url).pathname;
        return HttpResponse.json({ locked: true, reasons: [{ kind: "manual_lock" }] });
      }),
    );
    const user = userEvent.setup();
    renderWithQuery(
      <UserLockRow user={kid} status={{ locked: false, reasons: [] }} isLoading={false} />,
    );
    await user.click(screen.getByRole("button", { name: /lock/i }));
    await waitFor(() => expect(calledPath).toBe("/v1/users/kid1/lock"));
  });
});
