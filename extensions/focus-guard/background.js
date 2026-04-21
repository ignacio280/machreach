// MachReach Focus Guard — background service worker
// Blocks distracting domains while a MachReach focus session is active.

const BLOCKED_HOSTS = [
  "instagram.com",
  "tiktok.com",
  "twitter.com",
  "x.com",
  "facebook.com",
  "reddit.com",
  "snapchat.com",
  "pinterest.com",
  "twitch.tv",
  "9gag.com",
  "netflix.com"
];

// Domains we intentionally DO NOT block (YouTube allowed for study).
const ALLOWLIST = [
  "youtube.com",
  "youtu.be",
  // Always allow MachReach itself so internal navigation never gets blocked.
  "machreach.com",
  "localhost",
  "127.0.0.1"
];

function hostMatches(url, list) {
  try {
    const h = new URL(url).hostname.toLowerCase();
    return list.some(d => h === d || h.endsWith("." + d));
  } catch (_) {
    return false;
  }
}

async function isFocusActive() {
  const { focusActive, focusExpiresAt } = await chrome.storage.local.get(["focusActive", "focusExpiresAt"]);
  if (!focusActive) return false;
  // Stale-safe: if we haven't had a ping in 90s, consider it inactive.
  if (focusExpiresAt && Date.now() > focusExpiresAt) return false;
  return true;
}

chrome.webNavigation.onBeforeNavigate.addListener(async (details) => {
  if (details.frameId !== 0) return; // top-level only
  if (!details.url || !/^https?:/i.test(details.url)) return;
  if (hostMatches(details.url, ALLOWLIST)) return;
  if (!hostMatches(details.url, BLOCKED_HOSTS)) return;

  if (await isFocusActive()) {
    const blockUrl = chrome.runtime.getURL("blocked.html") +
      "?src=" + encodeURIComponent(details.url);
    try {
      await chrome.tabs.update(details.tabId, { url: blockUrl });
    } catch (e) {
      console.warn("[FocusGuard] update failed:", e);
    }
  }
});

// Initial state
chrome.runtime.onInstalled.addListener(() => {
  chrome.storage.local.set({ focusActive: false, focusExpiresAt: 0 });
});
