"use strict";

const images = { spectrum: "calendar-120.png", quiet: "calendar-quiet.png", paper: "calendar-paper.png" };
const compact = { spectrum: "calendar-50.png", quiet: "calendar-50-quiet.png", paper: "calendar-50-paper.png" };
document.querySelectorAll('input[name="palette"]').forEach(input => {
  input.addEventListener("change", () => {
    document.querySelector("#calendar-image").src = "assets/" + images[input.value];
    document.querySelector("#calendar-picture source").srcset = "assets/" + compact[input.value];
  });
});
document.querySelector(".copy").addEventListener("click", async event => {
  const status = document.querySelector("#copy-status");
  try {
    await navigator.clipboard.writeText(event.currentTarget.dataset.copy);
    status.textContent = "Copied";
  } catch {
    status.textContent = "Select command to copy";
  }
});
