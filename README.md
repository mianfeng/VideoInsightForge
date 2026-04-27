

# VideoInsightForge

**面向 Bilibili/YouTube 视频的“快转录 + 快评估 + 快总结”工具集**

`VideoInsightForge` 是一个高效的视频内容处理工具，旨在通过 ASR（语音识别）技术与 LLM（大模型）的结合，实现视频内容的快速转录、净化格式、深度评估及结构化总结。

## 核心特性

* **多模式交互**：
* **GUI 模式**：高对比度极简 Tkinter 界面，支持流式日志显示与参数实时调节。
* **CLI 模式**：支持单链接、本地文件及基于 B 站关键词的批量搜索转录。
* **浏览器插件**：配合本地 FastAPI 服务，一键提交当前标签页视频任务。


* **高效转录架构**：集成 `Faster-Whisper`，支持 `tiny/base/small` 多级模型，具备繁简自动转换功能。
* **长视频支持**：支持最高 **90 分钟** 视频的「转写 + 清洗」稳定链路。超长文本会自动分块并进行分层汇总，LLM 调用次数会增加。
* **V2 Pipeline（默认）**：`ASR -> Cleaner -> Segmenter -> Chunk Summaries -> Knowledge -> Application`，基于知识层并行生成 summary/evaluation/quotes/quick summary。
* **Prompt 驱动处理**：
* **净化 (Format)**：自动修正 ASR 识别错误，还原技术术语，去除口语冗余。
* **总结 (Summary)**：提炼核心观点与逻辑链，并生成可落地的行动清单。
* **评估 (Evaluation)**：以严苛标准对信息准确性、逻辑严谨性及信息密度进行多维度打分。


* **批量与搜索**：支持读取 `urls.txt` 批量处理，或通过关键字在 B 站搜索并自动抓取前 N 个视频进行分析。

---

## 快速开始

### 1. 环境准备

确保系统中已安装 **Python 3.8+** 和 **FFmpeg**。

* **安装 FFmpeg**:
* Windows: `winget install ffmpeg`
* macOS: `brew install ffmpeg`


* **安装依赖**:
```bash
pip install -r requirements.txt

```



### 2. 配置说明

复制配置文件模板：

```bash
cp config.example.json config.json

```

编辑 `config.json`，配置 LLM 的 API Key 和模型参数：

```json
{
  "llm": {
    "provider": "openai",
    "api_key": "your-api-key-here",
    "base_url": "https://api.openai.com/v1",
    "model": "gpt-4o-mini",
    "long_input_tokens": 6000,
    "chunk_tokens": 1800,
    "chunk_overlap_tokens": 200
  },
  "transcribe": {
    "model_size": "base",
    "cpu_threads": 4
  },
  "download": {
    "cookiefile": "data/bilibili-cookies.txt",
    "cookie_string_file": "data/bilibili-cookie.txt",
    "cookies_from_browser": "",
    "human_like": true,
    "http_headers": {
      "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Referer": "https://www.bilibili.com/"
    }
  },
  "pipeline": {
    "enable_parallel": true
  }
}

```

> 长视频（超过 `long_input_tokens`）会触发分块与分层汇总，成本与时长会增加。可根据模型上下文与预算调整 `chunk_tokens`。  
> 90 分钟保证范围是“转写 + 清洗稳定完成”，summary/evaluation 为 best effort。

### B 站 412 与拟人化下载

工具会在 B 站下载前执行轻量预热：先访问视频页面，再尝试访问页面封面资源，并在请求之间加入 300-1200ms 随机延迟。下载 URL 会自动清理 `spm_id_from`、`vd_source` 等跟踪参数，只保留视频主路径和必要分页参数。

如果 B 站仍返回 `HTTP Error 412: Precondition Failed`，程序会自动执行一次恢复重试，优先切换到有效 cookie 或浏览器 cookie。可选配置方式：

