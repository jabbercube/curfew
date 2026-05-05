# curfew

A modular, extensible tool for managing screentime across the household. 

## Tech Stack

A small dockerized core service exposes an HTTP API. Two extension surfaces enforce policy at different layers — **agents** that run on a managed device (Windows PCs, Macs) and **plugins** that run in-core (DNS sinkhole, smart plugs, router ACLs, and more).

## Plan

In the planning stage.

- [docs/PLAN.md](docs/PLAN.md) — the architecture (kernel + features).
- [docs/DECISIONS.md](docs/DECISIONS.md) — design decisions and rejected alternatives (ADRs).
- [docs/AGENTS.md](docs/AGENTS.md) — how on-device agents work and how to write one.
- [docs/PLUGINS.md](docs/PLUGINS.md) — how in-core plugins work and how to write one.
