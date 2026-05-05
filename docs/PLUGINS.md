# Plugins — explained

Companion to [PLAN.md](PLAN.md), [DECISIONS.md](DECISIONS.md), and [AGENTS.md](AGENTS.md). Read this when you want to understand or write **plugins** — the in-core extension surface.

For on-device extensions (programs that run on a kid PC or Mac), see [AGENTS.md](AGENTS.md). They're a different thing.

## What a plugin is

A plugin is a Python module that extends curfew-core in-process. Drop a folder into any directory listed in `CURFEW_PLUGINS_DIRS`, restart the core, and curfew imports your code at startup. When state changes for a user the plugin governs, the core schedules your `reconcile()` method as a background task — the originating API request returns immediately; reconcile runs after.

Plugins are how curfew talks to homelab-resident services: AdGuard for DNS sinkhole, Tasmota/Kasa smart plugs for power control, UniFi/OPNsense for router ACLs, Tailscale for tailnet ACLs, plus anything you write yourself (Wyze, IKEA Tradfri, SmartThings, your own tooling).

The model follows the same pattern as Home Assistant `custom_components`, MkDocs entry points, Django apps, pytest plugins via pluggy: code on disk, in a known shape, that the host imports and calls.

## When to write a plugin vs. an agent

The fork in the road:

- **Does enforcement code have to physically run on the device?** OS-level access to NTFS, registry, sandbox profiles — yes, that's an agent.
- **Or is it talking to a service over HTTP?** AdGuard, smart plugs, routers, Wyze — that's a plugin.

The split exists because of physics, not preference. A kid PC can't be reached from the homelab; AdGuard can. Pull-and-heartbeat is the right pattern for the former; direct function calls are right for the latter.

## What a plugin folder looks like

Each entry in `CURFEW_PLUGINS_DIRS` is a directory holding one subdirectory per plugin type. The default value points at the curfew repo's `plugins/` directory — that's where shipped plugins (`adguard`, `smart_plug`, etc.) and any operator-authored plugins live:

```
plugins/
├── adguard/                          # ours
│   ├── manifest.toml
│   ├── plugin.py
│   └── requirements.txt              # optional
├── smart_plug/                       # ours
│   ├── manifest.toml
│   └── plugin.py
└── wyze/                             # operator-authored
    ├── manifest.toml
    ├── plugin.py
    └── requirements.txt
```

Operators who want to keep their plugins outside the curfew repo can add more directories to `CURFEW_PLUGINS_DIRS`; same shape, different filesystem location.

Each subdirectory is a plugin type. The directory name doesn't have to match the type, but conventionally does.

### `manifest.toml`

```toml
type = "adguard"
name = "AdGuard Home"
version = "1.0.0"
description = "Sinkhole DNS via AdGuard Home REST API"
config_schema = "Config"               # name of the Pydantic model in plugin.py
```

`type` is the unique identifier the operator uses (`curfew plugin assign adguard ...`). `config_schema` names the Pydantic class in `plugin.py` that validates per-instance config.

### `plugin.py`

```python
from curfew.plugin import Plugin, ReconcileResult, Scope
from pydantic import BaseModel
import httpx

class Config(BaseModel):
    url: str
    api_token_env: str

class AdGuardPlugin(Plugin):
    def __init__(self, config: Config):
        self.config = config
        self.client = httpx.AsyncClient(base_url=config.url)

    async def reconcile(self, scope: Scope) -> ReconcileResult:
        if scope.locked:
            await self._add_rules(scope.user, scope.target_apps.urls)
        else:
            await self._clear_rules(scope.user)
        return ReconcileResult.ok()

    async def _add_rules(self, user, urls):
        # call AdGuard's REST API to install client rules for `user`
        ...

    async def _clear_rules(self, user):
        ...
```

That's the whole plugin. One class, one `reconcile()` method, one `Config`. No HTTP server, no auth, no docker, no polling, no heartbeat — the plugin SDK provides the types and base class; everything else is your business logic.

### `requirements.txt` (optional)

If your plugin needs pip dependencies, list them. Curfew installs them into curfew-core's shared Python environment at startup.

```
httpx>=0.25
```

