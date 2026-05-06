# curfew-agent-sdk-powershell

PowerShell module for the Windows agent (and any future Windows agent), per `docs/PLAN.md` §"Repository layout".

This directory is **not** a Python package despite living under `src/`. PowerShell module code lands here when the SDK ships. The hyphenated name (`curfew-agent-sdk-powershell` vs the Python SDK's underscore-named `curfew_agent_sdk_python`) follows PowerShell module naming conventions.

It is excluded from `pyproject.toml`'s wheel packages and from ruff scanning.
