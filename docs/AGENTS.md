# Agents — explained

Companion to [PLAN.md](PLAN.md), [DECISIONS.md](DECISIONS.md), and [PLUGINS.md](PLUGINS.md). Read this when you want to understand or write **agents** — the on-device extension surface.

For in-core extensions (Python modules that run inside curfew-core itself), see [PLUGINS.md](PLUGINS.md). They're a different thing.

## What an agent is

curfew-core is a small service that holds state — who's locked, who owns what, what apps to block. To actually *enforce* anything on a Windows PC or a Mac, code has to physically run on that device, with OS-level privileges. NTFS file ACLs can only be set from Windows; macOS sandbox profiles can only be applied from macOS.

An **agent** is that code: a small program that runs on a managed device, polls curfew-core on a timer, fetches the current lock status for the user it governs, and reconciles the device's state to match.

Agents exist because the alternative — curfew-core reaching out to the device — doesn't work. Kid PCs are behind home NAT, sleep half the time, and run on residential connections that don't accept inbound traffic. The agent has to initiate every conversation.

## One agent per device

Each managed device has at most one agent installed. The agent's *type* identifies what it knows how to do (`windows-agent`, `macos-agent`, etc.) — the type is implementation, not configuration. A device runs one agent because:

- The realistic failure modes (PC off, network down, agent crashed, OS rebooting) all knock out *every* enforcement on that device simultaneously. Per-thing-it-enforces heartbeats would give you N independent signals for one underlying signal.
- Tokens are per-device — revocation is "this PC is compromised, cut it off." That's the actual revocation case anyone has.
- Multiple things to enforce on the same device (block exes + apply registry policy + kill processes) live inside the one agent's reconcile logic.

## Agent code is first-party only

Unlike plugins, agents don't have a drop-in third-party extension model. Adding new behaviour to an agent means extending the agent's source — either contributing upstream or maintaining a fork. There is no `agent_plugins/` directory or equivalent.

If you want to add new on-device enforcement (e.g. a screen-time recorder for Windows alongside the existing app blocking), the realistic path is:

1. Open a PR against the windows-agent agent to add the capability natively, or
2. Maintain a fork of the agent with your additions.