- 将浏览器复制出来的整行 Cookie 字符串保存到 `data/bilibili-cookie.txt`。
- 或导出 Netscape 格式 cookies 到 `data/bilibili-cookies.txt`。
- 或在 `download.cookies_from_browser` 填 `edge` / `chrome` / `firefox` 后重试。浏览器 cookies 方式需要先关闭对应浏览器，否则 yt-dlp 可能无法复制 cookie 数据库。

程序会校验 B 站 cookie 是否包含 `SESSDATA`、`DedeUserID`、`bili_jct` 等登录态字段。文件存在但缺少这些字段时会跳过该 cookie，并在日志中给出明确 warning。

`data/` 已在 `.gitignore` 中，cookies 不会进入提交。

---

## 使用指南

### 图形界面 (GUI)

运行主程序，适合交互式处理单个视频或本地文件：

```bash
python gui.py

```

### 命令行 (CLI)

* **处理单条视频**：
```bash
python transcribe.py --url "https://www.bilibili.com/video/BV..." --prompts summary,evaluation

```

* **V2 默认流程（无需额外参数）**：
```bash
python transcribe.py --url "https://www.bilibili.com/video/BV..." --prompts summary,evaluation

```


* **处理本地文件**：
```bash
python transcribe.py --local "path/to/video.mp4"

```


* **B 站搜索批量处理**：
```bash
python transcribe.py --search "机器学习" --search-count 10 --prompts evaluation

```



### 浏览器扩展

1. **启动后端服务**：`python server.py`。
2. **加载扩展**：在 Chrome/Edge 扩展管理中开启“开发者模式”，选择“加载已解压的扩展程序”，指向 `extension/` 目录。
3. **操作**：在视频页面点击插件图标，配置模型后点击 "Process current tab"。

---

## 项目结构

```text
VideoInsightForge/
├── gui.py              # 高对比度极简 GUI 入口
├── transcribe.py       # 核心逻辑与 CLI 入口
├── server.py           # FastAPI 本地后端服务
├── config.json         # 运行配置
├── prompts/            # LLM 任务模板 (.md)
│   └── pipeline/       # V2 内部提示词模块
├── extension/          # 浏览器插件源码
├── src/                # 模块化组件库
│   ├── downloader.py   # 媒体下载与提取
│   ├── download_config.py # 下载配置、B 站预热与 412 恢复
│   ├── transcriber.py  # ASR 转录封装
│   ├── bilibili_search.py # B 站搜索集成
│   ├── utils.py        # 工具函数
│   └── pipeline/       # V2 Orchestrator 与 artifacts
└── output/             # 结果输出目录（raw/summary/evaluation/report/artifacts）

```

---

## 当前处理流程

```text
视频/音频输入
  ↓
Whisper ASR 转写
  ↓
Cleaner（文本净化）
  ↓
Segmenter（语义分段）
  ↓
Chunk Summaries（长文本分块摘要）
  ↓
Knowledge Layer（全局知识抽取）
  ↓
Application Layer（并行生成）
  ├─ summary
  ├─ evaluation
  ├─ quotes
  └─ quick_summary
  ↓
输出文件（raw + 各任务结果 + report + artifacts）
```

---

## 核心 Prompt 逻辑

项目通过 `prompts/` 目录下的 Markdown 文件定义处理逻辑：

* **`format.md`**：作为预处理层，负责修正术语与净化文稿。
* **`evaluation.md`**：采用严苛打分机制，评估内容的信息价值与稀缺性。
* **`summary.md`**：侧重可执行性，将内容转化为结构化方案。
* **`prompts/pipeline/*.md`**：V2 内部模块（cleaner/segmenter/chunk_summary/knowledge/insight_summary/evaluation/quotes/quick_summary）。

---

## 许可

[MIT License](https://www.google.com/search?q=LICENSE)

## 致谢

感谢 `faster-whisper`、`yt-dlp` 及 `bilibili-api-python` 等开源项目提供的技术支撑。
