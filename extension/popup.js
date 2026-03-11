const DEFAULT_SERVER = "http://127.0.0.1:8732";
const POLL_INTERVAL_MS = 1500;

const serverInput = document.getElementById("serverUrl");
const runBtn = document.getElementById("runBtn");
const openBtn = document.getElementById("openBtn");
const statusEl = document.getElementById("status");
const previewEl = document.getElementById("preview");
const pathsEl = document.getElementById("paths");
const modelSelect = document.getElementById("modelSize");
const promptList = document.getElementById("promptList");
const previewType = document.getElementById("previewType");

let currentJobId = null;
let polling = false;
let lastResult = null;
let cachedPrompts = [];
let cachedModelSize = "";

function setStatus(text, isError = false) {
  statusEl.textContent = text;
  statusEl.classList.toggle("error", isError);
}

function setPreview(text) {
  previewEl.textContent = text || "No result yet.";
}

function setPaths(rawFile, optimizedFiles) {
  let lines = [];
  if (rawFile) lines.push(`Raw: ${rawFile}`);
  if (optimizedFiles) {
    Object.entries(optimizedFiles).forEach(([name, path]) => {
      lines.push(`${name}: ${path}`);
    });
  }
  pathsEl.textContent = lines.join("\n");
}

function getServerUrl() {
  return serverInput.value.trim() || DEFAULT_SERVER;
}

function sendMessage(payload) {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage(payload, (response) => resolve(response));
  });
}

async function loadConfig() {
  const data = await chrome.storage.local.get(["serverUrl", "modelSize", "prompts"]);
  serverInput.value = data.serverUrl || DEFAULT_SERVER;
  if (data.modelSize) cachedModelSize = data.modelSize;
  if (Array.isArray(data.prompts)) cachedPrompts = data.prompts;
}

async function saveConfig() {
  await chrome.storage.local.set({
    serverUrl: serverInput.value.trim(),
    modelSize: modelSelect.value,
    prompts: getSelectedPrompts(),
  });
}

function getSelectedPrompts() {
  return Array.from(promptList.querySelectorAll("input:checked")).map((i) => i.dataset.name);
}

function updatePreviewOptions() {
  previewType.innerHTML = "";
  const opts = ["raw"];
  const promptNames = lastResult?.preview
    ? Object.keys(lastResult.preview)
    : getSelectedPrompts();
  promptNames.forEach((p) => {
    if (p && !opts.includes(p)) opts.push(p);
  });
  opts.forEach((name) => {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    previewType.appendChild(opt);
  });
}

function renderPreviewFromResult() {
  if (!lastResult) {
    setPreview("No result yet.");
    return;
  }
  const key = previewType.value || "raw";
  const preview = lastResult.preview?.[key] || "";
  setPreview(preview || "Empty preview.");
}

async function startJob() {
  setStatus("Reading current tab...");
  runBtn.disabled = true;
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.url) {
    setStatus("No active tab URL found.", true);
    runBtn.disabled = false;
    return;
  }

  const resp = await sendMessage({
    type: "start_job",
    url: tab.url,
    serverUrl: getServerUrl(),
    modelSize: modelSelect.value || undefined,
    prompts: getSelectedPrompts(),
  });

  if (!resp || !resp.ok) {
    setStatus(resp?.error || "Failed to start job.", true);
    runBtn.disabled = false;
    return;
  }

  currentJobId = resp.data.job_id;
  setStatus(`Job queued: ${currentJobId}\nURL: ${tab.url}`);
  pollJob();
}

async function pollJob() {
  if (polling || !currentJobId) return;
  polling = true;

  while (currentJobId) {
    const resp = await sendMessage({
      type: "get_status",
      jobId: currentJobId,
      serverUrl: getServerUrl(),
    });

    if (!resp || !resp.ok) {
      setStatus(resp?.error || "Failed to poll job.", true);
      break;
    }

    const job = resp.data;
    const statusLines = [`Status: ${job.status}`];
    if (job.result?.title) statusLines.push(`Title: ${job.result.title}`);
    if (job.error) statusLines.push(`Error: ${job.error}`);
    setStatus(statusLines.join("\n"), job.status === "failed");

    if (job.status === "succeeded") {
      lastResult = job.result || null;
      updatePreviewOptions();
      renderPreviewFromResult();
      setPaths(job.result?.raw_file, job.result?.optimized_files);
      break;
    }
    if (job.status === "failed") {
      setPreview("No result.");
      break;
    }

    await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
  }

  polling = false;
  runBtn.disabled = false;
}

async function openOutput() {
  const resp = await sendMessage({
    type: "open_output",
    serverUrl: getServerUrl(),
  });
  if (!resp || !resp.ok) {
    setStatus(resp?.error || "Failed to open output folder.", true);
  }
}

serverInput.addEventListener("change", saveConfig);
modelSelect.addEventListener("change", saveConfig);
previewType.addEventListener("change", renderPreviewFromResult);
runBtn.addEventListener("click", startJob);
openBtn.addEventListener("click", openOutput);

async function initMetadata() {
  const server = getServerUrl();
  try {
    const modelsResp = await sendMessage({ type: "get_models", serverUrl: server });
    if (modelsResp?.ok) {
      modelSelect.innerHTML = "";
      modelsResp.data.models.forEach((m) => {
        const opt = document.createElement("option");
        opt.value = m;
        opt.textContent = m;
        modelSelect.appendChild(opt);
      });
      if (cachedModelSize) {
        modelSelect.value = cachedModelSize;
      } else if (modelsResp.data.default) {
        modelSelect.value = modelsResp.data.default;
      }
    }
  } catch (_) {}

  try {
    const promptsResp = await sendMessage({ type: "get_prompts", serverUrl: server });
    if (promptsResp?.ok) {
      promptList.innerHTML = "";
      promptsResp.data.prompts.forEach((name) => {
        const row = document.createElement("label");
        row.className = "prompt-item";
        const input = document.createElement("input");
        input.type = "checkbox";
        input.dataset.name = name;
        input.addEventListener("change", () => {
          updatePreviewOptions();
          saveConfig();
        });
        const text = document.createElement("span");
        text.textContent = name;
        row.appendChild(input);
        row.appendChild(text);
        promptList.appendChild(row);
      });
      cachedPrompts.forEach((name) => {
        const input = promptList.querySelector(`input[data-name="${name}"]`);
        if (input) input.checked = true;
      });
      updatePreviewOptions();
    }
  } catch (_) {}
}

loadConfig()
  .then(initMetadata)
  .catch(() => {
    serverInput.value = DEFAULT_SERVER;
    initMetadata();
  });
