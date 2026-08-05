/* Focus page sections */
const SCENES = [
  { id: "silence", ic: "🤫", label: "Silencio" },
  { id: "rain", ic: "🌧", label: "Lluvia" },
  { id: "ocean", ic: "🌊", label: "Océano" },
  { id: "forest", ic: "🌲", label: "Bosque" },
  { id: "fire", ic: "🔥", label: "Fuego" },
  { id: "night", ic: "🌙", label: "Noche" },
];
const FX_COURSES = ["Cálculo II", "Termodinámica", "Química Orgánica", "Álgebra Lineal"];
const FX_EXAMS = { "Cálculo II": ["Prueba 2 — 4 ago", "Examen — 12 sep"], "Termodinámica": ["Control 3 — 10 ago"], "Química Orgánica": ["Informe de laboratorio — 13 ago"], "Álgebra Lineal": ["Examen — 26 ago"] };

function pad(n) { return String(n).padStart(2, "0"); }

/* ---- Focus Guard: no extension, no session ----------------------------
   The extension's content script stamps data-machreach-focus-guard on <html>
   and answers a ping event. We clear the marker before every check so an
   uninstalled extension cannot leave a stale "installed" attribute behind. */
const FOCUS_GUARD_STORE_URL = "https://chromewebstore.google.com/detail/djfnmpaihpkibcngaaekhnbalbaibgnk";

function focusGuardInstalled() {
  return document.documentElement.getAttribute("data-machreach-focus-guard") === "1";
}

function checkFocusGuard() {
  return new Promise((resolve) => {
    let settled = false;
    let timer = 0;
    const finish = (ok) => {
      if (settled) return;
      settled = true;
      document.removeEventListener("machreach-focus-guard-ready", onReady);
      clearTimeout(timer);
      resolve(!!ok);
    };
    const onReady = () => finish(focusGuardInstalled());
    try { document.documentElement.removeAttribute("data-machreach-focus-guard"); } catch (e) { /* ignore */ }
    document.addEventListener("machreach-focus-guard-ready", onReady);
    try { document.dispatchEvent(new CustomEvent("machreach-focus-guard-ping")); } catch (e) { /* ignore */ }
    timer = setTimeout(() => finish(false), 450);
  });
}

function FocusGuardModal({ onClose }) {
  return (
    <Modal title="Necesitas Focus Guard" sub="Instala la extensión gratuita de MachReach para iniciar una sesión. Bloquea los sitios que te distraen mientras estudias."
      onClose={onClose}
      foot={<>
        <button className="btn btn-ghost btn-sm" onClick={() => location.reload()}>Ya la instalé — recargar</button>
        <a className="btn btn-primary btn-sm" href={FOCUS_GUARD_STORE_URL} target="_blank" rel="noopener">Agregar a Chrome</a>
      </>}>
      <div className="mdl-warn">
        <IconShield size={20} />
        <div>Sin la extensión no podemos bloquear distracciones ni verificar el tiempo que estudiaste, así que el bloque no se puede iniciar.</div>
      </div>
    </Modal>
  );
}

function FocusHead({ scene, data = {} }) {
  const stats = data.stats || {};
  const live = !!data.live;
  return (
    <section className="fx-head">
      <div>
        <span className="pill">{live ? (data.status_label || "Listo para comenzar") : "Sesión en curso · Cálculo II"}</span>
        <h1 style={{ marginTop: 14 }}>Enfoque</h1>
        <p>Mostrando lo que has estudiado <b style={{ color: "var(--ink)" }}>hoy</b> · {live ? (data.today_label || "hoy") : "martes 4 de agosto"}</p>
      </div>
      <div className="fx-head-stats">
        <div className="fx-hs"><div className="n num">{live ? `${Number(stats.total_minutes || 0)}m` : "58m"}</div><div className="l">Hoy</div></div>
        <div className="fx-hs"><div className="n num">{live ? Number(stats.sessions || 0) : 2}</div><div className="l">Sesiones</div></div>
        <div className="fx-hs"><div className="n num">{live ? Number(stats.streak_days || 0) : 17}</div><div className="l">Racha</div></div>
        <div className="fx-hs"><div className="n num">{live ? Number(stats.xp_today || 0) : 215}</div><div className="l">XP hoy</div></div>
      </div>
    </section>
  );
}

