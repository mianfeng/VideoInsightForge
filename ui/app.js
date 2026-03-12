const PROMPT_COPY = {
  summary: "Actionable recap",
  evaluation: "Strict quality read",
  format: "Cleaned transcript",
};

const state = {
  sourceKind: "url",
  currentJobId: null,
  pollHandle: null,
  recentResults: [],
  currentResult: null,
  currentView: "",
  prompts: [],
  selectedPrompts: new Set(["summary"]),
  config: {},
};

const els = {
  healthChip: document.getElementById("healthChip"),
  settingsBtn: document.getElementById("settingsBtn"),
  outputBtn: document.getElementById("outputBtn"),
  selectionHeadline: document.getElementById("selectionHeadline"),
  selectionMeta: document.getElementById("selectionMeta"),
  sourceSwitch: document.getElementById("sourceSwitch"),
  sourceInputLabel: document.getElementById("sourceInputLabel"),
  sourceInput: document.getElementById("sourceInput"),
  browseBtn: document.getElementById("browseBtn"),
  sourceHint: document.getElementById("sourceHint"),
  modelSize: document.getElementById("modelSize"),
  noLlmToggle: document.getElementById("noLlmToggle"),
  promptList: document.getElementById("promptList"),
  promptHint: document.getElementById("promptHint"),
  runBtn: document.getElementById("runBtn"),
  runSummary: document.getElementById("runSummary"),
  resultTitle: document.getElementById("resultTitle"),
  platformPill: document.getElementById("platformPill"),
  updatedMeta: document.getElementById("updatedMeta"),
  resultTabs: document.getElementById("resultTabs"),
  resultFiles: document.getElementById("resultFiles"),
  readerContent: document.getElementById("readerContent"),
  jobStatus: document.getElementById("jobStatus"),
  llmProgress: document.getElementById("llmProgress"),
  logOutput: document.getElementById("logOutput"),
  clearLogBtn: document.getElementById("clearLogBtn"),
  refreshRecentBtn: document.getElementById("refreshRecentBtn"),
  recentList: document.getElementById("recentList"),
  settingsModal: document.getElementById("settingsModal"),
  closeSettingsBtn: document.getElementById("closeSettingsBtn"),
  providerInput: document.getElementById("providerInput"),
  apiKeyInput: document.getElementById("apiKeyInput"),
  baseUrlInput: document.getElementById("baseUrlInput"),
  llmModelInput: document.getElementById("llmModelInput"),
  defaultWhisperInput: document.getElementById("defaultWhisperInput"),
  saveSettingsBtn: document.getElementById("saveSettingsBtn"),
};

function isDesktopApiReady() {
  return Boolean(window.pywebview && window.pywebview.api);
}

async function fetchJson(url, options = {}) {
  const headers = new Headers(options.headers || {});
  if (!headers.has("Content-Type") && options.body) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(url, { ...options, headers });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `HTTP ${response.status}`);
  }
  return response.json();
}

function setChip(el, text, tone = "muted") {
  el.textContent = text;
  el.className = `status-chip ${tone}`;
}

function formatDate(ts) {
  if (!ts) return "Unknown time";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(ts * 1000));
}

function sourceMeta(kind) {
  if (kind === "local_video") {
    return {
      label: "本地视频文件",
      placeholder: "请选择一个本地视频文件",
      hint: "桌面模式会调用系统文件选择器。",
      browse: true,
      summary: "本地视频",
    };
  }
  if (kind === "local_audio") {
    return {
      label: "本地音频文件",
      placeholder: "请选择一个本地音频文件",
      hint: "更适合只做转写或语音内容分析。",
      browse: true,
      summary: "本地音频",
    };
  }
  return {
    label: "视频链接",
    placeholder: "https://www.bilibili.com/video/BV...",
    hint: "粘贴 Bilibili 或 YouTube 链接开始分析。",
    browse: false,
    summary: "在线链接",
  };
}

function summarizeInputValue(value) {
  if (!value) {
    return "等待选择输入";
  }

  if (state.sourceKind === "url") {
    try {
      const { hostname, pathname } = new URL(value);
      return `${hostname}${pathname.length > 18 ? `${pathname.slice(0, 18)}...` : pathname}`;
    } catch (error) {
      return value.length > 34 ? `${value.slice(0, 34)}...` : value;
    }
  }

  const parts = value.split(/[/\\]/);
  return parts[parts.length - 1] || value;
}

function updateSelectionSummary() {
  const meta = sourceMeta(state.sourceKind);
  const target = els.sourceInput.value.trim();
  const prompts = els.noLlmToggle.checked ? [] : collectSelectedPrompts();
  const promptText = els.noLlmToggle.checked
    ? "仅转写"
    : prompts.length
      ? `${prompts.length} 个 prompt`
      : "未选择 prompt";

  els.selectionHeadline.textContent = summarizeInputValue(target);
  els.selectionMeta.textContent = `${meta.summary} · ${els.modelSize.value || "tiny"} · ${promptText}`;
}

