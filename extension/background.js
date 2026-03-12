const DEFAULT_SERVER = "http://127.0.0.1:8732";

async function postJson(url, body) {
  const resp = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!resp.ok) {
    const msg = await resp.text();
    throw new Error(msg || `HTTP ${resp.status}`);
  }
  return await resp.json();
}

async function getJson(url) {
  const resp = await fetch(url);
  if (!resp.ok) {
    const msg = await resp.text();
    throw new Error(msg || `HTTP ${resp.status}`);
  }
  return await resp.json();
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  (async () => {
    const server = msg.serverUrl || DEFAULT_SERVER;
    if (msg.type === "start_job") {
      const data = await postJson(`${server}/jobs`, {
        target: msg.target || msg.url,
        url: msg.url,
        source_kind: msg.sourceKind || "url",
        model_size: msg.modelSize,
        prompts: msg.prompts || [],
      });
      sendResponse({ ok: true, data });
      return;
    }
    if (msg.type === "get_status") {
      const data = await getJson(`${server}/jobs/${msg.jobId}`);
      sendResponse({ ok: true, data });
      return;
    }
    if (msg.type === "get_prompts") {
      const data = await getJson(`${server}/prompts`);
      sendResponse({ ok: true, data });
      return;
    }
    if (msg.type === "health") {
      const data = await getJson(`${server}/health`);
      sendResponse({ ok: true, data });
      return;
    }
    if (msg.type === "get_models") {
      const data = await getJson(`${server}/models`);
      sendResponse({ ok: true, data });
      return;
    }
    if (msg.type === "get_recent_results") {
      const data = await getJson(`${server}/results/recent?limit=${msg.limit || 6}`);
      sendResponse({ ok: true, data });
      return;
    }
    if (msg.type === "open_output") {
      const data = await getJson(`${server}/open-output`);
      sendResponse({ ok: true, data });
      return;
    }
    sendResponse({ ok: false, error: "unknown message" });
  })().catch((err) => {
    sendResponse({ ok: false, error: err.message || String(err) });
  });
  return true;
});
