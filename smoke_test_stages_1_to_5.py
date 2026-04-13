"""Test di integrazione reale per la pipeline Value4Care.

Questo file e volutamente sperimentale: qui possiamo provare strategie di
estrazione senza toccare i sorgenti del progetto. In particolare, per i
certificati vaccinali, produciamo artefatti piu ricchi che preservano:
- il testo lineare
- il layout a colonne
- il legame spaziale tra una dose e i dettagli stampati sotto quella colonna
- i casi ambigui, senza forzare una classificazione falsa
- un reader intermedio pensato come input migliore per l'LLM
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import pdfplumber

# Configura i percorsi per importare dalla cartella src/
ROOT_DIR = Path(__file__).resolve().parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from clinical import DocumentFamily
from config import get_config
from stage1_pdf_reading import classify_document_family, extract_text_from_pdf
from stage2_llm_runtime import OllamaClient, build_document_prompt, parse_json_payload
from stage5_interface.api import build_app


DOSE_DATE_RE = re.compile(r"(?P<dose>\d+)\s*-\s*(?P<date>\d{2}[/-]\d{2}[/-]\d{2,4})")
FOOTER_PREFIXES = (
    "ATS DI ",
    "VIA DUCA ",
    "P.IVA:",
    "Documento firmato",
    "I dati presenti",
)
ZONE_EDGE_TOLERANCE = 6.0


def _artifact_paths(document_root: Path) -> dict[str, Path]:
    """Definisce tutti gli artefatti usati dal solo smoke test."""
    return {
        "document_dir": document_root,
        "extracted_text": document_root / "extracted_text.txt",
        "layout_text": document_root / "layout_text.txt",
        "layout_words": document_root / "layout_words.json",
        "reader_text": document_root / "reader_text.txt",
        "reader_json": document_root / "reader.json",
        "llm_prompt": document_root / "llm_prompt.txt",
        "llm_response": document_root / "llm_response.json",
    }


def _words_to_text(words: list[dict[str, Any]]) -> str:
    """Unisce le parole di un blocco in una frase breve."""
    return " ".join(str(word["text"]) for word in words).strip()


def _group_words_into_lines(
    words: list[dict[str, Any]],
    line_tolerance: float = 4.0,
) -> list[dict[str, Any]]:
    """Raggruppa parole con coordinate verticali simili nella stessa riga."""
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
    """Separa i blocchi orizzontali della riga."""
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


def _parse_vaccine_header(line: dict[str, Any]) -> dict[str, Any] | None:
    vaccine_label_parts: list[str] = []
    dose_columns: list[dict[str, Any]] = []
    seen_first_dose = False

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

    vaccine_label = " ".join(part for part in vaccine_label_parts if part).strip(" -")
    if vaccine_label and dose_columns:
        return {
            "vaccine_label": vaccine_label,
            "dose_columns": _build_dose_zones(dose_columns),
        }
    return None


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


def _overlap_length(start_a: float, end_a: float, start_b: float, end_b: float) -> float:
    return max(0.0, min(end_a, end_b) - max(start_a, start_b))


def _classify_block_to_doses(
    block: dict[str, Any],
    dose_columns: list[dict[str, Any]],
) -> dict[str, Any]:
    block_left = float(block["x0"])
    block_right = float(block["x1"])
    block_width = max(1.0, block_right - block_left)

    candidates: list[dict[str, Any]] = []
    for column in dose_columns:
        overlap = _overlap_length(
            block_left,
            block_right,
            float(column["zone_left"]),
            float(column["zone_right"]),
        )
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
                "dose_columns": header["dose_columns"],
                "detail_lines": [],
            }
            continue

        if current_section is None:
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
            current_section["detail_lines"].append(
                {
                    "text": line_text,
                    "blocks": detail_blocks,
                }
            )

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
    if "ambiguous_edge" in modes:
        return "medium"
    return "medium"


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

            vaccines.append(
                {
                    "page_number": page.get("page_number"),
                    "vaccine_label": section.get("vaccine_label"),
                    "header_line_text": section.get("header_line_text"),
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
        for dose in vaccine.get("doses", []):
            lines.append(
                f"DOSE {dose.get('dose_number')} | DATE {dose.get('date')} | CONFIDENCE {dose.get('confidence')}"
            )

            exact_texts = dose.get("exact_detail_texts", [])
            if exact_texts:
                lines.append("EXACT_DETAILS:")
                for text in exact_texts:
                    lines.append(f"- {text}")
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


def _build_vaccination_llm_prompt(reader_text: str, source_document: Path) -> tuple[str, str]:
    """Costruisce prompt e system dedicati al certificato vaccinale."""
    system_prompt = (
        "You are extracting structured vaccination records from a layout-aware reader. "
        "Return only valid JSON. Never invent information. If evidence is weak or ambiguous, "
        "keep the vaccine dose row but use null for uncertain fields and explain ambiguity in notes."
    )

    prompt = f"""
You are given a layout-aware reader output for one vaccination certificate.

Source document: {source_document}

Your task:
- Produce one JSON object only.
- Produce one vaccination entry for each vaccine dose that has a date.
- Use exact details as the strongest evidence.
- Use ambiguous details only when they strongly support a field; otherwise leave the field null and explain why in ambiguity_notes.
- Convert dates from DD/MM/YYYY or DD-MM-YYYY to YYYY-MM-DD.
- Do not invent product names, lot codes, dose amounts, or administration routes.
- If a dose exists but no reliable details are attached, still include the dose with null detail fields.

