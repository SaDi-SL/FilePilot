from pathlib import Path

PLUGIN_NAME = "Image Sorter"
PLUGIN_VERSION = "1.0.0"
PLUGIN_DESCRIPTION = "Describe what this plugin does."


def process(file_path: Path, context: dict) -> str | None:
    """
    file_path: path of the file being processed
    context: additional info from FilePilot
    return category name or None
    """

    name = file_path.name.lower()
    suffix = file_path.suffix.lower()

    # Example rule
    if suffix == ".txt" and "note" in name:
        return "notes"

    return None
    