  (function(){
    const modal = document.getElementById('mrOnboardingModal');
    const banner = document.getElementById('mrXpBanner');
    const stepContent = document.getElementById('mrStepContent');
    const stepDots = document.querySelectorAll('.mr-step-dot');
    const nextBtn = document.getElementById('mrStepNext');
    const backBtn = document.getElementById('mrStepBack');

    let state = {
      step: 0,
      country_iso: '',
      country_name: '',
      university_id: null,
      university_name: '',
      major_id: null,
      major_name: '',
      canvas_url: '',
      canvas_token: '',
      countries: [],
    };

    const STEPS = [
      { title:'Where are you studying?',
        sub:'Pick your country. This sets up your country leaderboard.',
        render: renderCountry },
      { title:'Which university?',
        sub:'Start typing. Create yours if you don\'t see it.',
        render: renderUniversity },
      { title:'What do you study?',
        sub:'Major, program, or field. We normalize duplicates.',
        render: renderMajor },
      { title:'Conectar Canvas (optional)',
        sub:'Paste your Canvas personal API token to auto-sync courses and assignments. Skip and do it later from Settings.',
        render: renderCanvas },
    ];

    function renderHeader(title, sub) {
      return `<h2 style="margin:0 0 8px;font-size:26px;letter-spacing:-.02em;">${title}</h2>
              <p style="margin:0 0 20px;color:#8B93A7;font-size:14px;">${sub}</p>`;
    }

    async function renderCountry() {
      if (!state.countries.length) {
        const r = await fetch('/api/academic/countries');
        const j = await r.json();
        state.countries = j.countries || [];
      }
      const hdr = renderHeader(STEPS[0].title, STEPS[0].sub);
      const opts = state.countries.map(c =>
        `<div class="mr-result ${state.country_iso===c.iso_code?'selected':''}" data-iso="${c.iso_code}" data-name="${c.name}">
          <span>${c.flag_emoji||''} ${c.name}</span>
          <span class="tag">${c.region||''}</span>
         </div>`).join('');
      stepContent.innerHTML = hdr +
        `<input class="mr-input" id="mrCountrySearch" placeholder="Search countries…" autocomplete="off">
         <div class="mr-results" id="mrCountryList">${opts}</div>`;
      const list = document.getElementById('mrCountryList');
      document.getElementById('mrCountrySearch').addEventListener('input', e => {
        const q = e.target.value.toLowerCase();
        list.querySelectorAll('.mr-result').forEach(el => {
          el.style.display = el.dataset.name.toLowerCase().includes(q) ? '' : 'none';
        });
      });
      list.addEventListener('click', e => {
        const el = e.target.closest('.mr-result');
        if (!el) return;
        state.country_iso = el.dataset.iso;
        state.country_name = el.dataset.name;
        list.querySelectorAll('.mr-result').forEach(r => r.classList.remove('selected'));
        el.classList.add('selected');
      });
    }

    async function renderUniversity() {
      const hdr = renderHeader(STEPS[1].title, `${STEPS[1].sub} — Country: ${state.country_name}`);
      stepContent.innerHTML = hdr +
        `<input class="mr-input" id="mrUnivSearch" placeholder="e.g. Stanford, PUC, UTFSM…" autocomplete="off">
         <div class="mr-results" id="mrUnivList"><div style="padding:20px;color:#8B93A7;text-align:center;">Start typing to search</div></div>`;
      const searchEl = document.getElementById('mrUnivSearch');
      const listEl = document.getElementById('mrUnivList');
      let debounce;
      const doSearch = async () => {
        const q = searchEl.value.trim();
        const r = await fetch(`/api/academic/universities?country=${encodeURIComponent(state.country_iso)}&q=${encodeURIComponent(q)}`);
        const j = await r.json();
        const rows = j.universities || [];
        let html = rows.map(u =>
          `<div class="mr-result ${state.university_id===u.id?'selected':''}" data-id="${u.id}" data-name="${u.name.replace(/"/g,'&quot;')}">
             <span>${u.name}${u.short_name?' <span class="tag">'+u.short_name+'</span>':''}</span>
             ${u.status==='pending'?'<span class="tag">pending</span>':''}
           </div>`).join('');
        if (q.length >= 3) {
          html += `<div class="mr-create-new" id="mrCreateUniv">＋ Create "${q}"</div>`;
        }
        listEl.innerHTML = html || `<div style="padding:20px;color:#8B93A7;text-align:center;">No matches${q.length>=3?' — create above':''}</div>`;
      };
      searchEl.addEventListener('input', () => { clearTimeout(debounce); debounce = setTimeout(doSearch, 200); });
      listEl.addEventListener('click', async e => {
        const createEl = e.target.closest('#mrCreateUniv');
        if (createEl) {
          const name = searchEl.value.trim();
          const r = await fetch('/api/academic/universities', {
            method:'POST', headers:{'Content-Type':'application/json'},
            body: JSON.stringify({ name, country_iso: state.country_iso })
          });
          const j = await r.json();
          if (j.ok && j.university) {
            state.university_id = j.university.id;
            state.university_name = j.university.name;
            doSearch();
          }
          return;
        }
        const el = e.target.closest('.mr-result');
        if (!el) return;
        state.university_id = parseInt(el.dataset.id, 10);
        state.university_name = el.dataset.name;
        listEl.querySelectorAll('.mr-result').forEach(r => r.classList.remove('selected'));
        el.classList.add('selected');
      });
    }

    async function renderMajor() {
      const hdr = renderHeader(STEPS[2].title, STEPS[2].sub);
      stepContent.innerHTML = hdr +
        `<input class="mr-input" id="mrMajorSearch" placeholder="e.g. Computer Science, Medicine, Economics…" autocomplete="off">
         <div class="mr-results" id="mrMajorList"><div style="padding:20px;color:#8B93A7;text-align:center;">Start typing your major</div></div>`;
      const searchEl = document.getElementById('mrMajorSearch');
      const listEl = document.getElementById('mrMajorList');
      let debounce;
      const doSearch = async () => {
        const q = searchEl.value.trim();
        if (!q) { listEl.innerHTML = '<div style="padding:20px;color:#8B93A7;text-align:center;">Start typing</div>'; return; }
        const r = await fetch(`/api/academic/majors?q=${encodeURIComponent(q)}&university_id=${state.university_id||''}`);
        const j = await r.json();
        const rows = j.majors || [];
        let html = rows.map(m =>
          `<div class="mr-result ${state.major_id===m.id?'selected':''}" data-id="${m.id}" data-name="${m.name.replace(/"/g,'&quot;')}">
             <span>${m.name}</span>${m.university_id?'<span class="tag">univ-specific</span>':'<span class="tag">global</span>'}
           </div>`).join('');
        if (q.length >= 2) {
          html += `<div class="mr-create-new" id="mrCreateMajor">＋ Add "${q}" as new major</div>`;
        }
        listEl.innerHTML = html;
      };
      searchEl.addEventListener('input', () => { clearTimeout(debounce); debounce = setTimeout(doSearch, 200); });
      listEl.addEventListener('click', async e => {
        const createEl = e.target.closest('#mrCreateMajor');
        if (createEl) {
          const name = searchEl.value.trim();
          const r = await fetch('/api/academic/majors', {
            method:'POST', headers:{'Content-Type':'application/json'},
            body: JSON.stringify({ name, university_id: state.university_id })
          });
          const j = await r.json();
          if (j.ok && j.major) {
            state.major_id = j.major.id;
            state.major_name = j.major.name;
            doSearch();
          }
          return;
        }
        const el = e.target.closest('.mr-result');
        if (!el) return;
        state.major_id = parseInt(el.dataset.id, 10);
        state.major_name = el.dataset.name;
        listEl.querySelectorAll('.mr-result').forEach(r => r.classList.remove('selected'));
        el.classList.add('selected');
      });
    }

    function renderCanvas() {
      const hdr = renderHeader(STEPS[3].title, STEPS[3].sub);
      stepContent.innerHTML = hdr +
        `<input class="mr-input" id="mrCanvasUrl" placeholder="https://canvas.instructure.com (or your school's)" value="${state.canvas_url||''}">
         <input class="mr-input" id="mrCanvasToken" type="password" placeholder="Canvas personal API token" style="margin-top:10px;" value="${state.canvas_token||''}">
         <p style="margin:14px 0 0;font-size:12px;color:#8B93A7;line-height:1.55;">
           Generate a token in Canvas: <strong>Account → Settings → + New Access Token</strong>.
           Stored encrypted; revoke anytime in Canvas.
         </p>`;
      document.getElementById('mrCanvasUrl').addEventListener('input', e => state.canvas_url = e.target.value.trim());
      document.getElementById('mrCanvasToken').addEventListener('input', e => state.canvas_token = e.target.value.trim());
      nextBtn.textContent = 'Finish →';
    }

    function go(step) {
      state.step = Math.max(0, Math.min(STEPS.length - 1, step));
      stepDots.forEach((d, i) => d.classList.toggle('active', i <= state.step));
      backBtn.style.visibility = state.step === 0 ? 'hidden' : 'visible';
      nextBtn.textContent = state.step === STEPS.length - 1 ? 'Finish →' : 'Continue →';
      STEPS[state.step].render();
    }

    async function finish() {
      nextBtn.disabled = true;
      nextBtn.textContent = 'Saving…';
      try {
        const r = await fetch('/api/academic/profile', {
          method:'POST', headers:{'Content-Type':'application/json'},
          body: JSON.stringify({
            country_iso: state.country_iso,
            university_id: state.university_id,
            major_id: state.major_id,
            canvas_url: state.canvas_url,
            canvas_token: state.canvas_token,
          })
        });
        const j = await r.json();
        if (j.ok) {
          modal.style.display = 'none';
          document.body.style.overflow = '';
        } else {
          nextBtn.disabled = false;
          nextBtn.textContent = 'Finish →';
          alert(j.error || 'Save failed');
        }
      } catch(e) {
        nextBtn.disabled = false;
        nextBtn.textContent = 'Finish →';
        mrNetworkError(e, 'No se pudo completar la acción. Revisa tu conexión e inténtalo de nuevo.');
      }
    }

    nextBtn.addEventListener('click', () => {
      if (state.step === 0 && !state.country_iso) { alert('Select a country'); return; }
      if (state.step === 1 && !state.university_id) { alert('Select or create a university'); return; }
      if (state.step === 2 && !state.major_id) { alert('Select or add a major'); return; }
      if (state.step === STEPS.length - 1) { finish(); return; }
      go(state.step + 1);
    });
    backBtn.addEventListener('click', () => go(state.step - 1));

    // Block all shortcuts that would bypass the modal
    function blockKeys(e) {
      if (modal.style.display === 'flex' && (e.key === 'Escape')) { e.preventDefault(); e.stopPropagation(); }
    }
    document.addEventListener('keydown', blockKeys, true);

    // Always wire the close button up front so it works no matter what branch runs
    const bannerCloseBtn = document.getElementById('mrXpBannerClose');
    function hideBanner() {
      // Use setProperty + !important so nothing in the global stylesheet can
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

    // Init: check whether we need to show the modal / banner
    async function init() {
      try {
        const r = await fetch('/api/academic/profile');
        if (!r.ok) return;
        const j = await r.json();
        // Priority 1: if setup isn't complete, show modal and hide banner entirely.
        if (j.needs_setup) {
          banner.style.display = 'none';
          modal.style.display = 'flex';
          document.body.style.overflow = 'hidden';
          go(0);
          return;
        }
        // Setup IS complete. Only show the 'previous progress preserved' banner if
        // the user has actual prior XP (i.e. a pre-existing account) and hasn't seen it.
        const hasPriorXp = (j.prior_xp || 0) > 0;
        if (hasPriorXp && !j.xp_preserve_banner_seen) {
          banner.style.display = 'flex';
          setTimeout(hideBanner, 8000);
        }
      } catch(_){}
    }
    init();
  })();