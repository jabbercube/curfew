import type { PropsWithChildren } from "react";
import { Link, useNavigate } from "@tanstack/react-router";
import { LogOut } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DarkModeToggle } from "@/components/DarkModeToggle";
import { useLogout, useMe } from "@/auth/useMe";

// Header + main content shell. Sidebar lands when admin CRUD pages do
// (slice 1 has only one screen, so no nav destinations to populate one).

export function Layout({ children }: PropsWithChildren) {
  const me = useMe();
  const logout = useLogout();
  const navigate = useNavigate();

  return (
    <div className="min-h-screen bg-background text-foreground">
      <header className="border-b">
        <div className="mx-auto flex h-14 max-w-5xl items-center justify-between gap-4 px-4">
          <Link to="/" className="font-semibold tracking-tight">
            curfew
          </Link>
          <div className="flex items-center gap-2">
            {me.data && me.data.kind === "user" && (
              <span className="text-sm text-muted-foreground">{me.data.username}</span>
            )}
            {me.data && (
              <Badge variant={me.data.role === "admin" ? "default" : "secondary"}>
                {me.data.role}
              </Badge>
            )}
            <DarkModeToggle />
            {me.data && (
              <Button
                type="button"
                variant="ghost"
                size="icon"
                aria-label="Log out"
                onClick={() =>
                  logout.mutate(undefined, {
                    onSettled: () => navigate({ to: "/login" }),
                  })
                }
              >
                <LogOut className="h-5 w-5" />
              </Button>
            )}
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-5xl px-4 py-6">{children}</main>
    </div>
  );
}
