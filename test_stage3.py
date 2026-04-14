"""Runner standalone per testare lo stage 3 della pipeline Value4Care.

Questo file prende l'output di stage1 e lo salva in un database SQLite locale,
per ora con focus sul caso vaccini senza passare dall'LLM.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent

DEFAULT_INTERPRETED_JSON = (
    ROOT_DIR
    / "artifacts"
    / "person001"
    / "CertificatoVaccinale_LMNLCU02E15D918M_20260303104746"
    / "interpreted_text.json"
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Test standalone di stage 3 su database SQLite.")
    parser.add_argument(
        "--interpreted-json",
        type=Path,
        default=DEFAULT_INTERPRETED_JSON,
        help="Path a interpreted_text.json prodotto da test_stage1.py",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help="Path finale del database SQLite. Se omesso usa artifacts/<person>/<person>_stage3.sqlite",
    )
    return parser


def _load_interpreted_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_patient_id(interpreted_json_path: Path, payload: dict[str, Any]) -> str:
    parent_name = interpreted_json_path.parent.parent.name if interpreted_json_path.parent.parent else ""
    if parent_name.startswith("person"):
        return parent_name
    tax_code = payload.get("patient", {}).get("tax_code")
    if tax_code:
        return tax_code.lower()
    return "patient_unknown"


def _resolve_db_path(interpreted_json_path: Path, payload: dict[str, Any], override: Path | None) -> Path:
    if override is not None:
        return override.resolve()
    patient_id = _resolve_patient_id(interpreted_json_path, payload)
    return ROOT_DIR / "artifacts" / patient_id / f"{patient_id}_stage3.sqlite"


def _normalize_vaccine_key(label: str) -> str:
    normalized = label.lower().strip()
    normalized = normalized.replace("*", " ")
    normalized = re.sub(r"[^a-z0-9]+", "_", normalized)
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return normalized


def _normalize_date(raw: str | None) -> str | None:
    if not raw:
        return None
    if re.match(r"\d{4}-\d{2}-\d{2}$", raw):
        return raw
    parts = re.split(r"[/-]", raw)
    if len(parts) != 3:
        return raw
    day, month, year = parts
    if len(year) == 2:
        year = f"20{year}" if int(year) <= 30 else f"19{year}"
    return f"{year}-{month}-{day}"


def _classify_vaccination_details(exact_detail_texts: list[str], ambiguous_detail_blocks: list[dict[str, Any]]) -> dict[str, Any]:
    evidence_type = "missing"
    if exact_detail_texts:
        evidence_type = "exact"
    elif ambiguous_detail_blocks:
        evidence_type = "ambiguous"

    amount_pattern = re.compile(r"\b\d+[\.,]?\d*\s*ML\b", re.IGNORECASE)
    code_pattern = re.compile(r"\b\d{6,}\b")

    dose_amount_text = None
    lot_code = None
    product_parts: list[str] = []

    for item in exact_detail_texts:
        if dose_amount_text is None and amount_pattern.search(item):
            dose_amount_text = item
            continue
        if lot_code is None and code_pattern.search(item):
            lot_code = code_pattern.search(item).group(0)
            continue
        product_parts.append(item)

    product_name = " ".join(product_parts).strip() or None
    ambiguity_notes = [block.get("text") for block in ambiguous_detail_blocks if block.get("text")]

    return {
        "product_name": product_name,
        "dose_amount_text": dose_amount_text,
        "lot_code": lot_code,
        "evidence_type": evidence_type,
        "ambiguity_notes": ambiguity_notes,
        "source_detail_texts": exact_detail_texts,
    }


def _build_vaccination_rows(payload: dict[str, Any], interpreted_json_path: Path) -> list[dict[str, Any]]:
    patient_id = _resolve_patient_id(interpreted_json_path, payload)
    document = payload.get("document", {})
    reader = payload.get("specialized", {}).get("vaccination_reader", {})
    rows: list[dict[str, Any]] = []

    for vaccine in reader.get("vaccines", []):
        vaccine_label = vaccine.get("vaccine_label") or "unknown_vaccine"
        vaccine_key = _normalize_vaccine_key(vaccine_label)
        for dose in vaccine.get("doses", []):
            details = _classify_vaccination_details(
                dose.get("exact_detail_texts", []),
                dose.get("ambiguous_detail_blocks", []),
            )
            dose_number = int(dose.get("dose_number")) if dose.get("dose_number") else None
            administration_date = _normalize_date(dose.get("date"))
            vaccination_id = f"{document.get('source_path')}::{vaccine_key}::{dose_number}::{administration_date}"
            rows.append(
                {
                    "vaccination_id": vaccination_id,
                    "document_id": document.get("source_path"),
                    "patient_id": patient_id,
                    "source_vaccine_label": vaccine_label,
                    "normalized_vaccine_key": vaccine_key,
                    "dose_number": dose_number,
                    "administration_date": administration_date,
                    "product_name": details["product_name"],
                    "dose_amount_text": details["dose_amount_text"],
                    "lot_code": details["lot_code"],
                    "confidence": dose.get("confidence"),
                    "evidence_type": details["evidence_type"],
                    "ambiguity_notes_json": json.dumps(details["ambiguity_notes"], ensure_ascii=False),
                    "source_detail_texts_json": json.dumps(details["source_detail_texts"], ensure_ascii=False),
                }
            )
    return rows


def _build_clinical_event_rows(vaccination_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for row in vaccination_rows:
        event_id = f"event::{row['vaccination_id']}"
        events.append(
            {
                "event_id": event_id,
                "patient_id": row["patient_id"],
                "document_id": row["document_id"],
                "event_type": "vaccination",
                "activity_label": f"Vaccination - {row['source_vaccine_label']}",
                "occurred_at": row["administration_date"],
                "source_table": "vaccinations",
                "source_row_id": row["vaccination_id"],
                "confidence": row["confidence"],
                "tags_json": json.dumps(
                    [
                        "vaccination",
                        row["normalized_vaccine_key"],
                        f"dose:{row['dose_number']}",
                    ],
                    ensure_ascii=False,
                ),
            }
        )
    return events


def _init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        PRAGMA foreign_keys = ON;

        CREATE TABLE IF NOT EXISTS patients (
            patient_id TEXT PRIMARY KEY,
            full_name TEXT,
            given_name TEXT,
            family_name TEXT,
            tax_code TEXT,
            birth_date TEXT,
            birth_place TEXT,
            residence_city TEXT,
            address_or_residence TEXT
        );

        CREATE TABLE IF NOT EXISTS documents (
            document_id TEXT PRIMARY KEY,
            patient_id TEXT NOT NULL,
            source_path TEXT NOT NULL,
            document_family TEXT,
            document_subcategory TEXT,
            issuing_organization TEXT,
            document_snapshot_date TEXT,
            parser TEXT,
            parser_version TEXT,
            FOREIGN KEY(patient_id) REFERENCES patients(patient_id)
        );

        CREATE TABLE IF NOT EXISTS document_artifacts (
            document_id TEXT PRIMARY KEY,
            extracted_text_path TEXT,
            interpreted_text_path TEXT,
            interpreted_json_path TEXT,
            prompt_main_path TEXT,
            layout_text_path TEXT,
            layout_words_path TEXT,
            reader_text_path TEXT,
            reader_json_path TEXT,
            FOREIGN KEY(document_id) REFERENCES documents(document_id)
        );

        CREATE TABLE IF NOT EXISTS vaccinations (
            vaccination_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            patient_id TEXT NOT NULL,
            source_vaccine_label TEXT,
            normalized_vaccine_key TEXT,
            dose_number INTEGER,
            administration_date TEXT,
            product_name TEXT,
            dose_amount_text TEXT,
            lot_code TEXT,
            confidence TEXT,
            evidence_type TEXT,
            ambiguity_notes_json TEXT,
            source_detail_texts_json TEXT,
            FOREIGN KEY(document_id) REFERENCES documents(document_id),
            FOREIGN KEY(patient_id) REFERENCES patients(patient_id)
        );

        CREATE TABLE IF NOT EXISTS clinical_events (
            event_id TEXT PRIMARY KEY,
            patient_id TEXT NOT NULL,
            document_id TEXT NOT NULL,
            event_type TEXT,
            activity_label TEXT,
            occurred_at TEXT,
            source_table TEXT,
            source_row_id TEXT,
            confidence TEXT,
            tags_json TEXT,
            FOREIGN KEY(document_id) REFERENCES documents(document_id),
            FOREIGN KEY(patient_id) REFERENCES patients(patient_id)
        );
        """
    )


