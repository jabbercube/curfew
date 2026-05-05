# Agents — explained

Companion to [PLAN.md](PLAN.md), [DECISIONS.md](DECISIONS.md), and [PLUGINS.md](PLUGINS.md). Read this when you want to understand or write **agents** — the on-device extension surface.

For in-core extensions (Python modules that run inside curfew-core itself), see [PLUGINS.md](PLUGINS.md). They're a different thing.

## What an agent is

curfew-core is a small service that holds state — who's locked, who owns what, what apps to block. To actually *enforce* anything on a Windows PC or a Mac, code has to physically run on that device, with OS-level privileges. NTFS file ACLs can only be set from Windows; macOS sandbox profiles can only be applied from macOS.

An **agent** is that code: a small program that runs on a managed device, polls curfew-core on a timer, fetches the current lock status for the user it governs, and reconciles the device's state to match.

Agents exist because the alternative — curfew-core reaching out to the device — doesn't work. Kid PCs are behind home NAT, sleep half the time, and run on residential connections that don't accept inbound traffic. The agent has to initiate every conversation.

## One agent per device

Each managed device has at most one agent installed. The agent's *type* identifies what it knows how to do (`windows-pc`, `macos-pc`, etc.) — the type is implementation, not configuration. A device runs one agent because:

- The realistic failure modes (PC off, network down, agent crashed, OS rebooting) all knock out *every* enforcement on that device simultaneously. Per-thing-it-enforces heartbeats would give you N independent signals for one underlying signal.
- Tokens are per-device — revocation is "this PC is compromised, cut it off." That's the actual revocation case anyone has.
- Multiple things to enforce on the same device (block exes + apply registry policy + kill processes) live inside the one agent's reconcile logic.

## Agent code is first-party only

Unlike plugins, agents don't have a drop-in third-party extension model. Adding new behaviour to an agent means extending the agent's source — either contributing upstream or maintaining a fork. There is no `agent_plugins/` directory or equivalent.

If you want to add new on-device enforcement (e.g. a screen-time recorder for Windows alongside the existing app blocking), the realistic path is:

1. Open a PR against the windows-pc agent to add the capability natively, or
2. Maintain a fork of the agent with your additions.

