"""Runner standalone per testare lo stage 4 di process mining sui vaccini.

Questo file legge `aggregated database/vaccini.sqlite`, costruisce due log distinti:
- log delle sessioni vaccinali
- log della progressione delle dosi per singolo vaccino

Salva XES, JSON di preview, summary JSON e un report di validazione.
Non genera CSV.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pm4py
from pm4py.visualization.dfg import visualizer as dfg_visualizer
from pm4py.visualization.dfg.util import dfg_gviz

ROOT_DIR = Path(__file__).resolve().parent
DEFAULT_VACCINI_DB = ROOT_DIR / "aggregated database" / "vaccini.sqlite"
DEFAULT_OUTPUT_DIR = ROOT_DIR / "aggregated database" / "vaccini_mining"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Test standalone di stage 4 sui vaccini con PM4Py."
    )
    parser.add_argument(
        "--vaccini-db",
        type=Path,
        default=DEFAULT_VACCINI_DB,
        help="Path al database SQLite vaccini generato da test_stage3.py",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Cartella in cui salvare XES e output di mining",
    )
    return parser


def _load_vaccini_rows(db_path: Path) -> list[dict[str, Any]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        columns = {
            row_info[1]
            for row_info in conn.execute("PRAGMA table_info(vaccini)").fetchall()
        }
        select_session = "sessione_id" if "sessione_id" in columns else "NULL as sessione_id"
        rows = [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT codice_persona, data, tipo_documento, tipo_evento,
                       sottotipo_evento, specifiche_sottotipo_evento,
                       {select_session}, care_thread, ente_erogatore, note, origine_documento
                FROM vaccini
                ORDER BY codice_persona, data, sottotipo_evento, specifiche_sottotipo_evento
                """
            )
        ]
    finally:
        conn.close()

    for row in rows:
        if not row.get("sessione_id") and row.get("codice_persona") and row.get("data"):
            row["sessione_id"] = f"{row['codice_persona']}::{row['data']}"
    return rows


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"nan", "nat", "none"}:
        return ""
    return text


def _parse_iso_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _session_signature(rows: list[dict[str, Any]]) -> str:
    vaccine_names = sorted({_safe_text(row.get("sottotipo_evento")) for row in rows if _safe_text(row.get("sottotipo_evento"))})
    if not vaccine_names:
        return "Sessione - vaccini non specificati"
    return "Sessione - " + " + ".join(vaccine_names)


def _dose_signature(rows: list[dict[str, Any]]) -> str:
    items = []
    for row in sorted(rows, key=lambda item: (_safe_text(item.get("sottotipo_evento")), item.get("specifiche_sottotipo_evento") or 0)):
        vaccine_type = _safe_text(row.get("sottotipo_evento"))
        dose_number = row.get("specifiche_sottotipo_evento")
        if vaccine_type and dose_number is not None:
            items.append(f"{vaccine_type}:{dose_number}")
        elif vaccine_type:
            items.append(vaccine_type)
    return " | ".join(items)


