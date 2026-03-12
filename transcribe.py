"""
视频转写工具 - 增强版（支持大模型文本整合）
- 快速转写视频为文本
- 自动调用大模型进行文本优化整合
- 支持 OpenAI、Anthropic、国内大模型等
- 显示每个阶段的耗时
- 支持多提示词处理
- 支持批量处理
- 支持本地视频文件
"""
import os
import sys
import json
import logging
import time
import argparse
import subprocess
from pathlib import Path
from typing import Dict, Optional, List, Tuple
from datetime import datetime

import yt_dlp
from faster_whisper import WhisperModel
from modelscope import snapshot_download
from src.pipeline import V2PipelineOrchestrator

# ==================== 配置 ====================
OUTPUT_DIR = Path("output")
DATA_DIR = Path("data")
MODEL_DIR = Path("models/whisper")
PROMPTS_DIR = Path("prompts")
PIPELINE_PROMPTS_DIR = PROMPTS_DIR / "pipeline"
CONFIG_FILE = Path("config.json")
INTERNAL_PROMPTS = {"chunk_summary"}

OUTPUT_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)
MODEL_DIR.mkdir(exist_ok=True)
PROMPTS_DIR.mkdir(exist_ok=True)
PIPELINE_PROMPTS_DIR.mkdir(exist_ok=True)
Path("logs").mkdir(exist_ok=True)

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/app.log", encoding="utf-8")
    ]
)
logger = logging.getLogger(__name__)

# B站搜索模块
try:
    from src.bilibili_search import search_bilibili_videos, format_duration, format_play_count
    BILIBILI_SEARCH_AVAILABLE = True
except ImportError:
    BILIBILI_SEARCH_AVAILABLE = False
    logger.warning("B站搜索模块不可用，请安装: pip install bilibili-api-python")

