"""Runner standalone per testare solo Ollama su un prompt file.

Uso tipico:
    .venv/bin/python test_stage2.py
    .venv/bin/python test_stage2.py --prompt-file path/al/tuo_prompt.txt
    .venv/bin/python test_stage2.py --prompt-file path/al/tuo_prompt.txt --system-file path/al/system.txt

Questo file e intenzionalmente separato da stage1:
serve per iterare sul prompt in modo rapido, vedere streaming/metrics e salvare la risposta.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

ROOT_DIR = Path(__file__).resolve().parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from config import get_config

DEFAULT_PROMPT_FILE = (
    ROOT_DIR
    / "artifacts"
    / "person001"
    / "CertificatoVaccinale_LMNLCU02E15D918M_20260303104746"
    / "prompt_main.txt"
)
DEFAULT_OUTPUT_DIR = ROOT_DIR / "artifacts" / "llm_only"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Test standalone di Ollama su un prompt file.")
    parser.add_argument(
        "--prompt-file",
        type=Path,
        default=DEFAULT_PROMPT_FILE,
        help="File di testo con il prompt da inviare a Ollama.",
    )
    parser.add_argument(
        "--system-file",
        type=Path,
        default=None,
        help="File opzionale con system prompt separato.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Override del modello Ollama. Se omesso usa config.py / env.",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="Override della base URL Ollama. Se omesso usa config.py / env.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=3600,
        help="Timeout della chiamata HTTP verso Ollama.",
    )
    parser.add_argument(
        "--think",
        action="store_true",
        help="Abilita esplicitamente il canale thinking nello stream.",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Passa raw=true a Ollama per evitare templating aggiuntivo.",
    )
    parser.add_argument(
        "--format-json",
        action="store_true",
        help="Passa format=json a Ollama.",
    )
    parser.add_argument(
        "--output-file",
        type=Path,
        default=None,
        help="Path finale del JSON di output. Se omesso viene creato in artifacts/llm_only/.",
    )
    return parser


def _default_output_file(prompt_file: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return DEFAULT_OUTPUT_DIR / f"{prompt_file.stem}__{timestamp}.json"


def _read_text_file(path: Path | None) -> str | None:
    if path is None:
        return None
    return path.read_text(encoding="utf-8")


def _print_duration(label: str, nanoseconds: Any) -> None:
    if not nanoseconds:
        print(f"-> {label}: n/d")
        return
    seconds = float(nanoseconds) / 1_000_000_000
    print(f"-> {label}: {seconds:.2f}s")


def _stream_ollama_generate(
    *,
    base_url: str,
    model: str,
    prompt: str,
    system: str | None,
    timeout_seconds: int,
    think: bool,
    raw: bool,
    format_json: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "stream": True,
        "think": think,
    }
    if system:
        payload["system"] = system
    if raw:
        payload["raw"] = True
    if format_json:
        payload["format"] = "json"

    print(f"-> POST {base_url.rstrip('/')}/api/generate")
    print(f"-> model: {model}")
    print(f"-> prompt chars: {len(prompt)}")
    print(f"-> think: {think}")
    print(f"-> raw: {raw}")
    print(f"-> format=json: {format_json}")
    print(f"-> timeout: {timeout_seconds}s")
    print("-> Streaming attivo: se arrivano chunk vedrai subito thinking o response.")

    response = requests.post(
        f"{base_url.rstrip('/')}/api/generate",
        json=payload,
        timeout=timeout_seconds,
        stream=True,
    )
    response.raise_for_status()

    thinking_parts: list[str] = []
    response_parts: list[str] = []
    final_payload: dict[str, Any] | None = None
    chunks_seen = 0
    in_thinking = False
    in_response = False

    for raw_line in response.iter_lines(decode_unicode=True):
        if not raw_line:
            continue

        chunk = json.loads(raw_line)
        final_payload = chunk
        chunks_seen += 1

        thinking_text = str(chunk.get("thinking", "") or "")
        response_text = str(chunk.get("response", "") or "")

        if thinking_text:
            if not in_thinking:
                if in_response:
                    print()
                    in_response = False
                print("\n-> THINKING")
                in_thinking = True
            print(thinking_text, end="", flush=True)
            thinking_parts.append(thinking_text)

        if response_text:
            if not in_response:
                if in_thinking:
                    print()
                    in_thinking = False
                print("\n-> RESPONSE")
                in_response = True
            print(response_text, end="", flush=True)
            response_parts.append(response_text)

        if chunk.get("done"):
            break

    if in_thinking or in_response:
        print()

    combined_payload = dict(final_payload or {})
    combined_payload["thinking"] = "".join(thinking_parts).strip()
    combined_payload["response"] = "".join(response_parts).strip()
    combined_payload["smoke_test_chunk_count"] = chunks_seen
    return payload, combined_payload


def main() -> int:
    args = _build_parser().parse_args()
    config = get_config()

    prompt_file = args.prompt_file.resolve()
    system_file = args.system_file.resolve() if args.system_file else None
    base_url = args.base_url or config.ollama_base_url
    model = args.model or config.ollama_model

    if not prompt_file.exists():
        print(f"ERRORE: prompt file non trovato: {prompt_file}")
        return 1
    if system_file is not None and not system_file.exists():
        print(f"ERRORE: system file non trovato: {system_file}")
        return 1

    prompt_text = _read_text_file(prompt_file)
    system_text = _read_text_file(system_file)

    output_file = args.output_file.resolve() if args.output_file else _default_output_file(prompt_file)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    print("=== TEST STAGE 2 ===")
    print(f"-> prompt file: {prompt_file}")
    print(f"-> system file: {system_file if system_file else 'none'}")
    print(f"-> output file: {output_file}")

    try:
        request_payload, raw_payload = _stream_ollama_generate(
            base_url=base_url,
            model=model,
            prompt=prompt_text or "",
            system=system_text,
            timeout_seconds=args.timeout_seconds,
            think=args.think,
            raw=args.raw,
            format_json=args.format_json,
        )
    except Exception as exc:
        print(f"\n-> ERRORE OLLAMA: {exc}")
        return 1

    print("\n-> METRICHE")
    print(f"-> prompt_eval_count: {raw_payload.get('prompt_eval_count', 'n/d')}")
    _print_duration("prompt_eval_duration", raw_payload.get("prompt_eval_duration"))
    print(f"-> eval_count: {raw_payload.get('eval_count', 'n/d')}")
    _print_duration("eval_duration", raw_payload.get("eval_duration"))
    _print_duration("load_duration", raw_payload.get("load_duration"))
    _print_duration("total_duration", raw_payload.get("total_duration"))
    print(f"-> chunks_seen: {raw_payload.get('smoke_test_chunk_count', 'n/d')}")

    output_payload = {
        "prompt_file": str(prompt_file),
        "system_file": str(system_file) if system_file else None,
        "request": request_payload,
        "response": raw_payload,
    }
    output_file.write_text(json.dumps(output_payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"-> risposta salvata in: {output_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
