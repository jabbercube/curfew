import { Lock, Unlock } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { LockStatus, User } from "@/users/queries";
import { useToggleLock } from "@/users/queries";

interface Props {
  user: User;
  status: LockStatus | undefined;
  isLoading: boolean;
}

export function UserLockRow({ user, status, isLoading }: Props) {
  const toggle = useToggleLock();
  const locked = status?.locked ?? false;
  const reasons = status?.reasons ?? [];

  // Unmanaged users have no rule pipeline. We show the row but disable the
  // toggle so it's clear there's nothing to lock.
  if (!user.managed) {
    return (
      <li className="flex items-center justify-between rounded-md border bg-muted/20 px-4 py-3 opacity-60">
        <div>
          <p className="font-medium">{user.username}</p>
          <p className="text-xs text-muted-foreground">unmanaged · lock rules don't apply</p>
        </div>
        <Badge variant="outline">{user.role}</Badge>
      </li>
    );
  }

  return (
    <li className="flex flex-wrap items-center justify-between gap-3 rounded-md border bg-card px-4 py-3">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <p className="truncate font-medium">{user.username}</p>
          <Badge variant="outline">{user.role}</Badge>
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          {isLoading ? (
            <span>Loading status…</span>
          ) : locked ? (
            <>
              <Badge variant="destructive">locked</Badge>
              {reasons.map((r, i) => (
                <span key={i}>{r.kind}</span>
              ))}
            </>
          ) : (
            <Badge variant="secondary">unlocked</Badge>
          )}
        </div>
      </div>
      <Button
        type="button"
        size="sm"
        variant={locked ? "outline" : "default"}
        disabled={toggle.isPending}
        onClick={() => toggle.mutate({ username: user.username, locked: !locked })}
      >
        {locked ? (
          <>
            <Unlock className="h-4 w-4" />
            Unlock
          </>
        ) : (
          <>
            <Lock className="h-4 w-4" />
            Lock
          </>
        )}
      </Button>
    </li>
  );
}
