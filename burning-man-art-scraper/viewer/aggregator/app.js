const DEFAULT_DATA_URL = "./data/aggregator_view.json";

function viewUrlForYear(year) {
  return year ? `/api/view?year=${year}` : DEFAULT_DATA_URL;
}

function viewUrlForRun(runId) {
  return runId ? `/api/view?run_id=${encodeURIComponent(runId)}` : DEFAULT_DATA_URL;
}

const FILTERS = [
  { id: "all", label: "All projects" },
  { id: "attention", label: "Needs review" },
  { id: "ready", label: "Export-ready" },
  { id: "low_confidence", label: "Low confidence" },
  { id: "missing_hero", label: "Missing images" },
  { id: "missing_attribution", label: "Missing attribution" },
  { id: "duplicates", label: "Duplicates" },
  { id: "incomplete", label: "Incomplete fields" },
  { id: "missing_proof", label: "No proof link" },
  { id: "has_image", label: "Has hero photo" },
];

let bundle = null;
let activeFilter = "all";
let selectedUid = null;
let hubStatus = null;
let ingestFile = null;
let detectedYear = null;
let currentRunId = null;
let alreadyProcessed = false;
let adminImportUrl = "";
let wizardStep = 1;
let selectedSource = "artist_website";

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function api(path, options = {}) {
  return fetch(path, options).then(async (res) => {
    const payload = await res.json().catch(() => ({}));
    if (!res.ok && !(res.status === 409 && payload.needs_confirm)) {
      throw new Error(payload.error || `Request failed (${res.status})`);
    }
    return payload;
  });
}

function showStep(step) {
  wizardStep = step;
  document.querySelectorAll(".wizard-tab").forEach((tab) => {
    tab.classList.toggle("active", Number(tab.dataset.step) === step);
  });
  document.querySelectorAll("[data-step-panel]").forEach((panel) => {
    panel.hidden = Number(panel.dataset.stepPanel) !== step;
  });
  const revealReview = step >= 3 || Boolean(bundle);
  document.querySelector(".controls")?.classList.toggle("is-dimmed", !revealReview);
  document.querySelector(".workspace")?.classList.toggle("is-dimmed", !revealReview);
}

function currentSourceId() {
  return document.querySelector('input[name="source-id"]:checked')?.value || "artist_website";
}

function syncSourceForm() {
  selectedSource = currentSourceId();
  const isArtist = selectedSource === "artist_website";
  const isBm = selectedSource === "burning_man_csv";
  document.getElementById("source-artist").hidden = !isArtist;
  document.getElementById("source-bm").hidden = !isBm;
  document.getElementById("identity-option").hidden = !isBm;
  document.getElementById("overwrite-warn").hidden = !isBm || !alreadyProcessed;
  updateContinueEnabled();
}

function clearInactiveSourceStatus() {
  setInspectMessage("");
  const summary = document.getElementById("prepare-summary");
  if (selectedSource === "artist_website") {
    if (summary && !summary.textContent.includes("Built ")) {
      summary.textContent = artistSourceReady()
        ? "Ready to crawl artist website."
        : "Choose a source in step 1 first.";
    }
  } else if (detectedYear) {
    summary.textContent = `Ready to prepare Burning Man ${detectedYear}.`;
  } else {
    summary.textContent = ingestFile
      ? "Detect year from the selected CSV to continue."
      : "Choose a source in step 1 first.";
  }
}

function artistSourceReady() {
  const artist = document.getElementById("artist-name").value.trim();
  const url = document.getElementById("website-url").value.trim();
  if (!artist || !url) return false;
  try {
    const parsed = new URL(url);
    return parsed.protocol === "http:" || parsed.protocol === "https:";
  } catch {
    return false;
  }
}

function updateContinueEnabled() {
  const btn = document.getElementById("goto-prepare");
  const prep = document.getElementById("run-prepare");
  if (selectedSource === "artist_website") {
    const ok = artistSourceReady();
    btn.disabled = !ok;
    prep.disabled = !ok;
    if (ok) {
      const summary = document.getElementById("prepare-summary");
      if (summary && !summary.textContent.includes("Built ")) {
        summary.textContent = "Ready to crawl artist website.";
      }
    }
  } else {
    btn.disabled = !ingestFile || !detectedYear;
    prep.disabled = !ingestFile || !detectedYear;
  }
}

