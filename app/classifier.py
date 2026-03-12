from pathlib import Path


def build_extension_lookup(rules: dict) -> dict:
    """
    تحويل قواعد الامتدادات إلى lookup مباشر:
    .pdf -> pdfs
    .docx -> documents
    """
    lookup = {}

    for category, extensions in rules.items():
        for ext in extensions:
            normalized_ext = ext.lower().strip()
            lookup[normalized_ext] = category

    return lookup


def get_file_category(file_path: Path, extension_lookup: dict) -> str:
    """تحديد الفئة من امتداد الملف."""
    suffix = file_path.suffix.lower().strip()
    return extension_lookup.get(suffix, "others")