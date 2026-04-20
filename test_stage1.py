"""Runner standalone per testare lo stage 1 vaccini della pipeline Value4Care.

Questo file contiene il parser vaccinale strutturale che:
- individua i documenti vaccinali dal nome file (`vaccin`)
- estrae il nome vaccino dalla parte sinistra in bold e font piu grande
- riconosce le dosi come header `numero - data`
- assegna come note solo il testo non-bold sotto dosi gia riconosciute

Gli artifact vengono scritti sotto:
- artifacts/<person>/<document_id>/

Di default viene scritto solo `interpreted_text.json`.
Gli artifact di debug vengono prodotti solo con `--debug-artifacts`.
Il ramo vaccini non genera prompt e non passa per stage 2.
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

DEFAULT_RAW_ROOT = ROOT_DIR / "data" / "raw"
DEFAULT_ARTIFACTS_ROOT = ROOT_DIR / "artifacts"

TAX_CODE_RE = re.compile(r"\b[A-Z]{6}\d{2}[A-Z]\d{2}[A-Z]\d{3}[A-Z]\b")
DOSE_DATE_RE = re.compile(r"(?P<dose>\d+)\s*-\s*(?P<date>\d{2}[/-]\d{2}[/-]\d{2,4})")
REGION_RE = re.compile(r"\bREGIONE\s+[A-ZÀ-ÖØ-Ý' ]+\b", re.IGNORECASE)

FOOTER_PREFIXES = (
    "ATS DI ",
    "ASST ",
    "ASL ",
    "VIA ",
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
VACCINATION_TABLE_END_PREFIXES = (
    "DATA DI CREAZIONE DEL DOCUMENTO",
    "DOCUMENTO FIRMATO DIGITALMENTE",
    "I DATI PRESENTI SONO RESI DISPONIBILI",
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
AUTHORITY_RES = (
    re.compile(r"\bATS DI\s+[A-ZÀ-ÖØ-Ý' ]+\b", re.IGNORECASE),
    re.compile(r"\bASST\s+[A-ZÀ-ÖØ-Ý' ]+\b", re.IGNORECASE),
    re.compile(r"\bASL\s+[A-ZÀ-ÖØ-Ý' ]+\b", re.IGNORECASE),
)


# ---------------------------------------------------------------------------
# CLI e discovery documenti
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    """Costruisce la CLI di stage 1 per i vaccini."""
    parser = argparse.ArgumentParser(description="Test standalone di stage 1 solo per i vaccini.")
    parser.add_argument("--pdf", type=Path, help="Processa esplicitamente un singolo PDF vaccinale.")
    parser.add_argument(
        "--person",
        type=str,
        help=(
            "Processa il documento vaccinale di una persona cercando l'unico PDF "
            "il cui nome contiene 'vaccin' nella cartella data/raw/<person>/."
        ),
    )
    parser.add_argument("--force", action="store_true", help="Riesegue stage1 anche se il JSON esiste gia.")
    parser.add_argument(
        "--debug-artifacts",
        dest="debug_artifacts",
        action="store_true",
        help="Scrive anche gli artifact di debug oltre al JSON interpretato.",
    )
    parser.add_argument("--debug", dest="debug_artifacts", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_ROOT, help="Root dei documenti grezzi.")
    parser.add_argument(
        "--artifacts-root",
        type=Path,
        default=DEFAULT_ARTIFACTS_ROOT,
        help="Root degli artifact di stage 1.",
    )
    parser.set_defaults(debug_artifacts=False)
    return parser


def _artifact_paths(document_root: Path) -> dict[str, Path]:
    """Restituisce i path standard degli artifact del documento."""
    return {
        "document_dir": document_root,
        "interpreted_json": document_root / "interpreted_text.json",
        "extracted_text": document_root / "extracted_text.txt",
        "interpreted_text": document_root / "interpreted_text.txt",
        "layout_text": document_root / "layout_text.txt",
        "layout_words": document_root / "layout_words.json",
        "grid_debug": document_root / "grid_debug.json",
        "reader_text": document_root / "reader_text.txt",
        "reader_json": document_root / "reader.json",
    }


def _resolve_document_artifact_dir(pdf_path: Path, artifacts_root: Path) -> Path:
    """Mappa un PDF nel path artifact di stage 1."""
    person_id = pdf_path.parent.name if pdf_path.parent.name.startswith("person") else "person_unknown"
    return artifacts_root / person_id / pdf_path.stem


def _looks_like_vaccination_pdf(path: Path) -> bool:
    """Riconosce i documenti vaccinali dal nome file."""
    return path.is_file() and path.suffix.lower() == ".pdf" and "vaccin" in path.name.casefold()


def _find_unique_vaccination_pdf(person_dir: Path) -> Path | None:
    """Restituisce l'unico PDF vaccinale della cartella paziente, se esiste."""
    matches = sorted(path for path in person_dir.iterdir() if _looks_like_vaccination_pdf(path))
    return matches[0] if len(matches) == 1 else None


def _discover_vaccination_pdfs(raw_root: Path) -> tuple[list[Path], list[str]]:
    """Scansiona tutte le persone e raccoglie i PDF vaccinali validi."""
    pdfs: list[Path] = []
    warnings: list[str] = []

    for person_dir in sorted(path for path in raw_root.iterdir() if path.is_dir() and path.name.startswith("person")):
        matches = sorted(path for path in person_dir.iterdir() if _looks_like_vaccination_pdf(path))
        if len(matches) == 1:
            pdfs.append(matches[0])
        elif not matches:
            warnings.append(f"{person_dir.name}: nessun PDF vaccinale trovato")
        else:
            joined = ", ".join(path.name for path in matches)
            warnings.append(f"ERRORE {person_dir.name}: trovati piu documenti vaccini: {joined}")
    return pdfs, warnings


