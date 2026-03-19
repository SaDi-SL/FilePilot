import json
import threading
from pathlib import Path

from app.config_loader import get_smart_rules_path
from app.content_reader import extract_file_content


DEFAULT_SMART_RULES = {
    "invoices": ["invoice", "bill", "receipt", "payment", "tax_invoice"],
    "resumes": ["resume", "cv", "curriculum_vitae", "job_application"],
    "contracts": ["contract", "agreement", "nda", "terms"],
    "reports": ["report", "summary", "analysis", "findings"],
    "presentations": ["presentation", "slides", "deck", "pitch"],
    "notes": ["note", "notes", "memo", "journal", "meeting notes"],
    "datasets": ["dataset", "data", "records", "table", "export"],
}

# ---- Module-level cache ----
# Rules do not change during file processing, so load them once.
# save_smart_rules() automatically invalidates the cache on save.

_rules_cache: dict | None = None
_rules_lock = threading.Lock()


def _invalidate_cache() -> None:
    global _rules_cache
    with _rules_lock:
        _rules_cache = None


def ensure_smart_rules_file() -> Path:
    smart_rules_path = get_smart_rules_path()
    smart_rules_path.parent.mkdir(parents=True, exist_ok=True)

    if not smart_rules_path.exists():
        with open(smart_rules_path, "w", encoding="utf-8") as file:
            json.dump(DEFAULT_SMART_RULES, file, indent=2, ensure_ascii=False)

    return smart_rules_path


def load_smart_rules() -> dict:
    global _rules_cache
    with _rules_lock:
        if _rules_cache is not None:
            return _rules_cache

        smart_rules_path = ensure_smart_rules_file()
        try:
            with open(smart_rules_path, "r", encoding="utf-8") as file:
                data = json.load(file)
            if isinstance(data, dict):
                _rules_cache = data
                return _rules_cache
        except Exception:
            pass

        _rules_cache = DEFAULT_SMART_RULES.copy()
        return _rules_cache


def save_smart_rules(rules: dict) -> None:
    smart_rules_path = ensure_smart_rules_file()
    with open(smart_rules_path, "w", encoding="utf-8") as file:
        json.dump(rules, file, indent=2, ensure_ascii=False)
    # Invalidate cache so new rules are loaded on next processing
    _invalidate_cache()


def normalize_text(text: str) -> str:
    return text.lower().strip()


def keyword_match_score(text: str, keywords: list[str]) -> int:
    score = 0
    normalized_text = normalize_text(text)
    for keyword in keywords:
        normalized_keyword = normalize_text(keyword)
        if normalized_keyword and normalized_keyword in normalized_text:
            score += 1
    return score


def classify_by_filename(file_path: Path, smart_rules: dict) -> str | None:
    name = normalize_text(file_path.stem)
    best_category = None
    best_score = 0
    for category, keywords in smart_rules.items():
        score = keyword_match_score(name, keywords)
        if score > best_score:
            best_score = score
            best_category = category
    return best_category if best_score > 0 else None


def classify_by_content(file_path: Path, smart_rules: dict) -> str | None:
    content = extract_file_content(file_path)
    if not content:
        return None
    best_category = None
    best_score = 0
    for category, keywords in smart_rules.items():
        score = keyword_match_score(content, keywords)
        if score > best_score:
            best_score = score
            best_category = category
    return best_category if best_score > 0 else None


def smart_classify(file_path: Path) -> str | None:
    """
    Smart classification based on:
    1) File name
    2) File content (if name yields no match)
    Rules are loaded from in-memory cache.
    """
    smart_rules = load_smart_rules()
    return (
        classify_by_filename(file_path, smart_rules)
        or classify_by_content(file_path, smart_rules)
    )