This is a deliberate trade-off. Per-device extension would mean either multiple agents per device (which we explicitly rejected — different failure-mode signals for the same underlying "is this device reachable" question) or a sub-plugin system inside the agent (real complexity for a use case that doesn't yet exist). The bring-your-own story for curfew lives on the plugin side, where it's a good fit.

## Lifecycle: what installation actually looks like

Concrete walkthrough for `windows-agent` on a PC named `gamingrig`, owned by user `kid1`:

### 1. Operator runs `curfew agent install`

```
curfew agent install windows-agent gamingrig --config '{"windows_user": "Kid1Local"}'
```

This:
- Inserts a row into `agents(device=gamingrig, type=windows-agent, config=...)` with `last_heartbeat` and `last_seen_version` NULL (no heartbeats yet).
- Mints a device-scoped bearer token, hashes it, inserts into `agent_tokens`.
- Prints `{token: { id, secret }, bootstrap: "..."}`. The secret is shown once.

### 2. Operator runs the bootstrap one-liner on `gamingrig`

The CLI printed something like:

```powershell
iex (irm https://curfew.homelab.local/bootstrap/windows-agent?token=SECRET)
```

That command:
- Fetches `GET /v1/agents/windows-agent/manifest` → `{ version, sha256, url }`.
- Downloads the agent artifact (a PowerShell script + the `curfew-agent-sdk-powershell` module bundled together) from `url`.
- Verifies the SHA-256 matches what the manifest said.
- Installs the files locally, drops `agent.config` (API URL, device name, bearer secret).
- Registers a Windows Scheduled Task that runs the agent every minute.

### 3. The agent runs every minute

Each tick:
- Compute its current state hash (cached from last tick).
- POST `/v1/devices/gamingrig/heartbeat` with `{state_hash, agent_version}`.
- Receive `{state_hash, agent_tick_seconds, ...}` back.
- If hashes match: nothing changed. Maybe report any local errors via the next heartbeat. Wait for next tick.
- If hashes differ: GET `/v1/devices/gamingrig/state` for the full picture (lock status, agent config, target apps, app catalog entries). Update local cache. Run the reconciler.
- Reconciler given `{locked: true}` + target_apps: apply NTFS deny-execute on the configured exe paths, set `URLBlocklist` registry policy, kill matching running processes.
- Reconciler given `{locked: false}`: undo all of the above.

Separately, on a slower tick (default hourly):
- GET `/v1/agents/windows-agent/manifest`. If `version` differs from installed, fetch `url`, verify hash, swap atomically.

### 4. Operator removes the agent

```
curfew agent uninstall gamingrig
```

This:
- Deletes the `agents` row.
- Sets `revoked_at` on outstanding tokens.
- Next heartbeat from `gamingrig` returns 401. The agent SDK treats that as "I've been disowned" and stops trying.
- Operator removes the local install (uninstall script, or just delete the scheduled task).

## What the agent author actually writes

Most of the loop above is in the SDK (`curfew_agent_sdk_python` for Linux/macOS, `curfew-agent-sdk-powershell` for Windows). The author writes a **reconciler** — given the current lock status and config, do the right thing. Sketch:

```powershell
# windows-agent/agent.ps1
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

### Last-seen and the limits of inference

`GET /v1/agents` includes each agent's `last_heartbeat` timestamp (or `null` if it has never reported in — assigned but not bootstrapped). The kernel doesn't interpret old timestamps as an alert: a powered-off PC has the same signature as a broken agent. Without independent evidence that the device is on, "no recent heartbeat" could mean either thing, and alerting on it produces noise.

The cases that genuinely matter:

- **Device off** — fine. Nothing to enforce, nothing to alert on.
- **Device on, agent running, agent can't reach the core** — handled agent-side by **auto-lock on disconnect** (a feature; see PLAN.md). The agent tracks "time since last successful fetch" and after `failclosed_after_seconds` invokes its reconciler with `{locked: true, reasons: [{kind: "failclosed"}]}`. The device stays locked even when offline from curfew's perspective.
- **Device on, agent broken or absent** — the actual enforcement-bypass case. To detect this, the core needs to know the device is on independently of the heartbeat. That's the **reachability monitoring** feature (also in PLAN.md): probe each device's `mac` or IP on a slow tick, alert when a reachable device hasn't heartbeated.

Until reachability monitoring lands, the operator inspects `GET /v1/agents` manually and uses last-seen as a hint, not an alert.

### Token compromise

If a device's token leaks, the operator runs `curfew agent token revoke gamingrig {token_id}` and the next heartbeat from that token returns 401. Mint a new token via `curfew agent token mint gamingrig` and rebootstrap.

### Agent code compromise

The bootstrap and update path are integrity-checked: `GET /v1/agents/{type}/manifest` returns a SHA-256, the agent verifies the artifact against it before running anything. If the curfew-core image itself is compromised, an attacker could publish a malicious manifest — manifest signing (a key on the core; public key embedded in the bootstrap) is a future tightening.

## How to write a new agent

Suppose you want a `linux-agent` agent. Steps:

1. **Pick the SDK flavour.** Linux → `curfew_agent_sdk_python`. (Windows would use the PowerShell SDK; for anything else, Python.)
2. **Write the reconciler.** Subclass the SDK's agent class, implement `reconcile(state)`, do whatever Linux-specific blocking/killing you want (iptables to localhost, kill processes, deny execute via setfacl, etc.).
3. **Build a bootstrap installer** — a small shell script that the operator runs on the target machine. It fetches the manifest, verifies hash, installs, registers a systemd timer (or cron, or whatever).
4. **Publish a version.** `curfew agent publish linux-agent 1.0.0 ./linux-agent.tar` ships the artifact + hash to curfew-core. From now on, `curfew agent install linux-agent <device>` will work.
5. **Test it** against `reftest_agent`'s patterns — drop assignment, heartbeat appears, `last_heartbeat` advances on each tick, lock toggles propagate within a tick.

The agent doesn't need to know about plugins or the rule pipeline. It only sees `{locked, reasons, target_apps, config}` and the reconciler dispatches.

## Why agents and plugins are separate

The other extension surface — **plugins** — runs *inside* curfew-core's process. AdGuard, smart plugs, router ACLs: code that lives in the homelab and reaches out to a service over HTTP. For those, polling and heartbeats would be wasted work; the core can call them directly when state changes. They're a different shape with a different lifecycle. See [PLUGINS.md](PLUGINS.md).

Agents are necessary because the device they govern can't be reached from the homelab. Plugins are the natural model when the thing being controlled *can* be reached. Same job — make the world reflect curfew's lock state — different physics.