function setInspectMessage(message = "", { ok = false } = {}) {
  const panel = document.getElementById("inspect-panel");
  const el = document.getElementById("csv-inspect-message");
  el.textContent = message || "";
  if (panel) {
    panel.hidden = !message;
    panel.classList.toggle("is-ok", Boolean(ok && message));
    panel.classList.toggle("is-error", Boolean(!ok && message));
  }
}

function setDetectedYear(year, message = "", already = false) {
  detectedYear = year || null;
  alreadyProcessed = Boolean(already);
  document.getElementById("detected-year-pill").textContent = detectedYear
    ? `Year ${detectedYear}`
    : "Year not detected";
  setInspectMessage(message || "", { ok: Boolean(detectedYear) });
  document.getElementById("overwrite-warn").hidden = !alreadyProcessed;
  if (!alreadyProcessed) {
    document.getElementById("confirm-overwrite").checked = false;
  }
  if (detectedYear && selectedSource === "burning_man_csv") {
    document.getElementById("prepare-summary").textContent =
      `Ready to prepare Burning Man ${detectedYear}` + (message ? ` — ${message}` : ".");
  }
  updateContinueEnabled();
}

function renderProcessSteps(steps) {
  const list = document.getElementById("prepare-steps");
  if (!steps?.length) {
    list.innerHTML = "";
    return;
  }
  list.innerHTML = steps
    .map((step) => {
      const mark =
        step.status === "done"
          ? "✓"
          : step.status === "running"
            ? "…"
            : step.status === "blocked" || step.status === "error"
              ? "!"
              : "•";
      return `<li class="${esc(step.status || "")}"><span class="step-mark">${mark}</span><span>${esc(step.label)}</span></li>`;
    })
    .join("");
}

function setBundle(data) {
  bundle = data;
  selectedUid = null;
  currentRunId = data.meta?.run_id || currentRunId;
  if (data.meta?.about) {
    document.getElementById("about").textContent = data.meta.about;
  }
  const label = data.meta?.run_label || data.meta?.year || "—";
  document.getElementById("year-pill").textContent = data.meta?.run_id
    ? `Run ${label}`
    : `Year ${label}`;
  renderChecklist(data.upload_checklist || {});
  renderFilters();
  renderGallery();
  document.getElementById("detail").hidden = true;
  document.querySelector(".workspace").classList.remove("detail-open");
  document.querySelector(".controls")?.classList.remove("is-dimmed");
  document.querySelector(".workspace")?.classList.remove("is-dimmed");
  document.getElementById("download-upload").disabled = false;
  document.getElementById("download-core").disabled = false;
  document.getElementById("run-deploy").disabled = !data.meta?.year;
}

function renderChecklist(c) {
  const items = [
    { label: "Projects", value: c.project_count ?? 0 },
    { label: "Export-ready", value: c.upload_ready_count ?? 0, tone: "ok" },
    { label: "Needs review", value: c.needs_attention_count ?? 0, tone: "warn" },
    { label: "Hero photos", value: c.with_hero_image ?? 0 },
    { label: "Missing proof", value: c.missing_proof_count ?? 0, tone: c.missing_proof_count ? "bad" : "" },
  ];
  document.getElementById("checklist").innerHTML = items
    .map(
      (item) => `
      <div class="stat ${item.tone || ""}">
        <span class="label">${esc(item.label)}</span>
        <span class="value">${esc(item.value)}</span>
      </div>`
    )
    .join("");
}

function renderFilters() {
  document.getElementById("filters").innerHTML = FILTERS.map(
    (filter) => `
    <button type="button" class="filter-btn ${filter.id === activeFilter ? "active" : ""}" data-filter="${filter.id}">
      ${esc(filter.label)}
    </button>`
  ).join("");
}

