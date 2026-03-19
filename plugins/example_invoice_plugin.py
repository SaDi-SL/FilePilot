from pathlib import Path

PLUGIN_NAME = "Invoice Detector"
PLUGIN_VERSION = "1.0.0"
PLUGIN_DESCRIPTION = "Detects invoice-like document files by filename."


def process(file_path: Path, context: dict) -> str | None:
    name = file_path.name.lower()
    suffix = file_path.suffix.lower()

    if suffix in [".pdf", ".doc", ".docx"]:
        if "invoice" in name or "bill" in name or "receipt" in name:
            return "invoices"

    return None