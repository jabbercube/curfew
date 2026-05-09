"""AdGuard Home plugin — DNS sinkhole per managed device.

Reconcile semantics:

- **Block leg** (``scope.locked == True``): for each managed device of
  ``scope.user`` with at least one MAC, ensure an AdGuard client exists
  with those MACs and ``upstreams=[sinkhole_upstream]``. If no client
  matches any of the device's MACs, register one (``name=device.slug``,
  ``ids=device.mac``); if a client matches but its upstream is wrong,
  update it. Curfew is the source of truth for managed devices, so the
  plugin auto-registers on first lock — operators don't need a separate
  push step before the first lock works.
- **Unblock leg** (``scope.locked == False``): for each managed device,
  if a matching AdGuard client has ``upstreams=[sinkhole_upstream]``,
  clear it (``upstreams=[]``). No auto-create on this leg — if there's
  no client there's nothing to clear.

Both legs are idempotent — safe to call repeatedly (safety-net resync).
HTTP failures are caught and converted to ``ReconcileResult.error`` so
the runtime audits the failure as ``plugin.reconcile_failed`` rather
than letting an exception cross the reconcile boundary.

Per ADR-013's secret convention, the AdGuard admin password is read
from an env var (config carries the env var *name*, not the password
itself), so ``state.sqlite`` and ``GET /v1/plugins`` stay free of
plaintext credentials. The username goes in plaintext config — it
isn't a secret.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from curfew.plugin import DeviceRef, Plugin, ReconcileResult, Scope
from pydantic import BaseModel, Field


class Config(BaseModel):
    """AdGuard Home connection + behavior knobs."""

    url: str = Field(description="AdGuard Home base URL, e.g. http://adguard.lan")
    username: str = Field(description="AdGuard admin username")
    password_env: str = Field(
        description=(
            "Name of the env var holding the AdGuard admin password. "
            "Per ADR-013, the password itself is never stored in state.sqlite."
        )
    )
    sinkhole_upstream: str = Field(
        default="0.0.0.0",
        description=(
            "Upstream address used to sinkhole locked clients. "
            "Default 0.0.0.0 makes all DNS resolution fail closed."
        ),
    )
    timeout_seconds: float = Field(
        default=10.0,
        description="HTTP timeout per AdGuard call.",
    )


class AdGuardPlugin(Plugin):
    def __init__(self, config: Config) -> None:
        self.config = config
        password = os.environ[config.password_env]
        self._client = httpx.AsyncClient(
            base_url=config.url.rstrip("/"),
            auth=(config.username, password),
            timeout=config.timeout_seconds,
        )

    async def reconcile(self, scope: Scope) -> ReconcileResult:
        targets = [d for d in scope.devices if d.managed and d.mac]
        if not targets:
            # Nothing actionable: avoid even the GET /clients call.
            return ReconcileResult.ok()

        try:
            mac_to_client = await self._fetch_clients()
            sinkhole = [self.config.sinkhole_upstream]
            for device in targets:
                existing = self._lookup(device, mac_to_client)
                if scope.locked:
                    await self._apply_block(device, existing, sinkhole)
                else:
                    await self._apply_unblock(existing, sinkhole)
        except httpx.HTTPError as exc:
            return ReconcileResult.error(f"adguard {type(exc).__name__}: {exc}")

        return ReconcileResult.ok()

    async def _fetch_clients(self) -> dict[str, dict[str, Any]]:
        """Index AdGuard's persistent clients by lowercase MAC."""
        r = await self._client.get("/control/clients")
        r.raise_for_status()
        result: dict[str, dict[str, Any]] = {}
        for client in r.json().get("clients") or []:
            for cid in client.get("ids") or []:
                result[cid.lower()] = client
        return result

    @staticmethod
    def _lookup(
        device: DeviceRef, mac_to_client: dict[str, dict[str, Any]]
    ) -> dict[str, Any] | None:
        for mac in device.mac:
            client = mac_to_client.get(mac.lower())
            if client is not None:
                return client
        return None

    async def _apply_block(
        self,
        device: DeviceRef,
        existing: dict[str, Any] | None,
        sinkhole: list[str],
    ) -> None:
        if existing is None:
            # First-time registration. ``filtering_enabled=False`` so the
            # client uses only the upstream override, not AdGuard's
            # filter rules — the sinkhole upstream is the whole story.
            payload = {
                "name": device.slug,
                "ids": list(device.mac),
                "upstreams": sinkhole,
                "use_global_settings": True,
                "use_global_blocked_services": True,
                "filtering_enabled": False,
            }
            r = await self._client.post("/control/clients/add", json=payload)
            r.raise_for_status()
            return
        if existing.get("upstreams") == sinkhole:
            return  # already sinkholed; idempotent skip
        updated = dict(existing)
        updated["upstreams"] = sinkhole
        r = await self._client.post(
            "/control/clients/update",
            json={"name": existing["name"], "data": updated},
        )
        r.raise_for_status()

    async def _apply_unblock(self, existing: dict[str, Any] | None, sinkhole: list[str]) -> None:
        if existing is None or existing.get("upstreams") != sinkhole:
            return  # no client, or already cleared; idempotent skip
        updated = dict(existing)
        updated["upstreams"] = []
        r = await self._client.post(
            "/control/clients/update",
            json={"name": existing["name"], "data": updated},
        )
        r.raise_for_status()
