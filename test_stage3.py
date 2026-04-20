"""Runner standalone per testare il database vaccini della pipeline Value4Care.

Questo file prende l'output di test_stage1.py e lo salva in due database SQLite
separati dentro `aggregated database/`:
- vaccini.sqlite
- anagrafiche_pazienti.sqlite

Per ora non crea nessun database aggregato e non passa dall'LLM.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent
DEFAULT_DB_DIR = ROOT_DIR / "aggregated database"
DEFAULT_ARTIFACTS_ROOT = ROOT_DIR / "artifacts"
DEFAULT_UTILITY_DIR = ROOT_DIR / "data" / "utility"
VACCINI_NOMI_TSV_NAME = "vaccini_nomi.tsv"
LEGACY_VACCINI_NOMI_CSV_NAME = "vaccini_nomi.csv"


def _looks_like_vaccination_artifact_dir(path: Path) -> bool:
    return path.is_dir() and "vaccin" in path.name.casefold() and (path / "interpreted_text.json").exists()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Test standalone del database vaccini senza usare l'LLM."
    )
    parser.add_argument(
        "--interpreted-json",
        type=Path,
        help="Path a interpreted_text.json prodotto da test_stage1.py",
    )
    parser.add_argument(
        "--person",
        type=str,
        help="Importa solo i certificati vaccinali di una persona, cercando gli artifacts stage1 sotto artifacts/<person>/.",
    )
    parser.add_argument(
        "--vaccini-all",
        action="store_true",
        help="Cicla su tutti gli artifacts vaccinali disponibili sotto artifacts/person*/CertificatoVaccinale*/.",
    )
    parser.add_argument(
        "--artifacts-root",
        type=Path,
        default=DEFAULT_ARTIFACTS_ROOT,
        help="Root degli artifacts stage1 organizzati per persona.",
    )
    parser.add_argument(
        "--db-dir",
        type=Path,
        default=DEFAULT_DB_DIR,
        help="Cartella in cui creare vaccini.sqlite e anagrafiche_pazienti.sqlite",
    )
    parser.add_argument(
        "--utility-dir",
        type=Path,
        default=DEFAULT_UTILITY_DIR,
        help="Cartella utility per le trasformazioni effective dei vaccini.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Reimporta un documento anche se nel database risultano gia righe con la stessa origine documento.",
    )
    return parser


def _load_interpreted_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _find_vaccination_artifacts_for_person(person_dir: Path) -> list[Path]:
    return sorted(path / "interpreted_text.json" for path in person_dir.iterdir() if _looks_like_vaccination_artifact_dir(path))


def _discover_vaccination_artifacts(artifacts_root: Path) -> tuple[list[Path], list[str]]:
    interpreted_paths: list[Path] = []
    warnings: list[str] = []
    for person_dir in sorted(path for path in artifacts_root.iterdir() if path.is_dir() and path.name.startswith("person")):
        matches = _find_vaccination_artifacts_for_person(person_dir)
        if matches:
            interpreted_paths.extend(matches)
        else:
            warnings.append(f"{person_dir.name}: nessun artifact vaccinale stage1 trovato")
    return interpreted_paths, warnings


def _resolve_patient_code(interpreted_json_path: Path, payload: dict[str, Any]) -> str:
    parent_name = interpreted_json_path.parent.parent.name if interpreted_json_path.parent.parent else ""
    if parent_name.startswith("person"):
        return parent_name
    tax_code = payload.get("patient", {}).get("tax_code")
    if tax_code:
        return tax_code.lower()
    return "patient_unknown"


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


def _derive_gender_from_tax_code(tax_code: str | None) -> str | None:
    """Deriva il genere dal giorno codificato nel codice fiscale."""
    cleaned = str(tax_code or "").strip().upper()
    if len(cleaned) < 11:
        return None
    raw_day = cleaned[9:11]
    if not raw_day.isdigit():
        return None
    day_value = int(raw_day)
    if day_value < 35:
        return "M"
    if day_value > 35:
        return "F"
    return None


def _flatten_note_text(exact_detail_texts: list[str], ambiguous_detail_blocks: list[dict[str, Any]]) -> str | None:
    parts: list[str] = []
    for item in exact_detail_texts:
        cleaned = str(item).strip()
        if cleaned:
            parts.append(cleaned)
    for block in ambiguous_detail_blocks:
        cleaned = str(block.get("text", "")).strip()
        if cleaned:
            parts.append(cleaned)
    if not parts:
        return None

    deduped: list[str] = []
    seen: set[str] = set()
    for part in parts:
        key = part.casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(part)
    return " | ".join(deduped)


def _effective_vaccination_sort_key(item: dict[str, Any]) -> tuple[str, str, int]:
    """Ordina le dosi effective per data crescente con tie-break stabile."""
    normalized_date = _normalize_date(item.get("data")) or "9999-99-99"
    return (
        normalized_date,
        str(item.get("document_id") or ""),
        int(item.get("payload_order") or 0),
    )


def _build_vaccini_raw_rows(payload: dict[str, Any], interpreted_json_path: Path) -> list[dict[str, Any]]:
    """Costruisce le righe raw in memoria, senza correzioni semantiche."""
    codice_persona = _resolve_patient_code(interpreted_json_path, payload)
    document = payload.get("document", {})
    reader = payload.get("specialized", {}).get("vaccination_reader", {})
    origine_documento = document.get("source_path")
    document_id = document.get("document_id") or interpreted_json_path.parent.name
    ente_erogatore = document.get("issuing_authority") or ""

    grouped_rows: dict[str, list[dict[str, Any]]] = {}
    payload_order = 0
    for vaccine in reader.get("vaccines", []):
        vaccine_label = (vaccine.get("vaccine_label") or "vaccino_sconosciuto").strip()
        for dose in vaccine.get("doses", []):
            payload_order += 1
            administration_date = _normalize_date(dose.get("date"))
            grouped_rows.setdefault(vaccine_label, []).append({
                "codice_persona": codice_persona,
                "data": administration_date,
                "tipo_documento": "riepilogo vaccinale",
                "tipo_evento": "vaccino",
                "sottotipo_evento_raw": vaccine_label,
                "dose_number_raw": dose.get("dose_number"),
                "specifiche_sottotipo_evento": None,
                "sessione_id": f"{codice_persona}::{administration_date}" if administration_date else None,
                "care_thread": "vaccinazioni",
                "ente_erogatore": ente_erogatore,
                "note": _flatten_note_text(
                    dose.get("exact_detail_texts", []),
                    dose.get("ambiguous_detail_blocks", []),
                ),
                "origine_documento": origine_documento,
                "document_id": document_id,
                "payload_order": payload_order,
            })

    return sorted(
        [row for vaccine_rows in grouped_rows.values() for row in vaccine_rows],
        key=lambda row: (
            row["codice_persona"],
            row["sottotipo_evento_raw"],
            row["payload_order"],
            row["data"] or "",
        ),
    )


def _resolve_vaccini_nomi_tsv_path(utility_dir: Path) -> Path:
    """Restituisce il path del TSV utility dei nomi vaccino."""
    return utility_dir / VACCINI_NOMI_TSV_NAME


def _resolve_legacy_vaccini_nomi_csv_path(utility_dir: Path) -> Path:
    """Restituisce il vecchio path CSV dei nomi vaccino, usato per migrazione morbida."""
    return utility_dir / LEGACY_VACCINI_NOMI_CSV_NAME


def _load_delimited_vaccini_rows(path: Path, delimiter: str) -> list[dict[str, str]]:
    """Carica un file delimitato dei nomi vaccino se esiste gia."""
    if not path.exists():
        return []

    rows: list[dict[str, str]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        for row in reader:
            nome_parsing = str(row.get("nome_parsing") or "").strip()
            if not nome_parsing:
                continue
            rows.append(
                {
                    "nome_parsing": nome_parsing,
                    "nome_corrected": str(row.get("nome_corrected") or "").strip(),
                }
            )
    return rows


def _load_vaccini_nomi_rows(utility_dir: Path) -> list[dict[str, str]]:
    """Carica i nomi vaccino dal TSV corrente o dal CSV legacy."""
    tsv_path = _resolve_vaccini_nomi_tsv_path(utility_dir)
    if tsv_path.exists():
        return _load_delimited_vaccini_rows(tsv_path, "\t")

    legacy_csv_path = _resolve_legacy_vaccini_nomi_csv_path(utility_dir)
    if legacy_csv_path.exists():
        return _load_delimited_vaccini_rows(legacy_csv_path, ",")

    return []


def _write_vaccini_nomi_tsv(tsv_path: Path, rows: list[dict[str, str]]) -> None:
    """Scrive il TSV dei nomi vaccino in ordine alfabetico."""
    tsv_path.parent.mkdir(parents=True, exist_ok=True)
    with tsv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["nome_parsing", "nome_corrected"], delimiter="\t")
        writer.writeheader()
        for row in sorted(rows, key=lambda item: item["nome_parsing"].casefold()):
            writer.writerow(
                {
                    "nome_parsing": row["nome_parsing"],
                    "nome_corrected": row["nome_corrected"],
                }
            )


def _sync_vaccini_nomi_csv(raw_rows: list[dict[str, Any]], utility_dir: Path) -> dict[str, str]:
    """Allinea il TSV dei nomi vaccino senza sovrascrivere le correzioni manuali."""
    tsv_path = _resolve_vaccini_nomi_tsv_path(utility_dir)
    existing_rows = _load_vaccini_nomi_rows(utility_dir)
    by_name = {row["nome_parsing"]: row for row in existing_rows}

    unique_names = sorted(
        {
            str(row.get("sottotipo_evento_raw") or "").strip()
            for row in raw_rows
            if str(row.get("sottotipo_evento_raw") or "").strip()
        },
        key=str.casefold,
    )

    for name in unique_names:
        by_name.setdefault(name, {"nome_parsing": name, "nome_corrected": ""})

    merged_rows = list(by_name.values())
    _write_vaccini_nomi_tsv(tsv_path, merged_rows)

    overrides: dict[str, str] = {}
    for row in merged_rows:
        corrected = row["nome_corrected"].strip()
        if corrected:
            overrides[row["nome_parsing"]] = corrected
    return overrides


def _apply_vaccine_name_overrides(raw_rows: list[dict[str, Any]], overrides: dict[str, str]) -> list[dict[str, Any]]:
    """Applica gli eventuali nomi effective ai vaccini raw."""
    effective_rows: list[dict[str, Any]] = []
    for row in raw_rows:
        normalized_name = overrides.get(row["sottotipo_evento_raw"], row["sottotipo_evento_raw"])
        effective_rows.append(
            {
                **row,
                "sottotipo_evento": normalized_name,
            }
        )
    return effective_rows


def _apply_polivalenti_passthrough(effective_rows: list[dict[str, Any]], utility_dir: Path) -> list[dict[str, Any]]:
    """Hook futuro per la gestione dei polivalenti; per ora lascia invariato il dataset."""
    _ = utility_dir
    return effective_rows


def _linearize_vaccine_doses(effective_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rinumera le dosi per paziente+vaccino in modo lineare e crescente."""
    grouped_rows: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in effective_rows:
        key = (row["codice_persona"], row["sottotipo_evento"])
        grouped_rows.setdefault(key, []).append(row)

    linearized_rows: list[dict[str, Any]] = []
    for vaccine_rows in grouped_rows.values():
        for linearized_index, row in enumerate(sorted(vaccine_rows, key=_effective_vaccination_sort_key), start=1):
            linearized_rows.append(
                {
                    **row,
                    "specifiche_sottotipo_evento": linearized_index,
                }
            )
    return linearized_rows