def _insert_patient(conn: sqlite3.Connection, patient_id: str, payload: dict[str, Any]) -> None:
    patient = payload.get("patient", {})
    conn.execute(
        """
        INSERT OR REPLACE INTO patients (
            patient_id, full_name, given_name, family_name, tax_code, birth_date,
            birth_place, residence_city, address_or_residence
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            patient_id,
            patient.get("full_name"),
            patient.get("given_name"),
            patient.get("family_name"),
            patient.get("tax_code"),
            patient.get("birth_date"),
            patient.get("birth_place"),
            patient.get("residence_city"),
            patient.get("address_or_residence"),
        ),
    )


def _insert_document(conn: sqlite3.Connection, patient_id: str, payload: dict[str, Any]) -> str:
    document = payload.get("document", {})
    document_id = document.get("source_path")
    conn.execute(
        """
        INSERT OR REPLACE INTO documents (
            document_id, patient_id, source_path, document_family, document_subcategory,
            issuing_organization, document_snapshot_date, parser, parser_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            document_id,
            patient_id,
            document.get("source_path"),
            document.get("document_family"),
            document.get("document_subcategory"),
            document.get("issuing_organization"),
            document.get("document_snapshot_date"),
            "vaccination_reader_v1",
            "test_stage3_v1",
        ),
    )
    return document_id