Return exactly this JSON shape:
{{
  "document_type": "vaccination_certificate",
  "source_document": "{source_document}",
  "vaccinations": [
    {{
      "vaccine": "string",
      "dose_number": 0,
      "administration_date": "YYYY-MM-DD",
      "product_name": "string or null",
      "dose_amount_text": "string or null",
      "lot_code": "string or null",
      "confidence": "high|medium|low",
      "evidence_type": "exact|ambiguous|missing",
      "ambiguity_notes": ["string", "..."]
    }}
  ]
}}

Reader output:
{reader_text}
""".strip()

    return system_prompt, prompt


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
                lines.append(
                    {
                        "top": round(float(raw_line["top"]), 1),
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
                    "vaccine_sections": _extract_vaccine_sections(lines),
                }
            )

    return {
        "source_path": str(path),
        "page_count": len(pages),
        "text": "\n\n".join(page["text"] for page in pages if page["text"]).strip(),
        "pages": pages,
    }


def main() -> int:
    print("=== AVVIO TEST PIPELINE REALE ===")
    config = get_config()

    pdf_path = (
        ROOT_DIR
        / "data"
        / "raw"
        / "person001"
        / "CertificatoVaccinale_LMNLCU02E15D918M_20260303104746.pdf"
    )

    if not pdf_path.exists():
        print(f"ERRORE: PDF non trovato in {pdf_path}")
        print("Usa un PDF esistente modificando il percorso nello script.")
        return 1

    artifacts_root = config.artifacts_dir / "pipeline_reale" / pdf_path.stem
    paths = _artifact_paths(artifacts_root)
    paths["document_dir"].mkdir(parents=True, exist_ok=True)

    print(f"\n[BLOCCO 1 - PDF READING] Analisi di: {pdf_path.name}")
    family = classify_document_family(pdf_path)
    print(f"-> Famiglia riconosciuta: {family.value}")

    extraction = extract_text_from_pdf(pdf_path)
    print(f"-> Pagine lette: {extraction.page_count}")
    print(f"-> Caratteri estratti: {extraction.character_count}")

    if extraction.character_count == 0:
        print("ERRORE: Il PDF e vuoto o e un'immagine senza testo estraibile.")
        print("In quel caso serve una pipeline OCR separata.")
        return 1

    paths["extracted_text"].write_text(extraction.text, encoding="utf-8")
    print(f"-> Artefatto testo grezzo salvato in: {paths['extracted_text']}")

    system_prompt: str | None = None
    prompt_input_text = extraction.text
    if family == DocumentFamily.VACCINATION_CERTIFICATE:
        print("-> Documento vaccinale rilevato: genero artefatti layout-aware nel solo smoke test.")
        layout_artifact = _extract_layout_artifact(pdf_path)
        reader = _build_vaccination_reader(layout_artifact)
        reader_text = _render_reader_text(reader)

        paths["layout_text"].write_text(layout_artifact["text"], encoding="utf-8")
        paths["layout_words"].write_text(
            json.dumps(layout_artifact, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        paths["reader_text"].write_text(reader_text, encoding="utf-8")
        paths["reader_json"].write_text(
            json.dumps(reader, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        system_prompt, prompt_input_text = _build_vaccination_llm_prompt(reader_text, pdf_path)
        print(f"-> Artefatto layout testuale: {paths['layout_text']}")
        print(f"-> Artefatto layout strutturato: {paths['layout_words']}")
        print(f"-> Artefatto reader testuale: {paths['reader_text']}")
        print(f"-> Artefatto reader strutturato: {paths['reader_json']}")
        print("-> Il reader e l'input intermedio pensato per il prompt LLM.")
    else:
        prompt_input_text = build_document_prompt(family, prompt_input_text)

    paths["llm_prompt"].write_text(prompt_input_text, encoding="utf-8")

    print("\n[BLOCCO 2 - LLM RUNTIME] Preparazione prompt e analisi")
    print(f"-> Prompt generato ({len(prompt_input_text)} caratteri).")

    client = OllamaClient(
        base_url=config.ollama_base_url,
        model=config.ollama_model,
        timeout_seconds=180,
    )

    print(f"-> Contatto Ollama su {config.ollama_base_url} con modello {config.ollama_model}...")
    try:
        raw_llm_payload = client.generate(prompt_input_text, system=system_prompt)
        raw_llm_text = str(raw_llm_payload.get("response", "")).strip()
        if not raw_llm_text:
            print("-> ATTENZIONE: Ollama ha risposto, ma il campo 'response' e vuoto.")
            print("-> Il test di stage 1 e comunque riuscito: controlla gli artefatti salvati.")
            return 0

        parsed_payload = parse_json_payload(raw_llm_text)
        print(f"-> Dati clinici estratti: {list(parsed_payload.keys())}")

        paths["llm_response"].write_text(
            json.dumps(
                {
                    "system_prompt": system_prompt,
                    "prompt": prompt_input_text,
                    "raw": raw_llm_payload,
                    "parsed": parsed_payload,
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        print(f"-> Prompt salvato in: {paths['llm_prompt']}")
        print(f"-> Risposta LLM salvata in: {paths['llm_response']}")
    except Exception as exc:
        print(f"-> ATTENZIONE: impossibile usare Ollama o parsare la risposta ({exc}).")
        print("-> Il test di stage 1 e comunque riuscito: controlla gli artefatti salvati sopra.")
        return 0

    print("\n[BLOCCO 3 - DATABASE] Artefatti pronti")
    print(f"-> Directory documento: {paths['document_dir']}")

    print("\n[BLOCCO 4 - MINING] Stato attuale")
    print("-> Qui agganceremo il parsing degli eventi clinici una volta fissato lo schema LLM.")

    print("\n[BLOCCO 5 - INTERFACE] Stato Applicazione")
    app = build_app()
    print(f"-> Applicazione '{app.title}' caricata correttamente e pronta per servire i dati.")

    print("\n=== TEST COMPLETATO CON SUCCESSO ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
