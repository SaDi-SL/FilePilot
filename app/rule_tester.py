"""
rule_tester.py — Rule Testing Engine for FilePilot.

Given a filename, simulates the full classification pipeline and returns
a detailed explanation of which rule would apply and why.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.classifier import build_extension_lookup, get_file_category
from app.smart_classifier import load_smart_rules, normalize_text, keyword_match_score


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class TestStep:
    """One step in the classification decision chain."""
    method: str          # "plugin" | "smart_name" | "smart_content" | "extension" | "default"
    matched: bool
    category: str | None
    reason: str
    detail: str = ""     # extra info (matched keywords, extension, plugin name)


@dataclass
class RuleTestResult:
    """Full result returned by test_filename()."""
    filename: str
    final_category: str
    final_method: str
    steps: list[TestStep] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def verdict_color(self) -> str:
        """Returns a color key for the UI based on outcome."""
        if self.final_category == "others":
            return "stat_amber"
        if self.final_method == "plugin":
            return "stat_blue"
        if self.final_method in ("smart_name", "smart_content"):
            return "stat_green"
        return "stat_blue"


# ── Simulation helpers ────────────────────────────────────────────────────────

def _simulate_plugins(filename: str, plugin_manager) -> tuple[str | None, str]:
    """
    Run the filename through loaded plugins.
    Returns (category, plugin_name) or (None, "").
    """
    if plugin_manager is None:
        return None, ""
    path = Path(filename)
    context = {"rules": {}}
    for plugin in plugin_manager.plugins:
        try:
            result = plugin["process"](path, context)
            if isinstance(result, str) and result.strip():
                return result.strip(), plugin["name"]
        except Exception:
            pass
    return None, ""


def _simulate_smart_name(filename: str, smart_rules: dict) -> tuple[str | None, list[str]]:
    """
    Try to classify by filename keywords.
    Returns (category, matched_keywords) or (None, []).
    """
    name = normalize_text(Path(filename).stem)
    best_cat, best_score, best_kws = None, 0, []

    for category, keywords in smart_rules.items():
        matched = [kw for kw in keywords if normalize_text(kw) in name]
        score = len(matched)
        if score > best_score:
            best_score, best_cat, best_kws = score, category, matched

    return (best_cat, best_kws) if best_score > 0 else (None, [])


def _simulate_smart_content(filename: str, smart_rules: dict) -> tuple[str | None, list[str]]:
    """
    Simulate content-based classification (using filename as proxy since
    the actual file may not exist on disk during testing).
    Returns (category, matched_keywords) or (None, []).
    """
    # Use full filename (including extension) as content proxy
    text = normalize_text(filename.replace("_", " ").replace("-", " "))
    best_cat, best_score, best_kws = None, 0, []

    for category, keywords in smart_rules.items():
        matched = [kw for kw in keywords if normalize_text(kw) in text]
        score = len(matched)
        if score > best_score:
            best_score, best_cat, best_kws = score, category, matched

    return (best_cat, best_kws) if best_score > 0 else (None, [])


def _simulate_extension(filename: str, extension_lookup: dict) -> tuple[str, str]:
    """
    Classify by file extension.
    Returns (category, extension).
    """
    suffix = Path(filename).suffix.lower().strip()
    category = extension_lookup.get(suffix, "others")
    return category, suffix


# ── Main tester ───────────────────────────────────────────────────────────────

def test_filename(
    filename: str,
    config: dict,
    plugin_manager=None,
) -> RuleTestResult:
    """
    Simulate the full classification pipeline for a given filename.

    Pipeline order (same as watcher.py):
      1. Plugins
      2. Smart classifier (filename keywords)
      3. Smart classifier (content — simulated from filename)
      4. Extension lookup
      5. Default: "others"

    Returns a RuleTestResult with every decision step explained.
    """
    filename = filename.strip()
    result = RuleTestResult(filename=filename, final_category="others", final_method="default")

    if not filename:
        result.warnings.append("Filename is empty.")
        return result

    # Validate extension
    suffix = Path(filename).suffix
    if not suffix:
        result.warnings.append("No file extension detected — will fall back to smart or 'others'.")

    rules = config.get("rules", {})
    extension_lookup = build_extension_lookup(rules)
    smart_rules = load_smart_rules()

    # ── Step 1: Plugins ───────────────────────────────────────────────────────
    plugin_cat, plugin_name = _simulate_plugins(filename, plugin_manager)
    if plugin_cat:
        result.steps.append(TestStep(
            method="plugin",
            matched=True,
            category=plugin_cat,
            reason=f"Plugin '{plugin_name}' returned category '{plugin_cat}'.",
            detail=f"Plugin: {plugin_name}",
        ))
        result.final_category = plugin_cat
        result.final_method = "plugin"
        # Note remaining steps as skipped
        result.steps.append(TestStep("smart_name",    False, None, "Skipped — plugin matched first."))
        result.steps.append(TestStep("smart_content", False, None, "Skipped — plugin matched first."))
        result.steps.append(TestStep("extension",     False, None, "Skipped — plugin matched first."))
        return result
    else:
        loaded = len(plugin_manager.plugins) if plugin_manager else 0
        result.steps.append(TestStep(
            method="plugin",
            matched=False,
            category=None,
            reason=f"No plugin matched. ({loaded} plugin(s) checked)",
            detail="",
        ))

    # ── Step 2: Smart — filename keywords ─────────────────────────────────────
    smart_name_cat, smart_name_kws = _simulate_smart_name(filename, smart_rules)
    if smart_name_cat:
        kw_list = ", ".join(f'"{k}"' for k in smart_name_kws)
        result.steps.append(TestStep(
            method="smart_name",
            matched=True,
            category=smart_name_cat,
            reason=f"Filename contains smart keywords: {kw_list}",
            detail=f"Keywords matched: {kw_list}",
        ))
        result.final_category = smart_name_cat
        result.final_method = "smart_name"
        result.steps.append(TestStep("smart_content", False, None, "Skipped — smart name matched first."))
        result.steps.append(TestStep("extension",     False, None, "Skipped — smart name matched first."))
        return result
    else:
        result.steps.append(TestStep(
            method="smart_name",
            matched=False,
            category=None,
            reason=f"No smart keywords found in filename '{Path(filename).stem}'.",
        ))

    # ── Step 3: Smart — content (simulated) ───────────────────────────────────
    smart_content_cat, smart_content_kws = _simulate_smart_content(filename, smart_rules)
    if smart_content_cat and smart_content_cat != smart_name_cat:
        kw_list = ", ".join(f'"{k}"' for k in smart_content_kws)
        result.steps.append(TestStep(
            method="smart_content",
            matched=True,
            category=smart_content_cat,
            reason=f"Content scan matched keywords: {kw_list}",
            detail=f"Keywords: {kw_list} (simulated from filename)",
        ))
        result.final_category = smart_content_cat
        result.final_method = "smart_content"
        result.steps.append(TestStep("extension", False, None, "Skipped — smart content matched first."))
        return result
    else:
        result.steps.append(TestStep(
            method="smart_content",
            matched=False,
            category=None,
            reason="No content keywords matched (simulated from filename).",
        ))

    # ── Step 4: Extension lookup ──────────────────────────────────────────────
    ext_cat, ext = _simulate_extension(filename, extension_lookup)
    if ext_cat != "others":
        matched_rule = next(
            (cat for cat, exts in rules.items() if ext in [e.lower().strip() for e in exts]),
            ext_cat,
        )
        result.steps.append(TestStep(
            method="extension",
            matched=True,
            category=ext_cat,
            reason=f"Extension '{ext}' is mapped to category '{ext_cat}' in Rules.",
            detail=f"Rule: {matched_rule} → {ext}",
        ))
        result.final_category = ext_cat
        result.final_method = "extension"
        return result
    else:
        all_exts = list(extension_lookup.keys())
        result.steps.append(TestStep(
            method="extension",
            matched=False,
            category=None,
            reason=f"Extension '{ext}' not found in any rule." if ext else "No extension to match.",
            detail=f"{len(all_exts)} extensions defined across all rules.",
        ))

    # ── Step 5: Default ───────────────────────────────────────────────────────
    result.steps.append(TestStep(
        method="default",
        matched=True,
        category="others",
        reason="No rule matched — file will go to the 'others' folder.",
        detail="Add a rule or smart keyword to classify this file.",
    ))
    result.final_category = "others"
    result.final_method = "default"

    if not suffix:
        result.warnings.append(
            "Tip: Files without extensions always fall through to 'others' "
            "unless a plugin or smart rule matches."
        )

    return result


def format_result_text(result: RuleTestResult) -> str:
    """Format result as readable multi-line text for display."""
    lines = [
        f"File: {result.filename}",
        f"Result: {result.final_category.upper()}  (via {result.final_method})",
        "",
        "Decision chain:",
    ]
    method_labels = {
        "plugin":        "1. Plugin check",
        "smart_name":    "2. Smart keywords (name)",
        "smart_content": "3. Smart keywords (content)",
        "extension":     "4. Extension rule",
        "default":       "5. Default fallback",
    }
    for step in result.steps:
        label = method_labels.get(step.method, step.method)
        icon = "+" if step.matched else "-"
        lines.append(f"  [{icon}] {label}: {step.reason}")
        if step.detail and step.matched:
            lines.append(f"        {step.detail}")

    if result.warnings:
        lines.append("")
        for w in result.warnings:
            lines.append(f"  ! {w}")

    return "\n".join(lines)