def _resolve_requested_pdfs(args: argparse.Namespace) -> tuple[list[Path], list[str]]:
    """Risolve i PDF da processare a partire dagli argomenti CLI."""
    if args.pdf:
        return [args.pdf.resolve()], []

    raw_root = args.raw_root.resolve()
    if not raw_root.exists():
        return [], [f"root documenti non trovata: {raw_root}"]

    if args.person:
        person_dir = raw_root / args.person
        if not person_dir.exists():
            return [], [f"cartella paziente non trovata: {person_dir}"]
        pdf_path = _find_unique_vaccination_pdf(person_dir)
        if pdf_path is not None:
            return [pdf_path], []

        matches = sorted(path.name for path in person_dir.iterdir() if _looks_like_vaccination_pdf(path))
        if not matches:
            return [], [f"{args.person}: nessun PDF vaccinale trovato"]
        return [], [f"ERRORE {args.person}: trovati piu documenti vaccini: {', '.join(matches)}"]

    return _discover_vaccination_pdfs(raw_root)


def _has_stage1_output(document_root: Path) -> bool:
    """Controlla se il JSON interpretato esiste gia."""
    return _artifact_paths(document_root)["interpreted_json"].exists()


# ---------------------------------------------------------------------------
# Primitive testo/layout
# ---------------------------------------------------------------------------


def _normalize_text(text: str) -> str:
    """Normalizza spazi multipli e trimming."""
    return re.sub(r"\s+", " ", text).strip()


def _words_to_text(words: list[dict[str, Any]]) -> str:
    """Concatena le word nell'ordine corrente."""
    return " ".join(str(word["text"]) for word in words).strip()


def _group_words_into_lines(words: list[dict[str, Any]], line_tolerance: float = 4.0) -> list[dict[str, Any]]:
    """Raggruppa le word del PDF in righe geometriche."""
    rows: list[dict[str, Any]] = []
    for word in sorted(words, key=lambda item: (float(item["top"]), float(item["x0"]))):
        top = float(word["top"])
        if not rows or abs(top - rows[-1]["top"]) > line_tolerance:
            rows.append({"top": top, "words": [word]})
        else:
            rows[-1]["words"].append(word)
    return rows


def _build_block_from_words(words: list[dict[str, Any]]) -> dict[str, Any]:
    """Costruisce un blocco geometrico a partire da una lista di word."""
    x0 = min(float(word["x0"]) for word in words)
    x1 = max(float(word["x1"]) for word in words)
    top = min(float(word["top"]) for word in words)
    bottom = max(float(word["bottom"]) for word in words)
    return {
        "x0": round(x0, 1),
        "x1": round(x1, 1),
        "center_x": round((x0 + x1) / 2, 1),
        "top": round(top, 1),
        "bottom": round(bottom, 1),
        "text": _words_to_text(words),
        "words": [
            {
                "text": str(word["text"]),
                "x0": round(float(word["x0"]), 1),
                "x1": round(float(word["x1"]), 1),
                "top": round(float(word["top"]), 1),
                "bottom": round(float(word["bottom"]), 1),
                "fontname": str(word.get("fontname") or ""),
                "size": round(float(word.get("size", 0.0)), 2),
            }
            for word in words
        ],
    }


def _split_block_on_inline_dose_header(block: dict[str, Any]) -> list[dict[str, Any]]:
    """Separa i blocchi misti `testo ... dose-data` in due blocchi distinti."""
    words = list(block.get("words") or [])
    if len(words) < 3:
        return [block]

    normalized_words = [str(word["text"]).strip() for word in words]
    for split_index in range(1, len(normalized_words)):
        right_text = " ".join(normalized_words[split_index:]).strip()
        if DOSE_DATE_RE.match(right_text):
            return [
                _build_block_from_words(words[:split_index]),
                _build_block_from_words(words[split_index:]),
            ]
    return [block]


def _group_line_words_into_blocks(words: list[dict[str, Any]], block_gap_threshold: float = 18.0) -> list[dict[str, Any]]:
    """Raggruppa le word di una riga in blocchi orizzontali."""
    sorted_words = sorted(words, key=lambda item: float(item["x0"]))
    if not sorted_words:
        return []

    grouped: list[list[dict[str, Any]]] = [[sorted_words[0]]]
    last_x1 = float(sorted_words[0]["x1"])
    for word in sorted_words[1:]:
        gap = float(word["x0"]) - last_x1
        if gap > block_gap_threshold:
            grouped.append([word])
        else:
            grouped[-1].append(word)
        last_x1 = float(word["x1"])

    blocks = [_build_block_from_words(group) for group in grouped]
    refined: list[dict[str, Any]] = []
    for block in blocks:
        refined.extend(_split_block_on_inline_dose_header(block))
    return refined


def _render_line_from_blocks(blocks: list[dict[str, Any]]) -> str:
    """Rende una riga leggibile dai blocchi ordinati."""
    return "\t".join(block["text"] for block in blocks if block["text"]).strip()


def _clone_line_with_blocks(line: dict[str, Any], blocks: list[dict[str, Any]], text_override: str | None = None) -> dict[str, Any]:
    """Clona una riga sostituendo il sottoinsieme di blocchi rilevante."""
    return {
        **line,
        "text": text_override if text_override is not None else _render_line_from_blocks(blocks),
        "blocks": list(blocks),
    }


# ---------------------------------------------------------------------------
# Tipografia e parsing righe vaccinali
# ---------------------------------------------------------------------------


def _word_is_bold(word: dict[str, Any]) -> bool:
    """Riconosce le word in bold dal nome font."""
    return "bold" in str(word.get("fontname") or "").casefold()