def _strip_internal_vaccini_fields(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rimuove i campi interni del passaggio raw -> effective prima di scrivere il DB."""
    cleaned_rows: list[dict[str, Any]] = []
    for row in rows:
        cleaned_rows.append(
            {
                key: value
                for key, value in row.items()
                if key not in {"payload_order", "dose_number_raw", "sottotipo_evento_raw"}
            }
        )
    return cleaned_rows


def _build_vaccini_rows(payload: dict[str, Any], interpreted_json_path: Path, utility_dir: Path) -> list[dict[str, Any]]:
    """Costruisce le righe finali effective a partire dal livello raw in memoria."""
    raw_rows = _build_vaccini_raw_rows(payload, interpreted_json_path)
    overrides = _sync_vaccini_nomi_csv(raw_rows, utility_dir)
    effective_rows = _apply_vaccine_name_overrides(raw_rows, overrides)
    effective_rows = _apply_polivalenti_passthrough(effective_rows, utility_dir)
    effective_rows = _linearize_vaccine_doses(effective_rows)
    effective_rows = _strip_internal_vaccini_fields(effective_rows)
    return sorted(
        effective_rows,
        key=lambda row: (
            row["codice_persona"],
            row["sottotipo_evento"],
            row["specifiche_sottotipo_evento"] or 0,
            row["data"] or "",
        ),
    )


def _build_anagrafica_row(payload: dict[str, Any], interpreted_json_path: Path) -> dict[str, Any]:
    patient = payload.get("patient", {})
    document = payload.get("document", {})
    tax_code = patient.get("tax_code")
    return {
        "codice_paziente": _resolve_patient_code(interpreted_json_path, payload),
        "nome": patient.get("given_name"),
        "cognome": patient.get("family_name"),
        "nome_completo": patient.get("full_name"),
        "genere": _derive_gender_from_tax_code(tax_code),
        "codice_fiscale": tax_code,
        "data_nascita": patient.get("birth_date"),
        "luogo_nascita": patient.get("birth_place"),
        "citta_residenza": patient.get("residence_city"),
        "indirizzo_residenza": patient.get("address_or_residence"),
        "data_rilevamento": document.get("document_snapshot_date"),
    }


def _ensure_anagrafiche_schema(conn: sqlite3.Connection) -> None:
    """Allinea la tabella anagrafiche al layout desiderato, preservando i dati."""
    desired_columns = [
        "codice_paziente",
        "nome",
        "cognome",
        "nome_completo",
        "genere",
        "codice_fiscale",
        "data_nascita",
        "luogo_nascita",
        "citta_residenza",
        "indirizzo_residenza",
        "data_rilevamento",
    ]
    existing_columns = [
        row_info[1]
        for row_info in conn.execute("PRAGMA table_info(anagrafiche_pazienti)").fetchall()
    ]
    if existing_columns == desired_columns:
        return

    conn.execute(
        """
        CREATE TABLE anagrafiche_pazienti_new (
            codice_paziente TEXT NOT NULL,
            nome TEXT,
            cognome TEXT,
            nome_completo TEXT,
            genere TEXT,
            codice_fiscale TEXT,
            data_nascita TEXT,
            luogo_nascita TEXT,
            citta_residenza TEXT,
            indirizzo_residenza TEXT,
            data_rilevamento TEXT NOT NULL,
            UNIQUE (codice_paziente, codice_fiscale, data_rilevamento)
        )
        """
    )

    common_columns = [column for column in desired_columns if column in existing_columns]
    if common_columns:
        column_list = ", ".join(common_columns)
        insert_columns = ", ".join(common_columns)
        conn.execute(
            f"""
            INSERT INTO anagrafiche_pazienti_new ({insert_columns})
            SELECT {column_list}
            FROM anagrafiche_pazienti
            """
        )

    conn.execute("DROP TABLE anagrafiche_pazienti")
    conn.execute("ALTER TABLE anagrafiche_pazienti_new RENAME TO anagrafiche_pazienti")


def _init_vaccini_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vaccini (
            codice_persona TEXT NOT NULL,
            data TEXT,
            tipo_documento TEXT NOT NULL,
            tipo_evento TEXT NOT NULL,
            sottotipo_evento TEXT,
            specifiche_sottotipo_evento INTEGER,
            sessione_id TEXT,
            care_thread TEXT NOT NULL,
            ente_erogatore TEXT,
            note TEXT,
            origine_documento TEXT NOT NULL,
            document_id TEXT,
            UNIQUE (codice_persona, data, sottotipo_evento, specifiche_sottotipo_evento, origine_documento)
        )
        """
    )
    columns = {
        row_info[1]
        for row_info in conn.execute("PRAGMA table_info(vaccini)").fetchall()
    }
    if "sessione_id" not in columns:
        conn.execute("ALTER TABLE vaccini ADD COLUMN sessione_id TEXT")
    if "document_id" not in columns:
        conn.execute("ALTER TABLE vaccini ADD COLUMN document_id TEXT")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS documenti_importati (
            document_id TEXT PRIMARY KEY,
            codice_persona TEXT NOT NULL,
            source_document TEXT NOT NULL,
            artifact_path TEXT NOT NULL,
            imported_at TEXT NOT NULL
        )
        """
    )


def _init_anagrafiche_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS anagrafiche_pazienti (
            codice_paziente TEXT NOT NULL,
            nome TEXT,
            cognome TEXT,
            nome_completo TEXT,
            genere TEXT,
            codice_fiscale TEXT,
            data_nascita TEXT,
            luogo_nascita TEXT,
            citta_residenza TEXT,
            indirizzo_residenza TEXT,
            data_rilevamento TEXT NOT NULL,
            UNIQUE (codice_paziente, codice_fiscale, data_rilevamento)
        )
        """
    )
    _ensure_anagrafiche_schema(conn)


