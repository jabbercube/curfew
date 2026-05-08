import { useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Trash2, UserPlus } from "lucide-react";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import { requireAdmin } from "@/auth/admin-guard";
import { AdminLayout } from "@/components/AdminLayout";
import { DeleteConfirmDialog } from "@/components/DeleteConfirmDialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  useCreateUser,
  useDeleteUser,
  usersQueryOptions,
  type User,
  type UserCreate,
} from "@/users/queries";

const ROLES = ["admin", "manager", "member"] as const;

const createSchema = z.object({
  username: z.string().min(1, "Username required"),
  password: z.string().min(8, "Password must be at least 8 characters"),
  role: z.enum(ROLES),
  managed: z.boolean(),
});

type CreateForm = z.infer<typeof createSchema>;

export const Route = createFileRoute("/users")({
  beforeLoad: async ({ context }) => {
    await requireAdmin(context);
    await context.queryClient.ensureQueryData(usersQueryOptions);
  },
  component: UsersPage,
});

function UsersPage() {
  const users = useQuery(usersQueryOptions);
  const [createOpen, setCreateOpen] = useState(false);
  const [toDelete, setToDelete] = useState<User | null>(null);
  const deleteUser = useDeleteUser();

  return (
    <AdminLayout>
      <div className="space-y-4">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">Users</h1>
            <p className="text-sm text-muted-foreground">
              Create operators and managed members. Lock state lives on the dashboard.
            </p>
          </div>
          <Dialog open={createOpen} onOpenChange={setCreateOpen}>
            <DialogTrigger asChild>
              <Button type="button">
                <UserPlus className="h-4 w-4" />
                New user
              </Button>
            </DialogTrigger>
            <CreateUserDialog onClose={() => setCreateOpen(false)} />
          </Dialog>
        </div>

        <div className="rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Username</TableHead>
                <TableHead>Role</TableHead>
                <TableHead>Managed</TableHead>
                <TableHead className="w-12" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {users.isLoading && (
                <TableRow>
                  <TableCell colSpan={4} className="text-muted-foreground">
                    Loading…
                  </TableCell>
                </TableRow>
              )}
              {users.data && users.data.length === 0 && (
                <TableRow>
                  <TableCell colSpan={4} className="text-muted-foreground">
                    No users yet.
                  </TableCell>
                </TableRow>
              )}
              {(users.data ?? []).map((user) => (
                <TableRow key={user.id}>
                  <TableCell className="font-medium">{user.username}</TableCell>
                  <TableCell>
                    <Badge variant={user.role === "admin" ? "default" : "secondary"}>
                      {user.role}
                    </Badge>
                  </TableCell>
                  <TableCell>{user.managed ? "yes" : "no"}</TableCell>
                  <TableCell>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      aria-label={`Delete ${user.username}`}
                      onClick={() => setToDelete(user)}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </div>

      <DeleteConfirmDialog
        open={toDelete !== null}
        onOpenChange={(open) => !open && setToDelete(null)}
        title={`Delete user ${toDelete?.username ?? ""}?`}
        description="This permanently removes the user. Devices owned by this user will block deletion until reassigned."
        pending={deleteUser.isPending}
        onConfirm={() => {
          if (!toDelete) return;
          const username = toDelete.username;
          deleteUser.mutate(username, {
            onSuccess: () => {
              toast.success(`Deleted ${username}`);
              setToDelete(null);
            },
            onError: (err) => {
              if (err instanceof ApiError && err.status === 409) {
                toast.error(`Cannot delete ${username}: owns one or more devices`);
              } else {
                toast.error(`Failed to delete ${username}`);
              }
              setToDelete(null);
            },
          });
        }}
      />
    </AdminLayout>
  );
}

function CreateUserDialog({ onClose }: { onClose: () => void }) {
  const create = useCreateUser();
  const form = useForm<CreateForm>({
    resolver: zodResolver(createSchema),
    defaultValues: { username: "", password: "", role: "member", managed: true },
  });

  const onSubmit = (values: CreateForm) => {
    const payload: UserCreate = values;
    create.mutate(payload, {
      onSuccess: (user) => {
        toast.success(`Created ${user.username}`);
        form.reset();
        onClose();
      },
      onError: (err) => {
        if (err instanceof ApiError && err.status === 409) {
          form.setError("username", {
            type: "manual",
            message: "Username already exists",
          });
        } else {
          toast.error("Failed to create user");
        }
      },
    });
  };

  return (
    <DialogContent>
      <DialogHeader>
        <DialogTitle>New user</DialogTitle>
        <DialogDescription>Operator (admin / manager) or managed member.</DialogDescription>
      </DialogHeader>
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4" noValidate>
        <div className="space-y-2">
          <Label htmlFor="username">Username</Label>
          <Input id="username" autoFocus {...form.register("username")} />
          {form.formState.errors.username && (
            <p className="text-sm text-destructive">{form.formState.errors.username.message}</p>
          )}
        </div>
        <div className="space-y-2">
          <Label htmlFor="password">Password</Label>
          <Input id="password" type="password" {...form.register("password")} />
          {form.formState.errors.password && (
            <p className="text-sm text-destructive">{form.formState.errors.password.message}</p>
          )}
        </div>
        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-2">
            <Label htmlFor="role">Role</Label>
            <Select
              value={form.watch("role")}
              onValueChange={(v) => form.setValue("role", v as CreateForm["role"])}
            >
              <SelectTrigger id="role">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {ROLES.map((r) => (
                  <SelectItem key={r} value={r}>
                    {r}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label htmlFor="managed">Managed</Label>
            <div className="flex h-10 items-center gap-2">
              <input
                id="managed"
                type="checkbox"
                className="h-4 w-4 rounded border-input"
                {...form.register("managed")}
              />
              <span className="text-sm text-muted-foreground">Lock rules apply</span>
            </div>
          </div>
        </div>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" disabled={create.isPending}>
            {create.isPending ? "Creating…" : "Create"}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  );
}
