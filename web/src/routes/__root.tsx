import type { QueryClient } from "@tanstack/react-query";
import { Outlet, createRootRouteWithContext } from "@tanstack/react-router";

// Root route. `context` is wired in main.tsx; route loaders read
// `context.queryClient` to gate access via the cached `me` query.
export const Route = createRootRouteWithContext<{ queryClient: QueryClient }>()({
  component: RootComponent,
});

function RootComponent() {
  return <Outlet />;
}
