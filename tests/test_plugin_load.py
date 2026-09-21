"""Load __init__.py the way Hermes loads a hyphenated plugin directory."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_register_loads_from_hyphenated_plugin_dir(tmp_path: Path):
    plugin_dir = tmp_path / "proton-pass"
    plugin_dir.mkdir()
    for name in ("__init__.py", "protonpass.py", "plugin.yaml"):
        (plugin_dir / name).write_text((ROOT / name).read_text())

    script = textwrap.dedent(
        f"""
        import importlib.util
        import sys
        import types
        from pathlib import Path

        plugin_dir = Path({str(plugin_dir)!r})
        init_file = plugin_dir / "__init__.py"
        ns = "hermes_plugins"
        ns_pkg = types.ModuleType(ns)
        ns_pkg.__path__ = []
        ns_pkg.__package__ = ns
        sys.modules[ns] = ns_pkg
        module_name = ns + ".proton_pass"
        spec = importlib.util.spec_from_file_location(
            module_name,
            init_file,
            submodule_search_locations=[str(plugin_dir)],
        )
        module = importlib.util.module_from_spec(spec)
        module.__package__ = module_name
        module.__path__ = [str(plugin_dir)]
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        assert callable(module.register)
        recorded = []
        class Ctx:
            def register_secret_source(self, source):
                recorded.append(source)
        module.register(Ctx())
        assert len(recorded) == 1
        assert recorded[0].__class__.__name__ == "ProtonPassSource"
        assert recorded[0].name == "protonpass"
        """
    )
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT / "tests" / "stubs"),
        "PYTHONNOUSERSITE": "1",
    }
    proc = subprocess.run(
        [sys.executable, "-S", "-c", script],
        cwd=str(plugin_dir.parent),
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