# ==================== 工具函数 ====================
def format_time(seconds: float) -> str:
    """格式化时间显示"""
    if seconds < 60:
        return f"{seconds:.1f}秒"
    elif seconds < 3600:
        minutes = int(seconds // 60)
        secs = seconds % 60
        return f"{minutes}分{secs:.0f}秒"
    else:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        return f"{hours}小时{minutes}分"

def estimate_tokens(text: str) -> int:
    """粗略估算 token 数（优先 tiktoken，回退为字符启发式）"""
    if not text:
        return 0
    try:
        import tiktoken  # type: ignore
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        # 非 ASCII（如中文）近似 1 char ≈ 1 token，ASCII 约 4 chars ≈ 1 token
        ascii_chars = sum(1 for c in text if ord(c) < 128)
        non_ascii = len(text) - ascii_chars
        return int(non_ascii + ascii_chars / 4)

def split_text_into_chunks(text: str, chunk_tokens: int, overlap_tokens: int) -> List[str]:
    """按 token 预算分块，tiktoken 可用时按 token 切分，否则按字符切分"""
    if not text:
        return []
    if chunk_tokens <= 0:
        return [text]
    overlap_tokens = max(0, min(overlap_tokens, chunk_tokens // 2))

    try:
        import tiktoken  # type: ignore
        enc = tiktoken.get_encoding("cl100k_base")
        ids = enc.encode(text)
        step = max(1, chunk_tokens - overlap_tokens)
        chunks = []
        for start in range(0, len(ids), step):
            end = min(len(ids), start + chunk_tokens)
            chunk_ids = ids[start:end]
            if chunk_ids:
                chunks.append(enc.decode(chunk_ids))
            if end >= len(ids):
                break
        return chunks
    except Exception:
        # 回退为字符切分（保守一些，适合中文）
        chunk_chars = max(200, chunk_tokens)
        overlap_chars = min(overlap_tokens, chunk_chars // 2)
        chunks = []
        i = 0
        n = len(text)
        while i < n:
            end = min(n, i + chunk_chars)
            # 尝试在末尾附近找断点
            window = text[i:end]
            cutoff = max(
                window.rfind("\n"),
                window.rfind("。"),
                window.rfind("！"),
                window.rfind("？"),
                window.rfind("!"),
                window.rfind("?"),
                window.rfind(";"),
                window.rfind("；"),
            )
            if cutoff >= max(0, len(window) - 200):
                end = i + cutoff + 1
            chunk = text[i:end]
            if chunk:
                chunks.append(chunk)
            if end >= n:
                break
            i = max(0, end - overlap_chars)
        return chunks

def detect_platform(url: str) -> str:
    """检测视频平台"""
    # 先检查是否为本地文件
    if os.path.exists(url) or (not url.startswith('http://') and not url.startswith('https://')):
        return 'Local'

    url_lower = url.lower()
    if 'bilibili.com' in url_lower or 'b23.tv' in url_lower:
        return 'Bilibili'
    elif 'youtube.com' in url_lower or 'youtu.be' in url_lower:
        return 'YouTube'
    else:
        return 'Unknown'


# ==================== 本地媒体检测 ====================
AUDIO_EXTS = {'.mp3', '.wav', '.m4a', '.aac', '.flac', '.ogg', '.opus', '.wma', '.aiff', '.alac'}
VIDEO_EXTS = {'.mp4', '.avi', '.mkv', '.mov', '.flv', '.wmv', '.webm', '.m4v'}

def _ffprobe_json(path: str, args: List[str]) -> Optional[dict]:
    cmd = ["ffprobe", "-v", "error"] + args + [path]
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            text=True
        )
        if not result.stdout:
            return None
        return json.loads(result.stdout)
    except (FileNotFoundError, subprocess.CalledProcessError, json.JSONDecodeError):
        return None

def detect_local_media_type(path: str) -> str:
    """判断本地媒体类型: audio / video / missing"""
    if not os.path.exists(path):
        return "missing"

    info = _ffprobe_json(path, ["-show_streams", "-of", "json"])
    if info and isinstance(info, dict):
        streams = info.get("streams", [])
        types = {s.get("codec_type") for s in streams if s.get("codec_type")}
        if "video" in types:
            return "video"
        if "audio" in types:
            return "audio"

    ext = Path(path).suffix.lower()
    if ext in AUDIO_EXTS:
        return "audio"
    if ext in VIDEO_EXTS:
        return "video"

    # 未知扩展名时默认按音频处理（更宽松）
    return "audio"

def get_media_title(path: str) -> str:
    """优先读取音频/视频元数据标题，失败则回退文件名"""
    info = _ffprobe_json(path, ["-show_entries", "format_tags=title", "-of", "json"])
    if info and isinstance(info, dict):
        tags = info.get("format", {}).get("tags", {}) or {}
        title = tags.get("title")
        if title and isinstance(title, str) and title.strip():
            return title.strip()
    return Path(path).stem

# ==================== 提示词管理 ====================
def list_available_prompts() -> List[str]:
    """列出所有可用的提示词"""
    prompts = []
    for file in PROMPTS_DIR.glob("*.md"):
        if file.name != "README.md":
            if file.stem in INTERNAL_PROMPTS:
                continue
            # 检查文件是否为空
            if file.stat().st_size > 0:
                # 检查是否包含必需的占位符
                try:
                    with open(file, 'r', encoding='utf-8') as f:
                        content = f.read()
                        if content.strip():  # 文件不为空
                            prompts.append(file.stem)
                except Exception as e:
                    logger.warning(f"无法读取提示词文件 {file}: {e}")
    return sorted(prompts)

def load_pipeline_prompt(prompt_name: str) -> str:
    """从 prompts/pipeline 加载 V2 内部提示词"""
    prompt_file = PIPELINE_PROMPTS_DIR / f"{prompt_name}.md"
    if not prompt_file.exists():
        logger.warning(f"V2 提示词不存在: {prompt_file}")
        return "{transcript_text}"

    with open(prompt_file, "r", encoding="utf-8") as f:
        content = f.read().strip()

    if not content:
        logger.warning(f"V2 提示词为空: {prompt_file}")
        return "{transcript_text}"

    if "{transcript_text}" not in content:
        content += "\n\n{transcript_text}"
    return content

# ==================== 配置管理 ====================
def load_config() -> dict:
    """加载配置文件"""
    if not CONFIG_FILE.exists():
        logger.warning(f"配置文件不存在: {CONFIG_FILE}")
        logger.warning("请复制 config.example.json 为 config.json 并填入 API key")
        return {}

    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def _get_pipeline_config(config: dict) -> dict:
    default_cfg = {
        "enable_parallel": True,
        "output_language": "zh",
    }
    cfg = (config or {}).get("pipeline", {}) or {}
    out = dict(default_cfg)
    out.update(cfg)
    out["enable_parallel"] = bool(out.get("enable_parallel", True))
    return out

def _get_llm_limits(config: dict) -> Tuple[int, int, int]:
    """返回 (long_input_tokens, chunk_tokens, chunk_overlap_tokens)"""
    llm_cfg = (config or {}).get("llm", {})
    long_input_tokens = int(llm_cfg.get("long_input_tokens", 6000))
    chunk_tokens = int(llm_cfg.get("chunk_tokens", 1800))
    chunk_overlap_tokens = int(llm_cfg.get("chunk_overlap_tokens", 200))
    return long_input_tokens, chunk_tokens, chunk_overlap_tokens

def optimize_text_with_pipeline_prompt(text: str, config: dict, prompt_name: str) -> Optional[str]:
    prompt_template = load_pipeline_prompt(prompt_name)
    if not prompt_template:
        return None
    return optimize_text_with_prompt_template(text, config, prompt_template, f"pipeline/{prompt_name}")

# ==================== 繁简转换 ====================
def traditional_to_simplified(text: str) -> str:
    """繁体转简体"""
    try:
        from opencc import OpenCC
        cc = OpenCC('t2s')
        return cc.convert(text)
    except ImportError:
        logger.warning("未安装 opencc-python-reimplemented，跳过繁简转换")
        return text

# ==================== 视频下载 ====================
def download_audio(video_url: str) -> tuple[str, str]:
    """下载视频音频"""
    start_time = time.time()
    logger.info(f"开始下载音频: {video_url}")

    output_template = str(DATA_DIR / "%(id)s.%(ext)s")

    ydl_opts = {
        'format': 'bestaudio[ext=m4a]/bestaudio/best',
        'outtmpl': output_template,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '64',
        }],
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True,
        'ffmpeg_location': r'D:\ffmpeg-master-latest-win64-gpl-shared\bin',
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(video_url, download=True)
        video_id = info.get("id")
        title = info.get("title", "未知标题")
        audio_path = str(DATA_DIR / f"{video_id}.mp3")

    elapsed = time.time() - start_time
    logger.info(f"音频下载完成: {title} (耗时: {format_time(elapsed)})")
    return audio_path, title

def extract_audio_from_local_video(video_path: str, quality: str = "fast") -> tuple[str, str]:
    """从本地视频提取音频"""
    start_time = time.time()
    logger.info(f"从本地视频提取音频: {video_path}")

    # 检查文件是否存在
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"本地视频文件不存在: {video_path}")

    # 检查是否为视频文件
    video_extensions = {'.mp4', '.avi', '.mkv', '.mov', '.flv', '.wmv', '.webm', '.m4v'}
    video_path_obj = Path(video_path)
    if video_path_obj.suffix.lower() not in video_extensions:
        raise ValueError(f"不支持的视频格式: {video_path_obj.suffix}")

    # 生成音频文件名
    title = video_path_obj.stem
    audio_path = str(DATA_DIR / f"{title}.mp3")

    # 音频质量映射
    quality_map = {
        "fast": "32",
        "medium": "64",
        "slow": "128"
    }
    bitrate = quality_map.get(quality, '64')

    try:
        # 使用 ffmpeg 提取音频并转换为 mp3
        cmd = [
            'ffmpeg',
            '-i', video_path,
            '-vn',  # 不处理视频
            '-acodec', 'libmp3lame',
            '-ab', f'{bitrate}k',
            '-ar', '44100',
            '-y',  # 覆盖已存在的文件
            audio_path
        ]

        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True
        )

        elapsed = time.time() - start_time
        logger.info(f"音频提取完成: {title} (耗时: {format_time(elapsed)})")
        return audio_path, title

    except subprocess.CalledProcessError as e:
        logger.error(f"音频提取失败: {e.stderr.decode('utf-8', errors='ignore')}")
        raise RuntimeError(f"FFmpeg 提取音频失败: {e}")
    except FileNotFoundError:
        raise RuntimeError("FFmpeg 未安装或不在 PATH 中,请先安装 FFmpeg")


