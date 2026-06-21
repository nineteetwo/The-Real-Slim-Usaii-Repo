#!/usr/bin/env python3
"""
parse_process.py — Local SLM Information Extractor
====================================================
Pipes parsed document text into a locally-running Small Language Model and
extracts structured information according to your system prompt.

Supported backends (all local, no API keys)
--------------------------------------------
  ollama       Ollama server          http://localhost:11434   (default)
  lmstudio     LM Studio              http://localhost:1234
  llamacpp     llama.cpp server       http://localhost:8080
  openai       Any OpenAI-compatible  http://localhost:<port>

Usage
-----
  # From a file
  python parse_process.py parsed.txt

  # Piped directly from doc_parser
  python doc_parser.py report.pdf | python parse_process.py -

  # One-shot with inline system prompt
  python parse_process.py parsed.txt --system "Extract all dates and amounts as JSON"

  # Use a prompt file, save output
  python parse_process.py parsed.txt --system-file prompt.txt -o result.json

  # Specify backend / model explicitly
  python parse_process.py parsed.txt --backend lmstudio --model mistral-7b-instruct

Options
-------
  -                        Read document text from stdin (pipe mode)
  --backend <name>         ollama | lmstudio | llamacpp | openai  (default: auto)
  --host <url>             Override base URL for the backend
  --model <name>           Model name / tag to use
  --system <prompt>        System prompt as a string
  --system-file <path>     Load system prompt from a .txt file
  --output / -o <path>     Save result to file (default: print to stdout)
  --format json|text       Expected output format hint for the model (default: text)
  --chunk-size <n>         Max characters per chunk for large docs (default: 12000)
  --overlap <n>            Overlap between chunks in characters (default: 500)
  --temperature <f>        Sampling temperature (default: 0.1)
  --max-tokens <n>         Max tokens per LLM response (default: 2048)
  --timeout <s>            HTTP timeout in seconds (default: 120)
  --list-models            List models available on the detected/chosen backend
  --quiet / -q             Suppress progress messages
  -h, --help               Show this help
"""

import argparse
import io
import json
import os
import re
import sys
import textwrap
import time
import urllib.error
import urllib.request
import socket
from pathlib import Path
from typing import Any, Optional

# Windows stdout fix
def _fix_stdout_encoding():
    if sys.platform != "win32":
        return
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        else:
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except (AttributeError, io.UnsupportedOperation):
        pass


# Logging
def log(msg: str, quiet: bool = False):
    if not quiet:
        print(f"[extractor] {msg}", file=sys.stderr, flush=True)


