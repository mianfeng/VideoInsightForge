const DEFAULT_SERVER = "http://127.0.0.1:8732";
const POLL_INTERVAL_MS = 1500;

const serverInput = document.getElementById("serverUrl");
const modelSelect = document.getElementById("modelSize");
const promptList = document.getElementById("promptList");
const runBtn = document.getElementById("runBtn");
const openBtn = document.getElementById("openBtn");
const statusEl = document.getElementById("status");
const previewEl = document.getElementById("preview");
const previewType = document.getElementById("previewType");
const resultTitleEl = document.getElementById("resultTitle");
const pathsEl = document.getElementById("paths");
const healthChip = document.getElementById("healthChip");
const tabMeta = document.getElementById("tabMeta");

let currentJobId = null;
let lastResult = null;
let lastRecent = null;
let cachedPrompts = [];
let cachedModelSize = "";

function sendMessage(payload) {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage(payload, (response) => resolve(response));
  });
}

function getServerUrl() {
  return serverInput.value.trim() || DEFAULT_SERVER;
}

function setStatus(text, tone = "muted") {
  statusEl.textContent = text;
  statusEl.className = "support-copy";
  if (tone === "error") {
    statusEl.style.color = "var(--danger)";
  } else if (tone === "success") {
    statusEl.style.color = "var(--success)";
  } else {
    statusEl.style.color = "";
  }
}

function setHealth(text, tone = "muted") {
  healthChip.textContent = text;
  healthChip.className = `status-chip ${tone}`;
}

function formatDate(ts) {
  if (!ts) return "Unknown";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(ts * 1000));
}

function getSelectedPrompts() {
  return Array.from(promptList.querySelectorAll("input:checked")).map((node) => node.dataset.name);
}

async function saveConfig() {
  await chrome.storage.local.set({
    serverUrl: serverInput.value.trim(),
    modelSize: modelSelect.value,
    prompts: getSelectedPrompts(),
  });
}

async function loadConfig() {
  const data = await chrome.storage.local.get(["serverUrl", "modelSize", "prompts"]);
  serverInput.value = data.serverUrl || DEFAULT_SERVER;
  cachedModelSize = data.modelSize || "";
  cachedPrompts = Array.isArray(data.prompts) ? data.prompts : [];
}

function buildViewNames() {
  const names = ["raw"];
  const previewKeys = lastResult?.preview ? Object.keys(lastResult.preview) : [];
  const recentKeys = lastRecent?.preview ? Object.keys(lastRecent.preview) : [];
  [...previewKeys, ...recentKeys].forEach((name) => {
    if (name && !names.includes(name)) {
      names.push(name);
    }
  });
  return names;
}

function renderPreviewOptions() {
  previewType.innerHTML = "";
  buildViewNames().forEach((name) => {
    const option = document.createElement("option");
    option.value = name;
    option.textContent = name;
    previewType.appendChild(option);
  });
}

function renderPaths(paths = {}) {
  pathsEl.innerHTML = "";
  Object.entries(paths).forEach(([label, path]) => {
    if (!path) return;
    const line = document.createElement("div");
    line.className = "path-item";
    line.textContent = `${label}: ${path}`;
    pathsEl.appendChild(line);
  });
}

function renderLatestResult() {
  const activeView = previewType.value || "raw";
  const result = lastResult || lastRecent;

  if (!result) {
    resultTitleEl.textContent = "No result yet";
    previewEl.textContent = "Run a task or wait for the desktop app to generate outputs.";
    renderPaths();
    return;
  }

  resultTitleEl.textContent = result.title || "Latest result";
  previewEl.textContent =
    result.preview?.[activeView] ||
    result.preview?.[result.primary_view] ||
    "No preview available.";

  const filePaths = lastResult
    ? {
        raw: lastResult.raw_file,
        ...(lastResult.optimized_files || {}),
        ...(lastResult.report_file ? { report: lastResult.report_file } : {}),
        ...(lastResult.artifacts_file ? { artifacts: lastResult.artifacts_file } : {}),
      }
    : lastRecent?.files || {};

  renderPaths(filePaths);
}

async function loadHealth() {
  const resp = await sendMessage({ type: "health", serverUrl: getServerUrl() });
  if (!resp?.ok) {
    setHealth("Server offline", "error");
    return;
  }
  setHealth(`Ready | ${resp.data.default_model}`, "success");
}

