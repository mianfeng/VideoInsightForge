"""
Local HTTP server and desktop UI host for VideoInsightForge.
"""
from __future__ import annotations

import io
import json
import logging
import os
import subprocess
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

_HERE = Path(__file__).parent.resolve()
os.chdir(_HERE)

import transcribe as tc  # noqa: E402


CONFIG_FILE = _HERE / "config.json"
OUTPUT_DIR = _HERE / "output"
UI_DIR = _HERE / "ui"
WHISPER_MODELS = ["tiny", "base", "small"]
VALID_SOURCE_KINDS = {"url", "local_video", "local_audio"}
TEXT_VIEW_PRIORITY = ["report", "summary", "evaluation", "format", "raw"]


class JobRequest(BaseModel):
    target: Optional[str] = None
    url: Optional[str] = None
    source_kind: Optional[str] = None
    model_size: Optional[str] = None
    prompts: List[str] = Field(default_factory=list)
    no_llm: bool = False


class JobStatus(BaseModel):
    job_id: str
    status: str
    source_kind: str
    created_at: float
    updated_at: float
    result: Optional[dict] = None
    error: Optional[str] = None
    logs: List[str] = Field(default_factory=list)
    llm_progress_text: str = ""
    recent_result: Optional[dict] = None


_jobs: Dict[str, dict] = {}
_lock = threading.Lock()


def _now() -> float:
    return time.time()


def _load_config() -> dict:
    try:
        return tc.load_config() or {}
    except Exception:
        return {}


def _save_config(config: dict):
    with open(CONFIG_FILE, "w", encoding="utf-8") as handle:
        json.dump(config, handle, ensure_ascii=False, indent=2)


def _deep_merge(base: dict, updates: dict) -> dict:
    merged = dict(base)
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            merged[key] = _deep_merge(base[key], value)
        else:
            merged[key] = value
    return merged


def _default_model_size() -> str:
    cfg = _load_config()
    return cfg.get("transcribe", {}).get("model_size", "tiny")


def _preview_text(text: str, limit: int = 500) -> str:
    collapsed = "\n".join(line.rstrip() for line in text.strip().splitlines() if line.strip())
    return collapsed[:limit]


def _extract_markdown_body(text: str) -> str:
    if "\n---\n" in text:
        return text.split("\n---\n", 1)[1].strip()

    lines = text.splitlines()
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    return "\n".join(lines).strip()


def _resolve_output_path(path_text: str) -> Path:
    path = Path(path_text).expanduser().resolve()
    output_root = OUTPUT_DIR.resolve()
    if output_root not in path.parents:
        raise HTTPException(status_code=400, detail="path must be inside output/")
    if not path.exists():
        raise HTTPException(status_code=404, detail="file not found")
    return path


def _infer_source_kind(target: str, declared: Optional[str]) -> str:
    if declared in VALID_SOURCE_KINDS:
        return declared
    if target.startswith(("http://", "https://")):
        return "url"

    media_type = tc.detect_local_media_type(target)
    if media_type == "video":
        return "local_video"
    return "local_audio"


def _job_view_files(result: dict) -> dict:
    views = {}
    report_file = result.get("report_file")
    if report_file:
        views["report"] = report_file

    raw_file = result.get("raw_file")
    if raw_file:
        views["raw"] = raw_file

    for name, path in (result.get("optimized_files") or {}).items():
        views[name] = path

    artifacts_file = result.get("artifacts_file")
    if artifacts_file:
        views["artifacts"] = artifacts_file

    return views


def _make_job_result(result: dict) -> dict:
    views = _job_view_files(result)
    preview = {"raw": _preview_text(result.get("transcript_text", "") or "")}
    for name, text in (result.get("optimized_texts") or {}).items():
        preview[name] = _preview_text(text or "")

    if result.get("report_file"):
        try:
            report_text = Path(result["report_file"]).read_text(encoding="utf-8")
            preview["report"] = _preview_text(_extract_markdown_body(report_text))
        except Exception:
            pass

    if result.get("artifacts_file"):
        try:
            artifact_text = Path(result["artifacts_file"]).read_text(encoding="utf-8")
            preview["artifacts"] = _preview_text(artifact_text)
        except Exception:
            pass

    primary_view = next((name for name in TEXT_VIEW_PRIORITY if views.get(name)), None)
    if not primary_view and views:
        primary_view = next(iter(views))

    return {
        "title": result.get("title"),
        "platform": result.get("platform"),
        "raw_file": result.get("raw_file"),
        "optimized_files": result.get("optimized_files") or {},
        "report_file": result.get("report_file"),
        "artifacts_file": result.get("artifacts_file"),
        "artifacts": result.get("artifacts_meta") or {},
        "pipeline": result.get("pipeline", "v2"),
        "preview": preview,
        "views": views,
        "primary_view": primary_view,
        "primary_file": views.get(primary_view) if primary_view else None,
    }


