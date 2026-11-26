"use strict";
document.getElementById("pair-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const code = document.getElementById("pair-code").value.trim();
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