function FocusNotes({ data = {} }) {
  if (data.live) {
    const exam = data.next_exam;
    return (
      <div className="fx-strip">
        <div className="fx-note rival">
          <span className="ico-badge" style={{ background: "var(--surface)" }}><IconFire size={17} color="var(--brand)" /></span>
          <div><div className="eye">Tu ritmo de hoy</div><div className="t">{Number(data.stats?.total_minutes || 0)} minutos de enfoque</div><div className="s">Cada bloque terminado se guarda en tu cuenta</div></div>
        </div>
        <div className="fx-note exam">
          <span className="ico-badge" style={{ background: "var(--surface)" }}><IconStar size={17} color="var(--plum)" /></span>
          <div><div className="eye">Próxima prueba</div><div className="t">{exam ? `${exam.name} — ${exam.course}` : "Sin evaluaciones próximas"}</div><div className="s">{exam ? `${exam.date} · ${exam.weight}% de la nota` : "Agrégala en Cursos para enfocar tu sesión"}</div></div>
          {exam && <div className="big num">{exam.days}<small>días</small></div>}
        </div>
      </div>
    );
  }
  return (
    <div className="fx-strip">
      <div className="fx-note rival">
        <span className="ico-badge" style={{ background: "var(--surface)" }}><IconFire size={17} color="var(--brand)" /></span>
        <div>
          <div className="eye">Rival del día</div>
          <div className="t">Camila te lleva 34 minutos</div>
          <div className="s">Un bloque de 35 min y la pasas</div>
        </div>
        <div className="big num">92m<small>de ella</small></div>
      </div>
      <div className="fx-note exam">
        <span className="ico-badge" style={{ background: "var(--surface)" }}><IconStar size={17} color="var(--plum)" /></span>
        <div>
          <div className="eye">Próxima prueba</div>
          <div className="t">Prueba 2 — Cálculo II</div>
          <div className="s">Series de Taylor · Integrales impropias</div>
        </div>
        <div className="big num">3<small>días</small></div>
      </div>
    </div>
  );
}

/* ---- Sound ------------------------------------------------------------
   Ambience uses the recorded loops where MachReach ships one and falls back
   to filtered noise for the rest, so every scene makes a sound. Both the
   loops and the chime run through one gain node the volume slider owns. */
const AMB_FILES = {
  rain: "/static/audio/rain.mp3",
  ocean: "/static/audio/beach.mp3",
  forest: "/static/audio/forest.mp3",
  fire: "/static/audio/fire-crackling.mp3",
};
const AMB_NOISE = {
  night: { freq: 320, q: 0.8, kind: "lowpass", smooth: 0.97 },
};

const focusAudio = {
  el: null,
  ctx: null,
  gain: null,
  nodes: [],
  volume: 0.45,
  _element() {
    if (!this.el) {
      this.el = document.createElement("audio");
      this.el.loop = true;
      this.el.preload = "none";
    }
    return this.el;
  },
  _context() {
    if (!this.ctx) {
      const Ctor = window.AudioContext || window.webkitAudioContext;
      if (!Ctor) return null;
      this.ctx = new Ctor();
    }
    // Browsers suspend the context until a gesture; every call site is a click.
    if (this.ctx.state === "suspended") this.ctx.resume().catch(() => {});
    return this.ctx;
  },
  setVolume(pct) {
    this.volume = Math.max(0, Math.min(1, pct / 100));
    if (this.el) this.el.volume = this.volume;
    if (this.gain) this.gain.gain.value = this.volume * 0.06;
  },
  stop() {
    if (this.el) { try { this.el.pause(); this.el.currentTime = 0; } catch (e) { /* ignore */ } }
    this.nodes.forEach((node) => { try { node.stop ? node.stop() : node.disconnect(); } catch (e) { /* ignore */ } });
    this.nodes = [];
    if (this.gain) { try { this.gain.disconnect(); } catch (e) { /* ignore */ } this.gain = null; }
  },
  play(scene) {
    this.stop();
    if (!scene || scene === "silence") return;
    const file = AMB_FILES[scene];
    if (file) {
      const el = this._element();
      el.src = file;
      el.volume = this.volume;
      const started = el.play();
      if (started && started.catch) started.catch(() => {});
      return;
    }
    const recipe = AMB_NOISE[scene];
    const ctx = this._context();
    if (!recipe || !ctx) return;
    const gain = ctx.createGain();
    gain.gain.value = this.volume * 0.06;
    gain.connect(ctx.destination);
    this.gain = gain;
    const filter = ctx.createBiquadFilter();
    filter.type = recipe.kind;
    filter.frequency.value = recipe.freq;
    filter.Q.value = recipe.q;
    filter.connect(gain);
    const buffer = ctx.createBuffer(1, ctx.sampleRate * 4, ctx.sampleRate);
    const data = buffer.getChannelData(0);
    let last = 0;
    for (let i = 0; i < data.length; i += 1) {
      last = last * recipe.smooth + (Math.random() * 2 - 1) * (1 - recipe.smooth);
      data[i] = last;
    }
    const source = ctx.createBufferSource();
    source.buffer = buffer;
    source.loop = true;
    source.connect(filter);
    source.start();
    this.nodes.push(source, filter);
  },
  /* A short jingle, not a beep: a rising motif with a plucked attack, played
     twice over (melody + a softer octave) so it carries across a room. The
     long break gets a longer, resolving phrase so the two are never confused. */
  jingle(kind = "work") {
    const ctx = this._context();
    if (!ctx) return;
    // C5 E5 G5 C6 for a finished block; the long break lands on a full
    // cadence and ends held, which reads as "session over".
    const motif = kind === "long"
      ? [[523.25, 0], [659.25, 0.14], [783.99, 0.28], [1046.5, 0.42], [783.99, 0.60], [1046.5, 0.74], [1318.5, 0.92]]
      : kind === "break"
        ? [[783.99, 0], [659.25, 0.13], [523.25, 0.26]]
        : [[523.25, 0], [659.25, 0.12], [783.99, 0.24], [1046.5, 0.38]];
    const hold = kind === "long" ? 1.5 : 0.5;
    const level = Math.max(0.06, this.volume) * 0.5;
    motif.forEach(([freq, offset], index) => {
      const last = index === motif.length - 1;
      const at = ctx.currentTime + offset;
      const decay = last ? hold : 0.34;
      // Two voices: a triangle carries the tune, a quiet sine an octave below
      // gives it body without turning it into a beep.
      [[freq, "triangle", 1], [freq / 2, "sine", 0.35]].forEach(([tone, shape, mix]) => {
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = shape;
        osc.frequency.setValueAtTime(tone, at);
        gain.gain.setValueAtTime(0.0001, at);
        gain.gain.exponentialRampToValueAtTime(level * mix, at + 0.012);
        gain.gain.exponentialRampToValueAtTime(0.0001, at + decay);
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.start(at);
        osc.stop(at + decay + 0.05);
      });
    });
  },
  bell(kind = "work") { this.jingle(kind); },
  chime(kind = "work") { this.bell(kind); },
};