# HTTP helpers  (stdlib only — no requests needed)
def _http_post(url: str, payload: dict, timeout: int = 1000) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (TimeoutError, socket.timeout) as exc:
        raise RuntimeError(
            f"Request to {url} timed out after {timeout} seconds. "
            "Try increasing --timeout, reducing prompt size, using --max-tokens 1024, "
            "or using a smaller/faster model."
        ) from exc
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}:\n{detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Cannot reach {url}: {exc.reason}") from exc


def _http_get(url: str, timeout: int = 10) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return {}


def _is_reachable(url: str, timeout: int = 3) -> bool:
    try:
        urllib.request.urlopen(url, timeout=timeout)
        return True
    except Exception:
        return False


# Backend base class
class Backend:
    name: str = "base"
    default_host: str = ""
    health_path: str = "/"

    def __init__(self, host: str, model: str, temperature: float,
                 max_tokens: int, timeout: int):
        self.host = host.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout

    def is_alive(self) -> bool:
        return _is_reachable(self.host + self.health_path)

    def list_models(self) -> list:
        raise NotImplementedError

    def chat(self, system: str, user: str) -> str:
        raise NotImplementedError


# Ollama backend  —  http://localhost:11434
class OllamaBackend(Backend):
    """
    Uses Ollama's /api/chat endpoint (stream=false).
    Install:  https://ollama.com/download
    Models:   ollama pull llama3.2:3b
              ollama pull phi3:mini
              ollama pull mistral
              ollama pull qwen2.5:3b
    """
    name = "ollama"
    default_host = "http://localhost:11434"
    health_path = "/api/tags"

    def list_models(self) -> list:
        data = _http_get(f"{self.host}/api/tags")
        return [m["name"] for m in data.get("models", [])]

    def chat(self, system: str, user: str) -> str:
        payload = {
            "model": self.model,
            "stream": False,
            "options": {
                "temperature": self.temperature,
                "num_predict": self.max_tokens,
            },
            "messages": [
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
        }
        resp = _http_post(f"{self.host}/api/chat", payload, self.timeout)
        return resp.get("message", {}).get("content", "").strip()


# OpenAI-compatible backend  —  LM Studio / Jan / TabbyAPI
class OpenAICompatBackend(Backend):
    """
    Any server exposing POST /v1/chat/completions.
    LM Studio:  https://lmstudio.ai  (default port 1234)
    Jan:        https://jan.ai
    TabbyAPI:   https://github.com/theroyallab/tabbyAPI
    """
    name = "openai"
    default_host = "http://localhost:1234"
    health_path = "/v1/models"

    def list_models(self) -> list:
        data = _http_get(f"{self.host}/v1/models")
        return [m["id"] for m in data.get("data", [])]

    def chat(self, system: str, user: str) -> str:
        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
        }
        resp = _http_post(f"{self.host}/v1/chat/completions", payload, self.timeout)
        choices = resp.get("choices", [])
        if not choices:
            raise RuntimeError(f"Empty choices in response: {resp}")
        return choices[0].get("message", {}).get("content", "").strip()


# llama.cpp native backend  —  http://localhost:8080
class LlamaCppBackend(Backend):
    """
    llama.cpp server using its native /completion endpoint.
    Use OpenAICompatBackend if you launch llama.cpp with --openai-compat.

    Run:  ./llama-server -m your_model.gguf --port 8080
    Repo: https://github.com/ggerganov/llama.cpp
    """
    name = "llamacpp"
    default_host = "http://localhost:8080"
    health_path = "/health"

    def list_models(self) -> list:
        data = _http_get(f"{self.host}/props")
        path = data.get("default_generation_settings", {}).get("model", "unknown")
        return [Path(path).stem]

    def chat(self, system: str, user: str) -> str:
        # Wrap in a chat-ML style prompt; works for most instruction-tuned GGUFs
        prompt = (
            f"<|im_start|>system\n{system}<|im_end|>\n"
            f"<|im_start|>user\n{user}<|im_end|>\n"
            f"<|im_start|>assistant\n"
        )
        payload = {
            "prompt": prompt,
            "temperature": self.temperature,
            "n_predict": self.max_tokens,
            "stop": ["<|im_end|>", "<|im_start|>"],
        }
        resp = _http_post(f"{self.host}/completion", payload, self.timeout)
        return resp.get("content", "").strip()


# Backend registry & auto-detection
BACKENDS = {
    "ollama":   OllamaBackend,
    "lmstudio": OpenAICompatBackend,
    "openai":   OpenAICompatBackend,   # generic alias
    "llamacpp": LlamaCppBackend,
}

_PROBE_ORDER = [
    ("ollama",   OllamaBackend,      "http://localhost:11434"),
    ("lmstudio", OpenAICompatBackend,"http://localhost:1234"),
    ("llamacpp", LlamaCppBackend,    "http://localhost:8080"),
]


def auto_detect_backend(quiet: bool = False):
    """Probe well-known local ports; return (backend_name, host)."""
    for name, cls, host in _PROBE_ORDER:
        probe = host + cls.health_path
        log(f"Probing {name} at {probe} …", quiet)
        if _is_reachable(probe, timeout=3):
            log(f"Found: {name} at {host}", quiet)
            return name, host
    raise RuntimeError(
        "No local LLM server found on standard ports.\n"
        "  Ollama:   ollama serve                        (port 11434)\n"
        "  LM Studio: start server inside the app        (port 1234)\n"
        "  llama.cpp: ./llama-server -m model.gguf       (port 8080)\n"
        "Or pass --backend and --host to specify manually."
    )


def auto_select_model(backend: Backend, quiet: bool = False) -> str:
    """Auto-select a local model when --model is omitted."""
    preferred_keywords = ["llama3.2", "llama3.1", "llama3", "qwen2.5", "qwen", "mistral", "phi3", "gemma"]

    if backend.name == "ollama":
        data = _http_get(f"{backend.host}/api/ps")
        running = []
        for m in data.get("models", []):
            name = m.get("name") or m.get("model")
            if name:
                running.append(name)
        if running:
            selected = running[0]
            log(f"Auto-selected running Ollama model: {selected}", quiet)
            return selected

    models = [m for m in backend.list_models() if m]
    if not models:
        raise RuntimeError(
            "No model was provided and no available models were found. "
            "For Ollama, run: ollama pull llama3.2:3b"
        )
    lowered = [(m, m.lower()) for m in models]
    for keyword in preferred_keywords:
        for original, low in lowered:
            if keyword in low:
                log(f"Auto-selected available model: {original}", quiet)
                return original
    selected = models[0]
    log(f"Auto-selected first available model: {selected}", quiet)
    return selected


def build_backend(backend_name, host, model, temperature, max_tokens, timeout, quiet):
    if backend_name and backend_name not in BACKENDS:
        sys.exit(f"[ERROR] Unknown backend '{backend_name}'. "
                 f"Choose: {', '.join(BACKENDS)}")
    if not backend_name:
        backend_name, detected_host = auto_detect_backend(quiet)
        host = host or detected_host
    else:
        cls = BACKENDS[backend_name]
        host = host or cls.default_host

    cls = BACKENDS[backend_name]
    backend = cls(host, model, temperature, max_tokens, timeout)
    if not backend.model:
        backend.model = auto_select_model(backend, quiet)
    return backend


# Document chunker
def chunk_text(text: str, chunk_size: int, overlap: int) -> list:
    """
    Split text into chunks of at most `chunk_size` characters with `overlap`
    character context carried into the next chunk.
    Prefers splitting on paragraph / sentence / word boundaries.
    """
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    pos = 0
    total = len(text)

    while pos < total:
        end = min(pos + chunk_size, total)

        if end < total:
            split = text.rfind("\n\n", pos, end)  # paragraph break
            if split <= pos:
                split = text.rfind("\n", pos, end)  # line break
            if split <= pos:
                split = text.rfind(". ", pos, end)  # sentence end
            if split <= pos:
                split = text.rfind(" ", pos, end)   # word boundary
            if split > pos:
                end = split

        chunks.append(text[pos:end].strip())
        next_pos = end - overlap
        pos = next_pos if next_pos > pos else end  # guard against infinite loop

    return [c for c in chunks if c]


# Result merger
def _try_parse_json(text: str):
    """Parse model JSON robustly without trusting malformed output."""
    if text is None:
        return None
    clean = str(text).strip()
    clean = re.sub(r"^```(?:json|JSON|[a-zA-Z0-9_-]+)?\s*", "", clean)
    clean = re.sub(r"\s*```$", "", clean)

    def attempt(s: str):
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            return None

    parsed = attempt(clean)
    if parsed is not None:
        return parsed

    # Extract first plausible JSON object or array from surrounding prose.
    starts = [i for i in (clean.find("{"), clean.find("[")) if i != -1]
    if starts:
        start = min(starts)
        end = max(clean.rfind("}"), clean.rfind("]"))
        if end > start:
            candidate = clean[start:end + 1]
            parsed = attempt(candidate)
            if parsed is not None:
                return parsed
            clean = candidate

    # Optional dependency: pip install json-repair
    try:
        from json_repair import repair_json
        repaired = repair_json(clean)
        return json.loads(repaired)
    except Exception:
        return None



def merge_results(results: list, fmt: str) -> str:
    """Merge multi-chunk extraction results into one coherent output."""
    if len(results) == 1:
        return results[0]

    if fmt == "json":
        parsed = [_try_parse_json(r) for r in results]
        valid = [p for p in parsed if p is not None]
        if valid:
            if all(isinstance(p, list) for p in valid):
                seen, merged = set(), []
                for lst in valid:
                    for item in lst:
                        key = json.dumps(item, sort_keys=True)
                        if key not in seen:
                            seen.add(key)
                            merged.append(item)
                return json.dumps(merged, indent=2, ensure_ascii=False)
            elif all(isinstance(p, dict) for p in valid):
                merged = {}
                for d in valid:
                    for k, v in d.items():
                        if k not in merged:
                            merged[k] = v
                        elif isinstance(v, list) and isinstance(merged[k], list):
                            seen_items = {json.dumps(i, sort_keys=True) for i in merged[k]}
                            for item in v:
                                if json.dumps(item, sort_keys=True) not in seen_items:
                                    merged[k].append(item)
                return json.dumps(merged, indent=2, ensure_ascii=False)

    # Plain text: join with chunk headers
    return "\n\n".join(f"[Chunk {i}]\n{r}" for i, r in enumerate(results, 1))



# Core extractor
def build_user_prompt(chunk: str, total: int, idx: int, fmt: str) -> str:
    header = (
        f"[Document chunk {idx} of {total} — analyse only this portion]\n\n"
        if total > 1 else ""
    )
    footer = (
        "\n\nRespond ONLY with valid JSON. "
        "No markdown fences, no preamble, no explanation."
        if fmt == "json" else ""
    )
    return f"{header}{chunk}{footer}"


def extract(text: str, system_prompt: str, backend: Backend,
            fmt: str, chunk_size: int, overlap: int, quiet: bool) -> str:
    chunks = chunk_text(text, chunk_size, overlap)
    n = len(chunks)
    if n > 1:
        log(f"Document split into {n} chunks (max {chunk_size} chars, overlap {overlap}).", quiet)

    results = []
    for i, chunk in enumerate(chunks, 1):
        log(f"Sending chunk {i}/{n} … ({len(chunk):,} chars)", quiet)
        user_prompt = build_user_prompt(chunk, n, i, fmt)
        t0 = time.perf_counter()
        response = backend.chat(system_prompt, user_prompt)
        elapsed = time.perf_counter() - t0
        log(f"  Response: {len(response):,} chars in {elapsed:.1f}s", quiet)
        results.append(response)

    return merge_results(results, fmt)



# System prompt helpers
DEFAULT_SYSTEM_PROMPT = textwrap.dedent("""\
    You are a precise information-extraction assistant.
    Read the provided document text carefully and extract all relevant
    information. Be thorough but concise.
    If a piece of information is not present in the text, state "not found".
""")


def load_system_prompt(inline, filepath, quiet) -> str:
    if inline:
        return inline.strip()
    if filepath:
        p = Path(filepath)
        if not p.exists():
            sys.exit(f"[ERROR] System prompt file not found: {filepath}")
        text = p.read_text(encoding="utf-8").strip()
        log(f"System prompt loaded from {p} ({len(text)} chars)", quiet)
        return text
    # Interactive fallback
    log("No system prompt provided — entering interactive input.", quiet)
    print("Enter your system prompt (blank line to finish):", file=sys.stderr)
    lines = []
    try:
        prev_blank = False
        while True:
            line = input()
            if line == "":
                if prev_blank or lines:
                    break
                prev_blank = True
            else:
                prev_blank = False
            lines.append(line)
    except EOFError:
        pass
    prompt = "\n".join(lines).strip()
    if not prompt:
        log("Using built-in default system prompt.", quiet)
        return DEFAULT_SYSTEM_PROMPT
    return prompt



# CLI
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="parse_process.py",
        description="Extract information from parsed document text using a local SLM.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument(
        "input", nargs="?", default="-",
        help="Path to parsed text file, or '-' to read from stdin (default).",
    )
    ap.add_argument(
        "--backend", metavar="NAME", choices=list(BACKENDS.keys()),
        help="Backend: ollama | lmstudio | llamacpp | openai. Default: auto-detect.",
    )
    ap.add_argument("--host",  metavar="URL",
                    help="Override backend base URL.")
    ap.add_argument("--model", metavar="NAME", default="",
                    help="Model name/tag (e.g. llama3.2:3b, phi3:mini, mistral).")
    ap.add_argument("--system", metavar="PROMPT",
                    help="System prompt string.")
    ap.add_argument("--system-file", metavar="PATH",
                    help="Path to a text file containing the system prompt.")
    ap.add_argument("-o", "--output", metavar="PATH",
                    help="Save result to this file.")
    ap.add_argument("--format", choices=["json", "text"], default="text",
                    help="Output format hint for the model (default: text).")
    ap.add_argument("--chunk-size", type=int, default=12000, metavar="N",
                    help="Max chars per chunk (default: 12000).")
    ap.add_argument("--overlap",    type=int, default=500,   metavar="N",
                    help="Overlap chars between chunks (default: 500).")
    ap.add_argument("--temperature",type=float, default=0.1, metavar="F",
                    help="Sampling temperature (default: 0.1).")
    ap.add_argument("--max-tokens", type=int, default=2048,  metavar="N",
                    help="Max tokens in model response (default: 2048).")
    ap.add_argument("--timeout",    type=int, default=1000,   metavar="S",
                    help="HTTP timeout seconds (default: 120).")
    ap.add_argument("--list-models", action="store_true",
                    help="List available models on the backend and exit.")
    ap.add_argument("--quiet", "-q", action="store_true",
                    help="Suppress progress messages.")
    return ap