# ==================== 音频转写 ====================
def transcribe_audio(
    audio_path: str,
    model_size: str = "tiny",
    cpu_threads: int = 4
) -> str:
    """转写音频为文本"""
    start_time = time.time()
    model_path = MODEL_DIR / f"whisper-{model_size}"

    # 下载模型（如果不存在）
    if not model_path.exists():
        logger.info(f"下载 Whisper {model_size} 模型...")
        model_map = {
            "tiny": "pengzhendong/faster-whisper-tiny",
            "base": "pengzhendong/faster-whisper-base",
            "small": "pengzhendong/faster-whisper-small",
        }
        repo_id = model_map.get(model_size)
        if not repo_id:
            raise ValueError(f"不支持的模型: {model_size}")

        download_start = time.time()
        snapshot_download(repo_id, local_dir=str(model_path))
        download_elapsed = time.time() - download_start
        logger.info(f"模型下载完成 (耗时: {format_time(download_elapsed)})")

    # 加载模型
    logger.info(f"加载 Whisper 模型 ({model_size})...")
    load_start = time.time()
    model = WhisperModel(
        model_size_or_path=str(model_path),
        device="cpu",
        compute_type="int8",
        cpu_threads=cpu_threads
    )
    load_elapsed = time.time() - load_start
    logger.info(f"模型加载完成 (耗时: {format_time(load_elapsed)})")

    # 转写
    logger.info("开始转写音频...")
    transcribe_start = time.time()
    segments_generator, info = model.transcribe(audio_path, language="zh")

    full_text = ""
    segment_count = 0
    for segment in segments_generator:
        full_text += segment.text.strip() + " "
        segment_count += 1

    full_text = full_text.strip()
    transcribe_elapsed = time.time() - transcribe_start
    logger.info(f"转写完成: {segment_count} 段 (耗时: {format_time(transcribe_elapsed)})")

    # 繁简转换
    logger.info("繁简转换...")
    convert_start = time.time()
    full_text = traditional_to_simplified(full_text)
    convert_elapsed = time.time() - convert_start
    logger.info(f"繁简转换完成 (耗时: {format_time(convert_elapsed)})")

    total_elapsed = time.time() - start_time
    logger.info(f"转写总耗时: {format_time(total_elapsed)}, 共 {len(full_text)} 字符")
    return full_text

