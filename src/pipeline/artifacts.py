from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class ChunkArtifact:
    index: int
    token_estimate: int
    summary: str


@dataclass
class PipelineStats:
    source_tokens: int
    cleaned_tokens: int
    knowledge_tokens: int
    chunk_count: int
    avg_chunk_tokens: int
    stage_durations: Dict[str, float] = field(default_factory=dict)
    app_parallel: bool = True


@dataclass
class PipelineArtifacts:
    cleaned_transcript: str
    semantic_segments: str
    chunk_summaries: List[ChunkArtifact]
    global_synthesis: str
    knowledge: str
    application_outputs: Dict[str, str]
    stats: PipelineStats