def _group_session_rows(rows: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        patient_id = _safe_text(row.get("codice_persona"))
        session_id = _safe_text(row.get("sessione_id"))
        session_date = _safe_text(row.get("data"))
        if not patient_id or not session_id or not session_date:
            continue
        grouped.setdefault((patient_id, session_id), []).append(row)
    return grouped


def _build_session_records(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    grouped = _group_session_rows(rows)
    for (patient_id, session_id), session_rows in sorted(grouped.items(), key=lambda item: (item[0][0], item[1][0].get("data", ""))):
        timestamp = _safe_text(session_rows[0].get("data"))
        if not timestamp:
            continue
        records.append(
            {
                "case:concept:name": f"{patient_id}::vaccinazioni",
                "concept:name": _session_signature(session_rows),
                "time:timestamp": timestamp,
                "patient_id": patient_id,
                "sessione_id": session_id,
                "care_thread": _safe_text(session_rows[0].get("care_thread")) or "vaccinazioni",
                "tipo_documento": _safe_text(session_rows[0].get("tipo_documento")),
                "source_document": _safe_text(session_rows[0].get("origine_documento")),
                "vaccines_list": " | ".join(sorted({_safe_text(row.get("sottotipo_evento")) for row in session_rows if _safe_text(row.get("sottotipo_evento"))})),
                "dose_list": _dose_signature(session_rows),
                "vaccine_count": len({_safe_text(row.get("sottotipo_evento")) for row in session_rows if _safe_text(row.get("sottotipo_evento"))}),
            }
        )
    return records


def _build_progression_records(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for row in rows:
        patient_id = _safe_text(row.get("codice_persona"))
        vaccine_type = _safe_text(row.get("sottotipo_evento"))
        timestamp = _safe_text(row.get("data"))
        dose_number = row.get("specifiche_sottotipo_evento")
        if not patient_id or not vaccine_type or not timestamp or dose_number is None:
            continue
        records.append(
            {
                "case:concept:name": f"{patient_id}::{vaccine_type}",
                "concept:name": f"{vaccine_type} - Dose {dose_number}",
                "time:timestamp": timestamp,
                "patient_id": patient_id,
                "vaccine_type": vaccine_type,
                "dose_number": dose_number,
                "sessione_id": _safe_text(row.get("sessione_id")),
                "care_thread": _safe_text(row.get("care_thread")) or "vaccinazioni",
                "tipo_documento": _safe_text(row.get("tipo_documento")),
                "source_document": _safe_text(row.get("origine_documento")),
                "note": _safe_text(row.get("note")),
            }
        )
    return records


def _format_event_dataframe(records: list[dict[str, Any]]) -> pd.DataFrame:
    dataframe = pd.DataFrame(records)
    dataframe["time:timestamp"] = pd.to_datetime(dataframe["time:timestamp"], errors="coerce", utc=True)
    dataframe = dataframe.dropna(subset=["time:timestamp"]).copy()

    for column in dataframe.columns:
        if column != "time:timestamp":
            dataframe[column] = dataframe[column].apply(_safe_text if dataframe[column].dtype == object else lambda x: x)

    sort_columns = ["case:concept:name", "time:timestamp"]
    if "dose_number" in dataframe.columns:
        sort_columns.append("dose_number")
    sort_columns.append("concept:name")
    dataframe = dataframe.sort_values(by=sort_columns, kind="stable").reset_index(drop=True)

    return pm4py.format_dataframe(
        dataframe,
        case_id="case:concept:name",
        activity_key="concept:name",
        timestamp_key="time:timestamp",
    )


def _serialize_variants(variants: dict[Any, Any]) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    for activities, value in variants.items():
        count = value if isinstance(value, int) else len(value)
        serialized.append({"activities": list(activities), "count": count})
    serialized.sort(key=lambda item: (-item["count"], item["activities"]))
    return serialized


def _serialize_dfg(dfg: dict[tuple[str, str], int]) -> list[dict[str, Any]]:
    edges = [{"from": source, "to": target, "count": count} for (source, target), count in dfg.items()]
    edges.sort(key=lambda item: (-item["count"], item["from"], item["to"]))
    return edges


def _save_progression_dfg_png(dataframe: pd.DataFrame, output_dir: Path, prefix: str, summary: dict[str, Any]) -> Path:
    filtered_dataframe = _filter_cases_with_transitions(dataframe)
    if filtered_dataframe.empty:
        raise ValueError("no cases with transitions available for DFG visualization")

    full_dfg, full_start_activities, full_end_activities = pm4py.discover_dfg(
        dataframe,
        case_id_key="case:concept:name",
        activity_key="concept:name",
        timestamp_key="time:timestamp",
    )
    filtered_dfg, filtered_start_activities, filtered_end_activities = pm4py.discover_dfg(
        filtered_dataframe,
        case_id_key="case:concept:name",
        activity_key="concept:name",
        timestamp_key="time:timestamp",
    )
    activities_count = dict(Counter(dataframe["concept:name"].tolist()))

    parameters = {
        dfg_visualizer.Variants.FREQUENCY.value.Parameters.START_ACTIVITIES: filtered_start_activities,
        dfg_visualizer.Variants.FREQUENCY.value.Parameters.END_ACTIVITIES: filtered_end_activities,
        dfg_visualizer.Variants.FREQUENCY.value.Parameters.FORMAT: "png",
        dfg_visualizer.Variants.FREQUENCY.value.Parameters.GRAPH_TITLE: prefix.replace('_', ' ').title(),
    }
    gviz = dfg_visualizer.apply(filtered_dfg, activities_count=activities_count, parameters=parameters)

    missing_start = {act: count for act, count in full_start_activities.items() if act not in filtered_start_activities}
    missing_end = {act: count for act, count in full_end_activities.items() if act not in filtered_end_activities}
    missing_activities = sorted(set(missing_start).union(set(missing_end)))
    if missing_activities:
        activities_color = dfg_gviz.get_activities_color(activities_count)
        for act in missing_activities:
            gviz.node(
                str(hash(act)),
                f"{act} ({activities_count.get(act, 0)})",
                style="filled",
                fillcolor=activities_color.get(act, "#FFFFFF"),
                fontsize="12",
            )

    for act, count in missing_start.items():
        gviz.edge("@@startnode", str(hash(act)), label=str(count), fontsize="12", penwidth="1.0")
    for act, count in missing_end.items():
        gviz.edge(str(hash(act)), "@@endnode", label=str(count), fontsize="12", penwidth="1.0")

    dfg_path = output_dir / f"{prefix}_dfg.png"
    dot_path = output_dir / f"{prefix}_dfg.dot"
    dot_path.write_text(gviz.source, encoding="utf-8")
    gviz.render(outfile=str(dfg_path), cleanup=True)

    excluded_cases = int(dataframe["case:concept:name"].nunique() - filtered_dataframe["case:concept:name"].nunique())
    if excluded_cases > 0:
        summary["dfg_visualization_note"] = (
            "DFG PNG generated with PM4Py default styling; isolated single-event cases "
            f"are shown through added start/end arcs (single_event_cases={excluded_cases})."
        )
    return dfg_path


def _transition_timing_stats(dataframe: pd.DataFrame) -> list[dict[str, Any]]:
    transition_gaps: dict[tuple[str, str], list[int]] = {}
    for _, group in dataframe.groupby("case:concept:name"):
        group = group.sort_values(by=["time:timestamp", "concept:name"], kind="stable")
        previous_row = None
        for _, row in group.iterrows():
            if previous_row is not None:
                gap = row["time:timestamp"] - previous_row["time:timestamp"]
                key = (str(previous_row["concept:name"]), str(row["concept:name"]))
                transition_gaps.setdefault(key, []).append(int(gap.days))
            previous_row = row

    stats: list[dict[str, Any]] = []
    for (source, target), gaps in transition_gaps.items():
        stats.append(
            {
                "from": source,
                "to": target,
                "count": len(gaps),
                "avg_gap_days": round(sum(gaps) / len(gaps), 2),
                "min_gap_days": min(gaps),
                "max_gap_days": max(gaps),
                "gap_days": gaps,
            }
        )
    stats.sort(key=lambda item: (-item["count"], item["from"], item["to"]))
    return stats


def _case_timeline(dataframe: pd.DataFrame) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for case_id, group in dataframe.groupby("case:concept:name"):
        group = group.sort_values(by=["time:timestamp", "concept:name"], kind="stable")
        timestamps = list(group["time:timestamp"])
        gaps = []
        for previous, current in zip(timestamps, timestamps[1:]):
            delta = current - previous
            gaps.append({"from": previous.isoformat(), "to": current.isoformat(), "gap_days": delta.days})
        results.append(
            {
                "case_id": case_id,
                "event_count": int(len(group)),
                "first_event": timestamps[0].isoformat() if timestamps else None,
                "last_event": timestamps[-1].isoformat() if timestamps else None,
                "gaps": gaps,
            }
        )
    return results


def _filter_cases_with_transitions(dataframe: pd.DataFrame) -> pd.DataFrame:
    case_sizes = dataframe.groupby("case:concept:name").size()
    valid_cases = case_sizes[case_sizes > 1].index
    filtered = dataframe[dataframe["case:concept:name"].isin(valid_cases)].copy()
    return filtered.reset_index(drop=True)


def _build_summary(dataframe: pd.DataFrame) -> dict[str, Any]:
    dfg, start_activities, end_activities = pm4py.discover_dfg(
        dataframe,
        case_id_key="case:concept:name",
        activity_key="concept:name",
        timestamp_key="time:timestamp",
    )
    variants = pm4py.get_variants(
        dataframe,
        case_id_key="case:concept:name",
        activity_key="concept:name",
        timestamp_key="time:timestamp",
    )
    process_tree = pm4py.discover_process_tree_inductive(
        dataframe,
        case_id_key="case:concept:name",
        activity_key="concept:name",
        timestamp_key="time:timestamp",
    )
    min_timestamp = dataframe["time:timestamp"].min()
    max_timestamp = dataframe["time:timestamp"].max()
    activities_counter = Counter(dataframe["concept:name"].tolist())

    return {
        "case_count": int(dataframe["case:concept:name"].nunique()),
        "event_count": int(len(dataframe)),
        "activity_count": int(dataframe["concept:name"].nunique()),
        "activities": sorted(dataframe["concept:name"].dropna().unique().tolist()),
        "activity_frequencies": dict(sorted(activities_counter.items())),
        "date_range": {
            "first_event": min_timestamp.isoformat() if pd.notna(min_timestamp) else None,
            "last_event": max_timestamp.isoformat() if pd.notna(max_timestamp) else None,
        },
        "start_activities": dict(sorted(start_activities.items())),
        "end_activities": dict(sorted(end_activities.items())),
        "variants": _serialize_variants(variants),
        "dfg_edges": _serialize_dfg(dfg),
        "process_tree": str(process_tree),
        "cases": _case_timeline(dataframe),
        "transition_timing_days": _transition_timing_stats(dataframe),
    }


def _try_save_visualizations(dataframe: pd.DataFrame, output_dir: Path, prefix: str, summary: dict[str, Any]) -> dict[str, str]:
    saved: dict[str, str] = {}

    try:
        if "progressione" in prefix:
            dfg_path = _save_progression_dfg_png(dataframe, output_dir, prefix, summary)
            saved["dfg_png"] = str(dfg_path)
        else:
            dfg, start_activities, end_activities = pm4py.discover_dfg(
                dataframe,
                case_id_key="case:concept:name",
                activity_key="concept:name",
                timestamp_key="time:timestamp",
            )
            dfg_path = output_dir / f"{prefix}_dfg.png"
            pm4py.save_vis_dfg(
                dfg,
                start_activities,
                end_activities,
                str(dfg_path),
                graph_title=prefix.replace('_', ' ').title(),
            )
            saved["dfg_png"] = str(dfg_path)
    except Exception as exc:  # pragma: no cover - best effort
        summary.setdefault("warnings", []).append(f"dfg_png_not_saved: {exc}")

    try:
        process_tree = pm4py.discover_process_tree_inductive(
            dataframe,
            case_id_key="case:concept:name",
            activity_key="concept:name",
            timestamp_key="time:timestamp",
        )
        tree_path = output_dir / f"{prefix}_process_tree.png"
        pm4py.save_vis_process_tree(
            process_tree,
            str(tree_path),
            graph_title=prefix.replace('_', ' ').title(),
        )
        saved["process_tree_png"] = str(tree_path)
    except Exception as exc:  # pragma: no cover - best effort
        summary.setdefault("warnings", []).append(f"process_tree_png_not_saved: {exc}")

    return saved


def _write_log_bundle(records: list[dict[str, Any]], output_dir: Path, prefix: str, input_db: Path) -> dict[str, Any]:
    dataframe = _format_event_dataframe(records)
    xes_path = output_dir / f"{prefix}.xes"
    json_path = output_dir / f"{prefix}.json"
    summary_path = output_dir / f"{prefix}_summary.json"

    event_log = pm4py.convert_to_event_log(dataframe, case_id_key="case:concept:name")
    pm4py.write_xes(event_log, str(xes_path), case_id_key="case:concept:name")
    json_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = _build_summary(dataframe)
    summary["input_db"] = str(input_db)
    summary["output_files"] = {
        "xes": str(xes_path),
        "event_log_json": str(json_path),
        "summary_json": str(summary_path),
    }
    summary["output_files"].update(_try_save_visualizations(dataframe, output_dir, prefix, summary))
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def _build_validation_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    rows_by_case: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        patient_id = _safe_text(row.get("codice_persona"))
        vaccine_type = _safe_text(row.get("sottotipo_evento"))
        if not patient_id or not vaccine_type:
            continue
        rows_by_case.setdefault((patient_id, vaccine_type), []).append(row)

    for (patient_id, vaccine_type), vaccine_rows in sorted(rows_by_case.items()):
        cleaned_rows = []
        for row in vaccine_rows:
            timestamp = _safe_text(row.get("data"))
            dose_number = row.get("specifiche_sottotipo_evento")
            cleaned_rows.append(
                {
                    "data": timestamp,
                    "dose_number": dose_number,
                    "sessione_id": _safe_text(row.get("sessione_id")),
                }
            )
            if not timestamp:
                issues.append(
                    {
                        "type": "missing_date",
                        "case_id": f"{patient_id}::{vaccine_type}",
                        "patient_id": patient_id,
                        "vaccine_type": vaccine_type,
                        "row": cleaned_rows[-1],
                    }
                )
        dated_rows = [row for row in cleaned_rows if row["data"] and row["dose_number"] is not None]
        dated_rows.sort(key=lambda row: (row["data"], row["dose_number"]))

        seen_doses: set[int] = set()
        previous_dose: int | None = None
        for row in dated_rows:
            dose_number = int(row["dose_number"])
            if dose_number in seen_doses:
                issues.append(
                    {
                        "type": "duplicate_dose",
                        "case_id": f"{patient_id}::{vaccine_type}",
                        "patient_id": patient_id,
                        "vaccine_type": vaccine_type,
                        "row": row,
                    }
                )
            seen_doses.add(dose_number)
            if previous_dose is not None and dose_number < previous_dose:
                issues.append(
                    {
                        "type": "dose_decrease",
                        "case_id": f"{patient_id}::{vaccine_type}",
                        "patient_id": patient_id,
                        "vaccine_type": vaccine_type,
                        "previous_dose": previous_dose,
                        "current_dose": dose_number,
                        "row": row,
                    }
                )
            if previous_dose is not None and dose_number > previous_dose + 1:
                issues.append(
                    {
                        "type": "dose_gap",
                        "case_id": f"{patient_id}::{vaccine_type}",
                        "patient_id": patient_id,
                        "vaccine_type": vaccine_type,
                        "previous_dose": previous_dose,
                        "current_dose": dose_number,
                        "row": row,
                    }
                )
            previous_dose = dose_number

    return {
        "issue_count": len(issues),
        "issues": issues,
    }


def main() -> int:
    args = _build_parser().parse_args()
    vaccini_db_path = args.vaccini_db.resolve()
    output_dir = args.output_dir.resolve()

    if not vaccini_db_path.exists():
        print(f"ERRORE: database vaccini non trovato: {vaccini_db_path}")
        return 1

    output_dir.mkdir(parents=True, exist_ok=True)
    rows = _load_vaccini_rows(vaccini_db_path)
    if not rows:
        print("ERRORE: il database vaccini e' vuoto.")
        return 1

    session_records = _build_session_records(rows)
    progression_records = _build_progression_records(rows)
    validation_report = _build_validation_report(rows)

    session_summary = _write_log_bundle(
        records=session_records,
        output_dir=output_dir,
        prefix="vaccini_sessioni_log",
        input_db=vaccini_db_path,
    )
    progression_summary = _write_log_bundle(
        records=progression_records,
        output_dir=output_dir,
        prefix="vaccini_progressione_log",
        input_db=vaccini_db_path,
    )

    validation_path = output_dir / "vaccini_validation_report.json"
    validation_path.write_text(json.dumps(validation_report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=== TEST STAGE 4 - PROCESS MINING VACCINI ===")
    print(f"-> input db: {vaccini_db_path}")
    print(f"-> output dir: {output_dir}")
    print(f"-> sessioni: {session_summary['event_count']} eventi / {session_summary['case_count']} case")
    print(f"-> progressione: {progression_summary['event_count']} eventi / {progression_summary['case_count']} case")
    print(f"-> validation issues: {validation_report['issue_count']}")
    print(f"-> sessioni xes: {output_dir / 'vaccini_sessioni_log.xes'}")
    print(f"-> progressione xes: {output_dir / 'vaccini_progressione_log.xes'}")
    print(f"-> validation report: {validation_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