# ==================== 大模型文本优化 ====================
def _safe_render_prompt(prompt_template: str, text: str) -> str:
    try:
        return prompt_template.format(transcript_text=text)
    except Exception:
        logger.warning("提示词模板格式化失败，回退为直接拼接文本")
        return f"{prompt_template}\n\n{text}"

def optimize_text_with_prompt_template(
    text: str,
    config: dict,
    prompt_template: str,
    prompt_label: str = "custom"
) -> Optional[str]:
    """使用给定提示词模板调用大模型"""
    if not config or "llm" not in config:
        logger.warning("未配置大模型，跳过文本优化")
        return None

    llm_config = config["llm"]
    provider = llm_config.get("provider", "openai")
    prompt = _safe_render_prompt(prompt_template, text)
    logger.info(f"使用 {provider} 和提示词 '{prompt_label}' 进行文本优化...")

    try:
        if provider == "openai":
            return _optimize_with_openai(prompt, llm_config)
        if provider == "anthropic":
            return _optimize_with_anthropic(prompt, llm_config)
        logger.error(f"不支持的提供商: {provider}")
        return None
    except Exception as e:
        logger.error(f"文本优化失败: {e}")
        return None

# 全局流式回调：GUI 可注入，用于实时显示 LLM 生成进度
_llm_stream_callback = None

def set_llm_stream_callback(cb):
    """注入流式进度回调，cb(chars_received: int, latest_chunk: str)"""
    global _llm_stream_callback
    _llm_stream_callback = cb

def clear_llm_stream_callback():
    global _llm_stream_callback
    _llm_stream_callback = None


def _optimize_with_openai(prompt: str, config: dict) -> str:
    """使用 OpenAI API 优化文本（支持流式输出进度回调）"""
    try:
        from openai import OpenAI
    except ImportError:
        logger.error("未安装 openai 库，请运行: pip install openai")
        return None

    start_time = time.time()

    client = OpenAI(
        api_key=config.get('api_key'),
        base_url=config.get('base_url', 'https://api.openai.com/v1')
    )

    try:
        use_stream = _llm_stream_callback is not None
        response = client.chat.completions.create(
            model=config.get('model', 'gpt-4o-mini'),
            messages=[{"role": "user", "content": prompt}],
            temperature=config.get('temperature', 0.3),
            max_tokens=config.get('max_tokens', 4000),
            stream=use_stream
        )

        if use_stream:
            # 流式：逐 chunk 拼接并回调进度
            optimized_text = ""
            for chunk in response:
                delta = chunk.choices[0].delta.content if chunk.choices else None
                if delta:
                    optimized_text += delta
                    try:
                        _llm_stream_callback(len(optimized_text), delta)
                    except Exception:
                        pass
        else:
            if hasattr(response, 'choices'):
                optimized_text = response.choices[0].message.content
            elif isinstance(response, dict):
                optimized_text = response.get('choices', [{}])[0].get('message', {}).get('content', '')
            elif isinstance(response, str):
                optimized_text = response
            else:
                logger.error(f"未知的响应格式: {type(response)}")
                return None

        if not optimized_text:
            logger.error("API 返回空内容")
            return None

        if optimized_text.strip().startswith(('<!doctype', '<html')):
            logger.error("API 返回了 HTML 页面而不是文本内容")
            return None

        elapsed = time.time() - start_time
        logger.info(f"文本优化完成，共 {len(optimized_text)} 字符 (耗时: {format_time(elapsed)})")
        return optimized_text

    except Exception as e:
        logger.error(f"API 调用失败: {e}")
        if hasattr(e, 'response'):
            logger.error(f"HTTP 状态码: {getattr(e.response, 'status_code', 'unknown')}")
        return None

def _optimize_with_anthropic(prompt: str, config: dict) -> str:
    """使用 Anthropic API 优化文本"""
    try:
        from anthropic import Anthropic
    except ImportError:
        logger.error("未安装 anthropic 库，请运行: pip install anthropic")
        return None

    start_time = time.time()
    client = Anthropic(api_key=config.get('api_key'))

    response = client.messages.create(
        model=config.get('model', 'claude-3-5-sonnet-20241022'),
        max_tokens=config.get('max_tokens', 4000),
        temperature=config.get('temperature', 0.3),
        messages=[
            {"role": "user", "content": prompt}
        ]
    )

    optimized_text = response.content[0].text
    elapsed = time.time() - start_time
    logger.info(f"文本优化完成 (耗时: {format_time(elapsed)})")
    return optimized_text