def _block_is_bold(block: dict[str, Any]) -> bool:
    """Classifica un blocco come bold se la maggioranza delle word e bold."""
    words = list(block.get("words") or [])
    if not words:
        return False
    bold_count = sum(1 for word in words if _word_is_bold(word))
    return bold_count >= max(1, len(words) // 2)


def _block_text_matches_dose_header(block: dict[str, Any]) -> bool:
    """Controlla se un blocco e un header dose-data puro."""
    return bool(DOSE_DATE_RE.fullmatch(_normalize_text(str(block.get("text") or ""))))


def _extract_name_fragment_from_blocks(blocks: list[dict[str, Any]]) -> str | None:
    """Estrae il frammento di nome vaccino dalla zona sinistra della riga."""
    words: list[dict[str, Any]] = []
    for block in blocks:
        words.extend(list(block.get("words") or []))
    if not words:
        return None

    bold_words = [word for word in words if _word_is_bold(word)]
    if not bold_words:
        return None

    max_size = max(float(word.get("size", 0.0)) for word in bold_words)
    size_threshold = max_size - 0.35
    selected = [
        word
        for word in words
        if _word_is_bold(word) and float(word.get("size", 0.0)) >= size_threshold
    ]
    return _normalize_text(_words_to_text(selected)) or None


def _partition_blocks_by_boundary(blocks: list[dict[str, Any]], boundary_x: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Divide i blocchi in parole a sinistra/destra di un confine X."""
    left_blocks: list[dict[str, Any]] = []
    right_blocks: list[dict[str, Any]] = []

    for block in blocks:
        words = list(block.get("words") or [])
        if not words:
            continue

        left_words = [word for word in words if float(word.get("x1", 0.0)) <= boundary_x]
        right_words = [word for word in words if float(word.get("x0", 0.0)) > boundary_x]

        if left_words:
            left_blocks.append(_build_block_from_words(left_words))
        if right_words:
            right_blocks.append(_build_block_from_words(right_words))

    return left_blocks, right_blocks


def _find_body_start_index(lines: list[dict[str, Any]]) -> int | None:
    """Trova l'inizio del corpo tabellare vaccinale nella pagina."""
    for index, line in enumerate(lines):
        if "HA EFFETTUATO LE SEGUENTI VACCINAZIONI" in str(line.get("text") or "").upper():
            return index + 1
    for index, line in enumerate(lines):
        if any(_block_is_bold(block) and _block_text_matches_dose_header(block) for block in line.get("blocks", [])):
            return index
    return None


def _is_vaccination_table_end_line(line: dict[str, Any] | str) -> bool:
    """Riconosce il blocco note che segue la tabella vaccinale finale."""
    raw_text = line if isinstance(line, str) else str(line.get("text") or "")
    normalized = _normalize_text(raw_text).upper()
    return any(normalized.startswith(prefix) for prefix in VACCINATION_TABLE_END_PREFIXES)


def _iter_vaccination_body_lines(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Restituisce solo le righe del corpo vaccinale, scartando header/footer."""
    flattened: list[dict[str, Any]] = []
    table_ended = False
    for page in pages:
        if table_ended:
            break
        lines = page.get("lines", [])
        start_index = _find_body_start_index(lines)
        if start_index is None:
            continue
        for line in lines[start_index:]:
            if _is_vaccination_table_end_line(line):
                table_ended = True
                break
            if _is_common_noise_line(line) or _is_footer_line(line):
                continue
            flattened.append(line)
    return flattened


def _extract_dose_columns_from_blocks(blocks: list[dict[str, Any]], page_number: int, source_kind: str) -> list[dict[str, Any]]:
    """Converte i blocchi header dose in strutture dose_columns."""
    columns: list[dict[str, Any]] = []
    for block in blocks:
        header_text = _normalize_text(str(block.get("text") or ""))
        match = DOSE_DATE_RE.fullmatch(header_text)
        if not match:
            continue
        columns.append(
            {
                "header_text": header_text,
                "dose_number": match.group("dose"),
                "date": match.group("date"),
                "x0": block["x0"],
                "x1": block["x1"],
                "center_x": block["center_x"],
                "source_kind": source_kind,
                "source_page_number": page_number,
            }
        )
    return columns


def _build_dose_zones(dose_columns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Costruisce le zone orizzontali di assegnazione dei dettagli dose."""
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


def _merge_section_dose_columns(current_section: dict[str, Any], new_columns: list[dict[str, Any]]) -> None:
    """Aggiunge nuove dosi a una sezione gia aperta senza duplicarle."""
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for column in list(current_section.get("dose_columns", [])) + new_columns:
        key = (str(column.get("dose_number") or ""), str(column.get("date") or ""))
        if key in seen:
            continue
        seen.add(key)
        merged.append(column)
    current_section["dose_columns"] = _build_dose_zones(merged)


def _append_label_continuation(current_section: dict[str, Any], line: dict[str, Any]) -> None:
    """Appende una continuation del nome vaccino a una sezione aperta."""
    normalized = _normalize_text(str(line["text"]))
    if not normalized:
        return
    current_section["vaccine_label_raw_lines"].append(normalized)
    current_section["label_continuation_lines"].append(normalized)
    current_section["name_wrapped"] = True
    if line["page_number"] != current_section["last_page_number"]:
        current_section["page_wrapped"] = True
    current_section["last_page_number"] = line["page_number"]


def _dose_sort_key(dose_column: dict[str, Any]) -> tuple[int, str, float]:
    """Ordina le dosi per numero crescente, poi data e posizione."""
    raw_dose = str(dose_column.get("dose_number") or "")
    try:
        dose_number = int(raw_dose)
    except ValueError:
        dose_number = 9999
    return (dose_number, str(dose_column.get("date") or ""), float(dose_column.get("center_x") or 0.0))


def _overlap_length(start_a: float, end_a: float, start_b: float, end_b: float) -> float:
    """Calcola la lunghezza di overlap tra due segmenti orizzontali."""
    return max(0.0, min(end_a, end_b) - max(start_a, start_b))


def _classify_block_to_doses(block: dict[str, Any], dose_columns: list[dict[str, Any]]) -> dict[str, Any]:
    """Assegna un blocco di dettaglio alla dose piu probabile."""
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


def _label_needs_review(vaccine_label: str) -> bool:
    """Segnala i label palesemente incompleti."""
    normalized = _normalize_text(vaccine_label)
    return not normalized or normalized.endswith("-") or normalized.endswith("/")


def _finalize_section(section: dict[str, Any]) -> dict[str, Any]:
    """Chiude una sezione vaccinale e costruisce il payload finale."""
    dose_columns = sorted(_build_dose_zones(list(section.get("dose_columns", []))), key=_dose_sort_key)
    vaccine_label = _normalize_text(" ".join(section.get("vaccine_label_raw_lines", [])))

    detail_lines = list(section.get("detail_lines", []))
    doses: list[dict[str, Any]] = []
    for dose_column in dose_columns:
        exact_blocks: list[dict[str, Any]] = []
        ambiguous_blocks: list[dict[str, Any]] = []
        for detail_line in detail_lines:
            for block in detail_line.get("blocks", []):
                assignment = _classify_block_to_doses(block, dose_columns)
                if assignment.get("assigned_dose_number") != dose_column.get("dose_number"):
                    continue
                payload = {
                    "text": block.get("text"),
                    "assignment_mode": assignment.get("assignment_mode"),
                    "candidate_doses": assignment.get("candidate_doses", []),
                }
                if assignment.get("assignment_mode") == "exact":
                    exact_blocks.append(payload)
                else:
                    ambiguous_blocks.append(payload)

        if exact_blocks and not ambiguous_blocks:
            confidence = "high"
        elif exact_blocks or ambiguous_blocks:
            confidence = "low"
        else:
            confidence = "missing_details"

        doses.append(
            {
                "dose_number": dose_column.get("dose_number"),
                "date": dose_column.get("date"),
                "header_text": dose_column.get("header_text"),
                "source_kind": dose_column.get("source_kind"),
                "source_page_number": dose_column.get("source_page_number"),
                "confidence": confidence,
                "exact_detail_texts": [block["text"] for block in exact_blocks],
                "ambiguous_detail_blocks": ambiguous_blocks,
            }
        )

    return {
        "page_number": section.get("first_page_number"),
        "last_page_number": section.get("last_page_number"),
        "vaccine_label_raw_lines": section.get("vaccine_label_raw_lines", []),
        "vaccine_label": vaccine_label,
        "header_line_text": section.get("header_line_text"),
        "header_continuation_lines": section.get("header_continuation_lines", []),
        "label_continuation_lines": section.get("label_continuation_lines", []),
        "name_wrapped": bool(section.get("name_wrapped")),
        "dose_wrapped": bool(section.get("dose_wrapped")),
        "page_wrapped": bool(section.get("page_wrapped")),
        "label_needs_review": _label_needs_review(vaccine_label),
        "doses": sorted(doses, key=_dose_sort_key),
    }


def _extract_vaccine_sections_from_rows(pages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Esegue il parsing principale del corpo vaccinale riga per riga."""
    sections: list[dict[str, Any]] = []
    current_section: dict[str, Any] | None = None
    row_debug: list[dict[str, Any]] = []

    for line in _iter_vaccination_body_lines(pages):
        blocks = list(line.get("blocks") or [])
        if not blocks:
            continue

        dose_blocks = [block for block in blocks if _block_is_bold(block) and _block_text_matches_dose_header(block)]
        first_dose_x0 = min((float(block["x0"]) for block in dose_blocks), default=None)

        if first_dose_x0 is not None:
            left_blocks, right_remainder_blocks = _partition_blocks_by_boundary(
                [block for block in blocks if block not in dose_blocks],
                first_dose_x0 - 4.0,
            )
        elif current_section and current_section.get("dose_columns"):
            boundary_x = min(float(column["x0"]) for column in current_section["dose_columns"]) - 4.0
            left_blocks, right_remainder_blocks = _partition_blocks_by_boundary(blocks, boundary_x)
        else:
            left_blocks, right_remainder_blocks = list(blocks), []

        label_text = _extract_name_fragment_from_blocks(left_blocks)
        note_blocks = [block for block in right_remainder_blocks if not _block_is_bold(block)]
        row_debug.append(
            {
                "page_number": line["page_number"],
                "text": line["text"],
                "label_text": label_text,
                "dose_headers": [_normalize_text(str(block["text"])) for block in dose_blocks],
                "non_bold_note_blocks": [_normalize_text(str(block["text"])) for block in note_blocks],
            }
        )

        if dose_blocks:
            source_kind = "inline_header"
            if current_section is not None and not label_text:
                source_kind = "page_continuation" if line["page_number"] != current_section["last_page_number"] else "line_continuation"
            row_dose_columns = _extract_dose_columns_from_blocks(dose_blocks, line["page_number"], source_kind=source_kind)

            if label_text:
                if current_section is not None:
                    sections.append(_finalize_section(current_section))
                current_section = {
                    "vaccine_label_raw_lines": [label_text],
                    "header_line_text": line["text"],
                    "header_continuation_lines": [],
                    "label_continuation_lines": [],
                    "dose_columns": _build_dose_zones(row_dose_columns),
                    "detail_lines": [],
                    "name_wrapped": False,
                    "dose_wrapped": False,
                    "page_wrapped": False,
                    "first_page_number": line["page_number"],
                    "last_page_number": line["page_number"],
                }
            elif current_section is not None:
                _merge_section_dose_columns(current_section, row_dose_columns)
                current_section["header_continuation_lines"].append(_render_line_from_blocks(dose_blocks))
                current_section["dose_wrapped"] = True
                if line["page_number"] != current_section["last_page_number"]:
                    current_section["page_wrapped"] = True
                current_section["last_page_number"] = line["page_number"]
            else:
                continue

            if current_section is not None and note_blocks:
                current_section["detail_lines"].append(_clone_line_with_blocks(line, note_blocks))
                current_section["last_page_number"] = line["page_number"]
            continue

        if current_section is None:
            continue

        appended = False
        if label_text:
            _append_label_continuation(current_section, _clone_line_with_blocks(line, left_blocks, text_override=label_text))
            appended = True

        continuation_note_blocks = [block for block in right_remainder_blocks if not _block_is_bold(block)]
        if not continuation_note_blocks and not label_text:
            continuation_note_blocks = [block for block in blocks if not _block_is_bold(block)]
        if continuation_note_blocks:
            current_section["detail_lines"].append(_clone_line_with_blocks(line, continuation_note_blocks))
            appended = True

        if appended and line["page_number"] != current_section["last_page_number"]:
            current_section["page_wrapped"] = True
        if appended:
            current_section["last_page_number"] = line["page_number"]

    if current_section is not None:
        sections.append(_finalize_section(current_section))

    return sections, {"parser": "row_column_v1", "rows": row_debug}


# ---------------------------------------------------------------------------
# Estrazione layout e reader tecnico
# ---------------------------------------------------------------------------


def _is_footer_line(line: dict[str, Any]) -> bool:
    """Riconosce le righe footer/istituzionali del certificato."""
    text = str(line["text"]).strip()
    return any(text.startswith(prefix) for prefix in FOOTER_PREFIXES)


def _is_common_noise_line(line: dict[str, Any]) -> bool:
    """Riconosce le righe di rumore comuni dei PDF esportati."""
    text = _normalize_text(str(line["text"]).lower())
    return any(text.startswith(prefix) for prefix in COMMON_NOISE_PREFIXES)


def _build_layout_artifact(path: Path) -> dict[str, Any]:
    """Estrae il layout PDF e costruisce le sezioni vaccinali."""
    pages: list[dict[str, Any]] = []
    with pdfplumber.open(path) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            raw_words = page.extract_words(use_text_flow=False, keep_blank_chars=False, extra_attrs=["fontname", "size"])
            raw_lines = _group_words_into_lines(raw_words)
            lines: list[dict[str, Any]] = []
            for raw_line in raw_lines:
                blocks = _group_line_words_into_blocks(raw_line["words"])
                line_text = _render_line_from_blocks(blocks)
                if not line_text:
                    continue
                top = min(float(block["top"]) for block in blocks)
                bottom = max(float(block["bottom"]) for block in blocks)
                lines.append(
                    {
                        "page_number": page_number,
                        "top": round(top, 1),
                        "bottom": round(bottom, 1),
                        "text": line_text,
                        "blocks": blocks,
                    }
                )

            pages.append(
                {
                    "page_number": page_number,
                    "width": round(float(page.width), 1),
                    "height": round(float(page.height), 1),
                    "text": "\n".join(line["text"] for line in lines).strip(),
                    "lines": lines,
                }
            )

    text = "\n\n".join(page["text"] for page in pages if page["text"]).strip()
    vaccination_sections, grid_debug = _extract_vaccine_sections_from_rows(pages)
    return {
        "source_path": str(path),
        "page_count": len(pages),
        "text": text,
        "pages": pages,
        "vaccination_sections": vaccination_sections,
        "grid_debug": grid_debug,
    }


def _build_vaccination_reader(layout_artifact: dict[str, Any]) -> dict[str, Any]:
    """Costruisce l'output tecnico del reader vaccinale."""
    return {
        "reader_name": "vaccination_row_column_stage1",
        "reader_version": "stage1_v1",
        "source_path": layout_artifact.get("source_path"),
        "page_count": layout_artifact.get("page_count"),
        "grid_model": layout_artifact.get("grid_debug", {}),
        "vaccines": layout_artifact.get("vaccination_sections", []),
    }


def _render_reader_text(reader: dict[str, Any]) -> str:
    """Rende una vista testuale leggibile dell'output tecnico del reader."""
    lines: list[str] = [
        "Vaccination certificate reader output.",
        "This is not the final clinical JSON.",
        "Use exact details as stronger evidence and ambiguous details as weaker evidence.",
    ]
    for vaccine in reader.get("vaccines", []):
        lines.append("")
        lines.append(f"VACCINE: {vaccine.get('vaccine_label')}")
        lines.append(f"HEADER: {vaccine.get('header_line_text')}")
        raw_lines = vaccine.get("vaccine_label_raw_lines", [])
        if raw_lines:
            lines.append("LABEL_RAW_LINES:")
            for raw_line in raw_lines:
                lines.append(f"- {raw_line}")
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
        lines.append(
            "FLAGS: "
            f"name_wrapped={vaccine.get('name_wrapped')} "
            f"dose_wrapped={vaccine.get('dose_wrapped')} "
            f"page_wrapped={vaccine.get('page_wrapped')} "
            f"label_needs_review={vaccine.get('label_needs_review')}"
        )
        for dose in vaccine.get("doses", []):
            lines.append(
                "DOSE "
                f"{dose.get('dose_number')} | DATE {dose.get('date')} | SOURCE {dose.get('source_kind')} | "
                f"PAGE {dose.get('source_page_number')} | CONFIDENCE {dose.get('confidence')}"
            )
            exact_texts = dose.get("exact_detail_texts", [])
            lines.append("EXACT_DETAILS:" if exact_texts else "EXACT_DETAILS: none")
            for dose_text in exact_texts:
                lines.append(f"- {dose_text}")
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


# ---------------------------------------------------------------------------
# Metadata documento e paziente
# ---------------------------------------------------------------------------


def _normalize_date(raw: str | None) -> str | None:
    """Normalizza una data `dd/mm/yyyy` in `yyyy-mm-dd`."""
    if not raw:
        return None
    parts = re.split(r"[/-]", raw)
    if len(parts) != 3:
        return raw
    day, month, year = parts
    if len(year) == 2:
        year = f"20{year}" if int(year) <= 30 else f"19{year}"
    return f"{year}-{month}-{day}"


def _extract_filename_snapshot_date(pdf_path: Path) -> str | None:
    """Estrae la data snapshot dal nome file del certificato."""
    match = re.search(r"_(\d{8})(?:\d{6})?(?:_|\.pdf$)", pdf_path.name)
    if not match:
        return None
    raw = match.group(1)
    return f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}"


def _normalize_street_prefix(address: str) -> str:
    """Normalizza i prefissi stradali piu comuni."""
    cleaned = _normalize_text(address).strip(" -")
    if not cleaned:
        return cleaned
    parts = cleaned.split(" ", 1)
    normalized_prefix = STREET_PREFIX_NORMALIZATION.get(parts[0].upper(), parts[0].upper())
    return normalized_prefix if len(parts) == 1 else f"{normalized_prefix} {parts[1]}".strip()


def _dedupe_preserve_order(items: list[Any]) -> list[Any]:
    """Rimuove i duplicati preservando l'ordine originale."""
    seen: set[str] = set()
    output: list[Any] = []
    for item in items:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True) if isinstance(item, (dict, list)) else str(item)
        if key not in seen:
            seen.add(key)
            output.append(item)
    return output


def _extract_vaccination_identity(text: str) -> dict[str, Any]:
    """Estrae identita paziente e codice fiscale dal testo del certificato."""
    lines = [_normalize_text(line) for line in text.splitlines() if _normalize_text(line)]
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
                full_name = _normalize_text(name_match.group(1))

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
                street_address = address_suffix or None
                next_line = lines[index + 1] if index + 1 < len(lines) else ""
                if next_line and not next_line.upper().startswith("HA EFFETTUATO") and not DOSE_DATE_RE.search(next_line):
                    identity_lines.append(next_line)
                    street_address = f"{street_address} {next_line}".strip() if street_address else next_line
                address_or_residence = _normalize_street_prefix(street_address) if street_address else residence_city
            break

    if not tax_code:
        tax_code_match = TAX_CODE_RE.search(text)
        if tax_code_match:
            tax_code = tax_code_match.group(0)

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


def _extract_typed_dates_for_vaccination(pdf_path: Path, patient_payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Raccoglie le date tipizzate usate nel JSON finale."""
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

    snapshot_date = _extract_filename_snapshot_date(pdf_path)
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
    return vaccination_dates


def _match_first(patterns: tuple[re.Pattern[str], ...], text: str) -> str | None:
    """Restituisce il primo match significativo tra piu regex."""
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            return _normalize_text(match.group(0).upper())
    return None


def _extract_issuer_metadata(layout_artifact: dict[str, Any]) -> dict[str, Any]:
    """Estrae regione ed ente erogatore da header e footer del documento."""
    observations: list[dict[str, Any]] = []
    for page in layout_artifact.get("pages", []):
        page_height = float(page.get("height") or 0.0)
        for line in page.get("lines", []):
            source = None
            if float(line["top"]) <= 140.0:
                source = "header"
            elif float(line["bottom"]) >= page_height - 140.0:
                source = "footer"
            if source is None:
                continue

            normalized = _normalize_text(str(line["text"]).upper())
            region = REGION_RE.search(normalized)
            authority = _match_first(AUTHORITY_RES, normalized)
            if region:
                observations.append(
                    {
                        "source": source,
                        "page": page["page_number"],
                        "kind": "region",
                        "text": normalized,
                        "value": _normalize_text(region.group(0).upper()),
                    }
                )
            if authority:
                observations.append(
                    {
                        "source": source,
                        "page": page["page_number"],
                        "kind": "authority",
                        "text": normalized,
                        "value": authority,
                    }
                )

    header_region = next((item["value"] for item in observations if item["source"] == "header" and item["kind"] == "region"), None)
    footer_region = next((item["value"] for item in observations if item["source"] == "footer" and item["kind"] == "region"), None)
    header_authority = next((item["value"] for item in observations if item["source"] == "header" and item["kind"] == "authority"), None)
    footer_authority = next((item["value"] for item in observations if item["source"] == "footer" and item["kind"] == "authority"), None)

    resolution_bits: list[str] = []
    if header_region or header_authority:
        resolution_bits.append("header")
    if footer_region or footer_authority:
        resolution_bits.append("footer")

    return {
        "issuer_region": header_region or footer_region,
        "issuing_authority": header_authority or footer_authority,
        "issuer_resolution_source": "+".join(resolution_bits) if resolution_bits else None,
        "issuer_observations": _dedupe_preserve_order(observations),
    }


# ---------------------------------------------------------------------------
# Costruzione contenuto e output finale
# ---------------------------------------------------------------------------


def _clean_common_lines(text: str) -> list[str]:
    """Pulisce le righe vuote e il rumore piu frequente."""
    cleaned: list[str] = []
    for raw_line in text.splitlines():
        line = _normalize_text(raw_line)
        if not line:
            continue
        if any(line.lower().startswith(prefix) for prefix in COMMON_NOISE_PREFIXES):
            continue
        cleaned.append(line)
    return cleaned


def _strip_vaccination_non_clinical_lines(lines: list[str]) -> list[str]:
    """Rimuove le righe non cliniche dal testo pulito del certificato."""
    start_index = 0
    for index, line in enumerate(lines):
        if line.upper().startswith("HA EFFETTUATO LE SEGUENTI VACCINAZIONI"):
            start_index = index + 1
            break

    filtered: list[str] = []
    for line in lines[start_index:]:
        if _is_vaccination_table_end_line(line):
            break
        if any(line.startswith(prefix) for prefix in FOOTER_PREFIXES):
            continue
        if line.startswith("SI CERTIFICA CHE"):
            continue
        if line.lower().startswith("il sig") or line.lower().startswith("la sig"):
            continue
        if line.lower().startswith("nato a ") or line.lower().startswith("nata a "):
            continue
        filtered.append(line)
    return filtered


def phase1_extract_text(pdf_path: Path) -> dict[str, Any]:
    """Esegue l'estrazione base del testo del PDF."""
    extraction = extract_text_from_pdf(pdf_path)
    return {
        "text": extraction.text,
        "page_count": extraction.page_count,
        "character_count": extraction.character_count,
    }


def phase2_classify_document(pdf_path: Path) -> dict[str, Any]:
    """Classifica il documento e abilita il parser vaccinale anche per i file `vaccin*`."""
    family = classify_document_family(pdf_path)
    use_vaccination_parser = family == DocumentFamily.VACCINATION_CERTIFICATE or _looks_like_vaccination_pdf(pdf_path)
    return {
        "document_family": DocumentFamily.VACCINATION_CERTIFICATE if use_vaccination_parser else family,
        "document_subcategory": "vaccination_document" if use_vaccination_parser else None,
        "use_vaccination_parser": use_vaccination_parser,
    }


def phase3_extract_vaccination_metadata(pdf_path: Path, extracted_text: dict[str, Any], layout_artifact: dict[str, Any]) -> dict[str, Any]:
    """Estrae metadata documento e identita paziente dal testo vaccinale."""
    patient = _extract_vaccination_identity(extracted_text["text"])
    dates = _extract_typed_dates_for_vaccination(pdf_path, patient)
    issuer = _extract_issuer_metadata(layout_artifact)
    return {
        "patient": patient,
        "dates": dates,
        "document": {
            "document_id": pdf_path.stem,
            "source_path": str(pdf_path),
            "document_snapshot_date": _extract_filename_snapshot_date(pdf_path),
            "issuer_region": issuer.get("issuer_region"),
            "issuing_authority": issuer.get("issuing_authority"),
            "issuer_resolution_source": issuer.get("issuer_resolution_source"),
            "issuer_observations": issuer.get("issuer_observations", []),
        },
    }


def phase4_build_vaccination_interpretation(extracted_text: dict[str, Any], layout_artifact: dict[str, Any], reader: dict[str, Any]) -> dict[str, Any]:
    """Costruisce il contenuto clinico pulito e gli artifact di debug."""
    cleaned_lines = _strip_vaccination_non_clinical_lines(_clean_common_lines(extracted_text["text"]))
    reader_text = _render_reader_text(reader)
    return {
        "content": {
            "cleaned_text": "\n".join(cleaned_lines).strip(),
            "relevant_lines": cleaned_lines[:80],
            "boilerplate_notes": [
                "common_prefix_filter_applied",
                "vaccination_non_clinical_lines_removed",
            ],
        },
        "specialized": {
            "vaccination_reader": reader,
        },
        "debug_artifacts": {
            "extracted_text": extracted_text["text"],
            "interpreted_text": reader_text,
            "layout_text": layout_artifact["text"],
            "layout_words": layout_artifact,
            "grid_debug": layout_artifact.get("grid_debug", {}),
            "reader_text": reader_text,
            "reader_json": reader,
        },
    }


def _render_interpreted_text(interpreted_json: dict[str, Any]) -> str:
    """Rende una vista testuale del JSON interpretato finale."""
    lines: list[str] = []
    document = interpreted_json["document"]
    patient = interpreted_json["patient"]
    content = interpreted_json["content"]
    dates = interpreted_json["dates"]
    reader = interpreted_json.get("specialized", {}).get("vaccination_reader", {})

    lines.append("DOCUMENT")
    lines.append(f"document_id: {document.get('document_id')}")
    lines.append(f"source_path: {document.get('source_path')}")
    lines.append(f"document_family: {document.get('document_family')}")
    lines.append(f"document_subcategory: {document.get('document_subcategory') or 'none'}")
    lines.append(f"document_snapshot_date: {document.get('document_snapshot_date') or 'unknown'}")
    lines.append(f"issuer_region: {document.get('issuer_region') or 'unknown'}")
    lines.append(f"issuing_authority: {document.get('issuing_authority') or 'unknown'}")
    lines.append(f"issuer_resolution_source: {document.get('issuer_resolution_source') or 'unknown'}")
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
    lines.append("CONTENT")
    lines.append(content.get("cleaned_text") or "")
    if reader:
        lines.append("")
        lines.append(_render_reader_text(reader).strip())
    return "\n".join(lines).strip() + "\n"


def phase5_build_output(pdf_path: Path, classification: dict[str, Any], metadata: dict[str, Any], interpretation: dict[str, Any]) -> dict[str, Any]:
    """Combina metadata, classificazione e interpretazione nel JSON finale."""
    interpreted_json = {
        "document": {
            **metadata["document"],
            "document_family": classification["document_family"].value,
            "document_subcategory": classification.get("document_subcategory"),
        },
        "patient": metadata["patient"],
        "dates": metadata["dates"],
        "classification": {
            "tags": ["vaccination_record"],
            "keyword_hits": ["vaccin"],
        },
        "content": interpretation["content"],
        "specialized": interpretation["specialized"],
    }
    return {
        "interpreted_json": interpreted_json,
        "interpreted_text": _render_interpreted_text(interpreted_json),
        "debug_artifacts": interpretation.get("debug_artifacts", {}),
    }


def _write_debug_artifacts(paths: dict[str, Path], output: dict[str, Any]) -> None:
    """Scrive gli artifact di debug opzionali di stage 1."""
    debug_artifacts = output.get("debug_artifacts", {})
    if not debug_artifacts:
        return
    paths["extracted_text"].write_text(debug_artifacts["extracted_text"], encoding="utf-8")
    paths["interpreted_text"].write_text(output["interpreted_text"], encoding="utf-8")
    paths["layout_text"].write_text(debug_artifacts["layout_text"], encoding="utf-8")
    paths["layout_words"].write_text(json.dumps(debug_artifacts["layout_words"], indent=2, ensure_ascii=False), encoding="utf-8")
    paths["grid_debug"].write_text(json.dumps(debug_artifacts.get("grid_debug", {}), indent=2, ensure_ascii=False), encoding="utf-8")
    paths["reader_text"].write_text(debug_artifacts["reader_text"], encoding="utf-8")
    paths["reader_json"].write_text(json.dumps(debug_artifacts["reader_json"], indent=2, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# Orchestrazione stage
# ---------------------------------------------------------------------------


def _run_stage1_for_pdf(pdf_path: Path, args: argparse.Namespace) -> int:
    """Esegue lo stage 1 vaccini su un singolo PDF."""
    document_root = _resolve_document_artifact_dir(pdf_path, args.artifacts_root.resolve())
    paths = _artifact_paths(document_root)

    if not args.force and _has_stage1_output(document_root):
        print("=== STAGE 1 GIA PRONTO ===")
        print(f"-> Documento: {pdf_path.name}")
        print(f"-> Artifact JSON esistente: {paths['interpreted_json']}")
        print("-> Salto l'estrazione per evitare passaggi inutili.")
        return 0

    paths["document_dir"].mkdir(parents=True, exist_ok=True)

    print("=== AVVIO STAGE 1 VACCINI ===")
    print(f"-> Documento: {pdf_path.name}")
    print(f"-> Artifact stage1: {paths['document_dir']}")

    print("\n[PHASE 1] Extract Text")
    extraction = phase1_extract_text(pdf_path)
    print(f"-> Pagine lette: {extraction['page_count']}")
    print(f"-> Caratteri estratti: {extraction['character_count']}")
    if extraction["character_count"] == 0:
        print("ERRORE: il PDF non contiene testo estraibile.")
        return 1

    print("\n[PHASE 2] Classify Document")
    classification = phase2_classify_document(pdf_path)
    print(f"-> Famiglia: {classification['document_family'].value}")
    if not classification["use_vaccination_parser"]:
        print("ERRORE: questo runner supporta solo documenti vaccinali.")
        return 1

    print("\n[PHASE 3] Parse Vaccination Layout + Metadata")
    layout_artifact = _build_layout_artifact(pdf_path)
    reader = _build_vaccination_reader(layout_artifact)
    metadata = phase3_extract_vaccination_metadata(pdf_path, extraction, layout_artifact)
    print(f"-> Paziente: {metadata['patient'].get('full_name') or 'unknown'}")
    print(f"-> Codice fiscale: {metadata['patient'].get('tax_code') or 'unknown'}")
    print(f"-> Voci vaccinali estratte: {len(reader.get('vaccines', []))}")
    print(f"-> Ente documento: {metadata['document'].get('issuing_authority') or 'unknown'}")

    print("\n[PHASE 4] Build Vaccination Interpretation")
    interpretation = phase4_build_vaccination_interpretation(extraction, layout_artifact, reader)
    print("-> JSON clinico vaccini costruito.")

    print("\n[PHASE 5] Write Output")
    output = phase5_build_output(pdf_path, classification, metadata, interpretation)
    paths["interpreted_json"].write_text(json.dumps(output["interpreted_json"], indent=2, ensure_ascii=False), encoding="utf-8")
    if args.debug_artifacts:
        _write_debug_artifacts(paths, output)

    print(f"-> Artifact standard: {paths['interpreted_json']}")
    if args.debug_artifacts:
        print("-> Artifact debug: extracted_text, interpreted_text, layout_*, reader_*")
    else:
        print("-> Nessun artifact debug scritto (usa --debug-artifacts per abilitarli).")

    print("\n=== STAGE 1 COMPLETATO ===")
    print("-> Parser vaccini pronto sugli artifact.")
    return 0


def main() -> int:
    """Entry point CLI di stage 1 vaccini."""
    args = _build_parser().parse_args()
    pdf_paths, warnings = _resolve_requested_pdfs(args)

    for warning in warnings:
        print(f"ATTENZIONE: {warning}")
    if not pdf_paths:
        print("ERRORE: nessun documento da processare.")
        return 1

    if not args.person and not args.pdf:
        print("-> Comportamento di default: scansione di tutti i pazienti sotto data/raw.")

    exit_code = 1 if any(warning.startswith("ERRORE ") for warning in warnings) else 0
    for index, pdf_path in enumerate(pdf_paths, start=1):
        if len(pdf_paths) > 1:
            print(f"\n##### CERTIFICATO VACCINALE {index}/{len(pdf_paths)} #####")
            print(f"-> Persona: {pdf_path.parent.name}")
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
