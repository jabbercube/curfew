import { createFileRoute, redirect } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { meQueryOptions } from "@/auth/useMe";
import { Layout } from "@/components/Layout";
import { UserLockRow } from "@/components/UserLockRow";
import { usersQueryOptions, useUserStatuses } from "@/users/queries";

export const Route = createFileRoute("/")({
  beforeLoad: async ({ context, location }) => {
    const me = await context.queryClient.ensureQueryData(meQueryOptions);
    if (!me) {
      throw redirect({ to: "/login", search: { redirect: location.href } });
    }
    // Warm the users query so the dashboard renders without a flash of empty.
    await context.queryClient.ensureQueryData(usersQueryOptions);
  },
  component: Dashboard,
});

function Dashboard() {
  const users = useQuery(usersQueryOptions);
  const usernames = (users.data ?? []).map((u) => u.username);
  const statuses = useUserStatuses(usernames);

  return (
    <Layout>
      <div className="space-y-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Users</h1>
          <p className="text-sm text-muted-foreground">
            Lock or unlock managed users. Status reasons surface why a user is currently locked.
          </p>
        </div>
        {users.isLoading && <p className="text-muted-foreground">Loading users…</p>}
        {users.data && users.data.length === 0 && (
          <p className="text-muted-foreground">No users yet. Ask an admin to create some.</p>
        )}
        <ul className="space-y-2">
          {(users.data ?? []).map((user, i) => {
            const statusQuery = statuses[i];
            return (
              <UserLockRow
                key={user.id}
                user={user}
                status={statusQuery?.data}
                isLoading={statusQuery?.isLoading ?? false}
              />
            );
          })}
        </ul>
      </div>
    </Layout>
  );
}
