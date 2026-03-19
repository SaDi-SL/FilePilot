import importlib.util
import logging
import sys
from pathlib import Path
from typing import Optional


class PluginManager:
    def __init__(self, plugins_dir: Path):
        self.plugins_dir = plugins_dir
        self.plugins: list[dict] = []
        self.failed_plugins: list[dict] = []

    def load_plugins(self) -> None:
        self.plugins = []
        self.failed_plugins = []

        if not self.plugins_dir.exists():
            self.plugins_dir.mkdir(parents=True, exist_ok=True)
            return

        for plugin_file in self.plugins_dir.glob("*.py"):
            if plugin_file.name == "__init__.py":
                continue

            plugin = self._load_single_plugin(plugin_file)
            if plugin is not None:
                self.plugins.append(plugin)

        logging.info(
            f"Plugins loaded: {len(self.plugins)} succeeded, {len(self.failed_plugins)} failed"
        )

    def _load_single_plugin(self, plugin_file: Path) -> Optional[dict]:
        try:
            module_name = f"plugin_{plugin_file.stem}"

            spec = importlib.util.spec_from_file_location(module_name, plugin_file)
            if spec is None or spec.loader is None:
                self.failed_plugins.append({
                    "file": plugin_file.name,
                    "reason": "Invalid import spec",
                })
                return None

            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)

            process_func = getattr(module, "process", None)
            plugin_name = getattr(module, "PLUGIN_NAME", plugin_file.stem)
            plugin_version = getattr(module, "PLUGIN_VERSION", "unknown")
            plugin_description = getattr(module, "PLUGIN_DESCRIPTION", "")

            if not callable(process_func):
                self.failed_plugins.append({
                    "file": plugin_file.name,
                    "reason": "Missing callable process()",
                })
                return None

            logging.info(f"Plugin loaded: {plugin_name} v{plugin_version}")

            return {
                "name": plugin_name,
                "version": plugin_version,
                "description": plugin_description,
                "process": process_func,
                "path": str(plugin_file),
                "status": "loaded",
            }

        except Exception as error:
            self.failed_plugins.append({
                "file": plugin_file.name,
                "reason": str(error),
            })
            logging.error(f"Failed to load plugin {plugin_file.name}: {error}")
            return None

    def classify_with_plugins(self, file_path: Path, context: dict) -> Optional[str]:
        for plugin in self.plugins:
            try:
                result = plugin["process"](file_path, context)
                if isinstance(result, str) and result.strip():
                    return result.strip()
            except Exception as error:
                logging.error(
                    f"Plugin '{plugin['name']}' raised an error during classification: {error}"
                )
        return None

    def get_plugin_names(self) -> list[str]:
        return [plugin["name"] for plugin in self.plugins]

    def get_plugins_info(self) -> list[dict]:
        return [
            {
                "name": plugin["name"],
                "version": plugin["version"],
                "description": plugin["description"],
                "path": plugin["path"],
                "status": plugin["status"],
            }
            for plugin in self.plugins
        ]

    def get_failed_plugins(self) -> list[dict]:
        return self.failed_plugins
