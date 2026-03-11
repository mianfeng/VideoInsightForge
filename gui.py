"""
VideoInsightForge GUI (high contrast minimalist)
"""

import os
import sys
import json
import threading
import subprocess
from pathlib import Path
from datetime import datetime
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, filedialog, messagebox, scrolledtext

_HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(_HERE))
os.chdir(_HERE)

CONFIG_FILE = _HERE / "config.json"
PROMPTS_DIR = _HERE / "prompts"
OUTPUT_DIR = _HERE / "output"
INTERNAL_PROMPTS = {"chunk_summary"}

WHISPER_MODELS = ["tiny", "base", "small"]

# High contrast palette
G_BG = "#0b0b0c"
G_BG_LINE = "#15161a"
G_SURFACE = "#111214"
G_INPUT = "#17181c"
G_DIVIDER = "#2a2c31"
G_ACCENT = "#65d1ff"
G_ACCENT_H = "#a9e7ff"
G_TEXT = "#f5f5f5"
G_SUBTEXT = "#b8b8b8"
G_SUCCESS = "#45d18f"
G_ERROR = "#ff6b6b"


def load_config() -> dict:
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg: dict):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def list_prompts():
    prompts = []
    if PROMPTS_DIR.exists():
        for p in sorted(PROMPTS_DIR.glob("*.md")):
            if p.stem in INTERNAL_PROMPTS:
                continue
            if p.stat().st_size > 0:
                prompts.append(p.stem)
    return prompts


def open_folder(path: str):
    if os.path.exists(path):
        subprocess.Popen(f'explorer "{path}"')


def pick_font(candidates, size=10, weight="normal"):
    try:
        families = set(tkfont.families())
        for name in candidates:
            if name in families:
                return (name, size, weight)
    except Exception:
        pass
    return ("Segoe UI", size, weight)