function filteredProjects() {
  if (!bundle) return [];
  const q = document.getElementById("search").value.trim().toLowerCase();
  let rows = [...(bundle.projects || [])];

  rows = rows.filter((p) => {
    if (activeFilter === "attention") return p.needs_attention;
    if (activeFilter === "ready") return p.upload_ready;
    if (activeFilter === "low_confidence") return p.review_flags?.includes("low_confidence") || p.review_flags?.includes("sparse_evidence");
    if (activeFilter === "missing_hero") return !p.hero?.url || p.review_flags?.includes("hero_missing");
    if (activeFilter === "missing_attribution") return p.review_flags?.includes("missing_attribution");
    if (activeFilter === "duplicates") return p.review_flags?.includes("duplicate_candidate");
    if (activeFilter === "incomplete") return p.review_flags?.includes("incomplete_fields") || !p.proof_url || !p.title;
    if (activeFilter === "missing_proof") return !p.proof_url;
    if (activeFilter === "has_image") return Boolean(p.hero?.url);
    return true;
  });

  if (q) {
    rows = rows.filter((p) =>
      [
        p.title,
        p.people?.contributor_display_name,
        p.people?.source_artist_credit,
        p.place?.playa_address,
        p.place?.project_location,
        p.place?.display,
        p.summary,
      ]
        .join(" ")
        .toLowerCase()
        .includes(q)
    );
  }

  const sort = document.getElementById("sort").value;
  rows.sort((a, b) => {
    if (sort === "contributor") {
      return (a.people?.contributor_display_name || "").localeCompare(b.people?.contributor_display_name || "");
    }
    if (sort === "title") {
      return (a.title || "").localeCompare(b.title || "");
    }
    return (b.review_priority || 0) - (a.review_priority || 0) || (a.title || "").localeCompare(b.title || "");
  });
  return rows;
}

function attentionChips(project) {
  const chips = [];
  if (project.upload_ready) chips.push({ text: "Export-ready", tone: "ok" });
  for (const label of project.flag_labels || []) {
    if (label === "Honorarium unknown") continue;
    const tone = label.toLowerCase().includes("missing") ? "bad" : "";
    chips.push({ text: label, tone });
  }
  return chips.slice(0, 3);
}

function currentFilterMeta() {
  const filter = FILTERS.find((item) => item.id === activeFilter) || FILTERS[0];
  const search = document.getElementById("search").value.trim();
  const rows = filteredProjects();
  let label = filter.label;
  if (search) {
    label = activeFilter === "all" ? `Search “${search}”` : `${filter.label} + search`;
  }
  return {
    filter_id: filter.id,
    filter_label: label,
    unfiltered: filter.id === "all" && !search,
    rows,
  };
}

function updateExportButtonLabels() {
  const btn = document.getElementById("download-upload");
  const coreBtn = document.getElementById("download-core");
  if (!bundle) {
    btn.textContent = "Download Artelier CSV";
    btn.disabled = true;
    if (coreBtn) {
      coreBtn.textContent = "Download Artelier core columns only";
      coreBtn.disabled = true;
    }
    return;
  }
  const meta = currentFilterMeta();
  const count = meta.rows.length;
  btn.disabled = count === 0;
  btn.textContent = meta.unfiltered
    ? `Download Artelier CSV (${count})`
    : `Download Artelier CSV — ${meta.filter_label} (${count})`;
  if (coreBtn) {
    coreBtn.disabled = count === 0;
    coreBtn.textContent = meta.unfiltered
      ? "Download Artelier core columns only"
      : `Download core CSV — ${meta.filter_label} (${count})`;
  }
}

function exportFilteredCsv(kind) {
  const year = Number(document.getElementById("year-switch").value || detectedYear || bundle?.meta?.year || 0);
  const runId = currentRunId || bundle?.meta?.run_id || "";
  const status = document.getElementById("export-status") || document.getElementById("deploy-status");
  if ((!year && !runId) || !bundle) {
    if (status) status.textContent = "Open a prepared run first.";
    return;
  }
  const meta = currentFilterMeta();
  if (!meta.rows.length) {
    if (status) status.textContent = "No projects match the current filter/search.";
    return;
  }
  const keys = [];
  for (const project of meta.rows) {
    if (project.uid) keys.push(project.uid);
    if (project.slug) keys.push(project.slug);
    if (project.title) keys.push(project.title);
  }
  if (status) {
    status.textContent = `Exporting ${meta.rows.length} project(s) — ${meta.filter_label}…`;
  }
  fetch("/api/export-csv", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      year: year || 0,
      run_id: runId,
      kind: runId ? "core" : kind,
      keys,
      filter_id: meta.filter_id,
      filter_label: meta.filter_label,
      unfiltered: meta.unfiltered,
    }),
  })
    .then(async (res) => {
      if (!res.ok) {
        const payload = await res.json().catch(() => ({}));
        throw new Error(payload.error || `Export failed (${res.status})`);
      }
      const disposition = res.headers.get("Content-Disposition") || "";
      const match = /filename="([^"]+)"/i.exec(disposition);
      const filename =
        match?.[1] ||
        `artelier_${kind === "core" ? "core_only" : "upload"}.csv`;
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      if (status) {
        status.textContent = `Downloaded ${meta.rows.length} project(s) — ${meta.filter_label}.`;
      }
      showStep(4);
    })
    .catch((err) => {
      if (status) status.textContent = err.message || "Export failed";
    });
}