/* ---- Timer that survives leaving the page ------------------------------
   The block is stored as an absolute end time, so it keeps running while the
   student is on another MachReach page (or the tab is throttled) and picks up
   exactly where it should when they come back. */
const FOCUS_MODES = { pomodoro: { work: 25 * 60, brk: 5 * 60, rounds: 4 }, custom: { work: 50 * 60, brk: 10 * 60, rounds: 2 } };
const FOCUS_STORE = "mr_focus_timer_v1";
// Time to claim once the long break starts, after which the XP is forfeit.
const CLAIM_WINDOW_MS = 30 * 60 * 1000;

/* Telling "moved to another MachReach page" apart from "closed the app".
   The verdict itself lives in mrFocusPresence (shared with shell.jsx through
   `window` — same bundle, first module to run takes the one clean reading).
   The heartbeat stamp survives as its legacy fallback and as the tell for a
   sessionStorage marker the browser resurrected long after the app closed. */
const FOCUS_ALIVE = "mr_focus_alive_v1";
const FOCUS_HEARTBEAT_MS = 3000;

function touchFocusHeartbeat() {
  try { localStorage.setItem(FOCUS_ALIVE, String(Date.now())); } catch (e) { /* private mode */ }
}

/* Duplicate of shell.jsx's mrFocusPresence, kept in both files because either
   module may load first; the window guard makes the second copy a no-op. */
function mrFocusPresence() {
  if (window.__mrFocusPresence) return window.__mrFocusPresence;
  const state = { hadTab: true, known: true, abandoned: false, channel: null, verdict: null };
  window.__mrFocusPresence = state;
  try {
    state.hadTab = sessionStorage.getItem("mr_focus_tab_v1") === "1";
    sessionStorage.setItem("mr_focus_tab_v1", "1");
  } catch (e) { state.hadTab = true; }   // cannot tell: never wipe a block on a guess
  let store = null;
  try { store = JSON.parse(localStorage.getItem("mr_focus_timer_v1") || "null"); } catch (e) { store = null; }
  const heartbeatStale = () => {
    try {
      const last = Number(localStorage.getItem(FOCUS_ALIVE) || 0);
      return !!last && Date.now() - last > 15000;
    } catch (e) { return false; }
  };
  if (typeof BroadcastChannel !== "undefined") {
    try {
      state.channel = new BroadcastChannel("mr_focus_presence_v1");
      state.channel.addEventListener("message", (event) => {
        if (event.data === "ping") state.channel.postMessage("pong");
      });
    } catch (e) { state.channel = null; }
  }
  const running = !!(store && store.running);
  if (!running || state.hadTab) {
    state.abandoned = running && state.hadTab && heartbeatStale();
    state.verdict = Promise.resolve(state.abandoned);
    return state;
  }
  state.known = false;
  state.verdict = new Promise((resolve) => {
    const settle = (abandoned) => { state.known = true; state.abandoned = abandoned; resolve(abandoned); };
    if (!state.channel) return settle(heartbeatStale());
    const timer = setTimeout(() => settle(true), 350);
    state.channel.addEventListener("message", (event) => {
      if (event.data === "pong") { clearTimeout(timer); settle(false); }
    });
    state.channel.postMessage("ping");
  });
  return state;
}

function focusAbandoned() {
  const presence = mrFocusPresence();
  return presence.known && presence.abandoned;
}

/* Every page that can show the timer keeps the stamp fresh. */
function useFocusHeartbeat(enabled = true) {
  React.useEffect(() => {
    if (!enabled) return undefined;
    touchFocusHeartbeat();
    const beat = setInterval(touchFocusHeartbeat, FOCUS_HEARTBEAT_MS);
    return () => clearInterval(beat);
  }, [enabled]);
}