def _run_v2_pipeline(
    transcript_text: str,
    config: dict,
    prompt_names: List[str],
    enable_parallel: bool
) -> dict:
    """运行 V2 提示词流水线，返回 optimized_texts/report/artifacts"""
    _, chunk_tokens, chunk_overlap_tokens = _get_llm_limits(config)

    def _runner(prompt_name: str, text: str) -> Optional[str]:
        return optimize_text_with_pipeline_prompt(text, config, prompt_name)

    def _splitter(text: str) -> List[str]:
        chunks = split_text_into_chunks(text, chunk_tokens, chunk_overlap_tokens)
        if chunks:
            token_counts = [estimate_tokens(c) for c in chunks]
            avg_tokens = int(sum(token_counts) / len(token_counts))
            logger.info(
                f"[V2] 分块统计: chunks={len(chunks)}, avg_tokens={avg_tokens}, "
                f"min_tokens={min(token_counts)}, max_tokens={max(token_counts)}"
            )
        return chunks

    orchestrator = V2PipelineOrchestrator(
        llm_runner=_runner,
        estimate_tokens=estimate_tokens,
        split_chunks=_splitter,
        logger=logger,
        enable_parallel=enable_parallel,
    )
    return orchestrator.run(transcript_text, prompt_names)


def _build_toc(entries: List[Tuple[str, str]]) -> str:
    lines = []
    for anchor, title in entries:
        lines.append(f"- [{title}](#{anchor})")
    return "\n".join(lines)


def _render_collapsible_section(text: str) -> str:
    body = (text or "").strip()
    if not body:
        return "无"
    return "\n".join(
        [
            "<details>",
            "<summary>点击展开</summary>",
            "",
            body,
            "",
            "</details>",
        ]
    )


def _build_single_markdown_report(
    title: str,
    video_url: str,
    pipeline: str,
    transcript_text: str,
    optimized_texts: Dict[str, str],
    artifacts_data: Optional[dict],
    artifacts_meta: dict,
    total_elapsed: float,
) -> str:
    app_outputs = ((artifacts_data or {}).get("application_outputs") or {}) if artifacts_data else {}
    quick_summary = (app_outputs.get("quick_summary") or "").strip()
    quotes = (app_outputs.get("quotes") or "").strip()
    summary = (optimized_texts.get("summary") or "").strip()
    evaluation = (optimized_texts.get("evaluation") or "").strip()
    formatted_text = (optimized_texts.get("format") or "").strip()

    toc_entries: List[Tuple[str, str]] = [
        ("overview", "概览"),
        ("toc", "目录"),
    ]
    if quick_summary:
        toc_entries.append(("quick-summary", "快速摘要"))
    if summary:
        toc_entries.append(("summary", "详细总结"))
    if evaluation:
        toc_entries.append(("evaluation", "质量评估"))
    if quotes:
        toc_entries.append(("quotes", "金句摘录"))
    toc_entries.extend(
        [
            ("stats", "处理统计"),
            ("formatted-transcript", "净化版文本"),
            ("raw-transcript", "原始转写"),
        ]
    )

    parts = [
        f"# {title}",
        "",
        '<a id="overview"></a>',
        "## 概览",
        "",
        f"- **视频链接**: {video_url}",
        f"- **Pipeline**: {pipeline}",
        f"- **总耗时**: {format_time(total_elapsed)}",
        "",
        '<a id="toc"></a>',
        "## 目录",
        "",
        _build_toc(toc_entries[2:]),
    ]

    if quick_summary:
        parts.extend(
            [
                "",
                '<a id="quick-summary"></a>',
                "## 快速摘要",
                "",
                quick_summary,
            ]
        )

    if summary:
        parts.extend(
            [
                "",
                '<a id="summary"></a>',
                "## 详细总结",
                "",
                summary,
            ]
        )

    if evaluation:
        parts.extend(
            [
                "",
                '<a id="evaluation"></a>',
                "## 质量评估",
                "",
                evaluation,
            ]
        )

    if quotes:
        parts.extend(
            [
                "",
                '<a id="quotes"></a>',
                "## 金句摘录",
                "",
                quotes,
            ]
        )

    stage_durations = artifacts_meta.get("stage_durations", {}) or {}
    parts.extend(
        [
            "",
            '<a id="stats"></a>',
            "## 处理统计",
            "",
            f"- **源文本 tokens**: {artifacts_meta.get('source_tokens', 0)}",
            f"- **净化后 tokens**: {artifacts_meta.get('cleaned_tokens', 0)}",
            f"- **知识层 tokens**: {artifacts_meta.get('knowledge_tokens', 0)}",
            f"- **分块数**: {artifacts_meta.get('chunk_count', 0)}",
            f"- **平均分块 tokens**: {artifacts_meta.get('avg_chunk_tokens', 0)}",
            f"- **应用层并行**: {'是' if artifacts_meta.get('app_parallel') else '否'}",
            "",
            "### 阶段耗时",
            "",
        ]
    )
    if stage_durations:
        for stage_name, seconds in stage_durations.items():
            parts.append(f"- **{stage_name}**: {seconds} 秒")
    else:
        parts.append("- 无")

    parts.extend(
        [
            "",
            '<a id="formatted-transcript"></a>',
            "## 净化版文本",
            "",
            _render_collapsible_section(formatted_text or transcript_text),
            "",
            '<a id="raw-transcript"></a>',
            "## 原始转写",
            "",
            _render_collapsible_section(transcript_text),
        ]
    )

    return "\n".join(parts).strip() + "\n"