function renderGallery() {
  const gallery = document.getElementById("gallery");
  const rows = filteredProjects();
  document.getElementById("result-count").textContent = bundle ? `${rows.length} shown` : "";
  updateExportButtonLabels();
  if (!bundle) {
    gallery.innerHTML =
      '<div class="loading">No run loaded yet. Choose Artist website or Burning Man CSV, then prepare.</div>';
    return;
  }
  if (!rows.length) {
    gallery.innerHTML = '<div class="empty">No projects match this filter.</div>';
    return;
  }
  gallery.innerHTML = rows
    .map((p) => {
      const key = projectKey(p);
      const chips = attentionChips(p)
        .map((chip) => `<span class="chip ${chip.tone}">${esc(chip.text)}</span>`)
        .join("");
      const media = p.hero?.url
        ? `<img src="${esc(p.hero.url)}" alt="" loading="lazy" onerror="this.replaceWith(Object.assign(document.createElement('div'),{className:'placeholder',textContent:'Image unavailable'}))" />`
        : '<div class="placeholder">No hero image</div>';
      const place = p.place?.display || p.place?.project_location || p.place?.playa_address || "No location";
      return `
        <button type="button" class="card ${selectedUid === key ? "active" : ""}" data-key="${esc(key)}">
          ${media}
          <div class="card-body">
            <h2>${esc(p.title || "Untitled")}</h2>
            <div class="meta">${esc(p.people?.name || p.people?.contributor_display_name || "No contributor yet")}</div>
            <div class="meta">${esc(place)}</div>
            <div class="chips">${chips}</div>
          </div>
        </button>`;
    })
    .join("");
}

function projectKey(p) {
  return p.uid || p.slug || p.title;
}

function findProject(key) {
  return (bundle?.projects || []).find((p) => projectKey(p) === key);
}

