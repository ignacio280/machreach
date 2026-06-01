// Runs on machreach.com (and localhost) pages.
// Reads the focus_float flag from localStorage and mirrors it into
// chrome.storage.local so the background worker can gate navigation.

(function () {
  // Announce presence to the page. Content scripts run in an isolated world
  // (they can't set page-visible window globals), but they SHARE the DOM — so
  // MachReach detects the extension by reading this attribute / event before
  // it lets a user start a focus timer.
  function announce() {
    try {
      document.documentElement.setAttribute("data-machreach-focus-guard", "1");
      document.dispatchEvent(new CustomEvent("machreach-focus-guard-ready"));
    } catch (_) {}
  }
  announce();
  document.addEventListener("DOMContentLoaded", announce);
  // Answer on-demand pings from the page (covers any race on first paint).
  document.addEventListener("machreach-focus-guard-ping", announce);

  function readFocus() {
    try {
      const raw = localStorage.getItem("focus_float");
      if (!raw) return { active: false };
      const ff = JSON.parse(raw);
      // Treat BREAK phases as inactive — the user should be free to scroll
      // distracting sites during a break. A break phase has workMinutes <= 0.
      const isBreak = !ff || !ff.active || (typeof ff.workMinutes === "number" && ff.workMinutes <= 0);
      return { active: !!(ff && ff.active && !isBreak), data: ff };
    } catch (_) {
      return { active: false };
    }
  }

  function push() {
    const s = readFocus();
    // Expire 60s after last ping so a crashed MachReach tab doesn't
    // lock you out forever.
    chrome.storage.local.set({
      focusActive: s.active,
      focusExpiresAt: Date.now() + 60_000
    });
  }

  push();
  setInterval(push, 10_000);

  // React immediately if focus is toggled in another MachReach tab.
  window.addEventListener("storage", (e) => {
    if (e.key === "focus_float") push();
  });
})();