# ==================== 主函数 ====================
def process_video(
    video_url: str,
    model_size: str = "tiny",
    cpu_threads: int = 4,
    enable_llm_optimization: bool = True,
    prompt_names: List[str] = None,
) -> dict:
    """处理视频：下载 + 转写 + 优化"""
    total_start = time.time()

    # 检测平台
    platform = detect_platform(video_url)

    print("\n" + "=" * 60)
    print("视频转写工具（增强版 - 支持大模型优化）")
    print(f"平台: {platform}")
    print("=" * 60 + "\n")

    # 加载配置
    config = load_config()
    pipeline_cfg = _get_pipeline_config(config)
    active_pipeline = "v2"

    # 如果没有指定提示词，使用空列表（由调用方决定默认行为）
    if prompt_names is None:
        prompt_names = []

    # 1. 下载音频或从本地媒体提取/读取音频
    if platform == "Local":
        media_type = detect_local_media_type(video_url)
        if media_type == "missing":
            logger.error(f"本地文件不存在: {video_url}")
            return {"success": False, "error": "本地文件不存在", "video_url": video_url, "platform": platform}

        if media_type == "audio":
            print("📁 步骤 1: 识别为本地音频，直接转写...")
            audio_path = video_url
            title = get_media_title(video_url)
        else:
            print("📁 步骤 1: 从本地视频提取音频...")
            try:
                audio_path, title = extract_audio_from_local_video(video_url)
            except Exception as e:
                logger.error(f"音频提取失败: {e}")
                return {"success": False, "error": str(e), "video_url": video_url, "platform": platform}
    else:
        print(f"📥 步骤 1: 下载音频 ({platform})...")
        try:
            audio_path, title = download_audio(video_url)
        except Exception as e:
            logger.error(f"下载失败: {e}")
            return {"success": False, "error": str(e), "video_url": video_url, "platform": platform}

    # 2. 转写音频
    print("\n🎤 步骤 2: 转写音频...")
    try:
        transcript_text = transcribe_audio(audio_path, model_size, cpu_threads)
    except Exception as e:
        logger.error(f"转写失败: {e}")
        return {"success": False, "error": str(e), "video_url": video_url, "title": title}

    # 3. 大模型优化（可选，支持多提示词链式处理）
    optimized_texts = {}
    report_text = ""
    artifacts_data = None
    artifacts_meta = {}
    formatted_text = transcript_text  # 默认使用原始转写

    if enable_llm_optimization and prompt_names:
        print(f"\n🤖 步骤 3: 大模型优化 (Pipeline={active_pipeline}, prompts={len(prompt_names)})...")
        v2_result = _run_v2_pipeline(
            transcript_text=transcript_text,
            config=config,
            prompt_names=prompt_names,
            enable_parallel=bool(pipeline_cfg.get("enable_parallel", True)),
        )
        optimized_texts = v2_result.get("optimized_texts", {}) or {}
        report_text = v2_result.get("report_text", "") or ""
        artifacts_data = v2_result.get("artifacts")
        artifacts_meta = v2_result.get("artifacts_meta", {}) or {}
        if "format" in optimized_texts:
            formatted_text = optimized_texts["format"]
        print("   - 已完成 V2 Orchestrator 流水线")

    # 4. 保存结果
    print("\n💾 步骤 4: 保存结果...")
    save_start = time.time()

    # 生成时间戳（格式：YYMMDD）
    timestamp = datetime.now().strftime("%y%m%d")

    safe_title = "".join(c for c in title if c.isalnum() or c in (' ', '-', '_'))[:50]
    output_prefix = f"{timestamp}_{safe_title}"

    for legacy_file in OUTPUT_DIR.glob(f"{output_prefix}_*"):
        if legacy_file.is_file():
            try:
                legacy_file.unlink()
            except OSError:
                pass

    report_file = OUTPUT_DIR / f"{output_prefix}_report.md"
    report_content = _build_single_markdown_report(
        title=title,
        video_url=video_url,
        pipeline=active_pipeline,
        transcript_text=transcript_text,
        optimized_texts=optimized_texts,
        artifacts_data=artifacts_data,
        artifacts_meta=artifacts_meta,
        total_elapsed=time.time() - total_start,
    )
    with open(report_file, "w", encoding="utf-8") as f:
        f.write(report_content)

    raw_file = None
    optimized_files = {}
    artifacts_file = None

    save_elapsed = time.time() - save_start
    logger.info(f"结果保存完成 (耗时: {format_time(save_elapsed)})")

    # 总耗时
    total_elapsed = time.time() - total_start

    print("\n" + "=" * 60)
    print("✅ 处理完成！")
    print(f"⏱️  总耗时: {format_time(total_elapsed)}")
    print(f"🧩 Pipeline: {active_pipeline}")
    print(f"📝 汇总报告: {report_file}")
    print("=" * 60)

    # 打印预览
    print("\n原始转写预览:")
    print("-" * 60)
    print(transcript_text[:200] + ("..." if len(transcript_text) > 200 else ""))
    print("-" * 60)

    return {
        "success": True,
        "title": title,
        "video_url": video_url,
        "platform": platform,
        "raw_file": str(raw_file) if raw_file else None,
        "optimized_files": optimized_files,
        "report_file": str(report_file) if report_file else None,
        "artifacts_file": str(artifacts_file) if artifacts_file else None,
        "transcript_text": transcript_text,
        "optimized_texts": optimized_texts,
        "artifacts_meta": artifacts_meta,
        "pipeline": active_pipeline,
        "total_time": total_elapsed
    }

