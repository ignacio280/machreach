    (function(){
      // 1) Scroll-reveal observer: any element with [.reveal, .reveal-fade, .reveal-scale, .reveal-left, .reveal-right]
      if ('IntersectionObserver' in window) {
        var io = new IntersectionObserver(function(entries){
          entries.forEach(function(e){
            if (e.isIntersecting) {
              e.target.classList.add('in-view');
              // Trigger count-up if element has data-count
              var el = e.target;
              if (el.dataset && el.dataset.count && !el.dataset.countDone) {
                el.dataset.countDone = '1';
                var target = parseFloat(el.dataset.count);
                var suffix = el.dataset.countSuffix || '';
                var prefix = el.dataset.countPrefix || '';
                var duration = parseInt(el.dataset.countDuration || '1500', 10);
                var decimals = parseInt(el.dataset.countDecimals || '0', 10);
                var start = performance.now();
                function step(now) {
                  var p = Math.min(1, (now - start) / duration);
                  var eased = 1 - Math.pow(1 - p, 3); // easeOutCubic
                  var val = target * eased;
                  el.textContent = prefix + val.toFixed(decimals) + suffix;
                  if (p < 1) requestAnimationFrame(step);
                  else el.textContent = prefix + target.toFixed(decimals) + suffix;
                }
                requestAnimationFrame(step);
              }
              io.unobserve(e.target);
            }
          });
        }, { threshold: 0.15, rootMargin: '0px 0px -40px 0px' });
        document.querySelectorAll('.reveal, .reveal-fade, .reveal-scale, .reveal-left, .reveal-right, [data-count]').forEach(function(el){ io.observe(el); });
      } else {
        // Fallback: reveal everything instantly
        document.querySelectorAll('.reveal, .reveal-fade, .reveal-scale, .reveal-left, .reveal-right').forEach(function(el){ el.classList.add('in-view'); });
      }

      // 2) Nav scroll state
      var nav = document.querySelector('.nav');
      function onScroll() {
        if (!nav) return;
        if (window.scrollY > 8) nav.classList.add('is-scrolled'); else nav.classList.remove('is-scrolled');
      }
      window.addEventListener('scroll', onScroll, { passive: true });
      onScroll();

      // 3) Spotlight cursor tracking on .spotlight cards
      document.addEventListener('mousemove', function(e){
        var t = e.target.closest && e.target.closest('.spotlight');
        if (!t) return;
        var r = t.getBoundingClientRect();
        t.style.setProperty('--mx', (e.clientX - r.left) + 'px');
        t.style.setProperty('--my', (e.clientY - r.top) + 'px');
      });

      // 4) Command palette (Cmd+K / Ctrl+K)
      var CMDK_ITEMS = window.__IS_LOGGED_IN__ ? [
        {t:'Student Dashboard', u:'/student', i:'🎓', s:'Main'},
        {t:'Courses', u:'/student/courses', i:'📚', s:'Main'},
        {t:'Flashcards', u:'/student/flashcards', i:'📇', s:'Study'},
        {t:'Quizzes', u:'/student/quizzes', i:'📝', s:'Study'},
        {t:'Focus Mode', u:'/student/focus', i:'🎯', s:'Tools'},
        {t:'Grade Sheet', u:'/student/gpa', i:'📈', s:'Tools'},
        {t:'Leaderboard', u:'/student/leaderboard', i:'🏆', s:'Social'},
        {t:'Settings', u:'/student/settings', i:'\u2699\uFE0F', s:'Other'},
        {t:'Log out', u:'/logout', i:'🚪', s:'Other'},
      ] : [
        {t:'Home', u:'/', i:'🏠', s:'Public'},
        {t:'Log in', u:'/login', i:'🔑', s:'Public'},
        {t:'Sign up', u:'/register', i:'\u2728', s:'Public'},
      ];

      function buildCmdK() {
        if (document.getElementById('cmdk-overlay')) return;
        var o = document.createElement('div');
        o.id = 'cmdk-overlay';
        o.className = 'cmdk-overlay';
        o.innerHTML =
          '<div class="cmdk-panel" role="dialog" aria-label="Command palette">'
          + '<div class="cmdk-input-wrap">'
          + '<span style="color:var(--text-muted);">🔍</span>'
          + '<input id="cmdk-input" type="text" placeholder="Jump to a page or feature…" autocomplete="off" />'
          + '<span class="cmdk-kbd">ESC</span>'
          + '</div>'
          + '<div id="cmdk-list" class="cmdk-list"></div>'
          + '</div>';
        document.body.appendChild(o);
        o.addEventListener('click', function(e){ if (e.target === o) closeCmdK(); });
        var input = o.querySelector('#cmdk-input');
        input.addEventListener('input', function(){ renderCmdK(input.value); });
        input.addEventListener('keydown', function(e){
          var items = o.querySelectorAll('.cmdk-item');
          var sel = o.querySelector('.cmdk-item.selected');
          var idx = Array.prototype.indexOf.call(items, sel);
          if (e.key === 'ArrowDown') { e.preventDefault(); var next = items[(idx+1+items.length)%items.length]; if (sel) sel.classList.remove('selected'); if (next) { next.classList.add('selected'); next.scrollIntoView({block:'nearest'}); } }
          else if (e.key === 'ArrowUp') { e.preventDefault(); var prev = items[(idx-1+items.length)%items.length]; if (sel) sel.classList.remove('selected'); if (prev) { prev.classList.add('selected'); prev.scrollIntoView({block:'nearest'}); } }
          else if (e.key === 'Enter') { e.preventDefault(); if (sel) window.location.href = sel.dataset.url; }
          else if (e.key === 'Escape') { e.preventDefault(); closeCmdK(); }
        });
      }
      function renderCmdK(q) {
        q = (q||'').trim().toLowerCase();
        var list = document.getElementById('cmdk-list');
        if (!list) return;
        var matches = CMDK_ITEMS.filter(function(it){ return !q || it.t.toLowerCase().indexOf(q) !== -1 || (it.s||'').toLowerCase().indexOf(q) !== -1; });
        if (!matches.length) { list.innerHTML = '<div class="cmdk-empty">No matches. Try a different keyword.</div>'; return; }
        var groups = {};
        matches.forEach(function(it){ (groups[it.s]=groups[it.s]||[]).push(it); });
        var html = '';
        Object.keys(groups).forEach(function(sec){
          html += '<div class="cmdk-section-title">' + sec + '</div>';
          groups[sec].forEach(function(it){
            html += '<div class="cmdk-item" data-url="' + it.u + '">'
              + '<span class="cmdk-icon">' + it.i + '</span>'
              + '<span>' + it.t + '</span>'
              + '<span class="cmdk-hint">\u21B5</span>'
              + '</div>';
          });
        });
        list.innerHTML = html;
        var first = list.querySelector('.cmdk-item');
        if (first) first.classList.add('selected');
        list.querySelectorAll('.cmdk-item').forEach(function(it){
          it.addEventListener('click', function(){ window.location.href = it.dataset.url; });
          it.addEventListener('mouseenter', function(){
            list.querySelectorAll('.cmdk-item').forEach(function(x){ x.classList.remove('selected'); });
            it.classList.add('selected');
          });
        });
      }
      function openCmdK() {
        buildCmdK();
        var o = document.getElementById('cmdk-overlay');
        o.classList.add('open');
        var input = document.getElementById('cmdk-input');
        if (input) { input.value = ''; input.focus(); }
        renderCmdK('');
      }
      function closeCmdK() {
        var o = document.getElementById('cmdk-overlay');
        if (o) o.classList.remove('open');
      }
      window.openCmdK = openCmdK;
      window.closeCmdK = closeCmdK;
      document.addEventListener('keydown', function(e){
        if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) {
          // Don't hijack if user is typing in another input
          e.preventDefault();
          var o = document.getElementById('cmdk-overlay');
          if (o && o.classList.contains('open')) closeCmdK(); else openCmdK();
        }
      });
    })();
