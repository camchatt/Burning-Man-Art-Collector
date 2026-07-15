const DEFAULT_DATA_URL = "./data/aggregator_view.json";

function viewUrlForYear(year) {
  return year ? `/api/view?year=${year}` : DEFAULT_DATA_URL;
}

const FILTERS = [
  { id: "all", label: "All projects" },
  { id: "attention", label: "Needs review" },
  { id: "ready", label: "Ready for Artelier" },
  { id: "missing_hero", label: "No hero photo" },
  { id: "missing_proof", label: "No proof link" },
  { id: "playa_uncertain", label: "Burner name unclear" },
  { id: "kind_uncertain", label: "Person vs org unclear" },
  { id: "has_image", label: "Has hero photo" },
  { id: "has_playa", label: "Has playa address" },
];

let bundle = null;
let activeFilter = "all";
let selectedUid = null;
let hubStatus = null;
let ingestFile = null;
let detectedYear = null;
let alreadyProcessed = false;
let adminImportUrl = "";
let wizardStep = 1;

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
  if (step === 3) {
    document.querySelector(".controls")?.classList.remove("is-dimmed");
    document.querySelector(".workspace")?.classList.remove("is-dimmed");
  }
}

function setDetectedYear(year, message = "", already = false) {
  detectedYear = year || null;
  alreadyProcessed = Boolean(already);
  document.getElementById("detected-year-pill").textContent = detectedYear
    ? `Year ${detectedYear}`
    : "Year not detected";
  document.getElementById("csv-inspect-message").textContent = message || "";
  document.getElementById("goto-prepare").disabled = !ingestFile || !detectedYear;
  document.getElementById("run-prepare").disabled = !ingestFile || !detectedYear;
  document.getElementById("overwrite-warn").hidden = !alreadyProcessed;
  if (!alreadyProcessed) {
    document.getElementById("confirm-overwrite").checked = false;
  }
  if (detectedYear) {
    document.getElementById("prepare-summary").textContent =
      `Ready to match Burning Man ${detectedYear} to the History Archive` +
      (message ? ` — ${message}` : ".");
    const deployYear = document.getElementById("year-switch");
    if (deployYear && ![...deployYear.options].some((opt) => opt.value === String(detectedYear))) {
      const opt = document.createElement("option");
      opt.value = String(detectedYear);
      opt.textContent = String(detectedYear);
      deployYear.appendChild(opt);
    }
    if (deployYear) deployYear.value = String(detectedYear);
  }
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
  if (data.meta?.about) {
    document.getElementById("about").textContent = data.meta.about;
  }
  document.getElementById("year-pill").textContent = `Year ${data.meta?.year ?? "—"}`;
  const year = data.meta?.year;
  if (year) {
    const yearSwitch = document.getElementById("year-switch");
    if (![...yearSwitch.options].some((opt) => opt.value === String(year))) {
      const opt = document.createElement("option");
      opt.value = String(year);
      opt.textContent = String(year);
      yearSwitch.appendChild(opt);
    }
    yearSwitch.value = String(year);
  }
  renderChecklist(data.upload_checklist || {});
  renderFilters();
  renderGallery();
  document.getElementById("detail").hidden = true;
  document.querySelector(".workspace").classList.remove("detail-open");
  document.getElementById("download-upload").disabled = false;
  document.getElementById("run-deploy").disabled = false;
}