def _insert_vaccini_rows(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> None:
    conn.executemany(
        """
        INSERT OR REPLACE INTO vaccini (
            codice_persona, data, tipo_documento, tipo_evento, sottotipo_evento,
            specifiche_sottotipo_evento, sessione_id, care_thread, ente_erogatore, note, origine_documento, document_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                row["codice_persona"],
                row["data"],
                row["tipo_documento"],
                row["tipo_evento"],
                row["sottotipo_evento"],
                row["specifiche_sottotipo_evento"],
                row["sessione_id"],
                row["care_thread"],
                row["ente_erogatore"],
                row["note"],
                row["origine_documento"],
                row["document_id"],
            )
            for row in rows
        ],
    )


def _insert_anagrafica_row(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO anagrafiche_pazienti (
            codice_paziente, nome, cognome, nome_completo, genere, codice_fiscale,
            data_nascita, luogo_nascita, citta_residenza, indirizzo_residenza, data_rilevamento
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row["codice_paziente"],
            row["nome"],
            row["cognome"],
            row["nome_completo"],
            row["genere"],
            row["codice_fiscale"],
            row["data_nascita"],
            row["luogo_nascita"],
            row["citta_residenza"],
            row["indirizzo_residenza"],
            row["data_rilevamento"],
        ),
    )


def _document_already_imported(conn: sqlite3.Connection, document_id: str | None) -> bool:
    if not document_id:
        return False
    row = conn.execute(
        "SELECT 1 FROM documenti_importati WHERE document_id = ? LIMIT 1",
        (document_id,),
    ).fetchone()
    return row is not None


def _register_imported_document(
    conn: sqlite3.Connection,
    document_id: str,
    codice_persona: str,
    source_document: str,
    artifact_path: str,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO documenti_importati (
            document_id, codice_persona, source_document, artifact_path, imported_at
        ) VALUES (?, ?, ?, ?, datetime('now'))
        """,
        (document_id, codice_persona, source_document, artifact_path),
    )


def _delete_document_rows(conn: sqlite3.Connection, source_document: str | None, document_id: str | None) -> None:
    """Rimuove le righe gia importate per un documento prima di un reimport forzato."""
    if source_document:
        conn.execute("DELETE FROM vaccini WHERE origine_documento = ?", (source_document,))
    elif document_id:
        conn.execute("DELETE FROM vaccini WHERE document_id = ?", (document_id,))
    if document_id:
        conn.execute("DELETE FROM documenti_importati WHERE document_id = ?", (document_id,))


def _resolve_requested_interpreted_jsons(args: argparse.Namespace) -> tuple[list[Path], list[str]]:
    artifacts_root = args.artifacts_root.resolve()
    warnings: list[str] = []

    if args.interpreted_json:
        return [args.interpreted_json.resolve()], warnings

    if args.vaccini_all or not args.person:
        if not artifacts_root.exists():
            return [], [f"root artifacts non trovata: {artifacts_root}"]
        return _discover_vaccination_artifacts(artifacts_root)

    person_dir = artifacts_root / args.person
    if not person_dir.exists():
        return [], [f"cartella artifacts non trovata: {person_dir}"]
    matches = _find_vaccination_artifacts_for_person(person_dir)
    if not matches:
        return [], [f"{args.person}: nessun artifact vaccinale stage1 trovato"]
    return matches, warnings


def _import_single_payload(
    interpreted_json_path: Path,
    vaccini_conn: sqlite3.Connection,
    anagrafiche_conn: sqlite3.Connection,
    force: bool,
    utility_dir: Path,
) -> tuple[str, str, int, bool]:
    payload = _load_interpreted_json(interpreted_json_path)
    vaccini_rows = _build_vaccini_rows(payload, interpreted_json_path, utility_dir)
    anagrafica_row = _build_anagrafica_row(payload, interpreted_json_path)
    document = payload.get("document", {})
    document_id = document.get("document_id") or interpreted_json_path.parent.name
    source_document = vaccini_rows[0]["origine_documento"] if vaccini_rows else document.get("source_path")

    if not force and _document_already_imported(vaccini_conn, document_id):
        return anagrafica_row["codice_paziente"], document_id, 0, True

    _delete_document_rows(vaccini_conn, source_document, document_id)
    _insert_vaccini_rows(vaccini_conn, vaccini_rows)
    _insert_anagrafica_row(anagrafiche_conn, anagrafica_row)
    _register_imported_document(
        vaccini_conn,
        document_id,
        anagrafica_row["codice_paziente"],
        source_document or "unknown_source_document",
        str(interpreted_json_path),
    )
    return anagrafica_row["codice_paziente"], document_id, len(vaccini_rows), False


def main() -> int:
    args = _build_parser().parse_args()
    interpreted_json_paths, warnings = _resolve_requested_interpreted_jsons(args)

    for warning in warnings:
        print(f"ATTENZIONE: {warning}")

    if not interpreted_json_paths:
        print("ERRORE: nessun artifact vaccinale da importare.")
        return 1

    db_dir = args.db_dir.resolve()
    db_dir.mkdir(parents=True, exist_ok=True)

    vaccini_db_path = db_dir / "vaccini.sqlite"
    anagrafiche_db_path = db_dir / "anagrafiche_pazienti.sqlite"
    utility_dir = args.utility_dir.resolve()
    utility_dir.mkdir(parents=True, exist_ok=True)
    vaccini_nomi_tsv_path = _resolve_vaccini_nomi_tsv_path(utility_dir)

    vaccini_conn = sqlite3.connect(vaccini_db_path, timeout=30)
    anagrafiche_conn = sqlite3.connect(anagrafiche_db_path, timeout=30)
    try:
        vaccini_conn.execute("PRAGMA journal_mode = WAL")
        anagrafiche_conn.execute("PRAGMA journal_mode = WAL")
        vaccini_conn.execute("PRAGMA busy_timeout = 30000")
        anagrafiche_conn.execute("PRAGMA busy_timeout = 30000")
        _init_vaccini_db(vaccini_conn)
        _init_anagrafiche_db(anagrafiche_conn)

        total_rows = 0
        imported_documents = 0
        skipped_documents = 0
        if not args.person:
            print("-> Comportamento di default: scansione di tutti gli artifacts vaccinali stage1.")

        for index, interpreted_json_path in enumerate(interpreted_json_paths, start=1):
            if len(interpreted_json_paths) > 1:
                print(f"\n##### IMPORT DATABASE VACCINI {index}/{len(interpreted_json_paths)} #####")
                print(f"-> Artifact: {interpreted_json_path}")
            elif args.person:
                print(f"-> Persona richiesta: {args.person}")

            if not interpreted_json_path.exists():
                print(f"ERRORE: file non trovato: {interpreted_json_path}")
                continue

            codice_persona, document_id, written_rows, skipped = _import_single_payload(
                interpreted_json_path,
                vaccini_conn,
                anagrafiche_conn,
                args.force,
                utility_dir,
            )
            if skipped:
                skipped_documents += 1
                print("-> Documento gia presente nel database, salto l'import per evitare passaggi inutili.")
                print(f"-> Codice persona: {codice_persona}")
                print(f"-> Document id: {document_id}")
                continue

            imported_documents += 1
            total_rows += written_rows
            print(f"-> Codice persona: {codice_persona}")
            print(f"-> Document id: {document_id}")
            print(f"-> Righe vaccini scritte: {written_rows}")
            print("-> Riga anagrafica scritta: 1")

        vaccini_conn.commit()
        anagrafiche_conn.commit()
    finally:
        vaccini_conn.close()
        anagrafiche_conn.close()

    print("\n=== TEST DATABASE VACCINI ===")
    print(f"-> cartella database: {db_dir}")
    print(f"-> vaccini sqlite: {vaccini_db_path}")
    print(f"-> anagrafiche sqlite: {anagrafiche_db_path}")
    print(f"-> utility nomi vaccini: {vaccini_nomi_tsv_path}")
    print(f"-> documenti importati: {imported_documents}")
    print(f"-> documenti saltati: {skipped_documents}")
    print(f"-> righe vaccini scritte in questo run: {total_rows}")
    print("-> aggregato non creato in questa fase.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
