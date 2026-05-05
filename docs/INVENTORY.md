# Inventory data model

What curfew tracks about people and devices, and why each value matters. This is the conceptual data model — it doesn't dictate file format (YAML, JSON, database table, etc.). Serialization choices come later.

## People

A person is anyone whose screentime curfew might govern (kids) or who manages it (parents). Schedules and budgets attach to **people**, not devices — a kid with one PC and one tablet shares one daily budget across both.

| Field | Why it matters |
|-------|----------------|
| `name` | Stable identifier used in the CLI, API, and UI. Short string (e.g. `kid1`), not a full real name. |
| `role` | `parent` or `child`. Distinguishes who governs from who is governed; future auth/RBAC keys off this. |

## Devices

A device is a physical thing curfew (or one of its plugins) can act on.

| Field | Why it matters |
|-------|----------------|
| `name` | Human-friendly identifier (e.g. `gamingrig`). Used in CLI/UI and as part of plugin instance names like `windows-pc:gamingrig`. |
| `owner` | *(optional)* The person whose screentime this device counts toward. Links the device to a user so schedule/budget logic knows whose budget activity here debits. **A device without an `owner` is treated as a *shared* device** — common-area TVs, family tablets, the playroom Switch — and is governed as a group rather than per-person. The CLI/API supports `--shared` operations (e.g. `curfew lock --shared`) to block or unblock all shared devices in one call. |
| `type` | Category — `pc`, `laptop`, `phone`, `tablet`, `console`, `tv`. Informs which plugins are applicable; a phone never gets `windows-pc`. |
| `os` | `windows`, `macos`, `linux`, `ios`, `android`. Selects the per-device plugin variant when one exists. |
| `mac` | One or more MAC addresses. Network-layer identity, stable across IP changes; needed by network-side plugins (DNS sinkhole, router ACL). A device commonly has multiple MACs — laptops with Wi-Fi *and* ethernet, phones/tablets that randomize MAC per network. All listed MACs are treated as identifying the same device. |
| `ip` | One or more IP addresses. *(optional)* Secondary network identity. Less stable than MAC, but some plugins can only see IP. |
| `managed` | Whether curfew governs this device at all. Lets the inventory list parents' devices for completeness without putting them under policy. |
| `plugins` | Which plugins are responsible for this device. Explicit because most devices use only one or two. A kid PC might have `[windows-pc, adguard]`; a phone might only have `[adguard]`. |
| `overrides` | Per-device deviations from the global app catalog. Reality varies — Steam at `C:\Program Files (x86)\Steam` on one PC, `D:\Games\Steam` on another. Keeps the global app definition clean while handling exceptions. |

## Why people *and* devices

Could be collapsed to "users with a list of devices," but separating them carries weight:

- **Schedules and budgets live on people**, not devices. One kid, two devices, one budget.
- **Plugin coverage is per-device** — DNS for the phone, OS-level lock for the PC. Different plugins, same person.
- A device may change hands (hand-me-down PC) and its `owner` field updates without rewriting policy.

## Out of scope

Conceptually adjacent fields that don't belong here:

- **VLAN / network segmentation** — a network concern, not a screentime one.
- **Static-vs-dynamic IP** — irrelevant; we key on MAC.
- **Hardware specs, purchase dates, warranty** — asset-management territory.
