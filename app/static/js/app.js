document.documentElement.classList.add("js");

document.querySelectorAll("form[data-confirm]").forEach((form) => {
  form.addEventListener("submit", (event) => {
    if (!window.confirm(form.dataset.confirm)) {
      event.preventDefault();
      event.stopImmediatePropagation();
    }
  });
});

function releaseForm(form) {
  delete form.dataset.sent;
}

document.querySelectorAll("form").forEach((form) => {
  form.addEventListener("submit", (event) => {
    if (form.dataset.sent === "1") {
      event.preventDefault();
      return;
    }
    form.dataset.sent = "1";
    window.setTimeout(() => releaseForm(form), 2500);
  });
});

window.addEventListener("pageshow", () => {
  document.querySelectorAll("form").forEach(releaseForm);
});

const zone = document.querySelector("[data-drop]");
if (zone) {
  const input = zone.querySelector('input[type="file"]');
  const form = zone.closest("form");
  const title = zone.querySelector(".drop-title");
  const error = document.querySelector("[data-drop-error]");
  ["dragenter", "dragover"].forEach((name) => {
    zone.addEventListener(name, (event) => {
      event.preventDefault();
      zone.classList.add("is-hot");
    });
  });
  ["dragleave", "drop"].forEach((name) => {
    zone.addEventListener(name, (event) => {
      event.preventDefault();
      zone.classList.remove("is-hot");
    });
  });
  zone.addEventListener("drop", (event) => {
    const file = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
    if (!file || !input) return;
    if (!file.name.toLowerCase().endsWith(".pdf")) {
      if (error) error.textContent = "That file is not a PDF.";
      return;
    }
    if (error) error.textContent = "";
    const transfer = new DataTransfer();
    transfer.items.add(file);
    input.files = transfer.files;
    if (title) title.textContent = file.name;
    form.requestSubmit();
  });
  input.addEventListener("change", () => {
    const file = input.files && input.files[0];
    if (!file) return;
    if (title) title.textContent = file.name;
    form.requestSubmit();
  });
}

const signatureText = { yes: "Present", no: "Not seen", unknown: "Not confirmed" };

function filled(value) {
  const text = (value || "").trim();
  return text || "—";
}

function applyRole(card) {
  const chosen = card.querySelector('input[type="radio"]:checked');
  const role = chosen ? chosen.value : "cheque";
  card.classList.remove("is-cheque", "is-typed", "is-omit");
  card.classList.add("is-" + role);
  card.querySelectorAll(".cheque-fields input, .cheque-fields textarea, .cheque-fields select").forEach((field) => {
    field.disabled = role !== "cheque";
  });
  card.querySelectorAll(".typed-fields textarea").forEach((field) => {
    field.disabled = role !== "typed";
  });
}

document.querySelectorAll("[data-cheque]").forEach((card) => {
  const sync = () => {
    card.querySelectorAll("[data-out]").forEach((node) => {
      const field = node.getAttribute("data-out");
      const input = card.querySelector(`[data-field="${field}"]`);
      if (!input) return;
      if (input.tagName === "SELECT") {
        node.textContent = signatureText[input.value] || "—";
      } else {
        node.textContent = filled(input.value);
      }
    });
  };
  card.addEventListener("input", sync);
  card.querySelectorAll('input[type="radio"]').forEach((radio) => {
    radio.addEventListener("change", () => {
      if (!radio.checked) return;
      applyRole(card);
    });
  });
  applyRole(card);
  sync();
});

const poller = document.querySelector("[data-poll]");
if (poller) {
  const next = poller.getAttribute("data-next");
  const tick = async () => {
    try {
      const response = await fetch(poller.getAttribute("data-poll"), { headers: { Accept: "application/json" } });
      const payload = await response.json();
      if (payload.status === "transcribing") {
        window.setTimeout(tick, 1200);
        return;
      }
      window.location.assign(next);
    } catch (error) {
      window.setTimeout(tick, 2000);
    }
  };
  window.setTimeout(tick, 700);
}

const testButton = document.querySelector("[data-test-model]");
if (testButton) {
  testButton.addEventListener("click", async () => {
    const result = document.querySelector("[data-test-result]");
    if (result) {
      result.className = "flag quiet";
      result.textContent = "Checking the connection…";
    }
    const body = {
      base_url: document.querySelector('[name="base_url"]').value,
      model: document.querySelector('[name="model"]').value,
      api_key: document.querySelector('[name="api_key"]').value,
    };
    try {
      const response = await fetch("/settings/test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const payload = await response.json();
      if (!result) return;
      result.textContent = payload.message || "No answer.";
      result.className = payload.ok ? "flag good" : "flag warn";
    } catch (error) {
      if (result) {
        result.textContent = "Could not reach ChequeScript.";
        result.className = "flag warn";
      }
    }
  });
}
