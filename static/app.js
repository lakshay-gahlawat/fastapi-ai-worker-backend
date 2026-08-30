const POLL_MS = 2000;

const form = document.getElementById("job-form");
const submitBtn = document.getElementById("submit-btn");
const statusPanel = document.getElementById("status-panel");
const statusChip = document.getElementById("status-chip");
const jobIdEl = document.getElementById("job-id");
const statusMessage = document.getElementById("status-message");
const progressBar = document.getElementById("progress-bar");
const resultBody = document.getElementById("result-body");

let pollTimer = null;
let pollStartedAt = 0;

function setStatus(status, jobId, message) {
  statusChip.textContent = status;
  statusPanel.className = `status-${status}`;
  jobIdEl.textContent = jobId || "—";
  statusMessage.textContent = message;
}

function setProgress(status) {
  const widths = {
    idle: "0%",
    queued: "28%",
    in_progress: "62%",
    completed: "100%",
    failed: "100%",
  };
  progressBar.style.width = widths[status] || "12%";
}

function renderResult(payload) {
  if (!payload) {
    resultBody.className = "empty-result";
    resultBody.textContent = "Waiting for a completed job.";
    return;
  }

  if (payload.status === "failed") {
    resultBody.className = "result-failed";
    resultBody.innerHTML = `<p class="error">${escapeHtml(payload.error || "Job failed.")}</p>
      <pre>${escapeHtml(JSON.stringify(payload, null, 2))}</pre>`;
    return;
  }

  const result = payload.result || {};
  const items = (result.action_items || [])
    .map(
      (item) => `<li>
        <strong>${escapeHtml(item.priority || "medium")}</strong>
        ${escapeHtml(item.description || "")}
        ${item.owner ? `<em>${escapeHtml(item.owner)}</em>` : ""}
      </li>`
    )
    .join("");

  const topics = (result.key_topics || [])
    .map((topic) => `<span class="topic">${escapeHtml(topic)}</span>`)
    .join("");

  resultBody.className = "result-ready";
  resultBody.innerHTML = `
    <p class="summary">${escapeHtml(result.summary || "")}</p>
    <div class="topics">${topics}</div>
    <h3>Action items</h3>
    <ul class="actions-list">${items || "<li>None extracted</li>"}</ul>
    <details>
      <summary>Raw JSON</summary>
      <pre>${escapeHtml(JSON.stringify(payload, null, 2))}</pre>
    </details>
    <p class="meta">
      Duration: ${payload.execution_duration_seconds ?? "—"}s ·
      ${payload.timestamp || ""} ·
      provider ${payload.provider || "—"}
    </p>
  `;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

async function pollJob(jobId) {
  try {
    const response = await fetch(`/api/v1/jobs/${encodeURIComponent(jobId)}`);
    if (!response.ok) {
      const detail = await response.json().catch(() => ({}));
      throw new Error(detail.detail || `Status ${response.status}`);
    }
    const payload = await response.json();
    const elapsed = ((Date.now() - pollStartedAt) / 1000).toFixed(1);
    setStatus(
      payload.status,
      payload.job_id,
      payload.status === "completed"
        ? `Completed in ${payload.execution_duration_seconds ?? elapsed}s.`
        : payload.status === "failed"
          ? payload.error || "Job failed."
          : `Polling every 2s · ${elapsed}s elapsed.`
    );
    setProgress(payload.status);

    if (payload.status === "completed" || payload.status === "failed") {
      renderResult(payload);
      submitBtn.disabled = false;
      return;
    }
  } catch (error) {
    setStatus("failed", jobId, error.message);
    setProgress("failed");
    submitBtn.disabled = false;
    return;
  }

  pollTimer = window.setTimeout(() => pollJob(jobId), POLL_MS);
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (pollTimer) {
    window.clearTimeout(pollTimer);
  }

  const text = document.getElementById("text").value.trim();
  const title = document.getElementById("title").value.trim();
  submitBtn.disabled = true;
  renderResult(null);
  setStatus("queued", "…", "Submitting job…");
  setProgress("queued");

  try {
    const response = await fetch("/api/v1/summarize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, title: title || null }),
    });
    const payload = await response.json();
    if (!response.ok) {
      const detail = Array.isArray(payload.detail)
        ? payload.detail.map((item) => item.msg || item).join("; ")
        : payload.detail || "Request failed";
      throw new Error(detail);
    }
    pollStartedAt = Date.now();
    setStatus("queued", payload.job_id, "Job queued. Waiting for a worker…");
    pollJob(payload.job_id);
  } catch (error) {
    setStatus("failed", null, error.message);
    setProgress("failed");
    submitBtn.disabled = false;
  }
});
