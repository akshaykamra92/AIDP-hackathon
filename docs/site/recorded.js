(() => {
  "use strict";

  const buttons = Array.from(document.querySelectorAll(".scenario-button"));
  const panels = new Map(
    ["certified", "comparison", "refusal", "scope-cache"].map((name) => [
      name,
      document.getElementById(`panel-${name}`),
    ]),
  );
  const announcement = document.getElementById("scenario-announcement");
  const labels = {
    certified: "Showing the recorded certified turnover answer.",
    comparison: "Showing the recorded turnover definition comparison.",
    refusal: "Showing the recorded refusal and request preview.",
    "scope-cache": "Showing the recorded location-scoped fulfillment ratio and separate result-cache checks.",
  };

  for (const button of buttons) {
    button.addEventListener("click", () => {
      const selected = button.dataset.scenario;
      if (!panels.has(selected)) return;

      for (const candidate of buttons) {
        const active = candidate.dataset.scenario === selected;
        candidate.classList.toggle("is-selected", active);
        candidate.setAttribute("aria-pressed", String(active));
      }
      for (const [name, panel] of panels) {
        panel.hidden = name !== selected;
      }
      announcement.textContent = labels[selected];
    });
  }

  const requestButton = document.getElementById("preview-request");
  const requestReceipt = document.getElementById("request-receipt");
  requestButton.addEventListener("click", () => {
    const expanded = requestButton.getAttribute("aria-expanded") === "true";
    requestButton.setAttribute("aria-expanded", String(!expanded));
    requestReceipt.hidden = expanded;
  });
})();