function readFocusStore() {
  try { return JSON.parse(localStorage.getItem(FOCUS_STORE) || "null"); } catch (e) { return null; }
}
function writeFocusStore(state) {
  try {
    if (state) localStorage.setItem(FOCUS_STORE, JSON.stringify(state));
    else localStorage.removeItem(FOCUS_STORE);
  } catch (e) { /* private mode */ }
}

/* Replay whatever elapsed while the page was gone and return the state the
   timer should resume in. `finishedPhaseId` is a work block that completed
   away from the page and still owes the server its minutes. */
function restoreFocusTimer() {
  const stored = readFocusStore();
  if (!stored || !stored.mode || !FOCUS_MODES[stored.mode]) return null;
  const cfg = FOCUS_MODES[stored.mode];
  const presence = mrFocusPresence();
  if (stored.running && (!presence.known || presence.abandoned)) {
    // The app was closed with a block running (or the verdict is still out —
    // a fresh tab waiting up to 350ms for another tab to answer). The block
    // does not survive a close: the timer comes back reset, and none of the
    // away time is credited. Blocks that had already finished stay claimable —
    // they were earned. `awaitingVerdict` tells the Timer to hold off writing
    // this reset down and to adopt the block if a live tab does answer.
    return {
      mode: stored.mode,
      courseId: stored.courseId || "",
      examId: stored.examId || "",
      phaseId: "",
      finishedPhaseId: "",
      pending: Array.isArray(stored.pending) ? stored.pending : [],
      claimUntil: Number(stored.claimUntil) || 0,
      phase: "work",
      round: 1,
      left: cfg.work,
      endsAt: 0,
      running: false,
      awaitingVerdict: !presence.known,
    };
  }
  const base = {
    mode: stored.mode,
    courseId: stored.courseId || "",
    examId: stored.examId || "",
    phaseId: stored.phaseId || "",
    finishedPhaseId: "",
    pending: Array.isArray(stored.pending) ? stored.pending : [],
    claimUntil: Number(stored.claimUntil) || 0,
  };
  if (!stored.running) {
    return { ...base, phase: stored.phase || "work", round: stored.round || 1,
             left: Math.max(0, Number(stored.left) || 0), endsAt: 0, running: false };
  }
  let phase = stored.phase === "break" ? "break" : "work";
  let round = Number(stored.round) || 1;
  let endsAt = Number(stored.endsAt) || 0;
  let finishedPhaseId = "";
  let guard = 0;
  while (endsAt && endsAt <= Date.now() && guard < 64) {
    guard += 1;
    if (phase === "work") {
      finishedPhaseId = finishedPhaseId || base.phaseId;
      base.phaseId = "";
      phase = "break";
      endsAt += cfg.brk * 1000;
    } else {
      phase = "work";
      round = round >= cfg.rounds ? 1 : round + 1;
      endsAt += cfg.work * 1000;
    }
  }
  return {
    ...base, phase, round, endsAt, running: true, finishedPhaseId,
    left: Math.max(0, Math.ceil((endsAt - Date.now()) / 1000)),
  };
}

/* Counts the long-break window down: unclaimed XP is gone when it hits zero. */
function ClaimCountdown({ until }) {
  const [left, setLeft] = React.useState(Math.max(0, Math.ceil((until - Date.now()) / 1000)));
  React.useEffect(() => {
    const timer = setInterval(() => setLeft(Math.max(0, Math.ceil((until - Date.now()) / 1000))), 1000);
    return () => clearInterval(timer);
  }, [until]);
  return <React.Fragment>Te quedan {pad(Math.floor(left / 60))}:{pad(left % 60)} para reclamarlo o lo pierdes.</React.Fragment>;
}

