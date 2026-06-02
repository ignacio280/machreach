  (function(){
    // Academic setup (country / university / major) is collected on the
    // dedicated /student/setup page and enforced server-side, so there is no
    // onboarding modal here anymore. This script only handles the small
    // "your previous progress was preserved" welcome banner for returning users.
    const banner = document.getElementById('mrXpBanner');
    if (!banner) return;

    const bannerCloseBtn = document.getElementById('mrXpBannerClose');
    function hideBanner() {
      // setProperty + !important so nothing in the global stylesheet can
      // accidentally re-show the banner once the user dismisses it.
      banner.style.setProperty('display', 'none', 'important');
      banner.setAttribute('hidden', '');
      try { fetch('/api/academic/banner/seen', { method:'POST' }); } catch(_){}
    }
    if (bannerCloseBtn) {
      ['click','pointerup','touchend'].forEach(ev =>
        bannerCloseBtn.addEventListener(ev, function(e){ e.preventDefault(); e.stopPropagation(); hideBanner(); }, true)
      );
    }

    async function init() {
      try {
        const r = await fetch('/api/academic/profile');
        if (!r.ok) return;
        const j = await r.json();
        // Users who still need setup are redirected to /student/setup by the
        // server — don't flash the banner at them.
        if (j.needs_setup) return;
        const hasPriorXp = (j.prior_xp || 0) > 0;
        if (hasPriorXp && !j.xp_preserve_banner_seen) {
          banner.style.display = 'flex';
          setTimeout(hideBanner, 8000);
        }
      } catch(_){}
    }
    init();
  })();