async function loadRecentResult() {
  const resp = await sendMessage({
    type: "get_recent_results",
    serverUrl: getServerUrl(),
    limit: 1,
  });

  if (!resp?.ok) {
    lastRecent = null;
    renderLatestResult();
    return;
  }

  lastRecent = resp.data.results?.[0] || null;
  renderPreviewOptions();
  renderLatestResult();
}

async function populateModels() {
  const resp = await sendMessage({ type: "get_models", serverUrl: getServerUrl() });
  if (!resp?.ok) return;

  modelSelect.innerHTML = "";
  resp.data.models.forEach((name) => {
    const option = document.createElement("option");
    option.value = name;
    option.textContent = name;
    modelSelect.appendChild(option);
  });
  modelSelect.value = cachedModelSize || resp.data.default || resp.data.models[0];
}

async function populatePrompts() {
  const resp = await sendMessage({ type: "get_prompts", serverUrl: getServerUrl() });
  if (!resp?.ok) return;

  promptList.innerHTML = "";
  resp.data.prompts.forEach((name) => {
    const row = document.createElement("label");
    row.className = "prompt-item";

    const input = document.createElement("input");
    input.type = "checkbox";
    input.dataset.name = name;
    input.checked = cachedPrompts.includes(name) || (!cachedPrompts.length && name === "summary");
    input.addEventListener("change", () => {
      renderPreviewOptions();
      saveConfig();
    });

    const text = document.createElement("span");
    text.textContent = name;

    row.appendChild(input);
    row.appendChild(text);
    promptList.appendChild(row);
  });
}

async function readCurrentTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) {
    tabMeta.textContent = "No active tab found.";
    return null;
  }

  const title = tab.title || "Untitled tab";
  const url = tab.url || "";
  tabMeta.textContent = `${title}\n${url}`;
  return tab;
}

async function startJob() {
  const tab = await readCurrentTab();
  if (!tab?.url) {
    setStatus("No active tab URL found.", "error");
    return;
  }

  runBtn.disabled = true;
  setStatus("Submitting job...");
  const resp = await sendMessage({
    type: "start_job",
    serverUrl: getServerUrl(),
    url: tab.url,
    modelSize: modelSelect.value || undefined,
    prompts: getSelectedPrompts(),
    target: tab.url,
    sourceKind: "url",
  });

  if (!resp?.ok) {
    setStatus(resp?.error || "Failed to start job.", "error");
    runBtn.disabled = false;
    return;
  }

  currentJobId = resp.data.job_id;
  setStatus(`Queued | ${tab.title || tab.url}`);
  pollJob();
}

async function pollJob() {
  if (!currentJobId) return;

  const resp = await sendMessage({
    type: "get_status",
    serverUrl: getServerUrl(),
    jobId: currentJobId,
  });

  if (!resp?.ok) {
    setStatus(resp?.error || "Polling failed.", "error");
    runBtn.disabled = false;
    currentJobId = null;
    return;
  }

  const job = resp.data;
  if (job.status === "running") {
    setStatus(job.llm_progress_text || "Running...");
  } else if (job.status === "failed") {
    setStatus(job.error || "Job failed.", "error");
    currentJobId = null;
    runBtn.disabled = false;
    return;
  } else if (job.status === "succeeded") {
    setStatus("Completed.", "success");
    lastResult = job.result || null;
    renderPreviewOptions();
    renderLatestResult();
    currentJobId = null;
    runBtn.disabled = false;
    loadRecentResult();
    return;
  }

  window.setTimeout(pollJob, POLL_INTERVAL_MS);
}

async function openOutput() {
  const resp = await sendMessage({
    type: "open_output",
    serverUrl: getServerUrl(),
  });
  if (!resp?.ok) {
    setStatus(resp?.error || "Failed to open output folder.", "error");
  }
}

async function bootstrap() {
  await loadConfig();
  await Promise.all([populateModels(), populatePrompts(), loadHealth(), readCurrentTab()]);
  await loadRecentResult();
  renderPreviewOptions();
  renderLatestResult();
}

serverInput.addEventListener("change", () => {
  saveConfig();
  loadHealth();
  loadRecentResult();
});
modelSelect.addEventListener("change", saveConfig);
previewType.addEventListener("change", renderLatestResult);
runBtn.addEventListener("click", startJob);
openBtn.addEventListener("click", openOutput);

bootstrap().catch((error) => {
  setStatus(error.message, "error");
  setHealth("Server offline", "error");
});
