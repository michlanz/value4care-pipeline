"""Runner standalone per testare solo lo stage 1 della pipeline Value4Care.

Questo file si ferma prima del modello.
Stage 1 qui significa:
- estrazione testo
- classificazione e primo tagging
- estrazione metadati base
- interpretazione del documento
- costruzione del primo prompt da passare a stage 2

I parser dedicati restano confinati qui finche non validiamo il comportamento.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import pdfplumber

ROOT_DIR = Path(__file__).resolve().parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from clinical import DocumentFamily
from stage1_pdf_reading import classify_document_family, extract_text_from_pdf
from stage2_llm_runtime import build_document_prompt

DEFAULT_PDF = (
    ROOT_DIR
    / "data"
    / "raw"
    / "person001"
    / "CertificatoVaccinale_LMNLCU02E15D918M_20260303104746.pdf"
)
DEFAULT_RAW_ROOT = ROOT_DIR / "data" / "raw"
DEFAULT_ARTIFACTS_ROOT = ROOT_DIR / "artifacts"
VACCINATION_GLOB = "CertificatoVaccinale*.pdf"
DATE_RE = re.compile(r"\b\d{2}[/-]\d{2}[/-]\d{2,4}\b")
TAX_CODE_RE = re.compile(r"\b[A-Z]{6}\d{2}[A-Z]\d{2}[A-Z]\d{3}[A-Z]\b")
UPPER_NAME_RE = re.compile(r"^[A-Z' ]{3,}$")
DOSE_DATE_RE = re.compile(r"(?P<dose>\d+)\s*-\s*(?P<date>\d{2}[/-]\d{2}[/-]\d{2,4})")
FOOTER_PREFIXES = (
    "ATS DI ",
    "VIA DUCA ",
    "P.IVA:",
    "Documento firmato",
    "I dati presenti",
)
COMMON_NOISE_PREFIXES = (
    "www.",
    "customerservice.",
    "pagina ",
    "salva fascicolo in pdf",
)
STREET_PREFIX_NORMALIZATION = {
    "V": "VIA",
    "V.": "VIA",
    "VIA": "VIA",
    "PZA": "PIAZZA",
    "P.ZA": "PIAZZA",
    "PZZA": "PIAZZA",
    "PIAZZA": "PIAZZA",
    "C.SO": "CORSO",
    "CORSO": "CORSO",
    "VLE": "VIALE",
    "V.LE": "VIALE",
    "VIALE": "VIALE",
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Test standalone di stage 1.")
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF, help="PDF da processare.")
    parser.add_argument(
        "--person",
        type=str,
        help=(
            "Processa il vaccino di una persona cercando automaticamente "
            "l'unico file che inizia con 'CertificatoVaccinale' nella cartella data/raw/<person>/."
        ),
    )
    parser.add_argument(
        "--vaccini-all",
        action="store_true",
        help=(
            "Cicla su tutte le cartelle person* sotto data/raw e cerca in ciascuna "
            "l'unico CertificatoVaccinale*.pdf."
        ),
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Abilita artefatti di debug aggiuntivi anche per documenti non vaccinali.",
    )
    parser.add_argument(
        "--raw-root",
        type=Path,
        default=DEFAULT_RAW_ROOT,
        help="Root dei documenti grezzi organizzati per persona.",
    )
    parser.add_argument(
        "--artifacts-root",
        type=Path,
        default=DEFAULT_ARTIFACTS_ROOT,
        help="Directory root degli artefatti per paziente/documento.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Riesegue stage1 anche se gli artefatti standard esistono gia.",
    )
    return parser


def _artifact_paths(document_root: Path) -> dict[str, Path]:
    return {
        "document_dir": document_root,
        "extracted_text": document_root / "extracted_text.txt",
        "interpreted_text": document_root / "interpreted_text.txt",
        "interpreted_json": document_root / "interpreted_text.json",
        "prompt_main": document_root / "prompt_main.txt",
        "layout_text": document_root / "layout_text.txt",
        "layout_words": document_root / "layout_words.json",
        "reader_text": document_root / "reader_text.txt",
        "reader_json": document_root / "reader.json",
    }


def _resolve_document_artifact_dir(pdf_path: Path, artifacts_root: Path) -> Path:
    person_id = pdf_path.parent.name if pdf_path.parent.name.startswith("person") else "person_unknown"
    document_id = pdf_path.stem
    return artifacts_root / person_id / document_id


def _find_unique_vaccination_pdf(person_dir: Path) -> Path | None:
    matches = sorted(path for path in person_dir.glob(VACCINATION_GLOB) if path.is_file())
    if len(matches) == 1:
        return matches[0]
    return None


def _discover_vaccination_pdfs(raw_root: Path) -> tuple[list[Path], list[str]]:
    pdfs: list[Path] = []
    warnings: list[str] = []
    for person_dir in sorted(path for path in raw_root.iterdir() if path.is_dir() and path.name.startswith("person")):
        matches = sorted(path for path in person_dir.glob(VACCINATION_GLOB) if path.is_file())
        if len(matches) == 1:
            pdfs.append(matches[0])
            continue
        if not matches:
            warnings.append(f"{person_dir.name}: nessun {VACCINATION_GLOB} trovato")
        else:
            warnings.append(
                f"{person_dir.name}: trovati {len(matches)} file {VACCINATION_GLOB}, serve un solo certificato vaccinale"
            )
    return pdfs, warnings


def _has_stage1_outputs(document_root: Path) -> bool:
    standard_paths = _artifact_paths(document_root)
    required = (
        standard_paths["extracted_text"],
        standard_paths["interpreted_text"],
        standard_paths["interpreted_json"],
        standard_paths["prompt_main"],
    )
    return all(path.exists() for path in required)


def _extract_filename_snapshot_date(pdf_path: Path) -> str | None:
    match = re.search(r"_(\d{8})(?:\d{6})?(?:_|\.pdf$)", pdf_path.name)
    if not match:
        return None
    raw = match.group(1)
    return f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}"


def _normalize_street_prefix(address: str) -> str:
    cleaned = re.sub(r"\s+", " ", address).strip(" -")
    if not cleaned:
        return cleaned
    parts = cleaned.split(" ", 1)
    prefix = parts[0].upper()
    normalized_prefix = STREET_PREFIX_NORMALIZATION.get(prefix, prefix)
    if len(parts) == 1:
        return normalized_prefix
    return f"{normalized_prefix} {parts[1]}".strip()


def _normalize_date(raw: str) -> str:
    parts = re.split(r"[/-]", raw)
    if len(parts) != 3:
        return raw
    day, month, year = parts
    if len(year) == 2:
        year = f"20{year}" if int(year) <= 30 else f"19{year}"
    return f"{year}-{month}-{day}"


def _dedupe_preserve_order(items: list[Any]) -> list[Any]:
    seen: set[str] = set()
    output: list[Any] = []
    for item in items:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True) if isinstance(item, (dict, list)) else str(item)
        if key in seen:
            continue
        seen.add(key)
        output.append(item)
    return output


def _words_to_text(words: list[dict[str, Any]]) -> str:
    return " ".join(str(word["text"]) for word in words).strip()


def _group_words_into_lines(
    words: list[dict[str, Any]],
    line_tolerance: float = 4.0,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for word in sorted(words, key=lambda item: (float(item["top"]), float(item["x0"]))):
        top = float(word["top"])
        if not rows or abs(top - rows[-1]["top"]) > line_tolerance:
            rows.append({"top": top, "words": [word]})
        else:
            rows[-1]["words"].append(word)
    return rows


def _group_line_words_into_blocks(
    words: list[dict[str, Any]],
    block_gap_threshold: float = 18.0,
) -> list[dict[str, Any]]:
    sorted_words = sorted(words, key=lambda item: float(item["x0"]))
    if not sorted_words:
        return []

    grouped_blocks: list[list[dict[str, Any]]] = [[sorted_words[0]]]
    last_x1 = float(sorted_words[0]["x1"])
    for word in sorted_words[1:]:
        gap = float(word["x0"]) - last_x1
        if gap > block_gap_threshold:
            grouped_blocks.append([word])
        else:
            grouped_blocks[-1].append(word)
        last_x1 = float(word["x1"])

    blocks: list[dict[str, Any]] = []
    for block_words in grouped_blocks:
        x0 = min(float(word["x0"]) for word in block_words)
        x1 = max(float(word["x1"]) for word in block_words)
        blocks.append(
            {
                "x0": round(x0, 1),
                "x1": round(x1, 1),
                "center_x": round((x0 + x1) / 2, 1),
                "text": _words_to_text(block_words),
                "words": [
                    {
                        "text": str(word["text"]),
                        "x0": round(float(word["x0"]), 1),
                        "x1": round(float(word["x1"]), 1),
                        "top": round(float(word["top"]), 1),
                        "bottom": round(float(word["bottom"]), 1),
                    }
                    for word in block_words
                ],
            }
        )
    return blocks


def _render_line_from_blocks(blocks: list[dict[str, Any]]) -> str:
    return "\t".join(block["text"] for block in blocks if block["text"]).strip()


def _dose_sort_key(dose_column: dict[str, Any]) -> tuple[int, str, float]:
    raw_dose = str(dose_column.get("dose_number") or "")
    try:
        dose_number = int(raw_dose)
    except ValueError:
        dose_number = 9999
    return (dose_number, str(dose_column.get("date") or ""), float(dose_column.get("center_x") or 0.0))


def _build_dose_zones(dose_columns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sorted_columns = sorted(dose_columns, key=lambda column: float(column["center_x"]))
    for index, column in enumerate(sorted_columns):
        if index == 0:
            left_boundary = float(column["x0"]) - 40.0
        else:
            previous = sorted_columns[index - 1]
            left_boundary = (float(previous["center_x"]) + float(column["center_x"])) / 2

        if index == len(sorted_columns) - 1:
            right_boundary = float(column["x1"]) + 40.0
        else:
            following = sorted_columns[index + 1]
            right_boundary = (float(column["center_x"]) + float(following["center_x"])) / 2

        column["zone_left"] = round(left_boundary, 1)
        column["zone_right"] = round(right_boundary, 1)
    return sorted_columns


def _extract_dose_columns_from_line(line: dict[str, Any]) -> list[dict[str, Any]]:
    dose_columns: list[dict[str, Any]] = []
    for block in line["blocks"]:
        block_text = str(block["text"])
        matches = list(DOSE_DATE_RE.finditer(block_text))
        for match in matches:
            dose_columns.append(
                {
                    "header_text": match.group(0),
                    "dose_number": match.group("dose"),
                    "date": match.group("date"),
                    "x0": block["x0"],
                    "x1": block["x1"],
                    "center_x": block["center_x"],
                }
            )
    return dose_columns


def _is_dose_only_header_line(line: dict[str, Any]) -> bool:
    if not line.get("blocks"):
        return False
    has_dose = False
    for block in line["blocks"]:
        block_text = str(block["text"]).strip()
        if not block_text:
            continue
        full_matches = list(DOSE_DATE_RE.finditer(block_text))
        if not full_matches:
            return False
        rebuilt = " ".join(match.group(0) for match in full_matches).strip()
        normalized = re.sub(r"\s+", " ", block_text)
        if rebuilt != normalized:
            return False
        has_dose = True
    return has_dose


def _merge_section_dose_columns(current_section: dict[str, Any], new_columns: list[dict[str, Any]]) -> None:
    existing = list(current_section.get("dose_columns", []))
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for column in existing + new_columns:
        key = (str(column.get("dose_number") or ""), str(column.get("date") or ""))
        if key in seen:
            continue
        seen.add(key)
        merged.append(column)
    current_section["dose_columns"] = _build_dose_zones(merged)


def _is_label_only_header_line(line: dict[str, Any], current_section: dict[str, Any]) -> bool:
    blocks = line.get("blocks") or []
    if len(blocks) != 1:
        return False

    normalized = re.sub(r"\s+", " ", str(line.get("text") or "").strip())
    if not normalized:
        return False
    if DOSE_DATE_RE.search(normalized) or any(char.isdigit() for char in normalized):
        return False
    if normalized != normalized.upper():
        return False

    tokens = normalized.split()
    if len(tokens) < 2 or len(tokens) > 4:
        return False

    forbidden_fragments = (
        "*",
        "+",
        "ML",
        "SIR",
        "FL",
        "AGHI",
        "INIETT",
        "CONC",
        "DOSI",
        "SOLVENTE",
        "POLVERE",
        "VACCINE",
        "COVID",
    )
    if any(fragment in normalized for fragment in forbidden_fragments):
        return False

    dose_columns = current_section.get("dose_columns", [])
    if not dose_columns:
        return False

    leftmost_zone = min(float(column.get("zone_left", column.get("x0", 0.0))) for column in dose_columns)
    block = blocks[0]
    block_right = float(block.get("x1", 0.0))
    return block_right <= leftmost_zone - 8.0


def _append_label_continuation(current_section: dict[str, Any], line_text: str) -> None:
    normalized = re.sub(r"\s+", " ", line_text.strip())
    if not normalized:
        return
    current_section.setdefault("label_continuation_lines", []).append(normalized)
    base_label = str(current_section.get("vaccine_label") or "").strip()
    current_section["vaccine_label"] = f"{base_label} {normalized}".strip()


def _parse_vaccine_header(line: dict[str, Any]) -> dict[str, Any] | None:
    vaccine_label_parts: list[str] = []
    seen_first_dose = False
    dose_columns = _extract_dose_columns_from_line(line)

    for block in line["blocks"]:
        block_text = str(block["text"])
        matches = list(DOSE_DATE_RE.finditer(block_text))
        if not matches:
            if not seen_first_dose:
                vaccine_label_parts.append(block_text)
            continue

        prefix = block_text[: matches[0].start()].strip()
        if prefix and not seen_first_dose:
            vaccine_label_parts.append(prefix)

        seen_first_dose = True

    vaccine_label = " ".join(part for part in vaccine_label_parts if part).strip(" -")
    if vaccine_label and dose_columns:
        return {"vaccine_label": vaccine_label, "dose_columns": _build_dose_zones(dose_columns)}
    return None


def _overlap_length(start_a: float, end_a: float, start_b: float, end_b: float) -> float:
    return max(0.0, min(end_a, end_b) - max(start_a, start_b))


def _classify_block_to_doses(block: dict[str, Any], dose_columns: list[dict[str, Any]]) -> dict[str, Any]:
    block_left = float(block["x0"])
    block_right = float(block["x1"])
    block_width = max(1.0, block_right - block_left)

    candidates: list[dict[str, Any]] = []
    for column in dose_columns:
        overlap = _overlap_length(block_left, block_right, float(column["zone_left"]), float(column["zone_right"]))
        center_distance = abs(float(block["center_x"]) - float(column["center_x"]))
        candidates.append(
            {
                "header_text": column["header_text"],
                "dose_number": column["dose_number"],
                "date": column["date"],
                "center_x": column["center_x"],
                "zone_left": column["zone_left"],
                "zone_right": column["zone_right"],
                "overlap": round(overlap, 1),
                "overlap_ratio": round(overlap / block_width, 3),
                "center_distance": round(center_distance, 1),
            }
        )

    overlapping = [candidate for candidate in candidates if candidate["overlap"] > 0]
    overlapping.sort(key=lambda candidate: (-candidate["overlap_ratio"], candidate["center_distance"]))

    if len(overlapping) == 1:
        chosen = overlapping[0]
        return {
            "assignment_mode": "exact",
            "assigned_dose_header": chosen["header_text"],
            "assigned_dose_number": chosen["dose_number"],
            "assigned_dose_date": chosen["date"],
            "candidate_doses": overlapping,
        }

    if len(overlapping) > 1:
        chosen = overlapping[0]
        secondary = overlapping[1]
        if (
            float(chosen["overlap_ratio"]) >= 0.9
            and float(secondary["overlap_ratio"]) <= 0.1
            and float(chosen["center_distance"]) < float(secondary["center_distance"])
        ):
            return {
                "assignment_mode": "exact",
                "assigned_dose_header": chosen["header_text"],
                "assigned_dose_number": chosen["dose_number"],
                "assigned_dose_date": chosen["date"],
                "candidate_doses": overlapping,
            }
        return {
            "assignment_mode": "ambiguous_multi_column",
            "assigned_dose_header": chosen["header_text"],
            "assigned_dose_number": chosen["dose_number"],
            "assigned_dose_date": chosen["date"],
            "candidate_doses": overlapping,
        }

    nearest = sorted(candidates, key=lambda candidate: candidate["center_distance"])
    chosen = nearest[0]
    return {
        "assignment_mode": "nearest_only",
        "assigned_dose_header": chosen["header_text"],
        "assigned_dose_number": chosen["dose_number"],
        "assigned_dose_date": chosen["date"],
        "candidate_doses": nearest[:2],
    }


def _extract_vaccine_sections(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    current_section: dict[str, Any] | None = None

    for line in lines:
        line_text = str(line["text"])
        if any(line_text.startswith(prefix) for prefix in FOOTER_PREFIXES):
            if current_section is not None:
                sections.append(current_section)
                current_section = None
            continue

        header = _parse_vaccine_header(line)
        if header is not None:
            if current_section is not None:
                sections.append(current_section)
            current_section = {
                "vaccine_label": header["vaccine_label"],
                "header_line_text": line_text,
                "header_continuation_lines": [],
                "label_continuation_lines": [],
                "dose_columns": header["dose_columns"],
                "detail_lines": [],
            }
            continue

        if current_section is None:
            continue

        if _is_label_only_header_line(line, current_section):
            _append_label_continuation(current_section, line_text)
            continue

        if _is_dose_only_header_line(line):
            continuation_columns = _extract_dose_columns_from_line(line)
            if continuation_columns:
                _merge_section_dose_columns(current_section, continuation_columns)
                current_section.setdefault("header_continuation_lines", []).append(line_text)
                continue

        detail_blocks: list[dict[str, Any]] = []
        for block in line["blocks"]:
            assignment = _classify_block_to_doses(block, current_section["dose_columns"])
            detail_blocks.append(
                {
                    "text": block["text"],
                    "x0": block["x0"],
                    "x1": block["x1"],
                    "assignment_mode": assignment["assignment_mode"],
                    "assigned_dose_header": assignment["assigned_dose_header"],
                    "assigned_dose_number": assignment["assigned_dose_number"],
                    "assigned_dose_date": assignment["assigned_dose_date"],
                    "candidate_doses": assignment["candidate_doses"],
                }
            )

        if detail_blocks:
            current_section["detail_lines"].append({"text": line_text, "blocks": detail_blocks})

    if current_section is not None:
        sections.append(current_section)
    return sections


def _reader_confidence_from_blocks(blocks: list[dict[str, Any]]) -> str:
    if not blocks:
        return "missing_details"
    modes = {str(block["assignment_mode"]) for block in blocks}
    if modes == {"exact"}:
        return "high"
    if "ambiguous_multi_column" in modes or "nearest_only" in modes:
        return "low"
    return "medium"


def _extract_layout_artifact(path: Path) -> dict[str, Any]:
    pages: list[dict[str, Any]] = []
    with pdfplumber.open(path) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            raw_words = page.extract_words(use_text_flow=False, keep_blank_chars=False)
            raw_lines = _group_words_into_lines(raw_words)
            lines: list[dict[str, Any]] = []
            for raw_line in raw_lines:
                blocks = _group_line_words_into_blocks(raw_line["words"])
                line_text = _render_line_from_blocks(blocks)
                if not line_text:
                    continue
                lines.append({"top": round(float(raw_line["top"]), 1), "text": line_text, "blocks": blocks})

            pages.append(
                {
                    "page_number": page_number,
                    "width": round(float(page.width), 1),
                    "height": round(float(page.height), 1),
                    "text": "\n".join(line["text"] for line in lines).strip(),
                    "lines": lines,
                    "vaccine_sections": _extract_vaccine_sections(lines),
                }
            )

    return {
        "source_path": str(path),
        "page_count": len(pages),
        "text": "\n\n".join(page["text"] for page in pages if page["text"]).strip(),
        "pages": pages,
    }


def _build_vaccination_reader(layout_artifact: dict[str, Any]) -> dict[str, Any]:
    vaccines: list[dict[str, Any]] = []
    for page in layout_artifact.get("pages", []):
        for section in page.get("vaccine_sections", []):
            doses: list[dict[str, Any]] = []
            for dose_column in section.get("dose_columns", []):
                exact_blocks: list[dict[str, Any]] = []
                ambiguous_blocks: list[dict[str, Any]] = []
                for detail_line in section.get("detail_lines", []):
                    for block in detail_line.get("blocks", []):
                        if block.get("assigned_dose_number") != dose_column.get("dose_number"):
                            continue
                        payload = {
                            "text": block.get("text"),
                            "assignment_mode": block.get("assignment_mode"),
                            "candidate_doses": block.get("candidate_doses", []),
                        }
                        if block.get("assignment_mode") == "exact":
                            exact_blocks.append(payload)
                        else:
                            ambiguous_blocks.append(payload)

                doses.append(
                    {
                        "dose_number": dose_column.get("dose_number"),
                        "date": dose_column.get("date"),
                        "header_text": dose_column.get("header_text"),
                        "confidence": _reader_confidence_from_blocks(exact_blocks + ambiguous_blocks),
                        "exact_detail_texts": [block["text"] for block in exact_blocks],
                        "ambiguous_detail_blocks": ambiguous_blocks,
                    }
                )

            doses.sort(key=_dose_sort_key)
            vaccines.append(
                {
                    "page_number": page.get("page_number"),
                    "vaccine_label": section.get("vaccine_label"),
                    "header_line_text": section.get("header_line_text"),
                    "header_continuation_lines": section.get("header_continuation_lines", []),
                    "label_continuation_lines": section.get("label_continuation_lines", []),
                    "doses": doses,
                }
            )

    return {
        "source_path": layout_artifact.get("source_path"),
        "page_count": layout_artifact.get("page_count"),
        "vaccines": vaccines,
    }


def _render_reader_text(reader: dict[str, Any]) -> str:
    lines: list[str] = [
        "Vaccination certificate reader output.",
        "This is not the final clinical JSON.",
        "Use exact details as stronger evidence and ambiguous details as weaker evidence.",
    ]
    for vaccine in reader.get("vaccines", []):
        lines.append("")
        lines.append(f"VACCINE: {vaccine.get('vaccine_label')}")
        lines.append(f"HEADER: {vaccine.get('header_line_text')}")
        continuation_lines = vaccine.get("header_continuation_lines", [])
        if continuation_lines:
            lines.append("HEADER_CONTINUATIONS:")
            for continuation_line in continuation_lines:
                lines.append(f"- {continuation_line}")
        label_continuation_lines = vaccine.get("label_continuation_lines", [])
        if label_continuation_lines:
            lines.append("LABEL_CONTINUATIONS:")
            for continuation_line in label_continuation_lines:
                lines.append(f"- {continuation_line}")
        for dose in vaccine.get("doses", []):
            lines.append(
                f"DOSE {dose.get('dose_number')} | DATE {dose.get('date')} | CONFIDENCE {dose.get('confidence')}"
            )
            exact_texts = dose.get("exact_detail_texts", [])
            if exact_texts:
                lines.append("EXACT_DETAILS:")
                for dose_text in exact_texts:
                    lines.append(f"- {dose_text}")
            else:
                lines.append("EXACT_DETAILS: none")

            ambiguous_blocks = dose.get("ambiguous_detail_blocks", [])
            if ambiguous_blocks:
                lines.append("AMBIGUOUS_DETAILS:")
                for block in ambiguous_blocks:
                    candidates = ", ".join(
                        f"dose {candidate.get('dose_number')} ({candidate.get('date')}, overlap={candidate.get('overlap_ratio')})"
                        for candidate in block.get("candidate_doses", [])
                    )
                    lines.append(
                        f"- {block.get('text')} | mode={block.get('assignment_mode')} | candidates={candidates}"
                    )
            else:
                lines.append("AMBIGUOUS_DETAILS: none")
    return "\n".join(lines).strip() + "\n"

def phase1_extract_text(pdf_path: Path) -> dict[str, Any]:
    extraction = extract_text_from_pdf(pdf_path)
    return {
        "text": extraction.text,
        "page_count": extraction.page_count,
        "character_count": extraction.character_count,
    }


def phase2_classify_document(pdf_path: Path, extracted_text: dict[str, Any]) -> dict[str, Any]:
    family = classify_document_family(pdf_path)
    text_lower = extracted_text["text"].lower()
    tags: list[str] = []
    matched_keywords: list[str] = []
    document_subcategory: str | None = None

    keyword_groups = { #TODO sistemare meglio le keyword in base al tipo di documento che si analizza
        "laboratory_report": ["laboratorio", "referto specialistico", "synlab"],
        "blood_test": ["emocromo", "globuli bianchi", "formula leucocitaria", "wbc"],
        "infectious_screening": ["hiv", "hbsag", "anti-core virus b"],
        #"covid_test": ["sars-cov-2", "tampone", "covid"],
        "electronic_prescription": ["ricetta elettronica", "prescrizione"],
        "vaccination_record": ["vaccin", "poliomielite", "morbillo"],
        "summary_index": ["fascicolo sanitario", "data pubblicazione"],
    }

    for tag, keywords in keyword_groups.items():
        found = [keyword for keyword in keywords if keyword in text_lower]
        if found:
            tags.append(tag)
            matched_keywords.extend(found)

    if family == DocumentFamily.VACCINATION_CERTIFICATE:
        document_subcategory = "vaccination_certificate"
    elif family == DocumentFamily.PRESCRIPTION:
        document_subcategory = "electronic_prescription"
    elif family == DocumentFamily.SUMMARY:
        document_subcategory = "summary_index"
    elif family == DocumentFamily.CLINICAL_DOCUMENT:
        if "blood_test" in tags:
            document_subcategory = "laboratory_blood_panel"
        elif "covid_test" in tags:
            document_subcategory = "covid_test_report"
        elif "laboratory_report" in tags:
            document_subcategory = "laboratory_report"

    return {
        "document_family": family,
        "document_subcategory": document_subcategory,
        "tags": _dedupe_preserve_order(tags),
        "keyword_hits": _dedupe_preserve_order(matched_keywords),
        "use_vaccination_parser": family == DocumentFamily.VACCINATION_CERTIFICATE,
    }


def _first_non_empty_match(patterns: list[re.Pattern[str]], text: str) -> str | None:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            value = " ".join(group for group in match.groups() if group).strip()
            if value:
                return re.sub(r"\s+", " ", value)
    return None


def _extract_patient_name(text: str) -> dict[str, Any]:
    full_name: str | None = None
    patterns = [
        re.compile(r"Nome\s+([A-ZÀ-ÖØ-Ý' ]+)\s+Cognome\s+([A-ZÀ-ÖØ-Ý' ]+)", re.IGNORECASE),
        re.compile(r"COGNOME E NOME[^:]*:\s*([A-ZÀ-ÖØ-Ý' ]+)", re.IGNORECASE),
    ]
    first = _first_non_empty_match(patterns, text)
    if first:
        full_name = first
    else:
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or len(line.split()) < 2 or any(char.isdigit() for char in line):
                continue
            if UPPER_NAME_RE.match(line) and len(line.split()) <= 4:
                full_name = re.sub(r"\s+", " ", line)
                break

    given_name = None
    family_name = None
    if full_name:
        parts = full_name.split()
        if len(parts) >= 2:
            family_name = parts[0]
            given_name = " ".join(parts[1:])

    return {
        "full_name": full_name,
        "given_name": given_name,
        "family_name": family_name,
    }


def _extract_vaccination_identity(text: str) -> dict[str, Any]:
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if line.strip()]
    full_name = None
    birth_place = None
    birth_date = None
    tax_code = None
    residence_city = None
    address_or_residence = None
    identity_lines: list[str] = []

    for index, line in enumerate(lines):
        lower = line.lower()
        if lower.startswith("il sig") or lower.startswith("la sig"):
            identity_lines.append(line)
            name_match = re.search(r"(?:il|la)\s+sig\.?\s+(.+)$", line, re.IGNORECASE)
            if name_match:
                full_name = re.sub(r"\s+", " ", name_match.group(1)).strip()

        if lower.startswith("nato a ") or lower.startswith("nata a "):
            identity_lines.append(line)
            birth_match = re.search(
                r"Nat[oa] a (?P<birth_place>.+?) il (?P<birth_date>\d{2}[/-]\d{2}[/-]\d{2,4}), codice fiscale (?P<tax_code>[A-Z0-9]+) residente a (?P<residence_city>.+?)(?:\s*-\s*(?P<address_suffix>.*))?$",
                line,
                re.IGNORECASE,
            )
            if birth_match:
                birth_place = birth_match.group("birth_place").strip()
                birth_date = _normalize_date(birth_match.group("birth_date"))
                tax_code = birth_match.group("tax_code").strip()
                residence_city = birth_match.group("residence_city").strip()
                address_suffix = (birth_match.group("address_suffix") or "").strip(" .")
                street_address = None
                if address_suffix:
                    street_address = address_suffix
                next_line = lines[index + 1] if index + 1 < len(lines) else ""
                if next_line and not next_line.upper().startswith("HA EFFETTUATO") and not DOSE_DATE_RE.search(next_line):
                    identity_lines.append(next_line)
                    if street_address:
                        street_address = f"{street_address} {next_line}".strip()
                    else:
                        street_address = next_line
                if street_address:
                    street_address = _normalize_street_prefix(street_address)
                    address_or_residence = street_address
                else:
                    address_or_residence = residence_city
            break

    given_name = None
    family_name = None
    if full_name:
        parts = full_name.split()
        if len(parts) >= 2:
            family_name = parts[0]
            given_name = " ".join(parts[1:])

    return {
        "full_name": full_name,
        "given_name": given_name,
        "family_name": family_name,
        "tax_code": tax_code,
        "birth_date": birth_date,
        "birth_place": birth_place,
        "residence_city": residence_city,
        "address_or_residence": address_or_residence,
        "raw_identity_lines": _dedupe_preserve_order(identity_lines),
    }


def _strip_vaccination_non_clinical_lines(lines: list[str]) -> list[str]:
    start_index = 0
    for index, line in enumerate(lines):
        if line.upper().startswith("HA EFFETTUATO LE SEGUENTI VACCINAZIONI"):
            start_index = index + 1
            break

    filtered: list[str] = []
    for line in lines[start_index:]:
        if line.startswith(FOOTER_PREFIXES):
            continue
        if line.startswith("SI CERTIFICA CHE"):
            continue
        if line.lower().startswith("il sig") or line.lower().startswith("la sig"):
            continue
        if line.lower().startswith("nato a ") or line.lower().startswith("nata a "):
            continue
        filtered.append(line)
    return filtered


def _extract_typed_dates(text: str) -> list[dict[str, Any]]:
    typed_dates: list[dict[str, Any]] = []
    for line in text.splitlines():
        line_clean = line.strip()
        if not line_clean:
            continue
        line_lower = line_clean.lower()
        for raw_date in DATE_RE.findall(line_clean):
            date_type = "unknown_document_date"
            if "nato" in line_lower or "data di nascita" in line_lower:
                date_type = "birth_date"
            elif "pubblicazione" in line_lower:
                date_type = "publication_date"
            elif "validato" in line_lower or "firmato digitalmente" in line_lower:
                date_type = "validation_date"
            elif "tipo ricetta" in line_lower or "prescrizione" in line_lower or "data:" in line_lower:
                date_type = "prescription_date"
            elif "generato in data" in line_lower or "rilasciato" in line_lower:
                date_type = "issue_date"
            elif "codice lab" in line_lower or "prelievo" in line_lower:
                date_type = "collection_date"
            typed_dates.append(
                {
                    "type": date_type,
                    "raw": raw_date,
                    "normalized": _normalize_date(raw_date),
                    "source_line": line_clean,
                }
            )
    return _dedupe_preserve_order(typed_dates)


def phase3_extract_base_metadata(
    pdf_path: Path,
    extracted_text: dict[str, Any],
    classification: dict[str, Any],
) -> dict[str, Any]:
    text = extracted_text["text"]
    tax_code_match = TAX_CODE_RE.search(text)
    all_typed_dates = _extract_typed_dates(text)

    if classification["use_vaccination_parser"]:
        patient_payload = _extract_vaccination_identity(text)
        snapshot_date = _extract_filename_snapshot_date(pdf_path)
        vaccination_dates: list[dict[str, Any]] = []
        if patient_payload.get("birth_date"):
            vaccination_dates.append(
                {
                    "type": "birth_date",
                    "raw": patient_payload["birth_date"],
                    "normalized": patient_payload["birth_date"],
                    "source_line": "vaccination_identity_block",
                }
            )
        if snapshot_date:
            vaccination_dates.append(
                {
                    "type": "document_snapshot_date",
                    "raw": pdf_path.stem,
                    "normalized": snapshot_date,
                    "source_line": pdf_path.name,
                    "meaning": "on this date the certificate attested the vaccinations listed in the document",
                }
            )
    else:
        patient_name = _extract_patient_name(text)
        birth_date = None
        for item in all_typed_dates:
            if item["type"] == "birth_date":
                birth_date = item["normalized"]
                break

        address_match = _first_non_empty_match(
            [
                re.compile(r"INDIRIZZO:\s*(.+)", re.IGNORECASE),
                re.compile(r"Residenza[:\s]+(.+)", re.IGNORECASE),
                re.compile(r"CITTA'[:\s]+(.+)", re.IGNORECASE),
            ],
            text,
        )
        raw_identity_lines = [
            line.strip()
            for line in text.splitlines()
            if line.strip()
            and (
                "nome" in line.lower()
                or "cognome" in line.lower()
                or "codice fiscale" in line.lower()
                or "nato" in line.lower()
                or "indirizzo" in line.lower()
            )
        ]
        patient_payload = {
            **patient_name,
            "tax_code": tax_code_match.group(0) if tax_code_match else None,
            "birth_date": birth_date,
            "birth_place": None,
            "residence_city": None,
            "address_or_residence": address_match,
            "raw_identity_lines": _dedupe_preserve_order(raw_identity_lines),
        }

    issuing_org = _first_non_empty_match(
        [
            re.compile(r"(SYNLAB[^\n]*)", re.IGNORECASE),
            re.compile(r"(REGIONE LOMBARDIA)", re.IGNORECASE),
            re.compile(r"(ATS DI [A-Z?-??-?' ]+)", re.IGNORECASE),
            re.compile(r"(PIATTAFORMA NAZIONALE DGC)", re.IGNORECASE),
        ],
        text,
    )

    if not patient_payload.get("tax_code") and tax_code_match:
        patient_payload["tax_code"] = tax_code_match.group(0)

    dates_payload = vaccination_dates if classification["use_vaccination_parser"] else all_typed_dates

    return {
        "patient": patient_payload,
        "dates": dates_payload,
        "issuing_organization": issuing_org,
    }


def _clean_common_lines(text: str) -> list[str]:
    cleaned: list[str] = []
    for raw_line in text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line:
            continue
        if any(line.lower().startswith(prefix) for prefix in COMMON_NOISE_PREFIXES):
            continue
        cleaned.append(line)
    return cleaned


def phase4_interpret_document(
    pdf_path: Path,
    extracted_text: dict[str, Any],
    classification: dict[str, Any],
    metadata: dict[str, Any],
    debug: bool = False,
) -> dict[str, Any]:
    cleaned_lines = _clean_common_lines(extracted_text["text"])
    if classification["use_vaccination_parser"]:
        cleaned_lines = _strip_vaccination_non_clinical_lines(cleaned_lines)
    cleaned_text = "\n".join(cleaned_lines).strip()
    interpretation: dict[str, Any] = {
        "content": {
            "cleaned_text": cleaned_text,
            "relevant_lines": cleaned_lines[:80],
            "boilerplate_notes": [
                "common_prefix_filter_applied",
                "full raw text preserved in extracted_text.txt",
            ],
        },
        "specialized": {},
        "debug_artifacts": {},
    }

    should_emit_debug = classification["use_vaccination_parser"] or debug
    if classification["use_vaccination_parser"]:
        layout_artifact = _extract_layout_artifact(pdf_path)
        reader = _build_vaccination_reader(layout_artifact)
        reader_text = _render_reader_text(reader)
        interpretation["specialized"] = {
            "vaccination_reader": reader,
            "vaccination_reader_text": reader_text,
        }
        if should_emit_debug:
            interpretation["debug_artifacts"] = {
                "layout_text": layout_artifact["text"],
                "layout_words": layout_artifact,
                "reader_text": reader_text,
                "reader_json": reader,
            }

    return interpretation

def _render_interpreted_text(interpreted_json: dict[str, Any]) -> str:
    lines: list[str] = []
    document = interpreted_json["document"]
    patient = interpreted_json["patient"]
    classification = interpreted_json["classification"]
    content = interpreted_json["content"]
    dates = interpreted_json["dates"]

    lines.append("DOCUMENT")
    lines.append(f"source_path: {document.get('source_path')}")
    lines.append(f"document_family: {document.get('document_family')}")
    lines.append(f"document_subcategory: {document.get('document_subcategory') or 'none'}")
    lines.append(f"issuing_organization: {document.get('issuing_organization') or 'unknown'}")
    lines.append(f"document_snapshot_date: {document.get('document_snapshot_date') or 'unknown'}")
    lines.append("")
    lines.append("PATIENT")
    lines.append(f"full_name: {patient.get('full_name') or 'unknown'}")
    lines.append(f"given_name: {patient.get('given_name') or 'unknown'}")
    lines.append(f"family_name: {patient.get('family_name') or 'unknown'}")
    lines.append(f"tax_code: {patient.get('tax_code') or 'unknown'}")
    lines.append(f"birth_date: {patient.get('birth_date') or 'unknown'}")
    lines.append(f"birth_place: {patient.get('birth_place') or 'unknown'}")
    lines.append(f"residence_city: {patient.get('residence_city') or 'unknown'}")
    lines.append(f"address_or_residence: {patient.get('address_or_residence') or 'unknown'}")
    lines.append("")
    lines.append("DATES")
    if dates:
        for item in dates:
            lines.append(f"- {item.get('type')}: {item.get('normalized')} ({item.get('raw')})")
    else:
        lines.append("- none")
    lines.append("")
    lines.append("CLASSIFICATION")
    lines.append(f"tags: {', '.join(classification.get('tags', [])) or 'none'}")
    lines.append(f"keyword_hits: {', '.join(classification.get('keyword_hits', [])) or 'none'}")
    lines.append("")
    lines.append("CONTENT")
    lines.append(content.get("cleaned_text") or "")

    specialized = interpreted_json.get("specialized", {})
    if specialized.get("vaccination_reader_text"):
        lines.append("")
        lines.append("SPECIALIZED")
        lines.append(specialized["vaccination_reader_text"].strip())
    return "\n".join(lines).strip() + "\n"


def _build_vaccination_prompt_from_interpreted_text(interpreted_text: str, source_document: Path) -> str:
    return f"""
