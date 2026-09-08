const state = { reviews: [], selected: null };
const $ = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status})`);
  return payload;
}

function shortDate(value) {
  if (!value) return "—";
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

async function loadStatus() {
  const status = await api("/api/status");
  const job = status.job;
  const failedAfterPublish = status.latest_failure && (
    !status.current || new Date(status.latest_failure.failed_at) > new Date(status.current.published_at)
  );
  if (job.status === "idle" && failedAfterPublish) {
    $("pipelineStatus").textContent = "failed";
    $("pipelineMessage").textContent = status.current
      ? `Serving the previous dataset. Last refresh failed: ${status.latest_failure.error}`
      : `No dataset has been published. Last refresh failed: ${status.latest_failure.error}`;
  } else {
    $("pipelineStatus").textContent = job.status;
    $("pipelineMessage").textContent = job.message;
  }
  $("pendingCount").textContent = (status.reviews.pending || 0) + (status.reviews.changed || 0);
  $("approvedCount").textContent = status.reviews.approved || 0;
  $("publishedAt").textContent = shortDate(status.current?.published_at);
  $("publicationId").textContent = status.current?.publication_id || "No dataset yet";
  const byType = status.pending_by_type || {};
  $("queueBreakdown").textContent = `${byType.staff || 0} staff inclusion · ${byType.identity || 0} Minerva identity · ${byType.publication || 0} publication`;
  $("refreshButton").disabled = ["queued", "running"].includes(job.status);
}

function filteredReviews() {
  const query = $("searchInput").value.trim().toLowerCase();
  if (!query) return state.reviews;
  return state.reviews.filter((item) =>
    [item.label, item.university, item.discipline, item.reason, item.entity_type]
      .some((value) => String(value || "").toLowerCase().includes(query))
  );
}

function renderQueue() {
  const queue = $("queue");
  queue.replaceChildren();
  const reviews = filteredReviews();
  if (!reviews.length) {
    const message = document.createElement("p");
    message.className = "empty-list";
    message.textContent = "No review items match this filter.";
    queue.append(message);
    return;
  }
  for (const item of reviews) {
    const button = document.createElement("button");
    button.className = "queue-item" + (state.selected?.review_key === item.review_key ? " active" : "");
    button.type = "button";
    button.setAttribute("role", "listitem");
    const row = document.createElement("span");
    row.className = "row";
    const kind = document.createElement("span");
    const typeLabel = item.entity_type === "identity" ? "Minerva identity" : item.entity_type;
    kind.textContent = `${typeLabel} · ${item.status}`;
    const place = document.createElement("span");
    place.textContent = item.university || "Unknown source";
    row.append(kind, place);
    const title = document.createElement("strong");
    title.textContent = item.label;
    const reason = document.createElement("small");
    reason.textContent = item.reason;
    button.append(row, title, reason);
    button.addEventListener("click", () => selectReview(item));
    queue.append(button);
  }
}

function selectReview(item) {
  state.selected = item;
  $("emptyState").hidden = true;
  $("detail").hidden = false;
  $("detailMeta").textContent = `${item.entity_type} · ${item.university || "Unknown"} · ${item.discipline || "No discipline"}`;
  $("detailTitle").textContent = item.label;
  $("detailStatus").textContent = item.status;
  $("detailReason").textContent = item.reason;
  $("payloadEditor").value = JSON.stringify(item.edited || item.candidate, null, 2);
  $("noteInput").value = item.note || "";
  const value = item.edited || item.candidate;
  const identity = item.entity_type === "identity";
  $("identityFields").hidden = !identity;
  $("payloadBlock").hidden = identity;
  $("rejectButton").hidden = identity;
  $("approveButton").textContent = identity ? "Save verified identity" : "Approve inclusion";
  $("deferButton").textContent = identity ? "Leave unresolved" : "Defer";
  $("repositoryAuthorName").value = value.repository_author_name || "";
  $("internalId").value = value.internal_id || "";
  $("orcid").value = value.orcid || "";
  $("evidenceUrl").value = value.evidence_url || "";
  const sourceUrl = value.profile_url || value.link || value.article_url;
  $("sourceLink").hidden = !sourceUrl;
  if (sourceUrl) $("sourceLink").href = sourceUrl;
  const steps = identity
    ? [
        "Open the official Find an Expert profile and verify this is the same person.",
        "Find an official ORCID, or open one known Minerva publication and inspect its exact author identity.",
        "Enter the exact Minerva author name plus the internal ID or ORCID, and paste the evidence URL.",
        "Save, then run Refresh. Publications are accepted only when Minerva returns the same exact ID.",
      ]
    : [
        "Open the official profile and check the appointment title and category.",
        "Approve only if the role fits the client-agreed research staff scope.",
        "Reject visitors, contractors or teaching-only roles if the client excluded them; defer if scope is unclear.",
      ];
  $("guideTitle").textContent = identity ? "How to verify a Minerva identity" : "How to decide staff inclusion";
  $("guideSteps").replaceChildren(...steps.map((step) => {
    const li = document.createElement("li");
    li.textContent = step;
    return li;
  }));
  $("actionMessage").textContent = identity
    ? "Saving does not immediately invent or add papers. The next refresh searches Minerva and accepts only exact ID matches."
    : "Approve includes this person and their already-validated publications; reject excludes both.";
  renderQueue();
}

async function loadReviews() {
  const status = $("statusFilter").value;
  const type = $("typeFilter").value;
  const payload = await api(`/api/reviews?status=${encodeURIComponent(status)}&type=${encodeURIComponent(type)}`);
  state.reviews = payload.reviews;
  if (state.selected) state.selected = state.reviews.find((item) => item.review_key === state.selected.review_key) || null;
  renderQueue();
  if (!state.selected) {
    $("emptyState").hidden = false;
    $("detail").hidden = true;
  }
}

async function decide(status) {
  if (!state.selected) return;
  let edited = null;
  if (status === "approved") {
    if (state.selected.entity_type === "identity") {
      edited = {
        ...state.selected.candidate,
        repository_author_name: $("repositoryAuthorName").value.trim(),
        internal_id: $("internalId").value.trim(),
        orcid: $("orcid").value.trim(),
        evidence_url: $("evidenceUrl").value.trim(),
      };
    } else {
      try {
        edited = JSON.parse($("payloadEditor").value);
      } catch (error) {
        $("actionMessage").textContent = `Invalid JSON: ${error.message}`;
        return;
      }
    }
  }
  $("actionMessage").textContent = "Saving decision…";
  try {
    const result = await api(`/api/reviews/${encodeURIComponent(state.selected.review_key)}/decision`, {
      method: "POST",
      body: JSON.stringify({ status, edited, note: $("noteInput").value }),
    });
    if (result.requires_refresh) alert(result.message);
    state.selected = null;
    await Promise.all([loadStatus(), loadReviews()]);
  } catch (error) {
    $("actionMessage").textContent = error.message;
  }
}

async function startRefresh() {
  if (!confirm("Refresh both universities from their official sources? The current dataset remains available while this runs.")) return;
  try {
    await api("/api/refresh", { method: "POST", body: JSON.stringify({ force: true }) });
    await loadStatus();
  } catch (error) {
    alert(error.message);
  }
}

$("refreshButton").addEventListener("click", startRefresh);
$("reloadButton").addEventListener("click", () => Promise.all([loadStatus(), loadReviews()]));
$("statusFilter").addEventListener("change", loadReviews);
$("typeFilter").addEventListener("change", loadReviews);
$("searchInput").addEventListener("input", renderQueue);
document.querySelectorAll("[data-decision]").forEach((button) =>
  button.addEventListener("click", () => decide(button.dataset.decision))
);

Promise.all([loadStatus(), loadReviews()]).catch((error) => {
  $("pipelineStatus").textContent = "Unavailable";
  $("pipelineMessage").textContent = error.message;
});
setInterval(() => loadStatus().catch(() => {}), 3000);