function setSourceKind(kind) {
  state.sourceKind = kind;
  const meta = sourceMeta(kind);
  els.sourceInputLabel.textContent = meta.label;
  els.sourceInput.placeholder = meta.placeholder;
  els.sourceHint.textContent = meta.hint;
  els.sourceInput.readOnly = meta.browse;
  els.browseBtn.classList.toggle("hidden", !meta.browse);

  els.sourceSwitch.querySelectorAll(".source-pill").forEach((button) => {
    button.classList.toggle("active", button.dataset.kind === kind);
  });

  if (meta.browse) {
    els.sourceInput.value = "";
  }

  updateSelectionSummary();
}

function renderPromptList() {
  els.promptList.innerHTML = "";
  state.prompts.forEach((name) => {
    const label = document.createElement("label");
    label.className = "prompt-option";

    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = state.selectedPrompts.has(name);
    input.disabled = els.noLlmToggle.checked;
    input.addEventListener("change", () => {
      if (input.checked) {
        state.selectedPrompts.add(name);
      } else {
        state.selectedPrompts.delete(name);
      }
      updateSelectionSummary();
    });

    const copy = document.createElement("div");
    copy.className = "prompt-copy";
    copy.innerHTML = `<strong>${name}</strong><span>${PROMPT_COPY[name] || "Reader output"}</span>`;

    label.appendChild(input);
    label.appendChild(copy);
    els.promptList.appendChild(label);
  });

  updateSelectionSummary();
}

function renderModelOptions(models, defaultValue) {
  const options = models.map((name) => `<option value="${name}">${name}</option>`).join("");
  els.modelSize.innerHTML = options;
  els.defaultWhisperInput.innerHTML = options;

  const activeModel = state.config?.transcribe?.model_size || defaultValue || models[0] || "tiny";
  els.modelSize.value = activeModel;
  els.defaultWhisperInput.value = activeModel;
  updateSelectionSummary();
}

function collectSelectedPrompts() {
  return Array.from(state.selectedPrompts);
}

function normalizeRecentResult(item) {
  return {
    id: item.id,
    title: item.title || "Untitled Result",
    updatedAt: item.updated_at || 0,
    files: item.files || {},
    preview: item.preview || {},
    views: item.views || Object.keys(item.files || {}),
    primaryView: item.primary_view || "raw",
    platform: item.platform || "",
  };
}

function normalizeJobResult(result) {
  return {
    id: result.raw_file || `job-${Date.now()}`,
    title: result.title || "Untitled Result",
    updatedAt: Date.now() / 1000,
    files: result.views || {},
    preview: result.preview || {},
    views: Object.keys(result.views || {}),
    primaryView: Object.keys(result.views || {})[0] || "raw",
    platform: result.platform || "",
  };
}

function renderReaderShell() {
  const result = state.currentResult;
  if (!result) {
    els.resultTitle.textContent = "暂无输出";
    els.updatedMeta.textContent = "完成分析后，结果会显示在这里。";
    setChip(els.platformPill, "Waiting", "muted");
    els.resultTabs.innerHTML = "";
    els.resultFiles.innerHTML = "";
    els.readerContent.textContent = "完成分析后，结果内容会显示在这里。";
    return;
  }

  els.resultTitle.textContent = result.title;
  els.updatedMeta.textContent = `Updated ${formatDate(result.updatedAt)}`;
  setChip(els.platformPill, result.platform || "Local result", "muted");

  els.resultTabs.innerHTML = "";
  result.views.forEach((view) => {
    const button = document.createElement("button");
    button.className = `tab-button ${state.currentView === view ? "active" : ""}`;
    button.textContent = view;
    button.addEventListener("click", () => selectResultView(view));
    els.resultTabs.appendChild(button);
  });

  els.resultFiles.innerHTML = "";
  result.views.forEach((view) => {
    const path = result.files[view];
    if (!path) return;
    const pill = document.createElement("button");
    pill.className = "file-pill";
    pill.textContent = `${view}: ${path.split("\\").pop()}`;
    pill.title = path;
    pill.addEventListener("click", () => selectResultView(view));
    els.resultFiles.appendChild(pill);
  });
}

async function loadViewContent(view) {
  const result = state.currentResult;
  if (!result) return;

  const path = result.files[view];
  if (!path) {
    els.readerContent.textContent = result.preview[view] || "No content available.";
    return;
  }

  els.readerContent.textContent = "Loading content...";
  try {
    const payload = await fetchJson(`/results/content?path=${encodeURIComponent(path)}`);
    els.readerContent.textContent = payload.content || "Empty file.";
  } catch (error) {
    els.readerContent.textContent = result.preview[view] || error.message;
  }
}

