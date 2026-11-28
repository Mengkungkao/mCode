"use strict";
(function () {
  // Follow the phone's light/dark setting on this page.
  const dark = window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  document.body.dataset.theme = dark;
  const params = new URLSearchParams(location.search);
  const notice = document.getElementById("pair-notice");
  if (params.has("used")) {
    notice.textContent = "That link or QR code was already used or has expired. Each one works once: show a new one on the device.";
    notice.classList.remove("hidden");
  } else if (params.has("wait")) {
    notice.textContent = "Too many tries. Wait a minute, then scan again.";
    notice.classList.remove("hidden");
  }
  if (params.toString()) history.replaceState(null, "", "/");
})();
document.getElementById("pair-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const code = document.getElementById("pair-code").value.replace(/\s+/g, "");
  const error = document.getElementById("pair-error");
  error.textContent = "";
  try {
    const response = await fetch("/api/pair", {
      method: "POST", credentials: "same-origin",
      headers: {"Content-Type": "application/json", "X-mCode": "1"},
      body: JSON.stringify({code}),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) { error.textContent = data.error || "That did not work."; return; }
    location.href = "/";
  } catch (e) {
    error.textContent = "mCode is not reachable.";
  }
});
