/* Frame player for the watch-mode demo: real DOM text swapped on a
 * timer — selectable, crisp, themeable; ~1s cadence like the real TUI.
 * Pauses on hover (so ledger tooltips hold still) and respects
 * prefers-reduced-motion with a single representative frame. */

(function () {
  "use strict";
  var el = document.querySelector("#demo pre");
  if (!el || !window.CCPACE_FRAMES || !window.CCPACE_FRAMES.length) return;

  var frames = window.CCPACE_FRAMES;
  var i = 0;
  el.innerHTML = frames[0];

  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

  var paused = false;
  var demo = document.getElementById("demo");
  demo.addEventListener("mouseenter", function () { paused = true; });
  demo.addEventListener("mouseleave", function () { paused = false; });

  setInterval(function () {
    if (paused || document.hidden) return;
    i = (i + 1) % frames.length;
    el.innerHTML = frames[i];
  }, 1000);
})();
