const DEFAULT_SERVER = "http://127.0.0.1:8732";
const POLL_INTERVAL_MS = 1500;

const serverInput = document.getElementById("serverUrl");
const modelSelect = document.getElementById("modelSize");
const promptList = document.getElementById("promptList");
const runBtn = document.getElementById("runBtn");
const openBtn = document.getElementById("openBtn");
const statusEl = document.getElementById("status");
const previewEl = document.getElementById("preview");
const resultTitleEl = document.getElementById("resultTitle");
const resultMetaEl = document.getElementById("resultMeta");
const healthChip = document.getElementById("healthChip");
const tabMeta = document.getElementById("tabMeta");
const selectionHeadline = document.getElementById("selectionHeadline");

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

function getPrimaryView(result) {
  const files = result?.files || result?.views || {};
  return result?.primary_view || (files.report ? "report" : Object.keys(files)[0] || "report");
}

function getPrimaryFile(result) {
  const files = result?.files || result?.views || {};
  const primaryView = getPrimaryView(result);
  return result?.primary_file || files[primaryView] || result?.report_file || null;
}

function getPrimaryPreview(result) {
  const primaryView = getPrimaryView(result);
  return result?.preview?.[primaryView] || result?.preview?.report || "No preview available.";
}

function getFileName(path) {
  if (!path) return "";
  const normalized = path.replace(/\//g, "\\");
  return normalized.split("\\").pop();
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

function renderLatestResult() {
  const result = lastResult || lastRecent;
  if (!result) {
    resultTitleEl.textContent = "暂无报告";
    resultMetaEl.textContent = "最近生成的汇总报告会显示在这里。";
    previewEl.textContent = "运行任务后，这里会展示报告预览。";
    return;
  }

  resultTitleEl.textContent = result.title || "Latest report";

  const fileName = getFileName(getPrimaryFile(result));
  resultMetaEl.textContent = fileName
    ? `${formatDate(result.updated_at)} · ${fileName}`
    : `${formatDate(result.updated_at)} · 汇总报告预览`;
  previewEl.textContent = getPrimaryPreview(result);
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
    input.addEventListener("change", saveConfig);

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
    selectionHeadline.textContent = "未找到当前标签页";
    tabMeta.textContent = "No active tab found.";
    return null;
  }

  const title = tab.title || "Untitled tab";
  const url = tab.url || "";
  selectionHeadline.textContent = title;
  tabMeta.textContent = url || "当前标签页没有可用 URL。";
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
    lastResult = job.result ? { ...job.result, updated_at: Date.now() / 1000 } : null;
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
  renderLatestResult();
}

serverInput.addEventListener("change", () => {
  saveConfig();
  loadHealth();
  loadRecentResult();
});
modelSelect.addEventListener("change", saveConfig);
runBtn.addEventListener("click", startJob);
openBtn.addEventListener("click", openOutput);

bootstrap().catch((error) => {
  setStatus(error.message, "error");
  setHealth("Server offline", "error");
});
