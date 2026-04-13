"""Standalone smoke test for the current stage1 -> stage5 pipeline block.

This script is intentionally simple:
- it uses a real local PDF for stage 1
- it uses the real prompt/parsing code for stage 2
- it mocks the LLM JSON answer so Ollama is not required
- it writes the stage 3 artifacts to a dedicated test folder
- it builds stage 4 event-log rows
- it checks that the stage 5 API entry point is alive
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent
SRC_DIR = ROOT_DIR / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from clinical import ClinicalEvent, ClinicalEventType, Diagnosis
from config import get_config
from stage1_pdf_reading import classify_document_family, extract_text_from_pdf
from stage2_llm_runtime import build_document_prompt, parse_json_payload
from stage3_database import PLANNED_TABLES, artifact_paths_for_document
from stage4_mining import build_event_log_rows, filter_events_by_type
from stage5_interface.api import build_app


DEFAULT_PDF = Path(
    "data/raw/person001/Documento_sanitario_LMNLCU02E15D918M_20260303224554.pdf"
)

MOCK_LLM_RESPONSE = """```json
{
  "patient_id": "person001",
  "document_family": "clinical_document",
  "diagnosis": {
    "label": "Frattura del piede",
    "diagnosed_at": "2026-04-10"
  }
}
```"""


def _to_repo_relative(path: Path) -> str:
    """Render a path relative to the repository when possible."""
    try:
        return str(path.relative_to(ROOT_DIR))
    except ValueError:
        return str(path)


def _resolve_pdf_path(raw_path: str | None) -> Path:
    """Resolve the user-selected PDF path or fall back to the bundled sample."""
    candidate = Path(raw_path) if raw_path is not None else DEFAULT_PDF
    if not candidate.is_absolute():
        candidate = ROOT_DIR / candidate
    return candidate.resolve()


def _write_stage3_artifacts(
    extracted_text: str,
    parsed_payload: dict[str, object],
    pdf_path: Path,
) -> dict[str, Path]:
    """Persist the minimal artifacts produced by this smoke test."""
    config = get_config()
    paths = artifact_paths_for_document(
        config.artifacts_dir / "standalone_block_test",
        pdf_path,
    )
    paths["document_dir"].mkdir(parents=True, exist_ok=True)
    paths["extracted_text"].write_text(extracted_text, encoding="utf-8")
    paths["llm_response"].write_text(
        json.dumps(parsed_payload, indent=2),
        encoding="utf-8",
    )
    return paths


def _stage5_status_payload() -> dict[str, str]:
    """Call the stage 5 root endpoint function without starting a server."""
    app = build_app()
    root_route = next(route for route in app.routes if getattr(route, "path", None) == "/")
    payload = root_route.endpoint()
    payload["app_title"] = app.title
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a minimal end-to-end smoke test for stage1 -> stage5.",
    )
    parser.add_argument(
        "--pdf",
        default=None,
        help=(
            "Optional PDF path. Default: the bundled sample clinical PDF under "
            "data/raw/person001/."
        ),
    )
    args = parser.parse_args()

    pdf_path = _resolve_pdf_path(args.pdf)
    if not pdf_path.exists():
        raise SystemExit(f"PDF not found: {pdf_path}")

    family = classify_document_family(pdf_path)
    extraction = extract_text_from_pdf(pdf_path)
    if extraction.character_count == 0:
        raise SystemExit(
            "Stage 1 extracted 0 characters. Use a text-based PDF for this smoke test."
        )

    prompt = build_document_prompt(family, extraction.text)
    parsed_payload = parse_json_payload(MOCK_LLM_RESPONSE)

    diagnosis_payload = parsed_payload["diagnosis"]
    if not isinstance(diagnosis_payload, dict):
        raise SystemExit("Mock stage 2 payload is invalid: 'diagnosis' must be an object.")

    diagnosis = Diagnosis(
        label=str(diagnosis_payload["label"]),
        diagnosed_at=date.fromisoformat(str(diagnosis_payload["diagnosed_at"])),
        source_document=pdf_path,
    )
    diagnosis_event = ClinicalEvent(
        event_type=ClinicalEventType.DIAGNOSIS,
        label=diagnosis.label,
        occurred_at=diagnosis.diagnosed_at,
        source_document=diagnosis.source_document,
        diagnosis=diagnosis,
        tags=("standalone_smoke_test",),
    )

    artifact_paths = _write_stage3_artifacts(
        extracted_text=extraction.text,
        parsed_payload=parsed_payload,
        pdf_path=pdf_path,
    )

    filtered_events = filter_events_by_type(
        [diagnosis_event],
        ClinicalEventType.DIAGNOSIS,
    )
    event_log_rows = build_event_log_rows(
        patient_id=str(parsed_payload["patient_id"]),
        events=filtered_events,
        care_thread_id="demo-thread-1",
    )

    summary = {
        "status": "ok",
        "stage1": {
            "pdf": _to_repo_relative(pdf_path),
            "document_family": family.value,
            "page_count": extraction.page_count,
            "character_count": extraction.character_count,
        },
        "stage2": {
            "llm_mode": "mocked_json_payload",
            "prompt_character_count": len(prompt),
            "parsed_keys": sorted(parsed_payload.keys()),
        },
        "stage3": {
            "planned_table_count": len(PLANNED_TABLES),
            "artifact_dir": _to_repo_relative(artifact_paths["document_dir"]),
            "saved_files": {
                "extracted_text": _to_repo_relative(artifact_paths["extracted_text"]),
                "llm_response": _to_repo_relative(artifact_paths["llm_response"]),
            },
        },
        "stage4": {
            "filtered_event_count": len(filtered_events),
            "event_log_rows": [asdict(row) for row in event_log_rows],
        },
        "stage5": _stage5_status_payload(),
    }

    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