**Heads up — shared environment.** All plugins share curfew-core's Python process and its installed packages. There's no per-plugin dependency isolation (Python can't really do that in one process). If the AdGuard plugin needs `httpx>=0.25` and the Wyze plugin needs `httpx<0.20`, one will lose. For the realistic plugin set this rarely matters — most plugins use common HTTP libraries with overlapping needs — but it's a constraint to be aware of when picking deps. If a conflict shows up, the fixes are: pick a wider compatible range, vendor a copy inside your plugin, or talk to the conflicting plugin's author.

## What curfew-core does at startup

1. Reads `CURFEW_PLUGINS_DIRS` (default: the bundled `plugins/` dir in the image; operators extend with extra colon-separated paths).
2. For each subdirectory: opens `manifest.toml`, validates required fields, optionally `pip install -r requirements.txt`, imports `plugin.py`, finds the class subclassing `Plugin`, registers it under its `type` name. **Exactly one `Plugin` subclass per `plugin.py`** — zero or multiple is a discovery error and that plugin is skipped (with a clear error visible on `GET /v1/plugins/types`).
3. Now there's a registry: `{"adguard": AdGuardPlugin, "smart_plug": SmartPlugPlugin, "wyze": WyzePlugin, ...}`.
4. For each row in the `plugins` table (the operator's assignments): instantiate the plugin with the row's config, hold the instance in memory.

If a plugin folder is malformed (missing manifest, broken Python, requirements fail to install), curfew logs a clear error and skips it — but the core still starts. Other plugins keep working; you'll see the broken one in `GET /v1/plugins/types` with an error status.

## Lifecycle: from "I want to use AdGuard" to "it's running"

1. **AdGuard plugin already exists** at `plugins/adguard/`. Curfew-core discovered it at startup. `GET /v1/plugins/types` lists it.
2. **Operator assigns it:**
   ```
   curfew plugin assign adguard \
     --config '{"url": "http://adguard.local/control", "api_token_env": "ADGUARD_TOKEN"}' \
     --governs '["*"]'
   ```
   Curfew validates the config against `Config` (the Pydantic model from manifest.toml's `config_schema`). On success, inserts a row into `plugins(type=adguard, instance_id="", config=..., governs=["*"], paused=false)` and instantiates `AdGuardPlugin` in memory.
3. **State changes.** Operator runs `curfew lock kid1`. Core flips `manual_lock=true`, computes the new effective lock status. The lock command's HTTP response returns immediately. In the background, curfew schedules `reconcile()` on every plugin governing kid1 → `adguard.reconcile(scope=...)` runs as a background task. The plugin makes HTTP calls to AdGuard. Success or failure is logged and audited; either way, the operator's lock command already succeeded. This means slow or broken plugins don't delay the operator's UX, but the operator must check the audit log if they need to confirm a specific plugin actually reconciled.
4. **Safety-net resync.** Every `plugin_resync_seconds` (default 300s = 5 min), curfew-core walks every governed user and calls `reconcile()` on every plugin that governs them. Catches missed events from a plugin restart or a momentarily dropped state change.
5. **Pause.** `curfew plugin pause adguard`. Sets `paused=true`. Reconciliation skips this plugin until unpause. The instance stays loaded; no state lost.
6. **Unassign.** `curfew plugin unassign adguard`. Deletes the row, drops the in-memory instance. Plugin code is still on disk; another `assign` re-instantiates it.

## Bring-your-own — write a plugin in 10 minutes

Say you want curfew to flip a Wyze smart plug when kid1 is locked. Wyze isn't in the official plugin set. Steps:

1. **Create `plugins/wyze/`.**

2. **Write `manifest.toml`:**

   ```toml
   type = "wyze"
   name = "Wyze Smart Plug"
   version = "0.1.0"
   description = "Cuts power to a Wyze plug when locked"
   config_schema = "Config"
   ```

3. **Write `plugin.py`:**

   ```python
   from curfew.plugin import Plugin, ReconcileResult, Scope
   from pydantic import BaseModel
   from wyze_sdk import Client

   class Config(BaseModel):
       email: str
       password_env: str       # env var holding the password
       device_mac: str

   class WyzePlugin(Plugin):
       def __init__(self, config: Config):
           import os
           self.client = Client(email=config.email, password=os.environ[config.password_env])
           self.device_mac = config.device_mac

       async def reconcile(self, scope: Scope) -> ReconcileResult:
           if scope.locked:
               self.client.plugs.turn_off(device_mac=self.device_mac)
           else:
               self.client.plugs.turn_on(device_mac=self.device_mac)
           return ReconcileResult.ok()
   ```

4. **List dependencies in `requirements.txt`:**

   ```
   wyze-sdk
   ```

5. **Restart curfew-core.** Plugin gets discovered, requirements installed.

6. **Assign:**

   ```
   curfew plugin assign wyze \
     --config '{"email": "you@example.com", "password_env": "WYZE_PASSWORD", "device_mac": "AA:BB:CC:DD:EE:FF"}' \
     --governs '["kid1"]'
   ```

7. **Done.** Lock kid1; the plug turns off within milliseconds.

You didn't write a docker image, an HTTP server, or anything that authenticates. You wrote a Python class with a `reconcile()` method.

## What can go wrong

### A plugin's reconcile raises an exception

Reconcile runs as a background task (not synchronous to the API request that triggered it). Curfew catches exceptions at the `reconcile()` boundary. The error gets logged and recorded in the audit log; the originating API call already returned successfully. On the next state change or the safety-net resync, reconcile is called again. The plugin doesn't bring down curfew-core, and a slow or broken plugin doesn't slow down the operator.

### A plugin hangs / blocks the event loop

Curfew calls `reconcile()` with a timeout (configurable via settings). If the call doesn't return in time, it's cancelled and treated as a failure. A plugin that hangs the entire async event loop (e.g., does blocking IO without `await`) can degrade curfew — that's a class of bug the plugin author has to avoid.

### A plugin's dependencies clash with curfew's

Curfew installs each plugin's `requirements.txt` into curfew-core's shared Python environment at startup. There is no per-plugin isolation (see "shared environment" note above) — conflicting versions across plugins are the operator's problem to resolve.

### A plugin file is malformed

Curfew logs a parse error at startup and skips the plugin. Other plugins still load. `GET /v1/plugins/types` includes the failed plugin with its error.

## What plugins do *not* need

These all apply to agents (the on-device side) and don't apply here:

- **Heartbeats.** Plugins are in-process. Their liveness is the core's. If curfew-core is up, the plugin is loaded.
- **Tokens.** No network boundary to authenticate. Reconcile is a function call, not an HTTP request.
- **Self-update / manifests.** Plugins are files on disk. To update: change the file, restart curfew-core. (Or build a deploy pipeline that does that.)
- **Polling loops.** The core calls reconcile when needed, plus the slow safety-net resync.

If you find yourself wanting any of those for an extension, it's probably an agent, not a plugin. Re-read the fork at the top of this doc.

## The mental model

A plugin is a Python class on disk with a `reconcile(scope)` method. Curfew imports it at startup, instantiates it with operator-supplied config, and calls reconcile when relevant state changes. That's the entire surface.

Everything that's not "the reconciler" — discovery, instantiation, dispatch, error handling, safety-net resync — is the kernel's job. The plugin author writes one class.
