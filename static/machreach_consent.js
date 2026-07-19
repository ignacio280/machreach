(function () {
  "use strict";

  var ONE_YEAR = 60 * 60 * 24 * 365;

  function hasConsentCookie() {
    return document.cookie.split(";").some(function (part) {
      return part.trim() === "cookie_consent=1";
    });
  }

  function writeConsentCookie(name, value) {
    var secure = window.location.protocol === "https:" ? "; Secure" : "";
    document.cookie = name + "=" + value + "; Path=/; Max-Age=" + ONE_YEAR + "; SameSite=Lax" + secure;
  }

  function setBannerVisibility(banner, visible) {
    banner.hidden = !visible;
    banner.setAttribute("aria-hidden", visible ? "false" : "true");
  }

  function saveConsent(banner, allowAnalytics) {
    writeConsentCookie("cookie_consent", "1");
    writeConsentCookie("analytics_consent", allowAnalytics ? "1" : "0");
    setBannerVisibility(banner, false);
    window.dispatchEvent(new CustomEvent("machreach:consentchange", {
      detail: { analytics: allowAnalytics },
    }));
  }

  function initializeConsentBanner() {
    var banner = document.getElementById("cookie-consent");
    if (!banner) return;

    if (hasConsentCookie()) {
      setBannerVisibility(banner, false);
      return;
    }

    setBannerVisibility(banner, true);
    banner.querySelectorAll("[data-consent-choice]").forEach(function (button) {
      button.addEventListener("click", function () {
        saveConsent(banner, button.getAttribute("data-consent-choice") === "analytics");
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initializeConsentBanner, { once: true });
  } else {
    initializeConsentBanner();
  }
}());