function renderDetail(project) {
  const detail = document.getElementById("detail");
  const body = document.getElementById("detail-body");
  detail.hidden = false;
  document.querySelector(".workspace").classList.add("detail-open");
  const people = project.people || {};
  const place = project.place || {};
  const hero = project.hero || {};
  const evidence = project.evidence || {};
  const flagChips = (project.flag_labels || [])
    .map((label) => `<span class="chip">${esc(label)}</span>`)
    .join("");
  const blocked = project.export_blocked_reason
    ? `<p class="chip bad">Blocked from export: ${esc(project.export_blocked_reason)}</p>`
    : project.upload_ready
      ? `<p class="chip ok">Export-ready</p>`
      : "";

  const canEdit = Boolean(currentRunId || bundle?.meta?.run_id);
  body.innerHTML = `
    <div class="detail-hero">
      ${
        hero.url
          ? `<img src="${esc(hero.url)}" alt="" onerror="this.parentElement.innerHTML='<div class=placeholder>Image unavailable</div>'" />`
          : '<div class="placeholder" style="height:160px;display:grid;place-items:center;color:#6b5e52">No hero image</div>'
      }
    </div>
    <h2>${esc(project.title || "Untitled")}</h2>
    <p class="meta">${esc(project.year)}${project.upload_ready ? " · Export-ready" : " · Needs review"}</p>
    <div class="chips" style="margin-top:0.5rem">${flagChips || '<span class="chip ok">No blocking flags</span>'}</div>
    ${blocked}

    ${
      canEdit
        ? `<form class="edit-grid" id="correction-form">
      <label>Project title<input name="project_title" value="${esc(project.title || "")}" /></label>
      <label>Artist / contributor<input name="contributor_name" value="${esc(people.name || people.contributor_display_name || "")}" /></label>
      <label>Year<input name="project_year" value="${esc(project.year || "")}" /></label>
      <label>Location<input name="project_location" value="${esc(place.project_location || place.display || place.playa_address || "")}" /></label>
      <label>Project type / style<input name="project_type" value="${esc(project.project_type || place.installation_type || "")}" /></label>
      <label>Collection<input name="collection" value="${esc(project.collection || place.theme_camp || "")}" /></label>
      <label>Hero image URL<input name="hero_image_url" value="${esc(hero.url || "")}" /></label>
      <label>Approval state
        <select name="approval_status">
          <option value="draft" ${project.approval_status === "draft" ? "selected" : ""}>draft</option>
          <option value="approved" ${project.approval_status === "approved" ? "selected" : ""}>approved</option>
          <option value="rejected" ${project.approval_status === "rejected" ? "selected" : ""}>rejected</option>
        </select>
      </label>
      <button type="submit" class="primary-btn">Save corrections</button>
      <p class="ops-status" id="correction-status"></p>
    </form>`
        : `<h3>Contributor</h3>
    <p><strong>Name:</strong> ${esc(people.name || people.contributor_display_name || "—")}</p>
    <p><strong>Alt / Burner Name:</strong> ${esc(people.alt_burner_name || people.playa_name || "—")}</p>
    <p><strong>Summary:</strong> ${esc(project.summary || "—")}</p>
    <h3>Place</h3>
    <p><strong>Location:</strong> ${esc(place.display || place.project_location || place.playa_address || "—")}</p>
    <p><strong>Type:</strong> ${esc(place.installation_type || "—")}</p>`
    }

    <h3>Proof &amp; evidence</h3>
    <p>${
      project.proof_url
        ? `<a href="${esc(project.proof_url)}" target="_blank" rel="noopener">Open source page →</a>`
        : "<span class='chip bad'>Missing proof link</span>"
    }</p>
    <div class="evidence-box">${esc(evidence.proof_description || project.summary || "No excerpt captured.")}</div>
  `;

  const form = document.getElementById("correction-form");
  if (form) {
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const data = new FormData(form);
      const corrections = Object.fromEntries(data.entries());
      const status = document.getElementById("correction-status");
      status.textContent = "Saving…";
      api("/api/records/update", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          run_id: currentRunId || bundle?.meta?.run_id,
          record_id: project.slug || project.uid || project.title,
          corrections,
        }),
      })
        .then((result) => {
          status.textContent = "Saved.";
          return fetch(result.viewer_reload || viewUrlForRun(currentRunId)).then((res) => res.json()).then(setBundle);
        })
        .then(() => {
          const refreshed = findProject(projectKey(project)) || findProject(corrections.project_title);
          if (refreshed) renderDetail(refreshed);
        })
        .catch((err) => {
          status.textContent = err.message || "Save failed";
        });
    });
  }
}

function populateYears(years, runs) {
  const yearSwitch = document.getElementById("year-switch");
  const current = yearSwitch.value;
  yearSwitch.innerHTML = "";
  for (const run of runs || []) {
    const opt = document.createElement("option");
    opt.value = `run:${run.run_id}`;
    if (run.label) {
      opt.textContent =
        run.source_id === "artist_website"
          ? `${run.label} · artist website`
          : run.label;
    } else {
      opt.textContent = run.run_id;
    }
    yearSwitch.appendChild(opt);
  }
  for (const year of years || []) {
    const opt = document.createElement("option");
    opt.value = String(year);
    opt.textContent = `BM ${year}`;
    yearSwitch.appendChild(opt);
  }
  if (current && [...yearSwitch.options].some((opt) => opt.value === current)) {
    yearSwitch.value = current;
  } else if (yearSwitch.options.length) {
    yearSwitch.selectedIndex = 0;
  }
}

function updateDisk(disk) {
  if (!disk) return;
  document.getElementById("disk-pill").textContent = `Data ${disk.total_mb ?? "—"} MB`;
}

function inspectArtistSource() {
  // Optional preflight only — never blocks Continue / Start.
  updateContinueEnabled();
  const artist = document.getElementById("artist-name").value.trim();
  const url = document.getElementById("website-url").value.trim();
  if (!artist || !url) {
    setInspectMessage("");
    return Promise.resolve();
  }
  setInspectMessage("Inspecting website…", { ok: false });
  const form = new FormData();
  form.append("source_id", "artist_website");
  form.append("artist_name", artist);
  form.append("website_url", url);
  form.append("portfolio_url", document.getElementById("portfolio-url").value.trim());
  form.append("max_pages", document.getElementById("max-pages").value || "150");
  form.append("render_javascript", document.getElementById("render-javascript").checked ? "1" : "0");
  return api("/api/inspect", { method: "POST", body: form })
    .then((result) => {
      setInspectMessage(result.message || "Artist website detected.", { ok: true });
      document.getElementById("prepare-summary").textContent =
        result.message || "Ready to crawl artist website.";
    })
    .catch((err) => {
      setInspectMessage(
        `${err.message} — you can still continue; prepare will validate the site.`,
        { ok: false }
      );
    });
}