You are given an interpreted stage1 output for one vaccination certificate.

Source document: {source_document}

Return one JSON object only.
Use the interpreted stage1 output below as your source.
Do not invent fields that are not supported by the interpreted text.

Interpreted stage1 output:
{interpreted_text}
""".strip()


def phase5_build_prompt_input(
    pdf_path: Path,
    classification: dict[str, Any],
    metadata: dict[str, Any],
    interpretation: dict[str, Any],
) -> dict[str, Any]:
    document_id = pdf_path.stem
    interpreted_json = {
        "document": {
            "document_id": document_id,
            "source_path": str(pdf_path),
            "document_family": classification["document_family"].value,
            "document_subcategory": classification.get("document_subcategory"),
            "issuing_organization": metadata.get("issuing_organization"),
            "document_snapshot_date": _extract_filename_snapshot_date(pdf_path),
        },
        "patient": metadata["patient"],
        "dates": metadata["dates"],
        "classification": {
            "tags": classification.get("tags", []),
            "keyword_hits": classification.get("keyword_hits", []),
        },
        "content": interpretation["content"],
        "specialized": interpretation.get("specialized", {}),
    }
    interpreted_text = _render_interpreted_text(interpreted_json)

    if classification["document_family"] == DocumentFamily.VACCINATION_CERTIFICATE:
        prompt_main = _build_vaccination_prompt_from_interpreted_text(interpreted_text, pdf_path)
    else:
        prompt_main = build_document_prompt(classification["document_family"], interpreted_text)

    return {
        "interpreted_text": interpreted_text,
        "interpreted_json": interpreted_json,
        "prompt_main": prompt_main,
    }


def _write_debug_artifacts(paths: dict[str, Path], interpretation: dict[str, Any]) -> None:
    debug_artifacts = interpretation.get("debug_artifacts", {})
    if not debug_artifacts:
        return
    if debug_artifacts.get("layout_text") is not None:
        paths["layout_text"].write_text(debug_artifacts["layout_text"], encoding="utf-8")
    if debug_artifacts.get("layout_words") is not None:
        paths["layout_words"].write_text(
            json.dumps(debug_artifacts["layout_words"], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    if debug_artifacts.get("reader_text") is not None:
        paths["reader_text"].write_text(debug_artifacts["reader_text"], encoding="utf-8")
    if debug_artifacts.get("reader_json") is not None:
        paths["reader_json"].write_text(
            json.dumps(debug_artifacts["reader_json"], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


def _resolve_requested_pdfs(args: argparse.Namespace) -> tuple[list[Path], list[str]]:
    raw_root = args.raw_root.resolve()
    warnings: list[str] = []

    # Comportamento di default: gira su tutte le persone e cerca automaticamente
    # l'unico CertificatoVaccinale*.pdf per ciascuna cartella paziente.
    if args.vaccini_all or not args.person:
        if not raw_root.exists():
            return [], [f"root documenti non trovata: {raw_root}"]
        return _discover_vaccination_pdfs(raw_root)

    person_dir = raw_root / args.person
    if not person_dir.exists():
        return [], [f"cartella paziente non trovata: {person_dir}"]
    pdf_path = _find_unique_vaccination_pdf(person_dir)
    if pdf_path is None:
        matches = sorted(path.name for path in person_dir.glob(VACCINATION_GLOB) if path.is_file())
        if not matches:
            return [], [f"{args.person}: nessun {VACCINATION_GLOB} trovato"]
        return [], [f"{args.person}: trovati piu certificati vaccinali: {', '.join(matches)}"]
    return [pdf_path], warnings


def _run_stage1_for_pdf(pdf_path: Path, args: argparse.Namespace) -> int:
    document_root = _resolve_document_artifact_dir(pdf_path, args.artifacts_root.resolve())
    paths = _artifact_paths(document_root)

    if not args.force and _has_stage1_outputs(document_root):
        print("=== STAGE 1 GIA PRONTO ===")
        print(f"-> Documento: {pdf_path.name}")
        print(f"-> Artefatti esistenti: {paths['document_dir']}")
        print("-> Salto l'estrazione per evitare passaggi inutili. Si puo passare direttamente alla fase dopo.")
        return 0

    paths["document_dir"].mkdir(parents=True, exist_ok=True)

    print("=== AVVIO TEST STAGE 1 ===")
    print(f"-> Documento: {pdf_path.name}")
    print(f"-> Artefatti stage1: {paths['document_dir']}")

    print("\n[PHASE 1] Extract Text")
    extraction = phase1_extract_text(pdf_path)
    print(f"-> Pagine lette: {extraction['page_count']}")
    print(f"-> Caratteri estratti: {extraction['character_count']}")
    if extraction["character_count"] == 0:
        print("ERRORE: il PDF non contiene testo estraibile.")
        return 1
    paths["extracted_text"].write_text(extraction["text"], encoding="utf-8")

    print("\n[PHASE 2] Classify Document")
    classification = phase2_classify_document(pdf_path, extraction)
    print(f"-> Famiglia: {classification['document_family'].value}")
    print(f"-> Sottocategoria: {classification.get('document_subcategory') or 'none'}")
    print(f"-> Tag: {', '.join(classification.get('tags', [])) or 'none'}")

    print("\n[PHASE 3] Extract Base Metadata")
    metadata = phase3_extract_base_metadata(pdf_path, extraction, classification)
    print(f"-> Paziente: {metadata['patient'].get('full_name') or 'unknown'}")
    print(f"-> Codice fiscale: {metadata['patient'].get('tax_code') or 'unknown'}")
    print(f"-> Date tipizzate trovate: {len(metadata.get('dates', []))}")

    print("\n[PHASE 4] Interpret Document")
    interpretation = phase4_interpret_document(pdf_path, extraction, classification, metadata, debug=args.debug)
    print("-> Interpretazione comune completata.")
    if classification["use_vaccination_parser"]:
        print("-> Parser vaccini dedicato attivato.")
    elif args.debug:
        print("-> Modalita debug attiva per artefatti aggiuntivi.")

    print("\n[PHASE 5] Build Prompt Input")
    prompt_input = phase5_build_prompt_input(pdf_path, classification, metadata, interpretation)
    paths["interpreted_text"].write_text(prompt_input["interpreted_text"], encoding="utf-8")
    paths["interpreted_json"].write_text(
        json.dumps(prompt_input["interpreted_json"], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    paths["prompt_main"].write_text(prompt_input["prompt_main"], encoding="utf-8")
    _write_debug_artifacts(paths, interpretation)

    print(f"-> Artefatto standard: {paths['extracted_text']}")
    print(f"-> Artefatto standard: {paths['interpreted_text']}")
    print(f"-> Artefatto standard: {paths['interpreted_json']}")
    print(f"-> Artefatto standard: {paths['prompt_main']}")
    if interpretation.get("debug_artifacts"):
        print("-> Artefatti debug: layout_* e reader_* salvati per questo documento.")

    print("\n=== STAGE 1 COMPLETATO ===")
    print("-> Il prompt e pronto per stage2.")
    print(f"-> Usa test_stage2.py con: {paths['prompt_main']}")
    return 0


def main() -> int:
    args = _build_parser().parse_args()
    pdf_paths, warnings = _resolve_requested_pdfs(args)

    for warning in warnings:
        print(f"ATTENZIONE: {warning}")

    if not pdf_paths:
        print("ERRORE: nessun documento da processare.")
        return 1

    exit_code = 0
    for index, pdf_path in enumerate(pdf_paths, start=1):
        if len(pdf_paths) > 1:
            print(f"\n##### CERTIFICATO VACCINALE {index}/{len(pdf_paths)} #####")
            print(f"-> Persona: {pdf_path.parent.name}")
            print("-> In modalita vaccini stage1 cerco automaticamente l'unico file che inizia con 'CertificatoVaccinale'.")
        if not pdf_path.exists():
            print(f"ERRORE: PDF non trovato in {pdf_path}")
            exit_code = 1
            continue
        run_code = _run_stage1_for_pdf(pdf_path.resolve(), args)
        if run_code != 0:
            exit_code = run_code
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