function renderChecklist(c) {
  const items = [
    { label: "Projects in year", value: c.project_count ?? 0 },
    { label: "Ready for Artelier", value: c.upload_ready_count ?? 0, tone: "ok" },
    { label: "Needs review", value: c.needs_attention_count ?? 0, tone: "warn" },
    { label: "Hero photos found", value: c.with_hero_image ?? 0 },
    { label: "Playa addresses", value: c.with_playa_address ?? 0 },
    { label: "Missing proof link", value: c.missing_proof_count ?? 0, tone: c.missing_proof_count ? "bad" : "" },
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
    if (activeFilter === "missing_hero") return !p.hero?.url || p.review_flags?.includes("hero_missing");
    if (activeFilter === "missing_proof") return !p.proof_url;
    if (activeFilter === "playa_uncertain") return p.review_flags?.includes("playa_name_uncertain");
    if (activeFilter === "kind_uncertain") return p.review_flags?.includes("contributor_kind_uncertain");
    if (activeFilter === "has_image") return Boolean(p.hero?.url);
    if (activeFilter === "has_playa") return Boolean(p.place?.playa_address);
    return true;
  });

  if (q) {
    rows = rows.filter((p) =>
      [
        p.title,
        p.people?.contributor_display_name,
        p.people?.source_artist_credit,
        p.place?.playa_address,
        p.place?.theme_camp,
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
  if (project.upload_ready) chips.push({ text: "Ready for Artelier", tone: "ok" });
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
  const year = Number(document.getElementById("year-switch").value || detectedYear || bundle?.meta?.year);
  const status = document.getElementById("export-status") || document.getElementById("deploy-status");
  if (!year || !bundle) {
    if (status) status.textContent = "Open a prepared year first.";
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
      year,
      kind,
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
        `artelier_${kind === "core" ? "core_only" : "bm_upload"}_${year}.csv`;
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
      '<div class="loading">No year loaded yet. Choose a PlayaEvents ART file and run Match &amp; build, or open a prepared year above.</div>';
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
      return `
        <button type="button" class="card ${selectedUid === key ? "active" : ""}" data-key="${esc(key)}">
          ${media}
          <div class="card-body">
            <h2>${esc(p.title || "Untitled")}</h2>
            <div class="meta">${esc(p.people?.name || p.people?.contributor_display_name || "No contributor yet")}</div>
            <div class="meta">${esc(p.place?.playa_address || "No playa address")}</div>
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
  const flagChips = (project.flag_labels || [])
    .map((label) => `<span class="chip">${esc(label)}</span>`)
    .join("");

  body.innerHTML = `
    <div class="detail-hero">
      ${
        hero.url
          ? `<img src="${esc(hero.url)}" alt="" onerror="this.parentElement.innerHTML='<div class=placeholder>Image unavailable</div>'" />`
          : '<div class="placeholder" style="height:160px;display:grid;place-items:center;color:#6b5e52">No hero image</div>'
      }
    </div>
    <h2>${esc(project.title || "Untitled")}</h2>
    <p class="meta">${esc(project.year)}${project.upload_ready ? " · Ready for Artelier" : " · Needs review"}</p>
    <div class="chips" style="margin-top:0.5rem">${flagChips || '<span class="chip ok">No blocking flags</span>'}</div>

    <h3>Contributor</h3>
    <p><strong>Person or Organization:</strong> ${esc(people.person_or_org || "unknown")}</p>
    <p><strong>Name:</strong> ${esc(people.name || people.contributor_display_name || "—")}</p>
    <p><strong>Alt / Burner Name:</strong> ${esc(people.alt_burner_name || people.playa_name || "—")}</p>
    <p><strong>Additional Credits:</strong> ${esc(people.additional_contributor_credits || "—")}</p>
    <p><strong>Source credit:</strong> ${esc(people.source_artist_credit || "—")}</p>
    <p><strong>Summary:</strong> ${esc(project.summary || "—")}</p>

    <h3>Place</h3>
    <p><strong>Playa address:</strong> ${esc(place.playa_address || "—")}</p>
    <p><strong>Theme camp:</strong> ${esc(place.theme_camp || "—")}</p>
    <p><strong>Type:</strong> ${esc(place.installation_type || "—")}</p>

    <h3>Proof &amp; hero</h3>
    <p>${
      project.proof_url
        ? `<a href="${esc(project.proof_url)}" target="_blank" rel="noopener">Open proof / archive page →</a>`
        : "<span class='chip bad'>Missing proof link</span>"
    }</p>
    <p><strong>Hero attribution:</strong> ${esc(hero.attribution || "—")}</p>
  `;
}

function populateYears(years) {
  const yearSwitch = document.getElementById("year-switch");
  const current = yearSwitch.value;
  yearSwitch.innerHTML = "";
  for (const year of years) {
    const opt = document.createElement("option");
    opt.value = String(year);
    opt.textContent = String(year);
    yearSwitch.appendChild(opt);
  }
  if (years.length) {
    yearSwitch.value = current && years.includes(Number(current)) ? current : String(years[0]);
  }
}

function updateDisk(disk) {
  if (!disk) return;
  document.getElementById("disk-pill").textContent = `Data ${disk.total_mb ?? "—"} MB`;
}

function inspectSelectedCsv() {
  if (!ingestFile) {
    setDetectedYear(null);
    return Promise.resolve();
  }
  document.getElementById("csv-inspect-message").textContent = "Detecting year…";
  const form = new FormData();
  form.append("file", ingestFile, ingestFile.name);
  return api("/api/inspect-csv", { method: "POST", body: form })
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
  if (!ingestFile || !detectedYear) {
    status.textContent = "Choose a PlayaEvents ART CSV in step 1 first.";
    return;
  }
  if (alreadyProcessed && !document.getElementById("confirm-overwrite").checked) {
    status.textContent = "Check “Yes, rebuild this year’s Aggregator outputs” before running again.";
    renderProcessSteps([
      { id: "year", label: `Year ${detectedYear}`, status: "done" },
      { id: "overwrite", label: "Confirm rebuild of Aggregator outputs", status: "blocked" },
    ]);
    return;
  }

  btn.disabled = true;
  status.textContent = "Matching History Archive… this can take several minutes.";
  renderProcessSteps([
    { id: "www", label: "Using uploaded PlayaEvents file (disk library untouched)", status: "running" },
    { id: "verify", label: "Matching History Archive for heroes & proof links", status: "pending" },
    { id: "identity", label: document.getElementById("run-identity-online").checked
      ? "Web search for unclear artist / Burner names"
      : "Using local names only (no web search)", status: "pending" },
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
      renderProcessSteps(result.steps || []);
      status.textContent =
        `Built ${result.project_count} projects` +
        (result.checklist?.with_hero_image != null
          ? ` · ${result.checklist.with_hero_image} with hero photos`
          : "") +
        (result.checklist?.upload_ready_count != null
          ? ` · ${result.checklist.upload_ready_count} ready for Artelier.`
          : ".");
      updateDisk(result.disk);
      return fetch(result.viewer_reload || DEFAULT_DATA_URL)
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
      btn.disabled = !ingestFile || !detectedYear;
    });
}

function loadYearView(year) {
  if (!year) {
    renderGallery();
    return Promise.resolve(null);
  }
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

function loadDefault() {
  // Prefer hub status latest_year; fall back to static cache if hub is offline.
  return refreshHubStatus()
    .then((status) => {
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
      populateYears(status.years || []);
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
    if (step === 2 && (!ingestFile || !detectedYear) && !bundle) {
      document.getElementById("csv-inspect-message").textContent = "Choose a PlayaEvents ART file in step 1 first.";
      showStep(1);
      return;
    }
    showStep(step);
  });
});

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
  const year = Number(document.getElementById("year-switch").value || detectedYear || bundle?.meta?.year);
  const log = document.getElementById("validate-log");
  log.textContent = "Validating…";
  api("/api/validate-upload", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ year }),
  })
    .then((result) => {
      if (result.ok) {
        log.textContent = `OK — ${result.row_count} upload-ready rows.`;
        document.getElementById("run-deploy").disabled = false;
        document.getElementById("download-upload").disabled = false;
      } else {
        log.textContent = `Failed — ${result.error_count} issue(s).`;
      }
    })
    .catch((err) => {
      log.textContent = err.message;
    });
});

document.getElementById("run-deploy").addEventListener("click", () => {
  const year = Number(document.getElementById("year-switch").value || detectedYear || bundle?.meta?.year);
  const status = document.getElementById("deploy-status");
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
      document.getElementById("download-upload").disabled = false;
      document.getElementById("download-core").disabled = false;
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
  const year = Number(document.getElementById("year-switch").value);
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

showStep(1);
loadDefault();
// refreshHubStatus runs inside loadDefault so the latest prepared year auto-opens.
