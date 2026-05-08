import { useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { MonitorSmartphone, Trash2 } from "lucide-react";
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
  devicesQueryOptions,
  useCreateDevice,
  useDeleteDevice,
  type Device,
  type DeviceCreate,
} from "@/devices/queries";
import { usersQueryOptions, type User } from "@/users/queries";

const TYPES = ["pc", "phone", "tablet", "console", "tv"] as const;
const OSES = ["windows", "macos", "linux", "ios", "android"] as const;

const SHARED_OWNER = "__shared__";

const createSchema = z.object({
  slug: z.string().min(1, "Slug required"),
  type: z.enum(TYPES),
  os: z.enum(OSES),
  ownerKey: z.string(),
  managed: z.boolean(),
});

type CreateForm = z.infer<typeof createSchema>;

export const Route = createFileRoute("/devices")({
  beforeLoad: async ({ context }) => {
    await requireAdmin(context);
    await context.queryClient.ensureQueryData(devicesQueryOptions);
    await context.queryClient.ensureQueryData(usersQueryOptions);
  },
  component: DevicesPage,
});

function DevicesPage() {
  const devices = useQuery(devicesQueryOptions);
  const users = useQuery(usersQueryOptions);
  const [createOpen, setCreateOpen] = useState(false);
  const [toDelete, setToDelete] = useState<Device | null>(null);
  const deleteDevice = useDeleteDevice();
  const usersById = new Map<number, User>((users.data ?? []).map((u) => [u.id, u]));

  return (
    <AdminLayout>
      <div className="space-y-4">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">Devices</h1>
            <p className="text-sm text-muted-foreground">
              Inventory of managed and shared devices. Owners are users; shared devices have no
              owner.
            </p>
          </div>
          <Dialog open={createOpen} onOpenChange={setCreateOpen}>
            <DialogTrigger asChild>
              <Button type="button">
                <MonitorSmartphone className="h-4 w-4" />
                New device
              </Button>
            </DialogTrigger>
            <CreateDeviceDialog users={users.data ?? []} onClose={() => setCreateOpen(false)} />
          </Dialog>
        </div>

        <div className="rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Slug</TableHead>
                <TableHead>Type</TableHead>
                <TableHead>OS</TableHead>
                <TableHead>Owner</TableHead>
                <TableHead>Managed</TableHead>
                <TableHead className="w-12" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {devices.isLoading && (
                <TableRow>
                  <TableCell colSpan={6} className="text-muted-foreground">
                    Loading…
                  </TableCell>
                </TableRow>
              )}
              {devices.data && devices.data.length === 0 && (
                <TableRow>
                  <TableCell colSpan={6} className="text-muted-foreground">
                    No devices yet.
                  </TableCell>
                </TableRow>
              )}
              {(devices.data ?? []).map((device) => {
                const owner = device.owner_id !== null ? usersById.get(device.owner_id) : null;
                return (
                  <TableRow key={device.id}>
                    <TableCell className="font-medium">{device.slug}</TableCell>
                    <TableCell>
                      <Badge variant="outline">{device.type}</Badge>
                    </TableCell>
                    <TableCell>{device.os}</TableCell>
                    <TableCell>
                      {owner ? (
                        owner.username
                      ) : device.owner_id !== null ? (
                        <span className="text-muted-foreground">
                          id {device.owner_id} (missing)
                        </span>
                      ) : (
                        <span className="text-muted-foreground">shared</span>
                      )}
                    </TableCell>
                    <TableCell>{device.managed ? "yes" : "no"}</TableCell>
                    <TableCell>
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        aria-label={`Delete ${device.slug}`}
                        onClick={() => setToDelete(device)}
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
      </div>

      <DeleteConfirmDialog
        open={toDelete !== null}
        onOpenChange={(open) => !open && setToDelete(null)}
        title={`Delete device ${toDelete?.slug ?? ""}?`}
        description="A device with an installed agent must have the agent uninstalled first."
        pending={deleteDevice.isPending}
        onConfirm={() => {
          if (!toDelete) return;
          const slug = toDelete.slug;
          deleteDevice.mutate(slug, {
            onSuccess: () => {
              toast.success(`Deleted ${slug}`);
              setToDelete(null);
            },
            onError: (err) => {
              if (err instanceof ApiError && err.status === 409) {
                toast.error(`Cannot delete ${slug}: agent still installed`);
              } else {
                toast.error(`Failed to delete ${slug}`);
              }
              setToDelete(null);
            },
          });
        }}
      />
    </AdminLayout>
  );
}

function CreateDeviceDialog({ users, onClose }: { users: User[]; onClose: () => void }) {
  const create = useCreateDevice();
  const form = useForm<CreateForm>({
    resolver: zodResolver(createSchema),
    defaultValues: {
      slug: "",
      type: "pc",
      os: "windows",
      ownerKey: SHARED_OWNER,
      managed: true,
    },
  });

  const onSubmit = (values: CreateForm) => {
    const payload: DeviceCreate = {
      slug: values.slug,
      type: values.type,
      os: values.os,
      owner_id: values.ownerKey === SHARED_OWNER ? null : Number(values.ownerKey),
      managed: values.managed,
      mac: [],
    };
    create.mutate(payload, {
      onSuccess: (device) => {
        toast.success(`Created ${device.slug}`);
        form.reset();
        onClose();
      },
      onError: (err) => {
        if (err instanceof ApiError && err.status === 409) {
          form.setError("slug", {
            type: "manual",
            message: "Slug already exists",
          });
        } else {
          toast.error("Failed to create device");
        }
      },
    });
  };

  return (
    <DialogContent>
      <DialogHeader>
        <DialogTitle>New device</DialogTitle>
        <DialogDescription>
          Slug is the URL handle. Owner is optional; shared devices apply to no one user.
        </DialogDescription>
      </DialogHeader>
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4" noValidate>
        <div className="space-y-2">
          <Label htmlFor="slug">Slug</Label>
          <Input id="slug" autoFocus {...form.register("slug")} />
          {form.formState.errors.slug && (
            <p className="text-sm text-destructive">{form.formState.errors.slug.message}</p>
          )}
        </div>
        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-2">
            <Label htmlFor="type">Type</Label>
            <Select
              value={form.watch("type")}
              onValueChange={(v) => form.setValue("type", v as CreateForm["type"])}
            >
              <SelectTrigger id="type">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {TYPES.map((t) => (
                  <SelectItem key={t} value={t}>
                    {t}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label htmlFor="os">OS</Label>
            <Select
              value={form.watch("os")}
              onValueChange={(v) => form.setValue("os", v as CreateForm["os"])}
            >
              <SelectTrigger id="os">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {OSES.map((o) => (
                  <SelectItem key={o} value={o}>
                    {o}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
        <div className="space-y-2">
          <Label htmlFor="owner">Owner</Label>
          <Select
            value={form.watch("ownerKey")}
            onValueChange={(v) => form.setValue("ownerKey", v)}
          >
            <SelectTrigger id="owner">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={SHARED_OWNER}>shared (no owner)</SelectItem>
              {users.map((u) => (
                <SelectItem key={u.id} value={String(u.id)}>
                  {u.username}
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
            <span className="text-sm text-muted-foreground">Subject to the rule pipeline</span>
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
