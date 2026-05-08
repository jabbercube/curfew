import { Link } from "@tanstack/react-router";
import { Boxes, LayoutDashboard, Package, Users } from "lucide-react";
import { useMe, type Me } from "@/auth/useMe";
import { cn } from "@/lib/utils";

interface NavLink {
  to: string;
  label: string;
  icon: typeof Users;
  adminOnly?: boolean;
}

const NAV: NavLink[] = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard },
  { to: "/users", label: "Users", icon: Users, adminOnly: true },
  { to: "/devices", label: "Devices", icon: Boxes, adminOnly: true },
  { to: "/apps", label: "Apps", icon: Package, adminOnly: true },
];

function visibleFor(me: Me | null | undefined): NavLink[] {
  if (!me) return NAV.filter((n) => !n.adminOnly);
  if (me.role === "admin") return NAV;
  return NAV.filter((n) => !n.adminOnly);
}

export function Sidebar() {
  const me = useMe();
  const links = visibleFor(me.data);
  return (
    <nav className="flex flex-col gap-1 p-2">
      {links.map((link) => (
        <Link
          key={link.to}
          to={link.to}
          className={cn(
            "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground",
          )}
          activeProps={{
            className: "bg-accent text-accent-foreground",
          }}
          activeOptions={{ exact: link.to === "/" }}
        >
          <link.icon className="h-4 w-4" />
          {link.label}
        </Link>
      ))}
    </nav>
  );
}