# ==================== 批量处理 ====================
def process_batch(
    video_urls: List[str],
    model_size: str = "tiny",
    cpu_threads: int = 4,
    enable_llm_optimization: bool = True,
    prompt_names: List[str] = None,
) -> List[dict]:
    """批量处理多个视频"""
    print("\n" + "=" * 60)
    print(f"批量处理模式 - 共 {len(video_urls)} 个视频")
    print("=" * 60)

    results = []
    for i, url in enumerate(video_urls, 1):
        print(f"\n{'='*60}")
        print(f"处理第 {i}/{len(video_urls)} 个视频")
        print(f"{'='*60}")

        try:
            result = process_video(
                video_url=url,
                model_size=model_size,
                cpu_threads=cpu_threads,
                enable_llm_optimization=enable_llm_optimization,
                prompt_names=prompt_names,
            )
            results.append(result)
        except Exception as e:
            logger.error(f"处理视频失败: {url}, 错误: {e}")
            results.append({
                "success": False,
                "video_url": url,
                "error": str(e)
            })

    # 生成批量处理报告
    print("\n" + "=" * 60)
    print("批量处理完成！")
    print("=" * 60)

    success_count = sum(1 for r in results if r.get("success", False))
    fail_count = len(results) - success_count

    print(f"\n✅ 成功: {success_count} 个")
    print(f"❌ 失败: {fail_count} 个")

    if fail_count > 0:
        print("\n失败的视频:")
        for r in results:
            if not r.get("success", False):
                print(f"  - {r.get('video_url', 'unknown')}: {r.get('error', 'unknown error')}")

    # 保存批量处理报告
    report_file = OUTPUT_DIR / f"batch_report_{int(time.time())}.json"
    with open(report_file, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"\n📊 详细报告已保存: {report_file}")

    return results