This is a deliberate trade-off. Per-device extension would mean either multiple agents per device (which we explicitly rejected — different failure-mode signals for the same underlying "is this device reachable" question) or a sub-plugin system inside the agent (real complexity for a use case that doesn't yet exist). The bring-your-own story for curfew lives on the plugin side, where it's a good fit.

## Lifecycle: what installation actually looks like

Concrete walkthrough for `windows-pc` on a PC named `gamingrig`, owned by user `kid1`:

### 1. Operator runs `curfew agent install`

```
curfew agent install windows-pc gamingrig --config '{"windows_user": "Kid1Local"}'
```

This:
- Inserts a row into `device_agents(device=gamingrig, type=windows-pc, config=...)`.
- Creates the matching `agent_instances(device=gamingrig)` runtime row in the same transaction (initially with no heartbeats yet).
- Mints a device-scoped bearer token, hashes it, inserts into `agent_tokens`.
- Prints `{token: { id, secret }, bootstrap: "..."}`. The secret is shown once.

### 2. Operator runs the bootstrap one-liner on `gamingrig`

The CLI printed something like:

```powershell
iex (irm https://curfew.homelab.local/bootstrap/windows-pc?token=SECRET)
```

That command:
- Fetches `GET /v1/agents/windows-pc/manifest` → `{ version, sha256, url }`.
- Downloads the agent artifact (a PowerShell script + the `curfew-agent-sdk-powershell` module bundled together) from `url`.
- Verifies the SHA-256 matches what the manifest said.
- Installs the files locally, drops `agent.config` (API URL, device name, bearer secret).
- Registers a Windows Scheduled Task that runs the agent every minute.

### 3. The agent runs every minute

Each tick:
- Compute its current state hash (cached from last tick).
- POST `/v1/devices/gamingrig/heartbeat` with `{state_hash, agent_version}`.
- Receive `{state_hash, agent_tick_seconds, drift_threshold_seconds, ...}` back.
- If hashes match: nothing changed. Maybe report any local errors via the next heartbeat. Wait for next tick.
- If hashes differ: GET `/v1/devices/gamingrig/state` for the full picture (lock status, agent config, target apps, app catalog entries). Update local cache. Run the reconciler.
- Reconciler given `{locked: true}` + target_apps: apply NTFS deny-execute on the configured exe paths, set `URLBlocklist` registry policy, kill matching running processes.
- Reconciler given `{locked: false}`: undo all of the above.

Separately, on a slower tick (default hourly):
- GET `/v1/agents/windows-pc/manifest`. If `version` differs from installed, fetch `url`, verify hash, swap atomically.

### 4. Operator removes the agent

```
curfew agent uninstall gamingrig
```

This:
- Deletes the `device_agents` row → `agent_instances` cascade-deletes in the same transaction.
- Sets `revoked_at` on outstanding tokens.
- Next heartbeat from `gamingrig` returns 401. The agent SDK treats that as "I've been disowned" and stops trying.
- Operator removes the local install (uninstall script, or just delete the scheduled task).

## What the agent author actually writes

Most of the loop above is in the SDK (`curfew_agent_sdk_python` for Linux/macOS, `curfew-agent-sdk-powershell` for Windows). The author writes a **reconciler** — given the current lock status and config, do the right thing. Sketch:

```powershell
# windows-pc/agent.ps1
Import-Module curfew-agent-sdk-powershell

Register-Reconciler -ScriptBlock {
    param($state)
    $cfg = $state.config
    if ($state.locked) {
        Apply-NTFSDeny -User $cfg.windows_user -Paths $state.target_apps.exe_paths
        Set-URLBlocklist -User $cfg.windows_user -Urls $state.target_apps.urls
        Stop-MatchingProcesses -Names $state.target_apps.process_names
    } else {
        Remove-NTFSDeny -User $cfg.windows_user -Paths $state.target_apps.exe_paths
        Clear-URLBlocklist -User $cfg.windows_user
    }
}

Start-CurfewAgent
```

The exact API surface is implementation detail — what matters is that the agent reduces to "given the state, do this." Polling, heartbeats, retries, the manifest fetch, hash verification — all in the SDK.

## What can go wrong

### Drift

`GET /v1/agents` reports each agent in one of three states:

- **`pending`** — assigned but `last_heartbeat IS NULL`. The operator ran `curfew agent install` but hasn't run the bootstrap on the device yet. Not an alert.
- **`healthy`** — heartbeating within `drift_threshold_seconds`.
- **`drifted`** — heartbeated at least once but not within the threshold. **This is the alert state.**

If an agent transitions to drifted (PC off, agent crashed, network down), the lock state in curfew-core is *unchanged* — the database still says kid1 is locked, but the device isn't enforcing because nothing's running there. **Drift = unenforced.**

Two mitigations exist as features (see PLAN.md):

- **Auto-lock on disconnect** — the agent tracks "time since last successful state fetch" and trips the reconciler with `{locked: true, reasons: [{kind: "failclosed"}]}` after `failclosed_after_seconds`. Even when the agent can't reach the core, it stays locked. Setting defaults to `0` (disabled).
- **Drift notifications** — a webhook or polled `?drifted=true` query so the operator gets pinged when an agent goes quiet.

### Token compromise

If a device's token leaks, the operator runs `curfew agent token revoke gamingrig {token_id}` and the next heartbeat from that token returns 401. Mint a new token via `curfew agent token mint gamingrig` and rebootstrap.

### Agent code compromise

The bootstrap and update path are integrity-checked: `GET /v1/agents/{type}/manifest` returns a SHA-256, the agent verifies the artifact against it before running anything. If the curfew-core image itself is compromised, an attacker could publish a malicious manifest — manifest signing (a key on the core; public key embedded in the bootstrap) is a future tightening.

## How to write a new agent

Suppose you want a `linux-pc` agent. Steps:

1. **Pick the SDK flavour.** Linux → `curfew_agent_sdk_python`. (Windows would use the PowerShell SDK; for anything else, Python.)
2. **Write the reconciler.** Subclass the SDK's agent class, implement `reconcile(state)`, do whatever Linux-specific blocking/killing you want (iptables to localhost, kill processes, deny execute via setfacl, etc.).
3. **Build a bootstrap installer** — a small shell script that the operator runs on the target machine. It fetches the manifest, verifies hash, installs, registers a systemd timer (or cron, or whatever).
4. **Publish a version.** `curfew agent publish linux-pc 1.0.0 ./linux-pc-agent.tar` ships the artifact + hash to curfew-core. From now on, `curfew agent install linux-pc <device>` will work.
5. **Test it** against `reftest_agent`'s patterns — drop assignment, heartbeat appears, drift surfaces if you stop the agent, lock toggles propagate within a tick.

The agent doesn't need to know about plugins or the rule pipeline. It only sees `{locked, reasons, target_apps, config}` and the reconciler dispatches.

## Why agents and plugins are separate

The other extension surface — **plugins** — runs *inside* curfew-core's process. AdGuard, smart plugs, router ACLs: code that lives in the homelab and reaches out to a service over HTTP. For those, polling and heartbeats would be wasted work; the core can call them directly when state changes. They're a different shape with a different lifecycle. See [PLUGINS.md](PLUGINS.md).

Agents are necessary because the device they govern can't be reached from the homelab. Plugins are the natural model when the thing being controlled *can* be reached. Same job — make the world reflect curfew's lock state — different physics.