def apply_styles():
    style = ttk.Style()
    style.theme_use("default")
    style.configure(
        "TCombobox",
        fieldbackground=G_INPUT,
        background=G_INPUT,
        foreground=G_TEXT,
        selectbackground=G_INPUT,
        selectforeground=G_TEXT,
        borderwidth=0,
        arrowcolor=G_SUBTEXT,
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", G_INPUT)],
        foreground=[("readonly", G_TEXT)],
    )
    style.configure(
        "Horizontal.TProgressbar",
        troughcolor=G_DIVIDER,
        background=G_ACCENT,
        borderwidth=0,
        thickness=3,
    )


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("VideoInsightForge")
        self.geometry("960x680")
        self.minsize(820, 560)
        self.configure(bg=G_BG)
        apply_styles()

        self._running = False
        self._cfg = load_config()
        self._llm_chars = 0
        self._init_fonts()

        self._build_ui()
        self._log("就绪。填写视频 URL / 本地视频 / 本地音频，选择提示词后点击「处理」。")

    def _init_fonts(self):
        ui_candidates = ["Fira Sans", "Microsoft YaHei UI", "Segoe UI"]
        mono_candidates = ["JetBrains Mono", "Cascadia Mono", "Consolas"]
        self._font_ui = pick_font(ui_candidates, 10)
        self._font_ui_bold = pick_font(ui_candidates, 11, "bold")
        self._font_ui_small = pick_font(ui_candidates, 9)
        self._font_ui_caption = pick_font(ui_candidates, 8, "bold")
        self._font_ui_title = pick_font(ui_candidates, 13, "bold")
        self._font_mono = pick_font(mono_candidates, 10)

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        # Background grid
        self._bg_canvas = tk.Canvas(self, bg=G_BG, highlightthickness=0, bd=0)
        self._bg_canvas.place(x=0, y=0, relwidth=1, relheight=1)
        # Canvas has a tag-based lower(); use tk call to lower the widget itself
        self._bg_canvas.tk.call("lower", self._bg_canvas._w)
        self.bind("<Configure>", self._draw_bg)

        # Header
        nav = tk.Frame(self, bg=G_SURFACE, height=52)
        nav.grid(row=0, column=0, sticky="ew")
        tk.Label(
            nav,
            text="  VideoInsightForge",
            bg=G_SURFACE,
            fg=G_TEXT,
            font=self._font_ui_title,
            pady=14,
        ).pack(side="left")
        tk.Frame(self, bg=G_DIVIDER, height=1).grid(row=0, column=0, sticky="sew")

        # Body
        body = tk.Frame(self, bg=G_BG)
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=0, minsize=270)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        self._build_sidebar(body)
        self._build_log_panel(body)

        # Progress
        self._progress = ttk.Progressbar(
            self, mode="indeterminate", style="Horizontal.TProgressbar"
        )
        self._progress.grid(row=2, column=0, sticky="ew")

    def _draw_bg(self, event=None):
        if not hasattr(self, "_bg_canvas"):
            return
        c = self._bg_canvas
        c.delete("bg")
        w = c.winfo_width()
        h = c.winfo_height()
        if w <= 0 or h <= 0:
            return
        for y in range(60, h, 120):
            c.create_line(0, y, w, y, fill=G_BG_LINE, tags="bg")
        for x in range(120, w, 180):
            c.create_line(x, 0, x, h, fill=G_BG_LINE, tags="bg")

    def _build_sidebar(self, parent):
        sb = tk.Frame(parent, bg=G_SURFACE, width=270)
        sb.grid(row=0, column=0, sticky="nsew")
        sb.columnconfigure(0, weight=1)
        sb.grid_propagate(False)
        tk.Frame(parent, bg=G_DIVIDER, width=1).grid(row=0, column=0, sticky="nse")

        row = 0

        def section(text, r, top=20):
            tk.Label(
                sb,
                text=text,
                bg=G_SURFACE,
                fg=G_SUBTEXT,
                font=self._font_ui_caption,
            ).grid(row=r, column=0, sticky="w", pady=(top, 4), padx=20)

        section("输入源", row)
        row += 1

        tk.Label(
            sb,
            text="在线视频 URL（B站 / YouTube）",
            bg=G_SURFACE,
            fg=G_SUBTEXT,
            font=self._font_ui_small,
        ).grid(row=row, column=0, sticky="w", padx=20)
        row += 1

        self._url_var = tk.StringVar()
        self._mk_entry(sb, self._url_var, row)
        row += 1

        tk.Label(
            sb,
            text="本地视频文件",
            bg=G_SURFACE,
            fg=G_SUBTEXT,
            font=self._font_ui_small,
        ).grid(row=row, column=0, sticky="w", padx=20, pady=(10, 0))
        row += 1

        lf = tk.Frame(sb, bg=G_SURFACE)
        lf.grid(row=row, column=0, sticky="ew", padx=20)
        row += 1
        lf.columnconfigure(0, weight=1)
        self._local_var = tk.StringVar()
        tk.Entry(
            lf,
            textvariable=self._local_var,
            bg=G_INPUT,
            fg=G_TEXT,
            insertbackground=G_TEXT,
            relief="flat",
            font=self._font_ui,
            bd=8,
            width=16,
        ).grid(row=0, column=0, sticky="ew", ipady=3)
        tk.Button(
            lf,
            text="浏览",
            command=self._browse_video,
            bg=G_INPUT,
            fg=G_TEXT,
            relief="flat",
            font=self._font_ui_small,
            padx=10,
            pady=3,
            cursor="hand2",
            activebackground=G_DIVIDER,
        ).grid(row=0, column=1, padx=(6, 0))

        tk.Label(
            sb,
            text="本地音频文件",
            bg=G_SURFACE,
            fg=G_SUBTEXT,
            font=self._font_ui_small,
        ).grid(row=row, column=0, sticky="w", padx=20, pady=(10, 0))
        row += 1

        af = tk.Frame(sb, bg=G_SURFACE)
        af.grid(row=row, column=0, sticky="ew", padx=20)
        row += 1
        af.columnconfigure(0, weight=1)
        self._audio_var = tk.StringVar()
        self._audio_var.trace_add("write", self._on_audio_change)
        tk.Entry(
            af,
            textvariable=self._audio_var,
            bg=G_INPUT,
            fg=G_TEXT,
            insertbackground=G_TEXT,
            relief="flat",
            font=self._font_ui,
            bd=8,
            width=16,
        ).grid(row=0, column=0, sticky="ew", ipady=3)
        tk.Button(
            af,
            text="浏览",
            command=self._browse_audio,
            bg=G_INPUT,
            fg=G_TEXT,
            relief="flat",
            font=self._font_ui_small,
            padx=10,
            pady=3,
            cursor="hand2",
            activebackground=G_DIVIDER,
        ).grid(row=0, column=1, padx=(6, 0))

        tk.Label(
            sb,
            text="优先级：本地音频 > 本地视频 > URL",
            bg=G_SURFACE,
            fg=G_SUBTEXT,
            font=self._font_ui_small,
        ).grid(row=row, column=0, sticky="w", padx=20, pady=(8, 0))
        row += 1

        section("提示词", row)
        row += 1
        self._prompt_vars = {}
        for name in list_prompts():
            var = tk.BooleanVar(value=(name == "summary"))
            tk.Checkbutton(
                sb,
                text=name,
                variable=var,
                bg=G_SURFACE,
                fg=G_TEXT,
                selectcolor=G_INPUT,
                activebackground=G_SURFACE,
                activeforeground=G_TEXT,
                font=self._font_ui,
                anchor="w",
                highlightthickness=0,
                bd=0,
            ).grid(row=row, column=0, sticky="w", padx=20)
            row += 1
            self._prompt_vars[name] = var

        section("Whisper 模型", row)
        row += 1
        cfg_size = self._cfg.get("transcribe", {}).get("model_size", "base")
        self._model_var = tk.StringVar(value=cfg_size)
        ttk.Combobox(
            sb,
            textvariable=self._model_var,
            values=WHISPER_MODELS,
            state="readonly",
            font=self._font_ui,
            width=14,
        ).grid(row=row, column=0, sticky="w", padx=20, pady=(0, 2))
        row += 1
        tk.Label(
            sb,
            text="tiny 快/低精  ·  base 推荐  ·  small 高精",
            bg=G_SURFACE,
            fg=G_SUBTEXT,
            font=self._font_ui_small,
        ).grid(row=row, column=0, sticky="w", padx=20)
        row += 1

        self._no_llm_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            sb,
            text="仅转写，不调用 LLM",
            variable=self._no_llm_var,
            bg=G_SURFACE,
            fg=G_SUBTEXT,
            selectcolor=G_INPUT,
            activebackground=G_SURFACE,
            font=self._font_ui_small,
            anchor="w",
            highlightthickness=0,
            bd=0,
        ).grid(row=row, column=0, sticky="w", padx=20, pady=(10, 0))
        row += 1

        # Buttons
        bf = tk.Frame(sb, bg=G_SURFACE)
        bf.grid(row=row, column=0, sticky="ew", padx=20, pady=(18, 0))
        bf.columnconfigure(0, weight=1)
        row += 1

        self._run_btn = tk.Button(
            bf,
            text="处理",
            bg=G_ACCENT,
            fg="#0d1117",
            relief="flat",
            font=self._font_ui_bold,
            pady=9,
            cursor="hand2",
            activebackground=G_ACCENT_H,
            activeforeground="#0d1117",
            command=self._start,
        )
        self._run_btn.grid(row=0, column=0, sticky="ew")

        tk.Button(
            bf,
            text="输出目录",
            bg=G_INPUT,
            fg=G_TEXT,
            relief="flat",
            font=self._font_ui,
            pady=7,
            cursor="hand2",
            activebackground=G_DIVIDER,
            activeforeground=G_TEXT,
            command=lambda: open_folder(str(OUTPUT_DIR)),
        ).grid(row=1, column=0, sticky="ew", pady=(6, 0))

        tk.Button(
            sb,
            text="设置 API Key / 模型",
            bg=G_SURFACE,
            fg=G_SUBTEXT,
            relief="flat",
            font=self._font_ui_small,
            cursor="hand2",
            activebackground=G_SURFACE,
            command=self._open_settings,
        ).grid(row=row, column=0, sticky="w", padx=18, pady=(8, 0))

    def _build_log_panel(self, parent):
        panel = tk.Frame(parent, bg=G_BG)
        panel.grid(row=0, column=1, sticky="nsew")
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(1, weight=1)

        hdr = tk.Frame(panel, bg=G_BG)
        hdr.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 6))

        tk.Label(
            hdr,
            text="运行日志",
            bg=G_BG,
            fg=G_TEXT,
            font=self._font_ui_bold,
        ).pack(side="left")
        tk.Button(
            hdr,
            text="清空",
            bg=G_BG,
            fg=G_SUBTEXT,
            relief="flat",
            font=self._font_ui_small,
            cursor="hand2",
            activebackground=G_BG,
            command=self._clear_log,
        ).pack(side="right")

        self._llm_progress_var = tk.StringVar(value="")
        tk.Label(
            hdr,
            textvariable=self._llm_progress_var,
            bg=G_BG,
            fg=G_ACCENT,
            font=self._font_ui_small,
        ).pack(side="right", padx=12)

        self._log_box = scrolledtext.ScrolledText(
            panel,
            bg=G_INPUT,
            fg=G_TEXT,
            font=self._font_mono,
            relief="flat",
            padx=12,
            pady=10,
            state="disabled",
            wrap="word",
            insertbackground=G_TEXT,
        )
        self._log_box.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 10))
        self._log_box.tag_config("normal", foreground=G_TEXT)
        self._log_box.tag_config("success", foreground=G_SUCCESS)
        self._log_box.tag_config("error", foreground=G_ERROR)

    def _mk_entry(self, parent, var, row):
        e = tk.Entry(
            parent,
            textvariable=var,
            bg=G_INPUT,
            fg=G_TEXT,
            insertbackground=G_TEXT,
            relief="flat",
            font=self._font_ui,
            bd=8,
        )
        e.grid(row=row, column=0, sticky="ew", padx=20, ipady=4)
        return e

    def _log(self, msg: str, level: str = "normal"):
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}]  {msg}\n"
        self._log_box.configure(state="normal")
        self._log_box.insert("end", line, level)
        self._log_box.see("end")
        self._log_box.configure(state="disabled")

    def _clear_log(self):
        self._log_box.configure(state="normal")
        self._log_box.delete("1.0", "end")
        self._log_box.configure(state="disabled")
        self._llm_progress_var.set("")

    def _browse_video(self):
        path = filedialog.askopenfilename(
            title="选择视频文件",
            filetypes=[
                ("视频文件", "*.mp4 *.avi *.mkv *.mov *.flv *.wmv *.webm *.m4v"),
                ("全部", "*.*"),
            ],
        )
        if path:
            self._local_var.set(path)
            self._audio_var.set("")
            self._url_var.set("")

    def _browse_audio(self):
        path = filedialog.askopenfilename(
            title="选择音频文件",
            filetypes=[
                ("音频文件", "*.mp3 *.wav *.m4a *.aac *.flac *.ogg *.opus *.wma *.aiff *.alac"),
                ("全部", "*.*"),
            ],
        )
        if path:
            self._audio_var.set(path)
            self._local_var.set("")
            self._url_var.set("")

    def _on_audio_change(self, *_):
        if self._audio_var.get().strip():
            self._local_var.set("")
            self._url_var.set("")

    def _start(self):
        if self._running:
            return

        url = self._url_var.get().strip()
        local = self._local_var.get().strip()
        audio = self._audio_var.get().strip()
        target = audio or local or url
        if not target:
            messagebox.showwarning("提示", "请输入视频 URL / 本地视频 / 本地音频")
            return

        selected_prompts = [k for k, v in self._prompt_vars.items() if v.get()]
        no_llm = self._no_llm_var.get()
        if not no_llm and not selected_prompts:
            if not messagebox.askyesno(
                "确认", "未选择任何提示词，将仅保存原始转写文本，继续？"
            ):
                return

        self._running = True
        self._llm_chars = 0
        self._run_btn.configure(state="disabled", text="处理中…")
        self._progress.start(10)
        self._clear_log()

        threading.Thread(
            target=self._worker,
            args=(target, self._model_var.get(), selected_prompts, no_llm),
            daemon=True,
        ).start()

    def _worker(self, target, model_size, prompt_names, no_llm):
        import io

        ERROR_KEYWORDS = (
            "[ERROR]",
            "ERROR:",
            "失败",
            "错误",
            "Not Found",
            "404",
            "403",
            "Exception",
            "Traceback",
        )
        SUCCESS_KEYWORDS = ("完成", "成功", "已保存", "Done")

        class LogRedirect(io.TextIOBase):
            def __init__(self, cb, force_err=False):
                self._cb = cb
                self._force_err = force_err

            def write(self, s):
                stripped = s.strip()
                if stripped:
                    if self._force_err:
                        level = "error"
                    elif any(k in stripped for k in ERROR_KEYWORDS):
                        level = "error"
                    elif any(k in stripped for k in SUCCESS_KEYWORDS):
                        level = "success"
                    else:
                        level = "normal"
                    self._cb(stripped, level)
                return len(s)

            def flush(self):
                pass

        old_out, old_err = sys.stdout, sys.stderr
        cb = lambda m, l: self.after(0, self._log, m, l)
        sys.stdout = LogRedirect(cb)
        sys.stderr = LogRedirect(cb, force_err=False)

        try:
            import transcribe as tc

            if not no_llm:
                def _stream_cb(chars: int, chunk: str):
                    self.after(0, self._llm_progress_var.set,
                               f"LLM 生成中… 已输出 {chars} 字符")
                tc.set_llm_stream_callback(_stream_cb)
            else:
                tc.clear_llm_stream_callback()

            result = tc.process_video(
                video_url=target,
                model_size=model_size,
                enable_llm_optimization=not no_llm,
                prompt_names=prompt_names if not no_llm else [],
            )
            tc.clear_llm_stream_callback()

            if result.get("success"):
                self.after(0, self._llm_progress_var.set, "")
                self.after(0, self._log, f"全部完成！输出目录：{OUTPUT_DIR}", "success")
                self.after(0, self._on_done, True)
            else:
                err = result.get("error", "未知错误")
                self.after(0, self._log, f"处理失败：{err}", "error")
                self.after(0, self._on_done, False)

        except Exception as e:
            self.after(0, self._log, f"异常：{e}", "error")
            self.after(0, self._on_done, False)
        finally:
            sys.stdout, sys.stderr = old_out, old_err

    def _on_done(self, success: bool):
        self._running = False
        self._progress.stop()
        self._llm_progress_var.set("")
        self._run_btn.configure(state="normal", text="处理")

    def _open_settings(self):
        win = tk.Toplevel(self)
        win.title("设置")
        win.geometry("500x280")
        win.configure(bg=G_SURFACE)
        win.resizable(False, False)
        win.grab_set()

        cfg = load_config()
        llm = cfg.get("llm", {})
        fields = [
            ("API Key", "api_key", llm.get("api_key", ""), True),
            ("Base URL", "base_url", llm.get("base_url", ""), False),
            ("模型名称", "model", llm.get("model", ""), False),
        ]
        entries = {}

        for i, (label, key, value, secret) in enumerate(fields):
            tk.Label(win, text=label, bg=G_SURFACE, fg=G_TEXT, font=self._font_ui).grid(
                row=i, column=0, sticky="w", padx=20, pady=10
            )
            var = tk.StringVar(value=value)
            tk.Entry(
                win,
                textvariable=var,
                bg=G_INPUT,
                fg=G_TEXT,
                insertbackground=G_TEXT,
                relief="flat",
                font=self._font_ui,
                width=36,
                bd=8,
                show="•" if secret else "",
            ).grid(row=i, column=1, padx=(0, 20), pady=10, ipady=5)
            entries[key] = var

        def _save():
            cfg["llm"] = cfg.get("llm", {})
            for key, var in entries.items():
                cfg["llm"][key] = var.get().strip()
            save_config(cfg)
            self._cfg = cfg
            self._log("设置已保存", "success")
            win.destroy()

        tk.Button(
            win,
            text="保存",
            bg=G_ACCENT,
            fg="#0d1117",
            relief="flat",
            font=self._font_ui_bold,
            pady=8,
            padx=30,
            cursor="hand2",
            activebackground=G_ACCENT_H,
            activeforeground="#0d1117",
            command=_save,
        ).grid(row=len(fields), column=0, columnspan=2, pady=(6, 16))


if __name__ == "__main__":
    app = App()
    app.mainloop()
