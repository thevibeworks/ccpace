/* Synthetic watch-mode frames for the demo player. Entirely fake
 * accounts and numbers — no real session data anywhere on this page.
 * A tiny builder keeps the four frames consistent; the tables below
 * are the data. */

(function () {
  "use strict";

  var NOTES = {
    "▁": "a 5h window that ran — height = 7d points it burned",
    "▂": "a 5h window that ran — height = 7d points it burned",
    "▃": "a 5h window that ran — height = 7d points it burned",
    "▄": "a 5h window that ran — height = 7d points it burned",
    "▅": "a 5h window that ran — height = 7d points it burned",
    "▆": "a 5h window that ran — height = 7d points it burned",
    "▇": "a 5h window that ran — height = 7d points it burned",
    "█": "a heavy 5h window — height = 7d points it burned",
    "·": "a window that ran, burned under a point",
    "░": "unknown — no samples on record for that window",
    "▮": "the window you are in now",
    "▫": "a 5h window still ahead of you — countable budget",
    "×": "the 7d pool will not cover this window at the current pace",
    "┤": "access ends here — derived subscription period end",
  };

  function glyphs(s, cls) {
    var out = "";
    for (var ch of s) {
      out += NOTES[ch]
        ? '<span class="g" data-note="' + NOTES[ch] + '">' + ch + "</span>"
        : ch;
    }
    return cls ? '<span class="' + cls + '">' + out + "</span>" : out;
  }

  function bar(fill, hot, rest) {
    return (
      '<span class="p">' + "█".repeat(fill) + "</span>" +
      '<span class="' + (hot ? "w" : "c") + '">' +
      (hot ? "▓" : "▒").repeat(hot || rest ? (hot || rest) : 0) + "</span>" +
      '<span class="c">' + "░".repeat(10 - fill - (hot || rest || 0)) + "</span>"
    );
  }

  function pad(s, n) { return (s + " ".repeat(n)).slice(0, Math.max(n, s.length)); }

  function rule(tier, tierCls, alias, note, noteHot) {
    var plain = "── [" + tier + "] " + alias + " · " + note + " ";
    var fill = Math.max(0, 70 - plain.length);
    return (
      '<span class="c">──</span> <span class="' + tierCls + '">[' + tier + "]</span> " +
      '<span class="b">' + alias + "</span> " +
      '<span class="' + (noteHot ? "w" : "c") + '">· ' + note + "</span> " +
      '<span class="c">' + "─".repeat(fill) + "</span>"
    );
  }

  function row(tag, util, utilCls, barHtml, remain, at, pace, paceCls) {
    return (
      '<span class="c">' + pad(tag, 5) + "</span> " +
      '<span class="' + (utilCls || "o") + '">' + String(util).padStart(3) + "%</span> " +
      barHtml + "  " + pad(remain, 8) + " " + pad(at, 13) +
      (pace ? ' <span class="' + (paceCls || "c") + '">' + pace + "</span>" : "")
    );
  }

  function frame(t) {
    var L = [];
    // work: 20x, comfortably under budget
    L.push(rule("20x", "m", "work", "period ends ~Aug 11", false));
    L.push(row("5h", String(t.w5), "o", bar(1, 0, 1), "3h " + t.wmin + "m", "@19:00", "0.3x", "c"));
    L.push(row("7d", "23", "o", bar(2, 0, 2), "4d 8h", "@Thu 13 00:00", "0.8x", "c"));
    L.push(row("fable", "21", "o", bar(2, 0, 2), "4d 8h", "@Thu 13 00:00", "0.8x", "c"));
    L.push("           " + glyphs("▂▁·▃▄▂·▁▅", "p") + glyphs("▮") + glyphs("▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫") + glyphs("┤", "w"));
    L.push('           <span class="c">budget: ~21 windows left · 3.7%/window stays even · </span><span class="w">period ends ~Aug 11</span>');
    L.push('           <span class="c">forecast: +31% rest of week on your pattern · lands ~54% (18d history)</span>');
    L.push("");
    // personal: 5x, pacing hot
    L.push(rule(" 5x", "k", "personal", "period ends ~Aug 8", true));
    L.push(row("5h", String(t.p5), "w", bar(2, 2, 0), "3h " + t.pmin + "m", "@16:00", "1.8x", "e"));
    L.push(row("7d", "64", "w", bar(6, 0, 1), "2d 2h", "@Sat 8 15:00", "1.2x", "w"));
    L.push('           ' + glyphs("▃▅▄▆·▂▇▅", "p") + glyphs("▮") + glyphs("▫▫▫▫▫▫▫") + glyphs("××", "e") + glyphs("┤", "w"));
    L.push(' <span class="w">!  5h pace 1.8x - cap ~13:47, 2h 12m before reset</span>');
    L.push(' <span class="w">!  7d pace 1.2x - cap ~Sat 8 03:00, 12h before reset; then hard stop until reset</span>');
    L.push('           <span class="c">budget: ~8 windows left · 4.5%/window stays even · </span><span class="w">period ends ~Aug 8</span>');
    L.push('           <span class="c">forecast: +41% rest of week on your pattern · </span><span class="w">lands ~105% (9d history)</span>');
    L.push("");
    // status line: stable left, tickers right
    var left = "reset 1h 41m (work) | r=refresh q=quit";
    var right = t.clock + " refresh " + t.count + "s";
    L.push('<span class="o">' + left + "</span>" +
      " ".repeat(Math.max(2, 70 - left.length - right.length)) +
      '<span class="c">' + right + "</span>");
    return L.join("\n");
  }

  window.CCPACE_FRAMES = [
    frame({ w5: 7, wmin: 48, p5: 46, pmin: 42, clock: "21:18:31", count: 513 }),
    frame({ w5: 7, wmin: 48, p5: 46, pmin: 42, clock: "21:18:32", count: 512 }),
    frame({ w5: 8, wmin: 47, p5: 47, pmin: 41, clock: "21:18:33", count: 511 }),
    frame({ w5: 8, wmin: 47, p5: 47, pmin: 41, clock: "21:18:34", count: 510 }),
  ];
})();