function inspectSelectedCsv() {
  if (!ingestFile) {
    setDetectedYear(null);
    return Promise.resolve();
  }
  setInspectMessage("Detecting year…", { ok: false });
  const form = new FormData();
  form.append("source_id", "burning_man_csv");
  form.append("file", ingestFile, ingestFile.name);
  return api("/api/inspect", { method: "POST", body: form })
    .then((result) => {
      setDetectedYear(result.year, result.message || "", result.already_processed);
    })
    .catch((err) => {
      setDetectedYear(null, err.message, false);
    });
}

function runPrepare() {
  const status = document.getElementById("prepare-status");
  const btn = document.getElementById("run-prepare");
  selectedSource = currentSourceId();

  if (selectedSource === "artist_website") {
    const artist = document.getElementById("artist-name").value.trim();
    const url = document.getElementById("website-url").value.trim();
    if (!artist || !url) {
      status.textContent = "Enter artist name and website URL in step 1.";
      return;
    }
    btn.disabled = true;
    status.textContent = "Crawling artist website… this can take several minutes.";
    renderProcessSteps([
      { id: "inspect", label: "Inspect source", status: "done" },
      { id: "crawl", label: "Crawl artist domain", status: "running" },
      { id: "extract", label: "Extract projects", status: "pending" },
      { id: "normalize", label: "Map to Artelier schema", status: "pending" },
    ]);
    const form = new FormData();
    form.append("source_id", "artist_website");
    form.append("artist_name", artist);
    form.append("website_url", url);
    form.append("portfolio_url", document.getElementById("portfolio-url").value.trim());
    form.append("max_pages", document.getElementById("max-pages").value || "150");
    form.append("render_javascript", document.getElementById("render-javascript").checked ? "1" : "0");
    api("/api/prepare-run", { method: "POST", body: form })
      .then((result) => {
        if (!result.ok) throw new Error(result.error || "Prepare failed");
        currentRunId = result.run_id;
        renderProcessSteps(result.steps || []);
        status.textContent = `Built ${result.project_count} projects for review.`;
        updateDisk(result.disk);
        return fetch(result.viewer_reload || viewUrlForRun(result.run_id))
          .then((res) => res.json())
          .then(setBundle)
          .then(() => refreshHubStatus())
          .then(() => showStep(3));
      })
      .catch((err) => {
        renderProcessSteps([{ id: "error", label: err.message || "Prepare failed", status: "error" }]);
        status.textContent = err.message || "Prepare failed";
      })
      .finally(() => {
        updateContinueEnabled();
      });
    return;
  }

  if (!ingestFile || !detectedYear) {
    status.textContent = "Choose a PlayaEvents ART CSV in step 1 first.";
    return;
  }
  if (alreadyProcessed && !document.getElementById("confirm-overwrite").checked) {
    status.textContent = "Confirm rebuild before running again.";
    return;
  }

  btn.disabled = true;
  status.textContent = "Preparing Burning Man year… this can take several minutes.";
  renderProcessSteps([
    { id: "www", label: "Using uploaded PlayaEvents file", status: "running" },
    { id: "verify", label: "Matching History Archive", status: "pending" },
    { id: "ingest", label: "Writing Artelier CSV + gallery preview", status: "pending" },
  ]);

  const form = new FormData();
  form.append("file", ingestFile, ingestFile.name);
  form.append("confirm_overwrite", document.getElementById("confirm-overwrite").checked ? "1" : "0");
  form.append("run_identity_online", document.getElementById("run-identity-online").checked ? "1" : "0");

  api("/api/prepare", { method: "POST", body: form })
    .then((result) => {
      if (result.needs_confirm) {
        document.getElementById("overwrite-warn").hidden = false;
        alreadyProcessed = true;
        renderProcessSteps(result.steps || []);
        status.textContent = result.error;
        btn.disabled = false;
        return null;
      }
      if (!result.ok) throw new Error(result.error || "Prepare failed");
      if (result.run_id) currentRunId = result.run_id;
      renderProcessSteps(result.steps || []);
      status.textContent = `Built ${result.project_count} projects.`;
      updateDisk(result.disk);
      const reload = result.viewer_reload_run || result.viewer_reload || viewUrlForYear(result.year);
      return fetch(reload)
        .then((res) => res.json())
        .then(setBundle)
        .then(() => refreshHubStatus())
        .then(() => showStep(3));
    })
    .catch((err) => {
      renderProcessSteps([{ id: "error", label: err.message || "Prepare failed", status: "error" }]);
      status.textContent = err.message || "Prepare failed";
    })
    .finally(() => {
      updateContinueEnabled();
    });
}

