(() => {
  "use strict";

  const byId = (id) => document.getElementById(id);
  const form = byId("ask-form");
  const questionInput = byId("question");
  const askButton = byId("ask-button");
  const askButtonLabel = byId("ask-button-label");
  const resultSection = byId("result-section");
  const loadingCard = byId("loading-card");
  const certifiedCard = byId("certified-card");
  const exploratoryCard = byId("exploratory-card");
  const errorCard = byId("error-card");
  const requestButton = byId("request-button");
  const sqlToggle = byId("sql-toggle");
  const sqlPanel = byId("sql-panel");
  const formatter = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 });
  let activeQuestion = "";
  let nearestMetricIds = [];
  let requestSerial = 0;

  function text(id, value) {
    byId(id).textContent = value == null || value === "" ? "—" : String(value);
  }

  function showState(state) {
    resultSection.hidden = false;
    loadingCard.hidden = state !== "loading";
    certifiedCard.hidden = state !== "certified";
    exploratoryCard.hidden = state !== "exploratory";
    errorCard.hidden = state !== "error";
    resultSection.setAttribute("aria-busy", state === "loading" ? "true" : "false");
    byId("result-timestamp").textContent = state === "loading" ? "" : new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  }

  function setSubmitting(submitting) {
    askButton.disabled = submitting;
    askButtonLabel.textContent = submitting ? "Finding the answer" : "Ask the warehouse";
  }

  function formatValue(value, metric) {
    if (value === null || value === undefined || value === "") return "—";
    if (typeof value !== "number") {
      const numeric = Number(value);
      if (!Number.isFinite(numeric)) return String(value);
      value = numeric;
    }
    const formatted = formatter.format(value);
    const unit = String(metric && metric.unit ? metric.unit : "").toLowerCase();
    // The approved SQL returns percentage points, so 12.5 is displayed as 12.5%.
    if (unit === "percent" || unit === "percentage" || unit === "%") return `${formatted}%`;
    if (unit === "days" || unit === "day") return `${formatted} days`;
    return formatted;
  }

  function formatPeriod(period) {
    if (!period || typeof period !== "object") return "Period not supplied";
    const start = period.start || period.start_date;
    const exclusiveEnd = period.end || period.end_date;
    if (start && exclusiveEnd) {
      let displayEnd = exclusiveEnd;
      if (/^\d{4}-\d{2}-\d{2}$/.test(exclusiveEnd)) {
        const date = new Date(`${exclusiveEnd}T00:00:00Z`);
        if (!Number.isNaN(date.getTime())) {
          date.setUTCDate(date.getUTCDate() - 1);
          displayEnd = date.toISOString().slice(0, 10);
        }
      }
      return `${start} – ${displayEnd}`;
    }
    return start || exclusiveEnd || "Period not supplied";
  }

  function prettyColumn(name) {
    return String(name).replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
  }

  function renderTable(data, metric) {
    const wrap = byId("answer-table-wrap");
    const table = byId("answer-table");
    const head = table.querySelector("thead");
    const body = table.querySelector("tbody");
    head.replaceChildren();
    body.replaceChildren();

    const rows = Array.isArray(data.rows) ? data.rows : [];
    const columns = Array.isArray(data.columns) && data.columns.length
      ? data.columns
      : (rows[0] ? Object.keys(rows[0]) : []);
    if (!rows.length || (rows.length === 1 && columns.length === 1 && columns[0] === "value")) {
      wrap.hidden = true;
      return;
    }

    const headerRow = document.createElement("tr");
    columns.forEach((column) => {
      const th = document.createElement("th");
      th.scope = "col";
      th.textContent = prettyColumn(column);
      headerRow.appendChild(th);
    });
    head.appendChild(headerRow);

    rows.forEach((row) => {
      const tr = document.createElement("tr");
      columns.forEach((column) => {
        const td = document.createElement("td");
        td.textContent = column === "value" ? formatValue(row[column], metric) : (row[column] == null ? "—" : String(row[column]));
        tr.appendChild(td);
      });
      body.appendChild(tr);
    });
    wrap.hidden = false;
  }

  function renderWarnings(warnings) {
    const container = byId("warnings");
    container.replaceChildren();
    if (!Array.isArray(warnings) || !warnings.length) {
      container.hidden = true;
      return;
    }
    const title = document.createElement("strong");
    title.textContent = "Please note";
    const list = document.createElement("ul");
    warnings.forEach((warning) => {
      const item = document.createElement("li");
      item.textContent = typeof warning === "string" ? warning : JSON.stringify(warning);
      list.appendChild(item);
    });
    container.append(title, list);
    container.hidden = false;
  }

  function renderCertified(data) {
    const metric = data.metric || {};
    const rows = Array.isArray(data.rows) ? data.rows : [];
    const scalar = rows.length === 1 && Object.prototype.hasOwnProperty.call(rows[0], "value");
    text("answer-metric-name", metric.name || metric.id || "Certified metric");
    text("answer-value", scalar ? formatValue(rows[0].value, metric) : `${rows.length} results`);
    text("answer-summary", data.summary || "Calculated from the approved metric definition.");
    text("answer-definition", metric.definition || metric.definition_text || "Approved definition not supplied.");
    text("answer-metric-id", metric.id || metric.metric_id || "—");
    text("answer-version", metric.version || "—");
    text("answer-owner", metric.owner || "—");
    text("answer-freshness", data.freshness || "Not available");
    text("answer-period", formatPeriod(data.intent && data.intent.period));
    const rowCount = typeof data.row_count === "number" ? data.row_count : rows.length;
    text("answer-row-count", `${rowCount} result ${rowCount === 1 ? "row" : "rows"}`);
    text("answer-sql", data.sql || "SQL was not included in this response.");
    const binds = data.binds && Object.keys(data.binds).length ? data.binds : null;
    byId("binds-wrap").hidden = !binds;
    if (binds) text("answer-binds", JSON.stringify(binds, null, 2));
    sqlPanel.hidden = true;
    sqlToggle.setAttribute("aria-expanded", "false");
    sqlToggle.firstChild.textContent = "Show SQL ";
    renderWarnings(data.warnings);
    renderTable(data, metric);
    showState("certified");
  }

  function renderNearest(metrics) {
    const wrap = byId("nearest-wrap");
    const list = byId("nearest-metrics");
    list.replaceChildren();
    nearestMetricIds = [];
    if (!Array.isArray(metrics) || !metrics.length) {
      wrap.hidden = true;
      return;
    }
    metrics.slice(0, 3).forEach((metric) => {
      if (metric.id) nearestMetricIds.push(metric.id);
      const item = document.createElement("div");
      item.className = "nearest-metric";
      const name = document.createElement("strong");
      name.textContent = metric.name || metric.id || "Metric";
      const definition = document.createElement("p");
      definition.textContent = metric.definition || "";
      item.append(name, definition);
      list.appendChild(item);
    });
    wrap.hidden = false;
  }

  function renderExploratory(data) {
    text("exploratory-message", data.message || "Try a question about an approved metric, or request a new definition for this one.");
    const terms = Array.isArray(data.unmapped_terms) ? data.unmapped_terms : [];
    const termsBox = byId("unmapped-terms");
    const wholeQuestion = terms.length === 1 && String(terms[0]).trim() === String(data.question || "").trim();
    if (terms.length && !wholeQuestion) {
      termsBox.textContent = `Unmapped terms: ${terms.join(", ")}`;
      termsBox.hidden = false;
    } else {
      termsBox.hidden = true;
    }
    renderNearest(data.nearest_metrics);
    requestButton.disabled = false;
    byId("request-feedback").textContent = "";
    showState("exploratory");
  }

  async function ask(question) {
    const serial = ++requestSerial;
    activeQuestion = question;
    setSubmitting(true);
    showState("loading");
    resultSection.scrollIntoView({ behavior: "smooth", block: "start" });
    try {
      const response = await fetch("/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question }),
      });
      let payload;
      try { payload = await response.json(); } catch { payload = {}; }
      if (!response.ok) {
        const detail = payload.detail;
        throw new Error(typeof detail === "string" ? detail : (payload.message || `The service returned ${response.status}.`));
      }
      if (serial !== requestSerial) return;
      const tier = String(payload.tier || "").toUpperCase();
      if (tier === "CERTIFIED") renderCertified(payload);
      else if (tier === "EXPLORATORY") renderExploratory(payload);
      else throw new Error("The service returned an unexpected answer format.");
    } catch (error) {
      if (serial !== requestSerial) return;
      text("error-message", error.message || "Please try again.");
      showState("error");
    } finally {
      if (serial === requestSerial) setSubmitting(false);
    }
  }

  async function requestCertification() {
    requestButton.disabled = true;
    text("request-feedback", "Sending request…");
    try {
      const response = await fetch("/certification-request", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: activeQuestion, nearest_metric_ids: nearestMetricIds }),
      });
      let payload;
      try { payload = await response.json(); } catch { payload = {}; }
      if (!response.ok) {
        const detail = payload.detail;
        throw new Error(typeof detail === "string" ? detail : "The request could not be filed.");
      }
      text("request-feedback", payload.request_id ? `Request #${payload.request_id} filed for review.` : "Certification request filed for review.");
      requestButton.disabled = true;
    } catch (error) {
      text("request-feedback", error.message || "Please try again.");
      requestButton.disabled = false;
    }
  }

  async function showComparison() {
    const button = byId("compare-button");
    const card = byId("comparison-card");
    const grid = card.querySelector(".comparison-grid");
    button.disabled = true;
    card.hidden = false;
    grid.hidden = true;
    text("comparison-note", "Loading the two definitions…");
    card.scrollIntoView({ behavior: "smooth", block: "start" });
    try {
      const response = await fetch("/demo/turnover-comparison", { cache: "no-store" });
      if (!response.ok) throw new Error("The comparison is not available right now.");
      const data = await response.json();
      text("comparison-period", `${data.state || "FL"} · ${formatPeriod(data.period)}`);
      text("comparison-approved", formatValue(data.certified && data.certified.value, { unit: "percent" }));
      text("comparison-ad-hoc", formatValue(data.ad_hoc && data.ad_hoc.value, { unit: "percent" }));
      text("comparison-approved-count", `${data.certified && data.certified.separations != null ? data.certified.separations : "—"} separations counted`);
      text("comparison-ad-hoc-count", `${data.ad_hoc && data.ad_hoc.separations != null ? data.ad_hoc.separations : "—"} separations counted`);
      text("comparison-note", data.definition_note || "Illustrative ad hoc interpretation counts internal transfers. The certified definition excludes them.");
      grid.hidden = false;
    } catch (error) {
      text("comparison-note", error.message || "The comparison could not be loaded.");
    } finally {
      button.disabled = false;
    }
  }

  async function checkHealth() {
    const pill = byId("health-pill");
    try {
      const response = await fetch("/health", { cache: "no-store" });
      if (!response.ok) throw new Error("unavailable");
      const data = await response.json();
      const connected = data.database === "connected";
      pill.classList.toggle("is-connected", connected);
      pill.classList.toggle("is-unavailable", !connected);
      text("health-text", connected ? "Database connected" : "Database unavailable");
    } catch {
      pill.classList.add("is-unavailable");
      text("health-text", "Service unavailable");
    }
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const question = questionInput.value.trim();
    if (question) ask(question);
  });
  questionInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      form.requestSubmit();
    }
  });
  document.querySelectorAll("[data-question]").forEach((button) => {
    button.addEventListener("click", () => {
      questionInput.value = button.dataset.question || "";
      form.requestSubmit();
    });
  });
  sqlToggle.addEventListener("click", () => {
    const expanded = sqlToggle.getAttribute("aria-expanded") === "true";
    sqlToggle.setAttribute("aria-expanded", expanded ? "false" : "true");
    sqlToggle.firstChild.textContent = expanded ? "Show SQL " : "Hide SQL ";
    sqlPanel.hidden = expanded;
  });
  requestButton.addEventListener("click", requestCertification);
  byId("compare-button").addEventListener("click", showComparison);
  checkHealth();
})();
