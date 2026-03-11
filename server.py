"""
Local HTTP server for VideoInsightForge.
"""
from __future__ import annotations

import os
import time
import uuid
import threading
import traceback
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

_HERE = Path(__file__).parent.resolve()
os.chdir(_HERE)

import transcribe as tc  # noqa: E402


OUTPUT_DIR = _HERE / "output"
WHISPER_MODELS = ["tiny", "base", "small"]


class JobRequest(BaseModel):
    url: str = Field(..., min_length=3)
    model_size: Optional[str] = None
    prompts: List[str] = Field(default_factory=list)
    no_llm: bool = False


class JobStatus(BaseModel):
    job_id: str
    status: str
    created_at: float
    updated_at: float
    result: Optional[dict] = None
    error: Optional[str] = None


_jobs: Dict[str, dict] = {}
_lock = threading.Lock()


def _now() -> float:
    return time.time()


def _default_model_size() -> str:
    cfg = tc.load_config() or {}
    return cfg.get("transcribe", {}).get("model_size", "tiny")


def _set_job(job_id: str, **updates):
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return
        job.update(updates)
        job["updated_at"] = _now()


def _run_job(job_id: str, req: JobRequest):
    _set_job(job_id, status="running")
    try:
        model_size = req.model_size or _default_model_size()
        enable_llm = not req.no_llm

        result = tc.process_video(
            video_url=req.url,
            model_size=model_size,
            enable_llm_optimization=enable_llm,
            prompt_names=req.prompts if enable_llm else [],
        )

        if not result.get("success", False):
            raise RuntimeError(result.get("error", "unknown error"))

        transcript = result.get("transcript_text", "") or ""
        preview = {"raw": transcript[:500]}
        for name, text in (result.get("optimized_texts") or {}).items():
            preview[name] = (text or "")[:500]

        _set_job(
            job_id,
            status="succeeded",
            result={
                "title": result.get("title"),
                "platform": result.get("platform"),
                "raw_file": result.get("raw_file"),
                "optimized_files": result.get("optimized_files") or {},
                "report_file": result.get("report_file"),
                "artifacts_file": result.get("artifacts_file"),
                "artifacts": result.get("artifacts_meta") or {},
                "pipeline": result.get("pipeline", "v2"),
                "preview": preview,
            },
            error=None,
        )
    except Exception as exc:
        _set_job(
            job_id,
            status="failed",
            error=str(exc),
            result={"trace": traceback.format_exc()},
        )


app = FastAPI(title="VideoInsightForge local server")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost", "http://127.0.0.1"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"ok": True, "time": _now()}


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


@app.post("/jobs", response_model=JobStatus)
def create_job(req: JobRequest):
    if not req.url.strip():
        raise HTTPException(status_code=400, detail="url is required")

    job_id = uuid.uuid4().hex
    now = _now()
    with _lock:
        _jobs[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "created_at": now,
            "updated_at": now,
            "result": None,
            "error": None,
        }

    t = threading.Thread(target=_run_job, args=(job_id, req), daemon=True)
    t.start()
    return _jobs[job_id]


@app.get("/jobs/{job_id}", response_model=JobStatus)
def get_job(job_id: str):
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="job not found")
        return job


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