function loadYearView(year) {
  if (!year) {
    renderGallery();
    return Promise.resolve(null);
  }
  currentRunId = null;
  return fetch(viewUrlForYear(year))
    .then((res) => {
      if (!res.ok) throw new Error(`No preview for ${year}`);
      return res.json();
    })
    .then((data) => {
      setBundle(data);
      showStep(3);
      return data;
    });
}

function loadRunView(runId) {
  currentRunId = runId;
  return fetch(viewUrlForRun(runId))
    .then((res) => {
      if (!res.ok) throw new Error(`No preview for run ${runId}`);
      return res.json();
    })
    .then((data) => {
      setBundle(data);
      showStep(3);
      return data;
    });
}

function loadDefault() {
  return refreshHubStatus()
    .then((status) => {
      if (status?.latest_run_id) {
        const yearSwitch = document.getElementById("year-switch");
        if (yearSwitch) yearSwitch.value = `run:${status.latest_run_id}`;
        return loadRunView(status.latest_run_id);
      }
      const year = status?.latest_year;
      if (year) {
        const yearSwitch = document.getElementById("year-switch");
        if (yearSwitch) yearSwitch.value = String(year);
        return loadYearView(year);
      }
      return fetch(DEFAULT_DATA_URL)
        .then((res) => {
          if (!res.ok) throw new Error("No preview loaded yet.");
          return res.json();
        })
        .then((data) => {
          setBundle(data);
          showStep(3);
        });
    })
    .catch(() => {
      renderGallery();
    });
}

function refreshHubStatus() {
  return api("/api/status")
    .then((status) => {
      hubStatus = status;
      adminImportUrl = status.admin_import_url || "";
      populateYears(status.years || [], status.runs || []);
      updateDisk(status.disk);
      document.getElementById("open-admin").disabled = !adminImportUrl;
      return status;
    })
    .catch(() => {
      document.getElementById("disk-pill").textContent = "Hub offline — start run_aggregator_hub.py";
      return null;
    });
}

document.querySelectorAll(".wizard-tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    const step = Number(tab.dataset.step);
    showStep(step);
  });
});

document.querySelectorAll('input[name="source-id"]').forEach((input) => {
  input.addEventListener("change", () => {
    syncSourceForm();
    clearInactiveSourceStatus();
    if (selectedSource === "artist_website") inspectArtistSource();
    else if (ingestFile) inspectSelectedCsv();
  });
});

document.getElementById("fill-clara-example")?.addEventListener("click", () => {
  document.querySelector('input[name="source-id"][value="artist_website"]').checked = true;
  syncSourceForm();
  document.getElementById("artist-name").value = "Clara Berta";
  document.getElementById("website-url").value = "https://claraberta.com/";
  document.getElementById("portfolio-url").value = "";
  document.getElementById("max-pages").value = "150";
  inspectArtistSource();
});

["artist-name", "website-url"].forEach((id) => {
  document.getElementById(id).addEventListener("input", updateContinueEnabled);
});
["artist-name", "website-url", "portfolio-url", "max-pages"].forEach((id) => {
  document.getElementById(id).addEventListener("change", inspectArtistSource);
  document.getElementById(id).addEventListener("blur", inspectArtistSource);
});
document.getElementById("render-javascript").addEventListener("change", inspectArtistSource);

document.getElementById("filters").addEventListener("click", (event) => {
  const button = event.target.closest("[data-filter]");
  if (!button) return;
  activeFilter = button.dataset.filter;
  renderFilters();
  renderGallery();
});

document.getElementById("search").addEventListener("input", renderGallery);
document.getElementById("sort").addEventListener("change", renderGallery);

document.getElementById("gallery").addEventListener("click", (event) => {
  const card = event.target.closest("[data-key]");
  if (!card) return;
  selectedUid = card.dataset.key;
  const project = findProject(selectedUid);
  renderGallery();
  if (project) renderDetail(project);
});