def _set_job(job_id: str, **updates):
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return
        job.update(updates)
        job["updated_at"] = _now()


def _append_job_log(job_id: str, line: str):
    clean = line.strip()
    if not clean:
        return

    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return
        logs = job.setdefault("logs", [])
        logs.append(clean)
        if len(logs) > 240:
            del logs[:-240]
        job["updated_at"] = _now()


class _JobStreamWriter(io.TextIOBase):
    def __init__(self, job_id: str, mirror):
        self.job_id = job_id
        self.mirror = mirror
        self._buffer = ""

    def write(self, text: str):
        if self.mirror is not None:
            self.mirror.write(text)
            self.mirror.flush()

        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            _append_job_log(self.job_id, line)
        return len(text)

    def flush(self):
        if self._buffer.strip():
            _append_job_log(self.job_id, self._buffer)
        self._buffer = ""
        if self.mirror is not None:
            self.mirror.flush()


class _JobLogHandler(logging.Handler):
    def __init__(self, job_id: str):
        super().__init__()
        self.job_id = job_id
        self.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
        )

    def emit(self, record: logging.LogRecord):
        try:
            _append_job_log(self.job_id, self.format(record))
        except Exception:
            pass


def _parse_result_file(file_path: Path, prompt_names: List[str]) -> Optional[Tuple[str, str]]:
    stem = file_path.stem
    suffix = file_path.suffix.lower()

    if suffix == ".json" and stem.endswith("_artifacts"):
        return stem[: -len("_artifacts")], "artifacts"

    if suffix != ".md":
        return None

    for kind in ["report", "raw", *sorted(prompt_names, key=len, reverse=True)]:
        marker = f"_{kind}"
        if stem.endswith(marker):
            return stem[: -len(marker)], kind

    return None


def _read_result_title(files: dict) -> str:
    for preferred in TEXT_VIEW_PRIORITY + ["raw"]:
        path_text = files.get(preferred)
        if not path_text:
            continue
        try:
            first_line = Path(path_text).read_text(encoding="utf-8").splitlines()[0].strip()
            if first_line.startswith("# "):
                return first_line[2:].strip()
        except Exception:
            continue
    return "Untitled Result"


def _read_result_preview(path_text: str) -> str:
    path = Path(path_text)
    try:
        if path.suffix.lower() == ".json":
            payload = json.loads(path.read_text(encoding="utf-8"))
            return _preview_text(json.dumps(payload, ensure_ascii=False, indent=2))
        body = _extract_markdown_body(path.read_text(encoding="utf-8"))
        return _preview_text(body)
    except Exception:
        return ""


def _collect_recent_results(limit: int = 12) -> List[dict]:
    OUTPUT_DIR.mkdir(exist_ok=True)
    groups: Dict[str, dict] = {}
    prompt_names = tc.list_available_prompts()

    for file_path in OUTPUT_DIR.iterdir():
        if not file_path.is_file():
            continue

        parsed = _parse_result_file(file_path, prompt_names)
        if not parsed:
            continue

        prefix, kind = parsed
        group = groups.setdefault(
            prefix,
            {
                "id": prefix,
                "updated_at": 0.0,
                "files": {},
            },
        )
        group["files"][kind] = str(file_path)
        group["updated_at"] = max(group["updated_at"], file_path.stat().st_mtime)

    results = []
    for prefix, group in groups.items():
        previews = {}
        for kind, path_text in group["files"].items():
            previews[kind] = _read_result_preview(path_text)

        ordered_views = [
            name for name in TEXT_VIEW_PRIORITY if name in group["files"]
        ] + [
            name
            for name in sorted(group["files"])
            if name not in TEXT_VIEW_PRIORITY and name != "artifacts"
        ]
        if "artifacts" in group["files"]:
            ordered_views.append("artifacts")

        primary_view = next((name for name in ordered_views if previews.get(name)), ordered_views[0] if ordered_views else "raw")
        results.append(
            {
                "id": prefix,
                "title": _read_result_title(group["files"]),
                "updated_at": group["updated_at"],
                "files": group["files"],
                "preview": previews,
                "views": ordered_views,
                "primary_view": primary_view,
                "primary_file": group["files"].get(primary_view),
            }
        )

    results.sort(key=lambda item: item["updated_at"], reverse=True)
    return results[:limit]


def _latest_recent_result() -> Optional[dict]:
    results = _collect_recent_results(limit=1)
    return results[0] if results else None


