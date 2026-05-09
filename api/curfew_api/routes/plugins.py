"""Plugin discovery + (step 11) assignment endpoints.

This slice (step 10) ships only ``GET /v1/plugins/types`` — what plugin
types curfew-core discovered at startup. The list reflects the in-memory
registry built by ``discover_plugins`` (PLUGINS.md §"What curfew-core does
at startup"), so failed plugins show up here with their error.

Step 11 will add ``GET/POST/PATCH/DELETE /v1/plugins`` (assignment CRUD)
and the in-memory instance manager that talks to the same registry.
"""

from __future__ import annotations

from curfew.plugin_loader import PluginRegistry, PluginType
from curfew.schemas import PluginTypeRead
from fastapi import APIRouter, Request

from curfew_api.auth import Operator

router = APIRouter(prefix="/v1/plugins", tags=["plugins"])


def _to_read(ptype: PluginType) -> PluginTypeRead:
    m = ptype.manifest
    return PluginTypeRead(
        type=ptype.type,
        name=m.name if m else None,
        version=m.version if m else None,
        description=m.description if m else None,
        config_schema=m.config_schema if m else None,
        error=ptype.error,
        has_requirements_txt=ptype.has_requirements_txt,
    )


@router.get("/types", response_model=list[PluginTypeRead])
def list_plugin_types(request: Request, actor: Operator) -> list[PluginTypeRead]:
    """List discovered plugin types from ``CURFEW_PLUGINS_DIRS``.

    Reads from the registry built once at startup. Adding or removing a
    plugin folder requires a curfew-core restart (per ADR-013); this
    endpoint just reports what was discovered.
    """
    registry: PluginRegistry = request.app.state.plugin_registry
    return [_to_read(p) for p in registry.all()]