async function selectResultView(view) {
  state.currentView = view;
  renderReaderShell();
  await loadViewContent(view);
}

async function selectResult(result, preferredView) {
  state.currentResult = result;
  state.currentView = preferredView || result.primaryView || result.views[0] || "raw";
  renderReaderShell();
  await loadViewContent(state.currentView);
  renderRecentList();
}

function renderRecentList() {
  els.recentList.innerHTML = "";
  if (!state.recentResults.length) {
    const empty = document.createElement("p");
    empty.className = "recent-preview";
    empty.textContent = "还没有输出。开始一次分析后，这里会保留最近结果。";
    els.recentList.appendChild(empty);
    return;
  }

  state.recentResults.forEach((rawItem) => {
    const item = normalizeRecentResult(rawItem);
    const card = document.createElement("button");
    card.className = `recent-card ${state.currentResult?.id === item.id ? "active" : ""}`;
    card.addEventListener("click", () => selectResult(item, item.primaryView));

    const title = document.createElement("p");
    title.className = "recent-title";
    title.textContent = item.title;

    const meta = document.createElement("p");
    meta.className = "recent-meta";
    meta.textContent = `${formatDate(item.updatedAt)} | ${item.views.join(" / ")}`;

    const preview = document.createElement("p");
    preview.className = "recent-preview";
    preview.textContent = item.preview[item.primaryView] || "No preview available.";

    card.appendChild(title);
    card.appendChild(meta);
    card.appendChild(preview);
    els.recentList.appendChild(card);
  });
}

function renderJobPayload(job) {
  const tone =
    job.status === "failed" ? "error" : job.status === "succeeded" ? "success" : "muted";
  setChip(els.jobStatus, job.status, tone);
  els.llmProgress.textContent = job.llm_progress_text || "No active generation.";
  els.logOutput.textContent = job.logs?.length ? job.logs.join("\n") : "No task activity yet.";

  if (job.status === "queued") {
    els.runSummary.textContent = "任务已提交，等待开始处理。";
  } else if (job.status === "running") {
    els.runSummary.textContent = "正在处理。结果完成后会自动出现在下方输出区。";
  } else if (job.status === "failed") {
    els.runSummary.textContent = job.error || "The task failed.";
  } else if (job.status === "succeeded") {
    els.runSummary.textContent = "已完成。结果已经进入输出区。";
  }
}

async function pollCurrentJob() {
  if (!state.currentJobId) return;

  try {
    const job = await fetchJson(`/jobs/${state.currentJobId}`);
    renderJobPayload(job);

    if (job.status === "succeeded" && job.result) {
      const result = normalizeJobResult(job.result);
      await selectResult(result, result.primaryView);
      await refreshRecentResults();
      if (state.recentResults.length) {
        const latest = normalizeRecentResult(state.recentResults[0]);
        await selectResult(latest, latest.primaryView);
      }
      state.currentJobId = null;
      els.runBtn.disabled = false;
      return;
    }

    if (job.status === "failed") {
      state.currentJobId = null;
      els.runBtn.disabled = false;
      return;
    }

    state.pollHandle = window.setTimeout(pollCurrentJob, 1500);
  } catch (error) {
    setChip(els.jobStatus, "connection error", "error");
    els.runSummary.textContent = error.message;
    els.runBtn.disabled = false;
    state.currentJobId = null;
  }
}

async function refreshRecentResults() {
  const payload = await fetchJson("/results/recent?limit=8");
  state.recentResults = payload.results || [];
  renderRecentList();

  if (!state.currentResult && state.recentResults.length) {
    const latest = normalizeRecentResult(state.recentResults[0]);
    await selectResult(latest, latest.primaryView);
  }
}

async function refreshHealth() {
  try {
    const health = await fetchJson("/health");
    setChip(els.healthChip, `Server ready | ${health.default_model}`, "success");
  } catch (error) {
    setChip(els.healthChip, "Server offline", "error");
  }
}

async function chooseMedia() {
  if (!isDesktopApiReady()) {
    els.runSummary.textContent = "本地文件选择需要通过 python gui.py 启动桌面版。";
    return;
  }

  const kind = state.sourceKind === "local_audio" ? "audio" : "video";
  const response = await window.pywebview.api.choose_media(kind);
  if (response?.path) {
    els.sourceInput.value = response.path;
    updateSelectionSummary();
  }
}

async function openOutput() {
  if (isDesktopApiReady()) {
    await window.pywebview.api.open_output();
    return;
  }
  await fetchJson("/open-output");
}

