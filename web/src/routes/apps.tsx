import { useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { PackagePlus, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import { requireAdmin } from "@/auth/admin-guard";
import { AdminLayout } from "@/components/AdminLayout";
import { DeleteConfirmDialog } from "@/components/DeleteConfirmDialog";
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
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  appsQueryOptions,
  useCreateApp,
  useDeleteApp,
  type App,
  type AppCreate,
} from "@/apps/queries";

// Comma-or-newline-separated list, trimmed, empty entries dropped.
function parseList(input: string): string[] {
  return input
    .split(/[,\n]/)
    .map((s) => s.trim())
    .filter(Boolean);
}

const createSchema = z.object({
  slug: z.string().min(1, "Slug required"),
  exe_paths: z.string(),
  process_names: z.string(),
  urls: z.string(),
});

type CreateForm = z.infer<typeof createSchema>;

export const Route = createFileRoute("/apps")({
  beforeLoad: async ({ context }) => {
    await requireAdmin(context);
    await context.queryClient.ensureQueryData(appsQueryOptions);
  },
  component: AppsPage,
});

function AppsPage() {
  const apps = useQuery(appsQueryOptions);
  const [createOpen, setCreateOpen] = useState(false);
  const [toDelete, setToDelete] = useState<App | null>(null);
  const deleteApp = useDeleteApp();

  return (
    <AdminLayout>
      <div className="space-y-4">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">Apps</h1>
            <p className="text-sm text-muted-foreground">
              Catalog of apps + URLs the agent or plugin layer can target.
            </p>
          </div>
          <Dialog open={createOpen} onOpenChange={setCreateOpen}>
            <DialogTrigger asChild>
              <Button type="button">
                <PackagePlus className="h-4 w-4" />
                New app
              </Button>
            </DialogTrigger>
            <CreateAppDialog onClose={() => setCreateOpen(false)} />
          </Dialog>
        </div>

        <div className="rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Slug</TableHead>
                <TableHead>Exe paths</TableHead>
                <TableHead>Process names</TableHead>
                <TableHead>URLs</TableHead>
                <TableHead className="w-12" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {apps.isLoading && (
                <TableRow>
                  <TableCell colSpan={5} className="text-muted-foreground">
                    Loading…
                  </TableCell>
                </TableRow>
              )}
              {apps.data && apps.data.length === 0 && (
                <TableRow>
                  <TableCell colSpan={5} className="text-muted-foreground">
                    No apps yet.
                  </TableCell>
                </TableRow>
              )}
              {(apps.data ?? []).map((app) => (
                <TableRow key={app.id}>
                  <TableCell className="font-medium">{app.slug}</TableCell>
                  <CountCell items={app.exe_paths} />
                  <CountCell items={app.process_names} />
                  <CountCell items={app.urls} />
                  <TableCell>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      aria-label={`Delete ${app.slug}`}
                      onClick={() => setToDelete(app)}
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
        title={`Delete app ${toDelete?.slug ?? ""}?`}
        description="Removing the app from the catalog. Agents / plugins consuming it will see the entry disappear on next reconcile."
        pending={deleteApp.isPending}
        onConfirm={() => {
          if (!toDelete) return;
          const slug = toDelete.slug;
          deleteApp.mutate(slug, {
            onSuccess: () => {
              toast.success(`Deleted ${slug}`);
              setToDelete(null);
            },
            onError: () => {
              toast.error(`Failed to delete ${slug}`);
              setToDelete(null);
            },
          });
        }}
      />
    </AdminLayout>
  );
}

function CountCell({ items }: { items: string[] }) {
  if (items.length === 0) {
    return <TableCell className="text-muted-foreground">—</TableCell>;
  }
  return (
    <TableCell>
      <span className="text-sm" title={items.join("\n")}>
        {items.length} {items.length === 1 ? "entry" : "entries"}
      </span>
    </TableCell>
  );
}

function CreateAppDialog({ onClose }: { onClose: () => void }) {
  const create = useCreateApp();
  const form = useForm<CreateForm>({
    resolver: zodResolver(createSchema),
    defaultValues: { slug: "", exe_paths: "", process_names: "", urls: "" },
  });

  const onSubmit = (values: CreateForm) => {
    const payload: AppCreate = {
      slug: values.slug,
      exe_paths: parseList(values.exe_paths),
      process_names: parseList(values.process_names),
      urls: parseList(values.urls),
    };
    create.mutate(payload, {
      onSuccess: (app) => {
        toast.success(`Created ${app.slug}`);
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
          toast.error("Failed to create app");
        }
      },
    });
  };

  return (
    <DialogContent>
      <DialogHeader>
        <DialogTitle>New app</DialogTitle>
        <DialogDescription>
          Lists are comma- or newline-separated. Empty fields are fine; the agent and plugin SDKs
          match by whichever shape they support.
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
        <div className="space-y-2">
          <Label htmlFor="exe_paths">Exe paths</Label>
          <Input
            id="exe_paths"
            placeholder="C:/Steam/steam.exe, /Applications/Steam.app/Contents/MacOS/steam"
            {...form.register("exe_paths")}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="process_names">Process names</Label>
          <Input
            id="process_names"
            placeholder="steam.exe, steam"
            {...form.register("process_names")}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="urls">URLs</Label>
          <Input
            id="urls"
            placeholder="https://store.steampowered.com"
            {...form.register("urls")}
          />
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
