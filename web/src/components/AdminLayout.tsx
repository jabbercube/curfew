import type { PropsWithChildren } from "react";
import { Link, useNavigate } from "@tanstack/react-router";
import { LogOut } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { DarkModeToggle } from "@/components/DarkModeToggle";
import { Sidebar } from "@/components/Sidebar";
import { useLogout, useMe } from "@/auth/useMe";

// Sidebar + content shell for protected pages. The sidebar shows on md+
// screens; on mobile, only the header is visible (no nav drawer in this
// slice — three pages don't justify the chrome). When the admin CRUD
// surface grows, a Sheet-based mobile drawer is the natural follow-up.

export function AdminLayout({ children }: PropsWithChildren) {
  const me = useMe();
  const logout = useLogout();
  const navigate = useNavigate();

  return (
    <div className="min-h-screen bg-background text-foreground">
      <header className="border-b">
        <div className="mx-auto flex h-14 max-w-6xl items-center justify-between gap-4 px-4">
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
      <div className="mx-auto flex max-w-6xl">
        <aside className="hidden w-56 shrink-0 border-r md:block">
          <Sidebar />
        </aside>
        <Separator orientation="vertical" className="hidden md:block" />
        <main className="flex-1 px-4 py-6">{children}</main>
      </div>
    </div>
  );
}