async function startJob() {
  const target = els.sourceInput.value.trim();
  if (!target) {
    els.runSummary.textContent = "请先提供链接或选择本地文件。";
    return;
  }

  const prompts = els.noLlmToggle.checked ? [] : collectSelectedPrompts();
  if (!els.noLlmToggle.checked && !prompts.length) {
    const confirmed = window.confirm("No prompts selected. Continue with raw transcript only?");
    if (!confirmed) return;
  }

  els.runBtn.disabled = true;
  els.logOutput.textContent = "Submitting job...";
  els.readerContent.textContent = "等待新的分析结果...";
  setChip(els.jobStatus, "queued", "muted");

  const payload = await fetchJson("/jobs", {
    method: "POST",
    body: JSON.stringify({
      target,
      source_kind: state.sourceKind,
      model_size: els.modelSize.value,
      prompts,
      no_llm: els.noLlmToggle.checked,
    }),
  });

  state.currentJobId = payload.job_id;
  renderJobPayload(payload);
  if (state.pollHandle) {
    clearTimeout(state.pollHandle);
  }
  state.pollHandle = window.setTimeout(pollCurrentJob, 900);
}

function openSettings() {
  els.providerInput.value = state.config?.llm?.provider || "";
  els.apiKeyInput.value = state.config?.llm?.api_key || "";
  els.baseUrlInput.value = state.config?.llm?.base_url || "";
  els.llmModelInput.value = state.config?.llm?.model || "";
  els.defaultWhisperInput.value =
    state.config?.transcribe?.model_size || els.defaultWhisperInput.value;
  els.settingsModal.classList.remove("hidden");
}

function closeSettings() {
  els.settingsModal.classList.add("hidden");
}

async function saveSettings() {
  const payload = {
    llm: {
      provider: els.providerInput.value.trim(),
      api_key: els.apiKeyInput.value.trim(),
      base_url: els.baseUrlInput.value.trim(),
      model: els.llmModelInput.value.trim(),
    },
    transcribe: {
      model_size: els.defaultWhisperInput.value,
    },
  };

  state.config = await fetchJson("/config", {
    method: "PUT",
    body: JSON.stringify(payload),
  });
  els.modelSize.value = state.config?.transcribe?.model_size || els.modelSize.value;
  closeSettings();
  els.runSummary.textContent = "设置已保存。";
  updateSelectionSummary();
  await refreshHealth();
}

async function bootstrap() {
  setSourceKind("url");

  try {
    const [health, models, prompts, config] = await Promise.all([
      fetchJson("/health"),
      fetchJson("/models"),
      fetchJson("/prompts"),
      fetchJson("/config"),
    ]);

    state.config = config || {};
    state.prompts = prompts.prompts || [];
    if (state.prompts.length && !state.selectedPrompts.size) {
      state.selectedPrompts.add(state.prompts[0]);
    }

    renderModelOptions(models.models || [], config?.transcribe?.model_size || models.default);
    renderPromptList();
    setChip(els.healthChip, `Server ready | ${health.default_model}`, "success");
    updateSelectionSummary();
    await refreshRecentResults();
  } catch (error) {
    setChip(els.healthChip, "Server offline", "error");
    els.runSummary.textContent = error.message;
  }
}

els.sourceSwitch.addEventListener("click", (event) => {
  const button = event.target.closest(".source-pill");
  if (!button) return;
  setSourceKind(button.dataset.kind);
});

els.browseBtn.addEventListener("click", chooseMedia);
els.sourceInput.addEventListener("input", updateSelectionSummary);
els.modelSize.addEventListener("change", updateSelectionSummary);
els.runBtn.addEventListener("click", () => {
  startJob().catch((error) => {
    els.runBtn.disabled = false;
    els.runSummary.textContent = error.message;
    setChip(els.jobStatus, "failed", "error");
  });
});
els.outputBtn.addEventListener("click", () => {
  openOutput().catch((error) => {
    els.runSummary.textContent = error.message;
  });
});
els.settingsBtn.addEventListener("click", openSettings);
els.closeSettingsBtn.addEventListener("click", closeSettings);
els.saveSettingsBtn.addEventListener("click", () => {
  saveSettings().catch((error) => {
    els.runSummary.textContent = error.message;
  });
});
els.noLlmToggle.addEventListener("change", () => {
  renderPromptList();
  els.promptHint.textContent = els.noLlmToggle.checked
    ? "已关闭 prompt 输出，当前只会生成转写结果。"
    : "只勾选这次真正需要的视角。";
  updateSelectionSummary();
});
els.clearLogBtn.addEventListener("click", (event) => {
  event.preventDefault();
  event.stopPropagation();
  els.logOutput.textContent = "Log cleared locally.";
});
els.refreshRecentBtn.addEventListener("click", () => {
  refreshRecentResults().catch((error) => {
    els.runSummary.textContent = error.message;
  });
});
els.settingsModal.addEventListener("click", (event) => {
  if (event.target === els.settingsModal) {
    closeSettings();
  }
});

bootstrap();
window.setInterval(refreshHealth, 12000);