function Timer({ scene, onRun, onCourse, onReward, data = {} }) {
  const MODES = FOCUS_MODES;
  // The stored block is read against the page-load presence verdict; a fresh
  // tab may still be waiting for it (see `awaitingVerdict` below).
  const restored = React.useRef(data.live ? restoreFocusTimer() : null).current;
  useFocusHeartbeat(!!data.live);
  const [mode, setMode] = React.useState(restored?.mode || "pomodoro");
  const [phase, setPhase] = React.useState(restored?.phase || "work");
  const [round, setRound] = React.useState(restored?.round || 1);
  const [running, setRunning] = React.useState(!!restored?.running);
  React.useEffect(() => { onRun && onRun(running); }, [running]);
  const cfg = MODES[mode];
  const total = phase === "work" ? cfg.work : cfg.brk;
  const [left, setLeft] = React.useState(restored ? restored.left : cfg.work);
  const [endsAt, setEndsAt] = React.useState(restored?.endsAt || 0);
  const [pending, setPending] = React.useState(restored?.pending || []);
  const [claimUntil, setClaimUntil] = React.useState(restored?.claimUntil || 0);
  // Blocks whose claim window ran out. Shown flat, not celebrated.
  const [lostBlocks, setLostBlocks] = React.useState(0);
  // The running claim animation. Held here rather than in the panel so that
  // clearing `pending` — which the claim itself does — cannot unmount it.
  const [claimFlow, setClaimFlow] = React.useState(null);
  const [claiming, setClaiming] = React.useState(false);
  const courses = data.courses?.length ? data.courses : FX_COURSES.map((name, i) => ({ id: i + 1, name, exams: (FX_EXAMS[name] || []).map((name, j) => ({ id: j + 1, name })) }));
  const [courseId, setCourseId] = React.useState(restored?.courseId || courses[0]?.id || "");
  const course = courses.find((c) => String(c.id) === String(courseId)) || courses[0];
  const [examId, setExamId] = React.useState(restored?.examId || "");
  const phaseId = React.useRef(restored?.phaseId || "");
  React.useEffect(() => { onCourse && onCourse(course?.id || ""); }, [course?.id]);
  // A fresh tab that found a running block waits (350ms at most) for another
  // tab to claim it is alive. Until then this Timer shows the reset state but
  // must not persist it — that would destroy the very block being decided on.
  const [verdictPending, setVerdictPending] = React.useState(!!restored?.awaitingVerdict);
  React.useEffect(() => {
    if (!restored?.awaitingVerdict) return undefined;
    let on = true;
    mrFocusPresence().verdict.then((abandoned) => {
      if (!on) return;
      setVerdictPending(false);
      if (abandoned) return;
      // Another MachReach tab is alive: the app was never closed. Adopt the
      // block exactly where the replay says it should be by now.
      const revived = restoreFocusTimer();
      if (!revived || !revived.running) return;
      setPhase(revived.phase);
      setRound(revived.round);
      setLeft(revived.left);
      setEndsAt(revived.endsAt);
      setRunning(true);
      phaseId.current = revived.phaseId || "";
      if (revived.finishedPhaseId) queuePhase(revived.finishedPhaseId);
    });
    return () => { on = false; };
  }, []);

  const beginVerifiedPhase = async () => {
    if (!data.live || phase !== "work") return true;
    const id = "focus-" + Date.now() + "-" + Math.random().toString(36).slice(2, 9);
    const response = await fetch("/api/student/focus/phase/start", { method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": (window.__MACHREACH_APP__ || {}).csrf || "" }, body: JSON.stringify({ phase_id: id, mode, expected_minutes: Math.round(cfg.work / 60), course_id: course?.id, exam_id: examId || null }) });
    const body = await response.json();
    if (!response.ok) { alert(body.error || "No se pudo iniciar la sesión."); return false; }
    phaseId.current = id;
    return true;
  };
  // A finished block is not credited on its own: it waits in `pending` until
  // the student claims it. That is what makes the long-break deadline mean
  // anything — nothing is banked automatically overnight.
  const queuePhase = (id) => {
    if (!data.live || !id || !course) return;
    setPending((current) => (current.some((p) => p.phase_id === id) ? current : [...current, {
      phase_id: id,
      mode,
      minutes: Math.round(cfg.work / 60),
      course_id: course.id,
      course_name: course.name,
      exam_id: examId || null,
    }]));
  };
  const saveVerifiedPhase = () => {
    const id = phaseId.current;
    phaseId.current = "";
    queuePhase(id);
  };

  // A block that ran out while the student was on another page is still theirs
  // to claim when they come back.
  React.useEffect(() => {
    if (restored?.finishedPhaseId) queuePhase(restored.finishedPhaseId);
  }, []);

  const skipModeReset = React.useRef(true);
  React.useEffect(() => {
    if (skipModeReset.current) { skipModeReset.current = false; return; }
    setRunning(false); setPhase("work"); setRound(1); setLeft(MODES[mode].work); setEndsAt(0);
    writeFocusStore(null);
  }, [mode]);

  // Ticks off the wall clock rather than counting intervals, so a throttled or
  // backgrounded tab cannot make the block last longer than it should.
  const advancing = React.useRef(false);
  React.useEffect(() => {
    if (!running || !endsAt) return undefined;
    const tick = () => {
      const remaining = Math.max(0, Math.ceil((endsAt - Date.now()) / 1000));
      setLeft(remaining);
      if (remaining > 0 || advancing.current) return;
      advancing.current = true;
      if (phase === "work") {
        // The last block of the cycle opens the long break, and with it the
        // 30-minute window to claim everything earned in this session.
        const lastOfCycle = round >= cfg.rounds;
        focusAudio.bell(lastOfCycle ? "long" : "work");
        if (lastOfCycle) setClaimUntil(Date.now() + CLAIM_WINDOW_MS);
        void saveVerifiedPhase();
        setPhase("break");
        setEndsAt(Date.now() + cfg.brk * 1000);
        setLeft(cfg.brk);
      } else {
        focusAudio.bell("break");
        setPhase("work");
        setRound((r) => (r >= cfg.rounds ? 1 : r + 1));
        setEndsAt(Date.now() + cfg.work * 1000);
        setLeft(cfg.work);
      }
      advancing.current = false;
    };
    tick();
    const t = setInterval(tick, 250);
    return () => clearInterval(t);
  }, [running, phase, mode, endsAt]);

  React.useEffect(() => {
    if (running && phase === "work" && !phaseId.current) void beginVerifiedPhase();
  }, [running, phase, round]);

  // Persist after every change so another page (or a reload) sees the block.
  React.useEffect(() => {
    if (!data.live || verdictPending) return;
    if (!running && !endsAt && !pending.length && left === MODES[mode].work && phase === "work" && round === 1) {
      writeFocusStore(null);
      return;
    }
    writeFocusStore({ mode, phase, round, running, left, endsAt, courseId, examId, phaseId: phaseId.current, pending, claimUntil });
  }, [mode, phase, round, running, left, endsAt, courseId, examId, pending, claimUntil, verdictPending]);

  const pct = 1 - left / total;
  const R = 132, C = 2 * Math.PI * R;
  const [kick, setKick] = React.useState(0);
  const [guardWanted, setGuardWanted] = React.useState(false);
  /* Returns the totals the server actually banked, so the claim animation can
     roll those and nothing else. Null means it failed and the user was told. */
  const claimPending = async () => {
    if (!pending.length || claiming) return null;
    setClaiming(true);
    try {
      const response = await fetch("/api/student/focus/claim", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": (window.__MACHREACH_APP__ || {}).csrf || "" },
        body: JSON.stringify({ phases: pending, course_id: course?.id, course_name: course?.name }),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok || body.ok === false) throw new Error(body.error || "No se pudo reclamar el XP.");
      const claimed = {
        minutes: Number(body.minutes_saved || 0),
        xp: Number(body.xp_awarded || 0),
        coins: Number(body.coins_awarded || 0),
        blocks: Number(body.saved_count || pending.length),
        streak: Number((body.stats || {}).streak_days || 0),
        stats: body.stats || null,
      };
      setPending([]);
      setClaimUntil(0);
      onReward && onReward(claimed);
      return claimed;
    } catch (error) {
      alert(error.message || "No se pudo reclamar el XP.");
      return null;
    } finally {
      setClaiming(false);
    }
  };

  /* Opens the celebration and fires the request together. The overlay plays
     its entrance while the server decides the totals, and holds on "Sumando…"
     if the answer is slow — so no figure is ever animated before it is real. */
  const startClaim = async () => {
    if (!pending.length || claiming) return;
    setClaimFlow({
      preview: {
        mins: pending.reduce((sum, p) => sum + (Number(p.minutes) || 0), 0),
        course: pending[0]?.course_name || course?.name || "Sin curso",
        rounds: pending.length,
      },
      result: null,
    });
    const claimed = await claimPending();
    if (!claimed) { setClaimFlow(null); return; }   // claimPending already told the user
    setClaimFlow((flow) => (flow ? { ...flow, result: claimed } : null));
  };

  // Unclaimed XP dies with the window. Without this an overnight session could
  // sit on a pile of blocks and bank them the next morning.
  React.useEffect(() => {
    if (!claimUntil || !pending.length) return undefined;
    const timer = setInterval(() => {
      if (Date.now() < claimUntil) return;
      setLostBlocks(pending.length);
      setPending([]);
      setClaimUntil(0);
      onReward && onReward({ expired: true, blocks: pending.length, minutes: 0, xp: 0, coins: 0 });
    }, 1000);
    return () => clearInterval(timer);
  }, [claimUntil, pending.length]);

  const reset = () => {
    setRunning(false); setPhase("work"); setRound(1); setLeft(cfg.work); setEndsAt(0);
    phaseId.current = "";
    writeFocusStore(null);
  };
  const toggle = async () => {
    // Focus Guard is mandatory: without it we can neither block distractions
    // nor trust the reported minutes, so no block may start or resume.
    if (!running && data.live && !(await checkFocusGuard())) return setGuardWanted(true);
    if (!running && left === cfg.work && phase === "work" && !(await beginVerifiedPhase())) return;
    setRunning((r) => {
      if (!r) { setKick((k) => k + 1); setEndsAt(Date.now() + Math.max(1, left) * 1000); }
      return !r;
    });
  };
  const ang = (-90 + pct * 360) * Math.PI / 180;
  const hx = 150 + R * Math.cos(ang), hy = 150 + R * Math.sin(ang);

  return (
    <section className={"timer" + (running ? " run" : "")}>
      <div className="fx-fields">
        <div className="fld">
          <label>Curso</label>
          <select value={courseId} onChange={(e) => { setCourseId(e.target.value); setExamId(""); }}>{courses.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select>
        </div>
        <div className="fld">
          <label>Preparando para</label>
          <select value={examId} onChange={(e) => setExamId(e.target.value)}><option value="">Sin evaluación</option>{(course?.exams || []).map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}</select>
        </div>
      </div>
      <div className="modes">
        <button className={mode === "pomodoro" ? "on" : ""} onClick={() => setMode("pomodoro")}>Pomodoro 25/5</button>
        <button className={mode === "custom" ? "on" : ""} onClick={() => setMode("custom")}>Bloque 50/10</button>
      </div>
      <div className="ring-wrap">
        {kick > 0 && <i className="shock" key={kick} />}
        <svg viewBox="0 0 300 300">
          <circle cx="150" cy="150" r={R} fill="none" stroke="var(--paper-2)" strokeWidth="16" />
          <circle cx="150" cy="150" r={R} fill="none" stroke="var(--ink)" strokeWidth="20" opacity=".12" />
          <circle cx="150" cy="150" r={R} fill="none" stroke={phase === "work" ? "var(--scene)" : "var(--good)"} strokeWidth="16" strokeLinecap="round"
            strokeDasharray={C} strokeDashoffset={C * (1 - pct)} transform="rotate(-90 150 150)" style={{ transition: "stroke-dashoffset .8s linear, stroke .4s" }} />
          {running && <circle className="head" cx={hx} cy={hy} r="9" fill={phase === "work" ? "var(--scene)" : "var(--good)"} stroke="var(--ink)" strokeWidth="2.5" />}
        </svg>
        <div className="ring-time" key={kick}>
          <div className="tt">{pad(Math.floor(left / 60))}:{pad(left % 60)}</div>
          <div className="tl">{phase === "work" ? "Bloque de enfoque" : "Descanso"}</div>
        </div>
      </div>
      <div className="orbit">
        {Array.from({ length: cfg.rounds }).map((_, i) => (
          <i key={i} className={i + 1 < round ? "done" : i + 1 === round ? "now" : ""} />
        ))}
      </div>
      <div className="fx-controls">
        <button className={"fx-start btn btn-lg " + (running ? "btn-ghost" : "btn-primary")} onClick={toggle}>
          {running ? <><IconClose size={17} /> Pausar</> : <><IconTimer size={19} /> {left === cfg.work && phase === "work" ? "Empezar" : "Reanudar"}</>}
        </button>
        <button className="btn btn-ghost btn-lg" onClick={reset}>Reiniciar</button>
      </div>
      {pending.length > 0 && (
        <ClaimPanel
          preview={{
            mins: pending.reduce((sum, p) => sum + (Number(p.minutes) || 0), 0),
            course: pending[0]?.course_name || course?.name || "Sin curso",
            rounds: pending.length,
          }}
          expiresAt={claimUntil}
          busy={claiming}
          onStart={startClaim}
        />
      )}
      {claimFlow && (
        <ClaimOverlay preview={claimFlow.preview} result={claimFlow.result}
          onClose={() => setClaimFlow(null)} />
      )}
      {lostBlocks > 0 && (
        <section className="clm lost">
          <div className="clm-hd">
            <div className="clm-txt">
              <div className="clm-eye">Ventana cerrada</div>
              <h3 className="clm-t">Perdiste el XP de {lostBlocks} {lostBlocks === 1 ? "bloque" : "bloques"}</h3>
              <p className="clm-s">Se acabó la ventana de 30 minutos del descanso largo.</p>
            </div>
          </div>
        </section>
      )}
      <div className="fx-status">Ronda {round} de {cfg.rounds} · {course?.name || "Sin curso"} · el tiempo se guarda al terminar el bloque</div>
      {guardWanted && <FocusGuardModal onClose={() => setGuardWanted(false)} />}
    </section>
  );
}

function Ambience({ scene, setScene }) {
  const [vol, setVol] = React.useState(() => {
    // Prerendered server-side, where localStorage does not exist.
    try {
      const stored = Number(localStorage.getItem("mr_ambience_volume"));
      return Number.isFinite(stored) && stored >= 0 && stored <= 100 ? stored : 45;
    } catch (e) { return 45; }
  });
  useAmbience(scene);
  // Audio only ever starts from the click that picks a scene — browsers block
  // playback that is not tied to a gesture.
  const pick = (id) => {
    setScene(id);
    focusAudio.setVolume(vol);
    focusAudio.play(id);
    try { localStorage.setItem("mr_ambience_sound", id); } catch (e) { /* private mode */ }
  };
  const changeVolume = (value) => {
    setVol(value);
    focusAudio.setVolume(value);
    try { localStorage.setItem("mr_ambience_volume", String(value)); } catch (e) { /* private mode */ }
  };
  React.useEffect(() => {
    focusAudio.setVolume(vol);
    return () => focusAudio.stop();
  }, []);
  return (
    <section className="pnl">
      <div className="pnl-h">
        <span className="ico-badge" style={{ background: "#DCEFF6" }}><IconSparkle size={17} /></span>
        <h3>Ambiente</h3>
      </div>
      <div className="scenes">
        {SCENES.map((s) => (
          <button key={s.id} className={"scene" + (scene === s.id ? " on" : "")} onClick={() => pick(s.id)}><span>{s.ic}</span>{s.label}</button>
        ))}
      </div>
      <div className="vol">
        <span className="mono">Volumen</span>
        <input type="range" min="0" max="100" value={vol} onChange={(e) => changeVolume(+e.target.value)} disabled={scene === "silence"} />
        <span className="mono num">{scene === "silence" ? "—" : vol + "%"}</span>
      </div>
    </section>
  );
}

/* What the finished block earned. The server already credits XP and coins on
   /focus/save — this is the receipt, which the React page never showed. */
/* Mirrors the extension's own rule (content.js): it blocks while a work block
   is running and stops the moment the student pauses or takes a break. */
function focusGuardBlocking() {
  const state = readFocusStore();
  if (!state) return false;
  return !!state.running && state.phase !== "break"
    && (!state.endsAt || Date.now() < Number(state.endsAt));
}

function Guard({ live = false }) {
  // null while we wait for the extension to answer the ping.
  const [installed, setInstalled] = React.useState(live ? null : true);
  // "Activo" has to mean sites are being blocked right now, not merely that
  // the extension exists — otherwise it reads as Activo all day.
  const [blocking, setBlocking] = React.useState(false);
  React.useEffect(() => {
    if (!live) { setBlocking(true); return undefined; }
    const tick = () => setBlocking(focusGuardBlocking());
    tick();
    const timer = setInterval(tick, 1000);
    return () => clearInterval(timer);
  }, [live]);
  React.useEffect(() => {
    if (!live) return undefined;
    let alive = true;
    let timer = 0;
    const check = () => checkFocusGuard().then((ok) => {
      if (!alive) return;
      setInstalled(ok);
      // Poll so disabling or removing the extension flips the chip on its own,
      // instead of the student having to reload to find out.
      timer = setTimeout(check, ok ? 4000 : 1500);
    });
    const onVisible = () => { if (!document.hidden) { clearTimeout(timer); check(); } };
    check();
    document.addEventListener("visibilitychange", onVisible);
    return () => { alive = false; clearTimeout(timer); document.removeEventListener("visibilitychange", onVisible); };
  }, [live]);
  return (
    <section className="pnl guard-pnl">
      <span className="ico-badge" style={{ background: installed === false ? "#FFDFE1" : blocking ? "#E1F3D6" : "#FFF2C9" }}><IconShield size={17} /></span>
      <h3>Focus Guard</h3>
      {installed === false
        ? <a className="tagchip off" href={FOCUS_GUARD_STORE_URL} target="_blank" rel="noopener"><i className="off-dot" />Offline</a>
        : installed === null
        ? <span className="tagchip idle"><i className="idle-dot" />Buscando…</span>
        : blocking
        ? <span className="tagchip"><i className="live-dot" />Activo</span>
        : <span className="tagchip idle"><i className="idle-dot" />Desactivado</span>}
    </section>
  );
}

function Benchmark({ plus, data = {}, courseId }) {
  const [bench, setBench] = React.useState(data.benchmark);
  React.useEffect(() => {
    if (!data.live || !courseId) return;
    let active = true;
    fetch(`/api/student/courses/${courseId}/benchmark`).then((response) => response.json()).then((body) => {
      if (!active) return;
      const myMinutes = Number(body.my_outcome?.total_focus_minutes || 0);
      setBench({ ...body, my_hours: Math.round((myMinutes / 60) * 10) / 10 });
    }).catch(() => { if (active) setBench({ has_data: false, min_required: 5 }); });
    return () => { active = false; };
  }, [data.live, courseId]);
  return (
    <section className="pnl">
      <div className="pnl-h">
        <span className="ico-badge" style={{ background: "#EFE7FF" }}><IconChart size={17} /></span>
        <h3>Benchmark del ramo</h3>
        {!plus && <PlusBadge small />}
      </div>
      <PlusGate locked={!plus} minHeight={190}
        title="Compárate con quienes ya aprobaron"
        copy="Plus muestra las horas promedio y la nota final promedio de estudiantes anónimos del mismo ramo, universidad y cohorte.">
        {data.live && !bench?.has_data ? <p style={{ fontSize: 13, color: "var(--ink-2)", fontWeight: 700, lineHeight: 1.5 }}>Aún no hay una muestra anónima suficiente. Solo mostramos datos del mismo ramo canónico, universidad y cohorte al reunir al menos {bench?.min_required || 5} resultados.</p> : <React.Fragment>
          <div className="bench">
            <div><div className="n num">{data.live ? `${bench.avg_hours}h` : "41h"}</div><div className="l">Horas promedio</div></div>
            <div><div className="n num">{data.live ? String(bench.avg_final_grade).replace(".", ",") : "5,4"}</div><div className="l">Nota final promedio</div></div>
            <div><div className="n num">{data.live ? `${bench.my_hours || 0}h` : "128h"}</div><div className="l">Tus horas</div></div>
            <div><div className="n num">{data.live ? `${bench.total_reports}` : "+3%"}</div><div className="l">{data.live ? "Resultados anónimos" : "Sobre el promedio"}</div></div>
          </div>
          <p style={{ fontSize: 12.5, color: "var(--ink-2)", marginTop: 12, fontWeight: 700, lineHeight: 1.4 }}>{data.live ? bench.methodology : "Basado en 23 estudiantes anónimos que rindieron Cálculo II en tu universidad."}</p>
        </React.Fragment>}
      </PlusGate>
    </section>
  );
}

Object.assign(window, { FocusHead, FocusNotes, Timer, Ambience, Guard, Benchmark, focusAudio, useFocusHeartbeat, focusAbandoned, touchFocusHeartbeat, SCENES });
