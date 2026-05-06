# curfew-agent-sdk (PowerShell)

PowerShell module for the Windows agent (and any future Windows agent), per `docs/PLAN.md` §"Repository layout" and ADR-014.

This directory is **not** a Python package — it's a PowerShell module. PowerShell module code (`*.psm1`, `*.psd1`) lands here when the SDK ships. Imported on-device as `Import-Module curfew-agent-sdk`. It is not a uv workspace member and is not part of `pyproject.toml`.

The Python sibling lives at `agents/sdk-python/`; both SDKs implement the same agent contract for their respective on-device languages.