document.getElementById("close-detail").addEventListener("click", () => {
  selectedUid = null;
  document.getElementById("detail").hidden = true;
  document.querySelector(".workspace").classList.remove("detail-open");
  renderGallery();
});

document.getElementById("ingest-file").addEventListener("change", (event) => {
  ingestFile = event.target.files?.[0] || null;
  document.getElementById("ingest-file-name").textContent = ingestFile ? ingestFile.name : "No file selected";
  document.getElementById("confirm-overwrite").checked = false;
  inspectSelectedCsv();
});

document.getElementById("goto-prepare").addEventListener("click", () => showStep(2));
document.getElementById("run-prepare").addEventListener("click", runPrepare);

document.getElementById("download-upload").addEventListener("click", () => exportFilteredCsv("upload"));
document.getElementById("download-core").addEventListener("click", () => exportFilteredCsv("core"));

document.getElementById("run-validate").addEventListener("click", () => {
  const year = Number(document.getElementById("year-switch").value || detectedYear || bundle?.meta?.year || 0);
  const runId = currentRunId || bundle?.meta?.run_id || "";
  const log = document.getElementById("validate-log");
  log.textContent = "Validating…";
  api("/api/validate-upload", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ year: Number.isFinite(year) ? year : 0, run_id: runId }),
  })
    .then((result) => {
      if (result.ok) {
        log.textContent = `OK — ${result.row_count} export-ready rows.`;
        document.getElementById("download-upload").disabled = false;
      } else {
        log.textContent = `Failed — ${result.error_count} issue(s).`;
      }
      showStep(4);
    })
    .catch((err) => {
      log.textContent = err.message;
    });
});

document.getElementById("run-deploy").addEventListener("click", () => {
  const year = Number(document.getElementById("year-switch").value || detectedYear || bundle?.meta?.year);
  const status = document.getElementById("deploy-status");
  if (!year || String(year).startsWith("run:")) {
    status.textContent = "Deploy package is currently available for Burning Man year runs.";
    return;
  }
  status.textContent = "Preparing package…";
  api("/api/prepare-deploy", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      year,
      export_anyway: document.getElementById("export-anyway").checked,
    }),
  })
    .then((result) => {
      if (!result.ok) {
        status.textContent = result.message || "Deploy package failed.";
        return;
      }
      status.textContent = result.message || "Package ready.";
      adminImportUrl = result.admin_import_url || adminImportUrl;
      document.getElementById("open-admin").disabled = !adminImportUrl || result.forced;
    })
    .catch((err) => {
      status.textContent = err.message;
    });
});

document.getElementById("open-admin").addEventListener("click", () => {
  if (adminImportUrl) window.open(adminImportUrl, "_blank", "noopener");
});

document.getElementById("switch-year").addEventListener("click", () => {
  const value = document.getElementById("year-switch").value;
  if (value.startsWith("run:")) {
    loadRunView(value.slice(4)).catch((err) => {
      document.getElementById("cleanup-status").textContent = err.message;
    });
    return;
  }
  const year = Number(value);
  api("/api/load-year", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ year }),
  })
    .then((result) => {
      if (!result.ok) throw new Error(result.error || "Could not open year");
      return loadYearView(result.year || year);
    })
    .catch((err) => {
      document.getElementById("cleanup-status").textContent = err.message;
    });
});

document.getElementById("run-cleanup").addEventListener("click", () => {
  const status = document.getElementById("cleanup-status");
  status.textContent = "Cleaning…";
  api("/api/cleanup", { method: "POST", body: "{}", headers: { "Content-Type": "application/json" } })
    .then((result) => {
      status.textContent = `Removed ${result.removed_count} paths (${result.mb_freed} MB).`;
      updateDisk(result.disk);
    })
    .catch((err) => {
      status.textContent = err.message;
    });
});

document.getElementById("file-input").addEventListener("change", (event) => {
  const file = event.target.files?.[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    try {
      setBundle(JSON.parse(String(reader.result || "{}")));
      showStep(3);
    } catch (err) {
      document.getElementById("gallery").innerHTML = `<div class="error">Invalid JSON: ${esc(err.message)}</div>`;
    }
  };
  reader.readAsText(file);
});

syncSourceForm();
showStep(1);
loadDefault();
