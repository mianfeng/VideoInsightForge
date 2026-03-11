import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from typing import Callable, Dict, List, Optional

from .artifacts import ChunkArtifact, PipelineArtifacts, PipelineStats


class V2PipelineOrchestrator:
    def __init__(
        self,
        llm_runner: Callable[[str, str], Optional[str]],
        estimate_tokens: Callable[[str], int],
        split_chunks: Callable[[str], List[str]],
        logger,
        enable_parallel: bool = True,
    ):
        self._llm_runner = llm_runner
        self._estimate_tokens = estimate_tokens
        self._split_chunks = split_chunks
        self._logger = logger
        self._enable_parallel = enable_parallel

    def run(self, transcript_text: str, selected_prompts: List[str]) -> Dict[str, object]:
        started = time.time()
        stage_durations: Dict[str, float] = {}

        source_tokens = self._estimate_tokens(transcript_text)
        self._logger.info(f"[V2] pipeline start, estimated input tokens={source_tokens}")

        cleaner_start = time.time()
        cleaned = self._llm_runner("cleaner", transcript_text) or transcript_text
        stage_durations["cleaner"] = round(time.time() - cleaner_start, 3)

        segment_start = time.time()
        semantic_segments = self._llm_runner("segmenter", cleaned) or ""
        stage_durations["segmenter"] = round(time.time() - segment_start, 3)

        chunk_start = time.time()
        chunks = self._split_chunks(cleaned) or [cleaned]
        chunk_summaries: List[ChunkArtifact] = []
        chunk_token_total = 0
        for idx, chunk in enumerate(chunks, 1):
            token_est = self._estimate_tokens(chunk)
            chunk_token_total += token_est
            summary = self._llm_runner("chunk_summary", chunk)
            if not summary:
                summary = chunk[:1000]
            chunk_summaries.append(
                ChunkArtifact(
                    index=idx,
                    token_estimate=token_est,
                    summary=summary.strip(),
                )
            )
        stage_durations["chunk_summaries"] = round(time.time() - chunk_start, 3)

        synthesis_start = time.time()
        global_synthesis = self._build_global_synthesis(chunk_summaries)
        stage_durations["global_synthesis"] = round(time.time() - synthesis_start, 3)

        knowledge_start = time.time()
        knowledge_input = (
            f"# 语义分段\n{semantic_segments}\n\n"
            f"# 全局综合\n{global_synthesis}\n"
        )
        knowledge = self._llm_runner("knowledge", knowledge_input) or global_synthesis
        stage_durations["knowledge"] = round(time.time() - knowledge_start, 3)

        app_start = time.time()
        app_outputs = self._run_application_layer(knowledge, selected_prompts)
        stage_durations["application"] = round(time.time() - app_start, 3)
        stage_durations["total"] = round(time.time() - started, 3)

        avg_chunk_tokens = int(chunk_token_total / len(chunks)) if chunks else 0
        cleaned_tokens = self._estimate_tokens(cleaned)
        knowledge_tokens = self._estimate_tokens(knowledge)

        stats = PipelineStats(
            source_tokens=source_tokens,
            cleaned_tokens=cleaned_tokens,
            knowledge_tokens=knowledge_tokens,
            chunk_count=len(chunks),
            avg_chunk_tokens=avg_chunk_tokens,
            stage_durations=stage_durations,
            app_parallel=self._enable_parallel,
        )

        artifacts = PipelineArtifacts(
            cleaned_transcript=cleaned,
            semantic_segments=semantic_segments,
            chunk_summaries=chunk_summaries,
            global_synthesis=global_synthesis,
            knowledge=knowledge,
            application_outputs=app_outputs,
            stats=stats,
        )

        optimized_texts: Dict[str, str] = {}
        if "format" in selected_prompts:
            optimized_texts["format"] = cleaned
        if "summary" in selected_prompts and app_outputs.get("summary"):
            optimized_texts["summary"] = app_outputs["summary"]
        if "evaluation" in selected_prompts and app_outputs.get("evaluation"):
            optimized_texts["evaluation"] = app_outputs["evaluation"]
        if "quotes" in selected_prompts and app_outputs.get("quotes"):
            optimized_texts["quotes"] = app_outputs["quotes"]
        if "quick_summary" in selected_prompts and app_outputs.get("quick_summary"):
            optimized_texts["quick_summary"] = app_outputs["quick_summary"]

        report_text = self._build_report(app_outputs)
        artifacts_meta = {
            "source_tokens": source_tokens,
            "cleaned_tokens": cleaned_tokens,
            "knowledge_tokens": knowledge_tokens,
            "chunk_count": len(chunks),
            "avg_chunk_tokens": avg_chunk_tokens,
            "stage_durations": stage_durations,
            "app_parallel": self._enable_parallel,
        }

        return {
            "optimized_texts": optimized_texts,
            "report_text": report_text,
            "artifacts": asdict(artifacts),
            "artifacts_meta": artifacts_meta,
            "application_outputs": app_outputs,
        }

    def _run_application_layer(self, knowledge: str, selected_prompts: List[str]) -> Dict[str, str]:
        tasks: Dict[str, str] = {}
        if "summary" in selected_prompts:
            tasks["summary"] = "insight_summary"
            tasks["quotes"] = "quotes"
            tasks["quick_summary"] = "quick_summary"
        if "evaluation" in selected_prompts:
            tasks["evaluation"] = "evaluation"
        if "quotes" in selected_prompts:
            tasks["quotes"] = "quotes"
        if "quick_summary" in selected_prompts:
            tasks["quick_summary"] = "quick_summary"

        if not tasks:
            return {}

        outputs: Dict[str, str] = {}
        if self._enable_parallel and len(tasks) > 1:
            with ThreadPoolExecutor(max_workers=min(4, len(tasks))) as executor:
                futures = {
                    executor.submit(self._llm_runner, prompt_name, knowledge): key
                    for key, prompt_name in tasks.items()
                }
                for future in as_completed(futures):
                    key = futures[future]
                    try:
                        outputs[key] = (future.result() or "").strip()
                    except Exception:
                        outputs[key] = ""
        else:
            for key, prompt_name in tasks.items():
                outputs[key] = (self._llm_runner(prompt_name, knowledge) or "").strip()
        return outputs

    @staticmethod
    def _build_global_synthesis(chunk_summaries: List[ChunkArtifact]) -> str:
        if not chunk_summaries:
            return ""
        lines = ["# Chunk Summaries"]
        for item in chunk_summaries:
            lines.append(f"## Chunk {item.index}")
            lines.append(item.summary)
        return "\n\n".join(lines)

    @staticmethod
    def _build_report(app_outputs: Dict[str, str]) -> str:
        parts = ["# 总报告 (V2 Pipeline)"]
        quick = app_outputs.get("quick_summary", "").strip()
        summary = app_outputs.get("summary", "").strip()
        evaluation = app_outputs.get("evaluation", "").strip()
        quotes = app_outputs.get("quotes", "").strip()

        if quick:
            parts.append("## 快速摘要")
            parts.append(quick)
        if summary:
            parts.append("## 结构化总结")
            parts.append(summary)
        if evaluation:
            parts.append("## 质量评估")
            parts.append(evaluation)
        if quotes:
            parts.append("## 金句")
            parts.append(quotes)
        return "\n\n".join(parts).strip()