def main():
    _fix_stdout_encoding()
    ap = build_parser()
    args = ap.parse_args()

    # Build backend
    try:
        backend = build_backend(
            args.backend, args.host, args.model,
            args.temperature, args.max_tokens, args.timeout, args.quiet,
        )
    except RuntimeError as exc:
        sys.exit(f"[ERROR] {exc}")

    log(f"Backend : {backend.name} @ {backend.host}", args.quiet)
    log(f"Model   : {backend.model or '(server default)'}", args.quiet)

    # List models and exit
    if args.list_models:
        try:
            models = backend.list_models()
        except Exception as exc:
            sys.exit(f"[ERROR] Cannot list models: {exc}")
        print("\nAvailable models:")
        for m in models:
            print(f"  {m}")
        return

    # Read document text
    if args.input == "-":
        log("Reading from stdin …", args.quiet)
        try:
            doc_text = sys.stdin.read()
        except KeyboardInterrupt:
            sys.exit(0)
    else:
        p = Path(args.input)
        if not p.exists():
            sys.exit(f"[ERROR] File not found: {args.input}")
        doc_text = p.read_text(encoding="utf-8", errors="replace")
        log(f"Input   : {p}  ({len(doc_text):,} chars)", args.quiet)

    if not doc_text.strip():
        sys.exit("[ERROR] Document text is empty.")

    # Load system prompt
    system_prompt = load_system_prompt(args.system, args.system_file, args.quiet)
    log(f"Prompt  : {len(system_prompt)} chars", args.quiet)

    # Extract
    try:
        result = extract(
            text=doc_text,
            system_prompt=system_prompt,
            backend=backend,
            fmt=args.format,
            chunk_size=args.chunk_size,
            overlap=args.overlap,
            quiet=args.quiet,
        )
    except RuntimeError as exc:
        sys.exit(f"[ERROR] Extraction failed: {exc}")

    # Output
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(result, encoding="utf-8")
        log(f"Saved   : {out}", args.quiet)
    else:
        try:
            print(result)
        except UnicodeEncodeError:
            print(result.encode("utf-8", errors="replace").decode("utf-8"))



# Importable API
__all__ = [
    "OllamaBackend", "OpenAICompatBackend", "LlamaCppBackend",
    "build_backend", "auto_select_model", "extract", "chunk_text",
]

if __name__ == "__main__":
    main()