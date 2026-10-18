"""Internal, explicit package check; exercises the installed extraction pipeline."""
from __future__ import annotations

import json
from pathlib import Path


def check_ocr(source: str, output: str) -> int:
    from app.content_reader import extract_file_content_result
    from app.ocr_runtime import discover_ocr_runtime

    source_path = Path(source).resolve()
    output_path = Path(output).resolve()
    if source_path == output_path:
        return 1
    runtime = discover_ocr_runtime()
    result = extract_file_content_result(source_path, lowercase=False, max_pdf_pages=1)
    report = {'status': result.status, 'method': result.method, 'text': result.text,
              'runtime_source': runtime.source, 'languages': list(runtime.languages)}
    # Never overwrite a document or an existing report.
    with output_path.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    return 0 if result.status == 'extracted' else 1
