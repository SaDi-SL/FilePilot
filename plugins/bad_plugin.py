PLUGIN_NAME = "Broken Plugin"
PLUGIN_VERSION = "1.0.0"
PLUGIN_DESCRIPTION = "This plugin is intentionally broken."


def not_process(file_path, context):
    return "broken_category"