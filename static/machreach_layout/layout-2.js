  /* ============================================================
   * GLOBAL FOCUS CONTROLLER
   * Runs on EVERY page so that when the user navigates away from
   * /student/focus the timer keeps ticking, the alarm fires, XP
   * gets credited, and pomodoro phases auto-advance — even though
   * the focus page itself has been unloaded.
   * ============================================================ */
  (function(){
    if (window.__focusGlobalCtrl) return; // singleton
    window.__focusGlobalCtrl = true;

    var keepEl = document.getElementById('focus-keepalive');
    var alarmEl = document.getElementById('focus-alarm');
    var widget = document.getElementById('focus-float');
    if (!widget) return;

    var alarmDataUri = null;
    var silenceDataUri = null;

    function buildSilenceWavDataUri(){
      var sr = 8000, n = sr;
      var buf = new ArrayBuffer(44 + n*2);
      var v = new DataView(buf);
      function w(o,s){ for(var i=0;i<s.length;i++) v.setUint8(o+i, s.charCodeAt(i)); }
      w(0,'RIFF'); v.setUint32(4, 36+n*2, true);
      w(8,'WAVEfmt '); v.setUint32(16,16,true); v.setUint16(20,1,true);
      v.setUint16(22,1,true); v.setUint32(24,sr,true);
      v.setUint32(28,sr*2,true); v.setUint16(32,2,true); v.setUint16(34,16,true);
      w(36,'data'); v.setUint32(40, n*2, true);
      var b = new Uint8Array(buf), s = '';
      for (var j=0; j<b.length; j++) s += String.fromCharCode(b[j]);
      return 'data:audio/wav;base64,' + btoa(s);
    }
    function buildAlarmWavDataUri(){
      var sr = 22050, dur = 1.4, n = Math.floor(sr*dur);
      var buf = new ArrayBuffer(44 + n*2);
      var v = new DataView(buf);
      function w(o,s){ for(var i=0;i<s.length;i++) v.setUint8(o+i, s.charCodeAt(i)); }
      w(0,'RIFF'); v.setUint32(4, 36+n*2, true);
      w(8,'WAVEfmt '); v.setUint32(16,16,true); v.setUint16(20,1,true);
      v.setUint16(22,1,true); v.setUint32(24,sr,true);
      v.setUint32(28,sr*2,true); v.setUint16(32,2,true); v.setUint16(34,16,true);
      w(36,'data'); v.setUint32(40, n*2, true);
      var freqs = [523.25, 659.25, 783.99];
      for (var i=0; i<n; i++){
        var t = i/sr, sa = 0;
        for (var k=0;k<3;k++){
          var st = k*0.35;
          if (t>=st && t<st+0.6){
            var lo = t-st, env = Math.exp(-lo*5);
            sa += Math.sin(2*Math.PI*freqs[k]*lo)*env*0.3;
          }
        }
        var val = Math.max(-1, Math.min(1, sa));
        v.setInt16(44 + i*2, val*0x7FFF, true);
      }
      var b = new Uint8Array(buf), s = '';
      for (var j=0; j<b.length; j++) s += String.fromCharCode(b[j]);
      return 'data:audio/wav;base64,' + btoa(s);
    }

    function ensureAudioReady(){
      if (!silenceDataUri){ silenceDataUri = buildSilenceWavDataUri(); keepEl.src = silenceDataUri; keepEl.volume = 0.001; }
      if (!alarmDataUri){ alarmDataUri = buildAlarmWavDataUri(); alarmEl.src = alarmDataUri; alarmEl.volume = 0.7; }
    }

    // WebAudio fallback alarm — much more reliable than HTML5 Audio when
    // it comes to autoplay policies, because once the AudioContext is
    // resumed via a user gesture it stays unlocked for the whole document.
    var alarmCtx = null;
    function ensureAlarmCtx(){
      try {
        if (!alarmCtx) alarmCtx = new (window.AudioContext || window.webkitAudioContext)();
      } catch(e){}
      return alarmCtx;
    }
    function resumeAlarmCtx(){
      try {
        var c = ensureAlarmCtx();
        if (c && c.state === 'suspended' && c.resume) c.resume().catch(function(){});
      } catch(e){}
    }
    function playAlarmWebAudio(){
      try {
        var ctx = ensureAlarmCtx();
        if (!ctx) return false;
        if (ctx.state === 'suspended') {
          // Try to resume; if blocked the bell won't play but no-op is fine.
          ctx.resume().catch(function(){});
        }
        var now = ctx.currentTime;
        var freqs = [523.25, 659.25, 783.99]; // C5 E5 G5
        freqs.forEach(function(f, i){
          var osc = ctx.createOscillator();
          var g = ctx.createGain();
          osc.type = 'sine';
          osc.frequency.value = f;
          var t0 = now + i*0.5;
          g.gain.setValueAtTime(0, t0);
          g.gain.linearRampToValueAtTime(0.3, t0 + 0.05);
          g.gain.exponentialRampToValueAtTime(0.001, t0 + 1.5);
          osc.connect(g); g.connect(ctx.destination);
          osc.start(t0); osc.stop(t0 + 1.6);
        });
        return true;
      } catch(e){ return false; }
    }

    function startKeepalive(){
      try {
        ensureAudioReady();
        var p = keepEl.play();
        if (p && p.catch) p.catch(function(){});
      } catch(e){}
    }
    function stopKeepalive(){
      try { keepEl.pause(); keepEl.currentTime = 0; } catch(e){}
    }
    function playAlarm(){
      // Belt-and-suspenders: fire BOTH the WebAudio bell AND the HTML5 audio
      // sample so at least one of them produces sound regardless of which
      // unlock path the browser honored on this page.
      playAlarmWebAudio();
      try {
        ensureAudioReady();
        alarmEl.currentTime = 0;
        var p = alarmEl.play();
        if (p && p.catch) p.catch(function(){});
      } catch(e){}
    }
    function showNotif(title, body){
      try {
        if (typeof Notification === 'undefined' || Notification.permission !== 'granted') return;
        var n = new Notification(title, { body: body, tag: 'machreach-focus' });
        n.onclick = function(){ window.focus(); window.location='/student/focus'; n.close(); };
      } catch(e){}
    }

    // Re-prime audio on EVERY user gesture on EVERY page.
    // We don't remove the listeners after one trigger because the audio
    // context can become suspended again (e.g. after long idle).
    function primeOnGesture(){
      ensureAudioReady();
      // Resume WebAudio context (this is what actually unlocks the alarm).
      resumeAlarmCtx();
      // Briefly play+pause keepalive at audible volume to mark it as a
      // genuine user-initiated playback (mute trick is unreliable in Chrome).
      try {
        var prev = keepEl.volume;
        keepEl.volume = 0.001;
        var p = keepEl.play();
        if (p && p.then) p.then(function(){
          // If a session is active, leave it playing as the keepalive.
          var d = readState();
          if (!(d && d.active)) {
            keepEl.pause(); keepEl.currentTime = 0;
          }
          keepEl.volume = prev;
        }).catch(function(){ keepEl.volume = prev; });
      } catch(e){}
      // If a session is active, ensure keepalive is running.
      var d2 = readState();
      if (d2 && d2.active) startKeepalive();
      // Request notification permission once.
      if (typeof Notification !== 'undefined' && Notification.permission === 'default'){
        try { Notification.requestPermission().catch(function(){}); } catch(e){}
      }
    }
    window.addEventListener('click', primeOnGesture, true);
    window.addEventListener('keydown', primeOnGesture, true);
    window.addEventListener('touchstart', primeOnGesture, true);

    function readState(){
      try { return JSON.parse(localStorage.getItem('focus_float')||'null'); } catch(e){ return null; }
    }
    function writeState(s){
      try { localStorage.setItem('focus_float', JSON.stringify(s)); } catch(e){}
    }
    function markPhaseSaved(id){
      try {
        var arr = JSON.parse(localStorage.getItem('focus_saved_phases')||'[]');
        if (arr.indexOf(id) === -1) arr.push(id);
        if (arr.length > 200) arr = arr.slice(-200);
        localStorage.setItem('focus_saved_phases', JSON.stringify(arr));
      } catch(e){}
    }
    function isPhaseSaved(id){
      try {
        var arr = JSON.parse(localStorage.getItem('focus_saved_phases')||'[]');
        return arr.indexOf(id) !== -1;
      } catch(e){ return false; }
    }

    var phaseRegistering = {};
    function registerFloatingPhaseStart(d, done){
      if (!d || d.isBreak || !d.workMinutes || !d.phaseId) { done(true); return; }
      if (d.serverRegistered) { done(true); return; }
      if (phaseRegistering[d.phaseId]) return;
      phaseRegistering[d.phaseId] = true;
      fetch('/api/student/focus/phase/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({
          phase_id: d.phaseId,
          mode: d.originalMode || 'pomodoro',
          expected_minutes: d.workMinutes || 0,
          course_id: d.courseId || null,
          exam_id: d.examId || null
        })
      }).then(function(resp){
        return resp.json().catch(function(){ return null; }).then(function(data){
          var ok = resp.ok && data && data.ok;
          if (ok) d.serverRegistered = true;
          done(!!ok);
        });
      }).catch(function(){
        done(false);
      }).finally(function(){
        delete phaseRegistering[d.phaseId];
      });
    }

    function advanceToPhase(np){
      if (!np) return;
      if (np.endAt && np.endAt > Date.now()){
        writeState(np);
      } else {
        var dur = (np.workMinutes && np.workMinutes>0) ? np.workMinutes*60*1000 : 5*60*1000;
        np.endAt = Date.now() + dur;
        writeState(np);
      }
      startKeepalive();
    }

    function creditPhase(d){
      // d: a focus_float phase object that just ended.
      // New model: phases are NOT saved automatically. They accumulate in
      // `focus_pending_phases` and the user must click "Reclamar" on
      // /student/focus to actually post them. Anti-cheat: every 4th work
      // phase opens a 30-min mandatory claim window enforced by that page.
      if (!d || !d.phaseId) return;
      if (isPhaseSaved(d.phaseId)) return;
      if (!d.workMinutes || d.workMinutes <= 0) return; // breaks don't credit
      if (d.workMinutes > 480) return;
      markPhaseSaved(d.phaseId);
      try {
        var arr = JSON.parse(localStorage.getItem('focus_pending_phases')||'[]');
        if (!Array.isArray(arr)) arr = [];
        arr.push({
          minutes: d.workMinutes,
          courseId: d.courseId || null,
          examId: d.examId || null,
          courseName: d.course || '',
          mode: d.originalMode || 'pomodoro',
          ts: Date.now(),
          phaseId: d.phaseId
        });
        localStorage.setItem('focus_pending_phases', JSON.stringify(arr));
      } catch(e){}
    }

    var phaseEndedFlag = {}; // {phaseId: true}
    function tick(){
      var d = readState();
      if (!d || !d.active){
        widget.style.display = 'none';
        stopKeepalive();
        return;
      }

      // Abandonment guard. If the saved focus_float is more than 12h old
      // (e.g. user closed the browser overnight, started a session days ago
      // and never returned, etc.) DO NOT credit it. Crediting a stale phase
      // is exactly how phantom hours appeared on real users' dashboards.
      // Clear the state silently so it can't auto-fire on the next tick.
      var ABANDON_MS = 12 * 60 * 60 * 1000;
      var refTs = 0;
      if (d.mode === 'stopwatch' && d.startAt) {
        refTs = d.startAt;
      } else if (d.mode === 'countdown' && d.endAt) {
        var w = (d.workMinutes && d.workMinutes > 0) ? d.workMinutes : 25;
        refTs = d.endAt - w * 60 * 1000;
      }
      if (refTs && (Date.now() - refTs) > ABANDON_MS) {
        try { localStorage.removeItem('focus_float'); } catch(e) {}
        widget.style.display = 'none';
        stopKeepalive();
        return;
      }

      widget.style.display = 'block';
      // Make sure keepalive stays running (browser may have paused it).
      if (keepEl && keepEl.paused) startKeepalive();

      if (d.mode === 'countdown'){
        var left = d.endAt - Date.now();
        if (left < 0) left = 0;
        var m = Math.floor(left/60000), s = Math.floor((left%60000)/1000);
        var t = document.getElementById('ff-time');
        var l = document.getElementById('ff-label');
        if (t) t.textContent = String(m).padStart(2,'0')+':'+String(s).padStart(2,'0');
        if (l) l.textContent = d.label || 'Focus';
        if (left <= 0 && d.phaseId && !phaseEndedFlag[d.phaseId]){
          phaseEndedFlag[d.phaseId] = true;
          // When the student is actively on /student/focus, the page-level
          // controller owns credit + audio + chain advancement. If we ALSO
          // credit/advance here we race with it: this widget calls
          // markPhaseSaved + writeState(nextPhase), and the page-level
          // saveFocusSession then reads the FRESH focus_float.phaseId, marks
          // the next-phase id as saved, and the actual next phase's save is
          // blocked by the dedupe ("only the first session counts" bug).
          var onFocusPage = (typeof window !== 'undefined' && window.location && window.location.pathname === '/student/focus');
          if (onFocusPage) return;
          // Credit XP for work phases.
          creditPhase(d);
          // Audible + visual alert (works in background because keepalive kept us unthrottled).
          playAlarm();
          if (d.workMinutes > 0){
            showNotif('Sesión de focus completada', 'Time for a break!');
          } else {
            showNotif('Break over', 'Back to focus!');
          }
          // Advance to next phase or end.
          // Stop the chain at the long-break boundary: the focus page owns
          // the mandatory 30-min claim window. Detect via the chained label
          // ("Descanso largo" / "Long Break"), which is set by the focus page.
          var isLongBreakNext = !!(d.nextPhase && d.nextPhase.label &&
            (d.nextPhase.label.indexOf('largo') !== -1 || d.nextPhase.label.indexOf('Long') !== -1));
          if (isLongBreakNext && d.workMinutes > 0){
            try { localStorage.setItem('focus_mandatory_until', String(Date.now() + 30*60*1000)); } catch(e){}
            d.active = false;
            writeState(d);
            widget.style.display = 'none';
            stopKeepalive();
            showNotif('¡Reclama tu descanso largo!', 'Tienes 30 min para reclamar tus recompensas en el Modo Enfoque.');
          } else if (d.nextPhase){
            // Re-base nextPhase.endAt off NOW so a long pause doesn't make it instantly expire.
            var np = d.nextPhase;
            // The original endAt was relative to the previous phase's endAt; preserve duration.
            // We don't know the duration directly — recompute from workMinutes (work) or label (best-effort 5min default for break is wrong).
            // Safer: nextPhase.endAt was already absolute; if it's already in the past, just skip ahead.
            registerFloatingPhaseStart(np, function(ok){
              if (!ok) {
                d.active = false;
                writeState(d);
                widget.style.display = 'none';
                stopKeepalive();
                showNotif('No se pudo verificar la sesiÃ³n', 'Vuelve a Modo Enfoque para continuar.');
                return;
              }
              advanceToPhase(np);
            });
          } else {
            d.active = false;
            writeState(d);
            widget.style.display = 'none';
            stopKeepalive();
          }
        }
      } else {
        // stopwatch
        var elapsed = Math.floor((Date.now()-d.startAt)/1000);
        var m2 = Math.floor(elapsed/60), s2 = elapsed%60;
        var t2 = document.getElementById('ff-time');
        var l2 = document.getElementById('ff-label');
        if (t2) t2.textContent = String(m2).padStart(2,'0')+':'+String(s2).padStart(2,'0');
        if (l2) l2.textContent = d.label || 'Reading';
      }
    }

    // Boot: if a session is already active when this page loads, start keepalive immediately.
    var initial = readState();
    if (initial && initial.active){
      ensureAudioReady();
      startKeepalive();
      widget.style.display = 'block';
    }

    setInterval(tick, 1000);
    tick();

    window.addEventListener('storage', function(e){ if (e.key === 'focus_float') tick(); });
  })();

  function closeFocusFloat(){
    try { localStorage.removeItem('focus_float'); } catch(e){}
    var el = document.getElementById('focus-float');
    if (el) el.style.display='none';
    var k = document.getElementById('focus-keepalive');
    if (k){ try { k.pause(); k.currentTime = 0; } catch(e){} }
  }
