(() => {
  "use strict";

  const byId = (id) => document.getElementById(id);
  let metrics = [];
  let activeMetric = null;
  let querySerial = 0;
  let queryController = null;
  let bearerToken = null;

  function text(id, value) {
    byId(id).textContent = value == null ? "" : String(value);
  }

  function labelForColumn(name) {
    return String(name).replaceAll("_", " ").toLowerCase().replace(/\b\w/g, (char) => char.toUpperCase());
  }

  function approvalLabel(person, timestamp) {
    const parts = [];
    if (person) parts.push(String(person));
    if (timestamp) parts.push(String(timestamp).slice(0, 10));
    return parts.length ? parts.join(" · ") : "Not supplied";
  }

  function formatValue(value, unit) {
    if (value == null) return "—";
    const number = Number(value);
    if (!Number.isFinite(number)) return String(value);
    const formatted = new Intl.NumberFormat(undefined, { maximumFractionDigits: 4 }).format(number);
    const normalizedUnit = String(unit || "").trim();
    if (normalizedUnit.toLowerCase() === "percent" || normalizedUnit === "%") return `${formatted}%`;
    return normalizedUnit ? `${formatted} ${normalizedUnit}` : formatted;
  }

  async function api(url, options) {
    let response;
    try {
      const headers = new Headers((options && options.headers) || {});
      if (bearerToken && url !== "/health") headers.set("Authorization", `Bearer ${bearerToken}`);
      response = await fetch(url, { cache: "no-store", ...options, headers });
    } catch (error) {
      if (error && error.name === "AbortError") throw error;
      throw new Error("The server could not be reached. Check the connection and try again.");
    }
    let payload;
    try {
      payload = await response.json();
    } catch {
      throw new Error("The server returned an unreadable response.");
    }
    if (!response.ok) {
      const detail = payload && payload.detail;
      const message = typeof detail === "string" ? detail : detail && detail.message;
      throw new Error(message || `The request failed (${response.status}).`);
    }
    return payload;
  }

  function setConnection(state, label) {
    const pill = byId("connection-pill");
    pill.classList.remove("connected", "degraded");
    if (state) pill.classList.add(state);
    text("connection-text", label);
  }

  async function checkHealth() {
    try {
      const health = await api("/health");
      if (health.status === "ok" && health.database === "connected") {
        setConnection("connected", "Oracle connected");
      } else if (health.security === "missing") {
        setConnection("degraded", "Access setup needed");
      } else if (health.catalog === "missing") {
        setConnection("degraded", "Catalog setup needed");
      } else {
        setConnection("degraded", "Oracle unavailable");
      }
    } catch {
      setConnection("degraded", "Connection unavailable");
    }
  }

  function clearAccess() {
    bearerToken = null;
    metrics = [];
    activeMetric = null;
    if (queryController) queryController.abort();
    querySerial += 1;
    byId("access-token").value = "";
    byId("access-form").hidden = false;
    byId("access-summary").hidden = true;
    byId("metric-form").hidden = true;
    byId("reload-button").hidden = true;
    byId("result-section").hidden = true;
    renderDefinition(null);
    text("catalog-feedback", "Connect to load certified metrics.");
    byId("catalog-feedback").hidden = false;
  }

  async function connectAccess(event) {
    event.preventDefault();
    const candidate = byId("access-token").value.trim();
    const feedback = byId("access-feedback");
    feedback.hidden = true;
    if (!/^[A-Za-z0-9_-]{43}$/.test(candidate)) {
      feedback.textContent = "Enter the application token provided by your administrator.";
      feedback.hidden = false;
      return;
    }
    bearerToken = candidate;
    byId("access-connect").disabled = true;
    try {
      const account = await api("/me");
      if (!account || !account.user_id || !Array.isArray(account.locations)) {
        throw new Error("The server returned an unreadable access profile.");
      }
      byId("access-token").value = "";
      byId("access-form").hidden = true;
      const locations = account.locations.map((item) => `${item.display_name} (${item.location_key})`);
      const locationLabel = locations.length ? locations.join(", ") : "No active locations";
      text("access-summary-text", `${account.display_name} · ${account.role} · ${locationLabel}`);
      byId("access-summary").hidden = false;
      await loadCatalog();
    } catch (error) {
      bearerToken = null;
      feedback.textContent = error.message || "Access could not be verified.";
      feedback.hidden = false;
    } finally {
      byId("access-connect").disabled = false;
    }
  }

  function defaultDates() {
    const now = new Date();
    const end = new Date(now.getFullYear(), now.getMonth(), 1);
    const start = new Date(now.getFullYear(), now.getMonth() - 1, 1);
    const iso = (value) => `${value.getFullYear().toString().padStart(4, "0")}-${(value.getMonth() + 1).toString().padStart(2, "0")}-01`;
    byId("start-date").value = iso(start);
    byId("end-date").value = iso(end);
  }

  function clearFormError() {
    byId("form-feedback").hidden = true;
    text("form-feedback", "");
  }

  function invalidateResult() {
    if (queryController) queryController.abort();
    querySerial += 1;
    byId("result-section").hidden = true;
    byId("run-button").disabled = false;
    text("run-label", "Run certified query");
    clearFormError();
  }

  function showFormError(message) {
    invalidateResult();
    text("form-feedback", message);
    byId("form-feedback").hidden = false;
  }

  function renderDefinition(metric) {
    byId("definition-empty").hidden = Boolean(metric);
    byId("definition-content").hidden = !metric;
    if (!metric) return;
    text("definition-id", metric.id);
    text("definition-name", metric.name);
    text("definition-text", metric.definition);
    text("definition-owner", metric.owner);
    text("definition-version", metric.version);
    text("definition-unit", metric.unit);
    text("definition-aggregation", metric.aggregation);
    text("definition-source", metric.source_view);
    text("definition-reviewed", approvalLabel(metric.reviewed_by, metric.reviewed_at));
    text("definition-certified", approvalLabel(metric.certified_by, metric.certified_at));
    const note = String(metric.review_note || "").trim();
    text("definition-review-note", note);
    byId("definition-review-note").hidden = !note;
  }

  function renderDimensions(metric) {
    const container = byId("dimension-options");
    container.replaceChildren();
    const allowed = Array.isArray(metric.allowed_dims) ? metric.allowed_dims : [];
    if (!allowed.length) {
      const message = document.createElement("p");
      message.className = "no-options";
      message.textContent = "No grouping columns are approved for this metric.";
      container.append(message);
      return;
    }
    for (const name of allowed) {
      const label = document.createElement("label");
      label.className = "choice-label";
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.name = "dimension";
      checkbox.value = String(name);
      const caption = document.createElement("span");
      caption.textContent = labelForColumn(name);
      label.append(checkbox, caption);
      container.append(label);
    }
  }

  function allowedFilters() {
    return activeMetric && Array.isArray(activeMetric.allowed_filters) ? activeMetric.allowed_filters : [];
  }

  function refreshFilterButton() {
    const allowed = allowedFilters();
    byId("add-filter").disabled = !allowed.length || byId("filter-rows").children.length >= allowed.length;
  }

  function addFilter() {
    const allowed = allowedFilters();
    if (!allowed.length || byId("filter-rows").children.length >= allowed.length) return;
    const used = new Set(Array.from(byId("filter-rows").querySelectorAll("select"), (select) => select.value));
    const row = document.createElement("div");
    row.className = "filter-row";
    const index = byId("filter-rows").children.length + 1;
    const select = document.createElement("select");
    select.setAttribute("aria-label", `Filter ${index} column`);
    for (const name of allowed) {
      const option = document.createElement("option");
      option.value = String(name);
      option.textContent = labelForColumn(name);
      select.append(option);
    }
    select.value = String(allowed.find((name) => !used.has(name)) || allowed[0]);
    const value = document.createElement("input");
    value.type = "text";
    value.maxLength = 256;
    value.placeholder = "Exact value";
    value.setAttribute("aria-label", `Filter ${index} value`);
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "remove-filter";
    remove.textContent = "×";
    remove.setAttribute("aria-label", `Remove filter ${index}`);
    remove.addEventListener("click", () => { row.remove(); refreshFilterButton(); invalidateResult(); });
    row.append(select, value, remove);
    byId("filter-rows").append(row);
    refreshFilterButton();
    invalidateResult();
    select.focus();
  }

  function selectMetric(metricId) {
    if (queryController) queryController.abort();
    querySerial += 1;
    byId("run-button").disabled = false;
    text("run-label", "Run certified query");
    byId("result-section").hidden = true;
    clearFormError();
    activeMetric = metrics.find((metric) => metric.id === metricId) || null;
    renderDefinition(activeMetric);
    byId("filter-rows").replaceChildren();
    if (activeMetric) renderDimensions(activeMetric);
    byId("dimension-fieldset").hidden = !activeMetric;
    byId("filter-fieldset").hidden = !activeMetric || allowedFilters().length === 0;
    refreshFilterButton();
  }

  async function loadCatalog() {
    const feedback = byId("catalog-feedback");
    feedback.hidden = false;
    feedback.className = "feedback";
    if (!bearerToken) {
      feedback.textContent = "Connect to load certified metrics.";
      byId("metric-form").hidden = true;
      return;
    }
    feedback.textContent = "Loading certified metrics…";
    byId("metric-form").hidden = true;
    byId("reload-button").hidden = true;
    try {
      const payload = await api("/metrics");
      if (!payload || !Array.isArray(payload.metrics)) throw new Error("The metric catalog returned an unexpected response.");
      metrics = payload.metrics.filter((metric) => metric && metric.id && metric.status === "CERTIFIED")
        .sort((left, right) => String(left.name).localeCompare(String(right.name)));
      if (!metrics.length) {
        feedback.classList.add("is-empty");
        feedback.textContent = "No certified registered metrics are available yet. A metric owner can register, review, and certify one with the repository's local commands.";
        byId("reload-button").hidden = false;
        renderDefinition(null);
        return;
      }
      const select = byId("metric-select");
      select.replaceChildren();
      for (const metric of metrics) {
        const option = document.createElement("option");
        option.value = String(metric.id);
        option.textContent = `${metric.name || metric.id} · v${metric.version}`;
        select.append(option);
      }
      select.value = String(metrics[0].id);
      selectMetric(select.value);
      feedback.hidden = true;
      byId("metric-form").hidden = false;
    } catch (error) {
      feedback.classList.add("is-error");
      feedback.textContent = error.message || "The certified metric catalog could not be loaded.";
      byId("reload-button").hidden = false;
      renderDefinition(null);
    }
  }

  function isMonthBoundary(value) {
    const match = /^(\d{4})-(\d{2})-01$/.exec(value);
    return Boolean(match && Number(match[1]) > 0 && Number(match[2]) >= 1 && Number(match[2]) <= 12);
  }

  function collectFilters() {
    const filters = {};
    for (const row of byId("filter-rows").children) {
      const column = row.querySelector("select").value;
      const value = row.querySelector("input").value.trim();
      if (!value) throw new Error(`Enter a value for ${labelForColumn(column)} or remove that filter.`);
      if (Object.hasOwn(filters, column)) throw new Error(`Use ${labelForColumn(column)} only once.`);
      filters[column] = value;
    }
    return filters;
  }

  function renderTable(data, metric) {
    const table = byId("result-table");
    const head = table.querySelector("thead");
    const body = table.querySelector("tbody");
    head.replaceChildren();
    body.replaceChildren();
    const columns = Array.isArray(data.columns) ? data.columns : [];
    const rows = Array.isArray(data.rows) ? data.rows : [];
    for (const column of columns) {
      const cell = document.createElement("th");
      cell.scope = "col";
      cell.textContent = labelForColumn(column);
      head.append(cell);
    }
    for (const row of rows) {
      const tr = document.createElement("tr");
      for (const column of columns) {
        const cell = document.createElement("td");
        cell.textContent = column === "value" ? formatValue(row[column], metric.unit) : (row[column] == null ? "—" : String(row[column]));
        tr.append(cell);
      }
      body.append(tr);
    }
    byId("result-table-wrap").hidden = columns.length === 1 && rows.length === 1;
  }

  function renderResult(data) {
    const metric = data.metric || activeMetric || {};
    const rows = Array.isArray(data.rows) ? data.rows : [];
    const columns = Array.isArray(data.columns) ? data.columns : [];
    text("result-title", "Certified answer");
    text("result-metric-name", `${metric.name || metric.id || "Metric"} · version ${metric.version || "—"} · ${metric.owner || "Owner not supplied"}`);
    text("result-definition", metric.definition || "Approved definition not supplied.");
    text("result-owner", metric.owner || "Not supplied");
    text("result-version", metric.version || "—");
    text("result-reviewed", approvalLabel(metric.reviewed_by, metric.reviewed_at));
    text("result-certified", approvalLabel(metric.certified_by, metric.certified_at));
    text("result-source", metric.source_view || "Not supplied");
    const period = data.period || {};
    text("result-period", `${period.start || ""} → ${period.end || ""} (end exclusive)`);
    const scalar = rows.length === 1 && columns.length === 1 && columns[0] === "value";
    byId("result-value").hidden = !scalar;
    text("result-value", scalar ? formatValue(rows[0].value, metric.unit) : "");
    const count = Number.isInteger(data.row_count) ? data.row_count : rows.length;
    text("result-count", `${count} ${count === 1 ? "result row" : "result rows"} · ${metric.source_view || "approved view"}`);
    renderTable(data, metric);
    text("coverage-note", data.coverage_note || "");
    byId("coverage-note").hidden = !data.coverage_note;
    text("result-sql", data.sql || "");
    text("result-binds", JSON.stringify(data.binds || {}, null, 2));
    byId("sql-details").open = false;
    byId("result-card").hidden = false;
    byId("result-error").hidden = true;
    byId("query-loading").hidden = true;
  }

  async function runQuery(event) {
    event.preventDefault();
    clearFormError();
    if (!activeMetric) return showFormError("Choose a certified metric first.");
    const start = byId("start-date").value;
    const end = byId("end-date").value;
    if (!isMonthBoundary(start) || !isMonthBoundary(end)) {
      return showFormError("Enter the first day of a month for both dates.");
    }
    if (start >= end) return showFormError("The end month must come after the start month.");
    const monthIndex = (value) => Number(value.slice(0, 4)) * 12 + Number(value.slice(5, 7));
    if (monthIndex(end) - monthIndex(start) > 36) {
      return showFormError("Choose a period of at most 36 months.");
    }
    let filters;
    try {
      filters = collectFilters();
    } catch (error) {
      return showFormError(error.message);
    }
    const dimensions = Array.from(byId("dimension-options").querySelectorAll("input:checked"), (input) => input.value);
    const request = {
      metric_id: activeMetric.id,
      start_date: start,
      end_date: end,
      dimensions,
      filters,
    };
    if (queryController) queryController.abort();
    queryController = new AbortController();
    const serial = ++querySerial;
    byId("run-button").disabled = true;
    text("run-label", "Running query…");
    byId("result-section").hidden = false;
    text("result-title", "Running certified query");
    byId("result-card").hidden = true;
    byId("result-error").hidden = true;
    byId("query-loading").hidden = false;
    try {
      const data = await api("/metric-query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(request),
        signal: queryController.signal,
      });
      if (serial !== querySerial) return;
      if (!data || data.tier !== "CERTIFIED" || !Array.isArray(data.rows)) {
        throw new Error("The server did not return a certified answer.");
      }
      renderResult(data);
    } catch (error) {
      if (serial !== querySerial || (error && error.name === "AbortError")) return;
      text("result-title", "Query could not run");
      text("result-error", error.message || "The query could not be completed.");
      byId("result-error").hidden = false;
      byId("query-loading").hidden = true;
    } finally {
      if (serial === querySerial) {
        byId("run-button").disabled = false;
        text("run-label", "Run certified query");
      }
    }
  }

  byId("metric-select").addEventListener("change", (event) => selectMetric(event.target.value));
  byId("access-form").addEventListener("submit", connectAccess);
  byId("access-clear").addEventListener("click", clearAccess);
  byId("metric-form").addEventListener("input", invalidateResult);
  byId("metric-form").addEventListener("change", invalidateResult);
  byId("add-filter").addEventListener("click", addFilter);
  byId("reload-button").addEventListener("click", loadCatalog);
  byId("metric-form").addEventListener("submit", runQuery);
  defaultDates();
  void checkHealth();
})();