def _insert_artifacts(conn: sqlite3.Connection, document_id: str, interpreted_json_path: Path) -> None:
    document_dir = interpreted_json_path.parent
    def maybe(name: str) -> str | None:
        path = document_dir / name
        return str(path) if path.exists() else None

    conn.execute(
        """
        INSERT OR REPLACE INTO document_artifacts (
            document_id, extracted_text_path, interpreted_text_path, interpreted_json_path,
            prompt_main_path, layout_text_path, layout_words_path, reader_text_path, reader_json_path
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            document_id,
            maybe("extracted_text.txt"),
            maybe("interpreted_text.txt"),
            str(interpreted_json_path),
            maybe("prompt_main.txt"),
            maybe("layout_text.txt"),
            maybe("layout_words.json"),
            maybe("reader_text.txt"),
            maybe("reader.json"),
        ),
    )


def _insert_vaccinations(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> None:
    conn.executemany(
        """
        INSERT OR REPLACE INTO vaccinations (
            vaccination_id, document_id, patient_id, source_vaccine_label, normalized_vaccine_key,
            dose_number, administration_date, product_name, dose_amount_text, lot_code,
            confidence, evidence_type, ambiguity_notes_json, source_detail_texts_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                row["vaccination_id"],
                row["document_id"],
                row["patient_id"],
                row["source_vaccine_label"],
                row["normalized_vaccine_key"],
                row["dose_number"],
                row["administration_date"],
                row["product_name"],
                row["dose_amount_text"],
                row["lot_code"],
                row["confidence"],
                row["evidence_type"],
                row["ambiguity_notes_json"],
                row["source_detail_texts_json"],
            )
            for row in rows
        ],
    )


def _insert_events(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> None:
    conn.executemany(
        """
        INSERT OR REPLACE INTO clinical_events (
            event_id, patient_id, document_id, event_type, activity_label, occurred_at,
            source_table, source_row_id, confidence, tags_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                row["event_id"],
                row["patient_id"],
                row["document_id"],
                row["event_type"],
                row["activity_label"],
                row["occurred_at"],
                row["source_table"],
                row["source_row_id"],
                row["confidence"],
                row["tags_json"],
            )
            for row in rows
        ],
    )


def main() -> int:
    args = _build_parser().parse_args()
    interpreted_json_path = args.interpreted_json.resolve()
    if not interpreted_json_path.exists():
        print(f"ERRORE: file non trovato: {interpreted_json_path}")
        return 1

    payload = _load_interpreted_json(interpreted_json_path)
    patient_id = _resolve_patient_id(interpreted_json_path, payload)
    db_path = _resolve_db_path(interpreted_json_path, payload, args.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    vaccination_rows = _build_vaccination_rows(payload, interpreted_json_path)
    event_rows = _build_clinical_event_rows(vaccination_rows)

    conn = sqlite3.connect(db_path)
    try:
        _init_db(conn)
        _insert_patient(conn, patient_id, payload)
        document_id = _insert_document(conn, patient_id, payload)
        _insert_artifacts(conn, document_id, interpreted_json_path)
        _insert_vaccinations(conn, vaccination_rows)
        _insert_events(conn, event_rows)
        conn.commit()
    finally:
        conn.close()

    print("=== TEST STAGE 3 ===")
    print(f"-> interpreted json: {interpreted_json_path}")
    print(f"-> db sqlite: {db_path}")
    print(f"-> patient_id: {patient_id}")
    print(f"-> vaccinations inserted: {len(vaccination_rows)}")
    print(f"-> clinical events inserted: {len(event_rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
