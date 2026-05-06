import importlib


def test_packages_import() -> None:
    for name in ("curfew", "curfew_api", "curfew_cli", "curfew_agent_sdk_python"):
        importlib.import_module(name)