# ==================== 命令行入口 ====================
def main():
    """命令行入口"""
    parser = argparse.ArgumentParser(
        description="视频转写工具 - 支持多提示词和批量处理",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 交互式运行
  python transcribe.py

  # 单个视频，使用默认提示词
  python transcribe.py --url "https://www.bilibili.com/video/BV1xxx"

  # 单个视频，使用多个提示词
  python transcribe.py --url "https://..." --prompts evaluation,summary

  # 批量处理
  python transcribe.py --batch urls.txt

  # B站搜索并转录（默认前5个）
  python transcribe.py --search "Python教程"

  # B站搜索并转录前10个
  python transcribe.py --search "Python教程" --search-count 10

  # 列出可用的提示词
  python transcribe.py --list-prompts
        """
    )

    parser.add_argument('--url', type=str, help='视频链接')
    parser.add_argument('--local', type=str, help='本地视频/音频文件路径')
    parser.add_argument('--batch', type=str, help='批量处理文件（每行一个 URL）')
    parser.add_argument('--search', type=str, help='B站搜索关键词')
    parser.add_argument('--search-count', type=int, default=5, help='搜索结果数量（默认5）')
    parser.add_argument('--search-order', type=str, default='totalrank',
                        choices=['totalrank', 'pubdate', 'click', 'dm'],
                        help='搜索排序方式：totalrank=综合排序, pubdate=最新发布, click=最多播放, dm=最多弹幕')
    parser.add_argument('--prompts', type=str, help='提示词名称，多个用逗号分隔（如: evaluation,summary）')
    parser.add_argument('--no-llm', action='store_true', help='禁用大模型优化')
    parser.add_argument('--model-size', type=str, default='tiny', choices=['tiny', 'base', 'small'], help='Whisper 模型大小')
    parser.add_argument('--cpu-threads', type=int, default=4, help='CPU 线程数')
    parser.add_argument('--list-prompts', action='store_true', help='列出所有可用的提示词')

    args = parser.parse_args()

    # 列出可用提示词
    if args.list_prompts:
        prompts = list_available_prompts()
        print("\n可用的提示词:")
        for prompt in prompts:
            print(f"  - {prompt}")
        return

    # 解析提示词
    prompt_names = None
    if args.prompts:
        prompt_names = [p.strip() for p in args.prompts.split(',')]
        # 验证提示词是否存在
        available = list_available_prompts()
        for p in prompt_names:
            if p not in available:
                print(f"错误: 提示词 '{p}' 不存在")
                print(f"可用的提示词: {', '.join(available)}")
                return

    # B站搜索模式
    if args.search:
        if not BILIBILI_SEARCH_AVAILABLE:
            print("错误: B站搜索功能不可用")
            print("请安装依赖: pip install bilibili-api-python")
            return

        print(f"\n🔍 搜索B站视频: {args.search}")
        print(f"   数量: {args.search_count}")
        print(f"   排序: {args.search_order}")

        # 搜索视频
        videos = search_bilibili_videos(
            keyword=args.search,
            count=args.search_count,
            order=args.search_order
        )

        if not videos:
            print("错误: 搜索无结果或搜索失败")
            return

        # 显示搜索结果
        print(f"\n📊 找到 {len(videos)} 个视频:")
        for i, video in enumerate(videos, 1):
            print(f"  {i}. {video['title']}")
            print(f"     时长: {format_duration(video['duration'])}, "
                  f"播放: {format_play_count(video['play'])}, "
                  f"UP主: {video['author']}")

        # 提取URL列表
        urls = [video['url'] for video in videos]

        # 如果没有指定提示词，使用所有可用的提示词
        if prompt_names is None:
            prompt_names = list_available_prompts()
            if prompt_names:
                print(f"\n未指定提示词，将使用所有可用的提示词: {', '.join(prompt_names)}")
            else:
                print("\n警告: 未找到可用的提示词，将只进行原始转写")

        # 调用批量处理
        print(f"\n🎬 开始批量转录...")
        process_batch(
            video_urls=urls,
            model_size=args.model_size,
            cpu_threads=args.cpu_threads,
            enable_llm_optimization=not args.no_llm,
            prompt_names=prompt_names,
        )
        return

    # 批量处理模式
    if args.batch:
        batch_file = Path(args.batch)
        if not batch_file.exists():
            print(f"错误: 批量处理文件不存在: {batch_file}")
            return

        with open(batch_file, 'r', encoding='utf-8') as f:
            urls = [line.strip() for line in f if line.strip() and not line.startswith('#')]

        if not urls:
            print("错误: 批量处理文件为空")
            return

        # 如果没有指定提示词，使用所有可用的提示词
        if prompt_names is None:
            prompt_names = list_available_prompts()
            if prompt_names:
                print(f"未指定提示词，将使用所有可用的提示词: {', '.join(prompt_names)}")
            else:
                print("警告: 未找到可用的提示词，将只进行原始转写")

        process_batch(
            video_urls=urls,
            model_size=args.model_size,
            cpu_threads=args.cpu_threads,
            enable_llm_optimization=not args.no_llm,
            prompt_names=prompt_names,
        )
        return

    # 单个视频处理
    video_url = args.url or args.local
    if not video_url:
        # 交互式模式
        print("\n请输入视频链接或本地视频/音频文件路径:")
        video_url = input("> ").strip()

        if not video_url:
            print("错误: 请输入有效的视频链接")
            return

        # 询问是否启用大模型
        if not args.no_llm:
            print("\n是否启用大模型文本优化？(y/n，默认 y):")
            enable_opt = input("> ").strip().lower()
            enable_llm = enable_opt != 'n'

            if enable_llm and not prompt_names:
                # 显示可用提示词
                available = list_available_prompts()
                print(f"\n可用的提示词: {', '.join(available)}")
                print("请选择提示词（多个用逗号分隔，直接回车则选择全部）:")
                prompts_input = input("> ").strip()

                if prompts_input:
                    # 如果用户输入了内容，就按用户输入的来
                    prompt_names = [p.strip() for p in prompts_input.split(',')]
                else:
                    # 如果用户没输入内容（直接回车），就使用全部可用的提示词
                    prompt_names = available

        else:
            enable_llm = False
    else:
        # 命令行模式
        enable_llm = not args.no_llm

        # 如果没有指定提示词，使用所有可用的提示词
        if enable_llm and prompt_names is None:
            prompt_names = list_available_prompts()
            if prompt_names:
                print(f"未指定提示词，将使用所有可用的提示词: {', '.join(prompt_names)}")
            else:
                print("警告: 未找到可用的提示词，将只进行原始转写")

    try:
        process_video(
            video_url=video_url,
            model_size=args.model_size,
            cpu_threads=args.cpu_threads,
            enable_llm_optimization=enable_llm,
            prompt_names=prompt_names,
        )
    except Exception as e:
        logger.error(f"处理失败: {e}")
        print(f"\n错误: {e}")

if __name__ == "__main__":
    main()