def _run_job(job_id: str, req: JobRequest):
    target = (req.target or req.url or "").strip()
    source_kind = _infer_source_kind(target, req.source_kind)
    model_size = req.model_size or _default_model_size()

    _set_job(
        job_id,
        status="running",
        source_kind=source_kind,
        error=None,
        llm_progress_text="",
    )
    _append_job_log(job_id, f"Starting job for {source_kind}: {target}")

    old_out, old_err = sys.stdout, sys.stderr
    out_writer = _JobStreamWriter(job_id, old_out)
    err_writer = _JobStreamWriter(job_id, old_err)
    log_handler = _JobLogHandler(job_id)
    tc.logger.addHandler(log_handler)

    sys.stdout = out_writer
    sys.stderr = err_writer

    try:
        if not req.no_llm:
            def _stream_cb(chars: int, _chunk: str):
                _set_job(job_id, llm_progress_text=f"LLM generating: {chars} chars")

            tc.set_llm_stream_callback(_stream_cb)
        else:
            tc.clear_llm_stream_callback()

        result = tc.process_video(
            video_url=target,
            model_size=model_size,
            enable_llm_optimization=not req.no_llm,
            prompt_names=req.prompts if not req.no_llm else [],
        )

        tc.clear_llm_stream_callback()
        out_writer.flush()
        err_writer.flush()

        if not result.get("success", False):
            raise RuntimeError(result.get("error", "unknown error"))

        payload = _make_job_result(result)
        _set_job(
            job_id,
            status="succeeded",
            result=payload,
            error=None,
            llm_progress_text="",
        )
        _append_job_log(job_id, "Job completed successfully.")
    except Exception as exc:
        tc.clear_llm_stream_callback()
        out_writer.flush()
        err_writer.flush()
        _set_job(
            job_id,
            status="failed",
            error=str(exc),
            result={"trace": traceback.format_exc()},
            llm_progress_text="",
        )
        _append_job_log(job_id, f"Job failed: {exc}")
    finally:
        tc.logger.removeHandler(log_handler)
        sys.stdout = old_out
        sys.stderr = old_err


app = FastAPI(title="VideoInsightForge local server")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost", "http://127.0.0.1"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/ui", StaticFiles(directory=UI_DIR), name="ui")


@app.get("/")
def index():
    return FileResponse(UI_DIR / "index.html")


@app.get("/health")
def health():
    return {
        "ok": True,
        "time": _now(),
        "default_model": _default_model_size(),
    }


@app.get("/prompts")
def list_prompts():
    try:
        prompts = tc.list_available_prompts()
    except Exception:
        prompts = []
    return {"prompts": prompts}


@app.get("/models")
def list_models():
    return {"models": WHISPER_MODELS, "default": _default_model_size()}


@app.get("/config")
def get_config():
    return _load_config()


@app.put("/config")
def update_config(payload: dict = Body(...)):
    current = _load_config()
    merged = _deep_merge(current, payload or {})
    _save_config(merged)
    return merged


@app.post("/jobs", response_model=JobStatus)
def create_job(req: JobRequest):
    target = (req.target or req.url or "").strip()
    if not target:
        raise HTTPException(status_code=400, detail="target is required")

    job_id = uuid.uuid4().hex
    now = _now()
    source_kind = _infer_source_kind(target, req.source_kind)

    with _lock:
        _jobs[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "source_kind": source_kind,
            "created_at": now,
            "updated_at": now,
            "result": None,
            "error": None,
            "logs": ["Job queued."],
            "llm_progress_text": "",
            "recent_result": None,
        }

    thread = threading.Thread(target=_run_job, args=(job_id, req), daemon=True)
    thread.start()
    return {**_jobs[job_id], "recent_result": _latest_recent_result()}


@app.get("/jobs/{job_id}", response_model=JobStatus)
def get_job(job_id: str):
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="job not found")
        payload = dict(job)
    payload["recent_result"] = _latest_recent_result()
    return payload


@app.get("/results/recent")
def recent_results(limit: int = Query(default=12, ge=1, le=50)):
    return {"results": _collect_recent_results(limit=limit)}


@app.get("/results/content")
def result_content(path: str):
    resolved = _resolve_output_path(path)
    if resolved.suffix.lower() == ".json":
        content = json.dumps(
            json.loads(resolved.read_text(encoding="utf-8")),
            ensure_ascii=False,
            indent=2,
        )
        return {"path": str(resolved), "kind": "json", "content": content}

    text = resolved.read_text(encoding="utf-8")
    return {"path": str(resolved), "kind": "markdown", "content": text}


@app.get("/open-output")
def open_output():
    OUTPUT_DIR.mkdir(exist_ok=True)
    try:
        subprocess.Popen(["explorer", str(OUTPUT_DIR)])
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="127.0.0.1", port=8732, reload=False)
