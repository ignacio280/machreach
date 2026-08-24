/* Herramientas — Flashcards + Quizzes */
const DECKS = [
  { n: "Series de Taylor", c: "Cálculo II", cards: 48, done: 31, due: 12, dc: "#FF6A2B" },
  { n: "Grupos funcionales", c: "Química Orgánica", cards: 96, done: 74, due: 8, dc: "#6E4CD8" },
  { n: "Ciclo de Carnot", c: "Termodinámica", cards: 34, done: 12, due: 15, dc: "#6FB03A" },
  { n: "Diagonalización", c: "Álgebra Lineal", cards: 27, done: 20, due: 3, dc: "#2FA8C6" },
  { n: "Integrales impropias", c: "Cálculo II", cards: 41, done: 38, due: 0, dc: "#FF6A2B" },
  { n: "Nomenclatura IUPAC", c: "Química Orgánica", cards: 62, done: 29, due: 6, dc: "#6E4CD8" },
];
const CARD_SAMPLE = { c: "Cálculo II", dc: "#FF6A2B", q: "¿Cuál es el radio de convergencia de la serie de Taylor de 1/(1−x) en x₀ = 0?", a: "R = 1. La serie ∑xⁿ converge para |x| < 1 y diverge en |x| ≥ 1." };

const QUIZZES = [
  { n: "Prueba 2 — Cálculo II (2023)", m: "Prueba oficial · 18 preguntas · Cálculo II", s: 84 },
  { n: "Apuntes ciclo de Carnot", m: "Apuntes · 12 preguntas · Termodinámica", s: 61 },
  { n: "Grupos funcionales — repaso", m: "Apuntes · 20 preguntas · Química Orgánica", s: 92 },
  { n: "Examen 2022 — Álgebra Lineal", m: "Prueba oficial · 25 preguntas · Álgebra Lineal", s: null },
];

function FlipCard() {
  const [on, setOn] = React.useState(false);
  return (
    <div className={"flip" + (on ? " on" : "")} onClick={() => setOn(!on)}>
      <div className="flip-in">
        <div className="flip-f">
          <div className="flip-tag"><i style={{ background: CARD_SAMPLE.dc }} />{CARD_SAMPLE.c} · Vista previa</div>
          <div className="flip-q">{CARD_SAMPLE.q}</div>
          <div className="flip-foot">Toca para girar<span className="mono">1/48</span></div>
        </div>
        <div className="flip-b">
          <div className="flip-tag"><i style={{ background: CARD_SAMPLE.dc }} />Respuesta</div>
          <div className="flip-q">{CARD_SAMPLE.a}</div>
          <div className="grade-btns" onClick={(e) => e.stopPropagation()}>
            <button className="g-again" onClick={() => setOn(false)}>Otra vez</button>
            <button className="g-hard" onClick={() => setOn(false)}>Difícil</button>
            <button className="g-good" onClick={() => setOn(false)}>Bien</button>
            <button className="g-easy" onClick={() => setOn(false)}>Fácil</button>
          </div>
        </div>
      </div>
    </div>
  );
}

function FlashcardsTab({ data = {} }) {
  const decks = data.decks || DECKS;
  const total = decks.reduce((a, d) => a + d.cards, 0);
  const done = decks.reduce((a, d) => a + d.done, 0);
  const due = decks.reduce((a, d) => a + d.due, 0);
  return (
    <div className="sh-stage col" style={{ display: "flex", flexDirection: "column", gap: 18 }}>
      <div className="fc-today">
        <div>
          <div className="fct-eye">Repaso espaciado · hoy</div>
          <div className="fct-title">{due} cartas listas para repasar</div>
          <div className="fct-pills">
            <span className="fct-pill new">{total - done} nuevas</span>
            <span className="fct-pill due">{due} pendientes</span>
            <span className="fct-pill late">{Math.max(0, due - 2)} atrasadas</span>
          </div>
        </div>
        <a href={decks[0]?.href || "/student/flashcards"} className="btn btn-primary btn-lg">▶ Repasar ahora</a>
      </div>
      <FlipCard />
      <div>
        <div className="cos-sub" style={{ marginTop: 4 }}>
          <h4>Tus mazos</h4>
          <span className="mono">{total} cartas · {done} dominadas</span>
        </div>
        <div className="decks">
          {decks.map((d) => (
            <a href={d.href || "/student/flashcards"} className="deck" key={d.id || d.n} style={{ "--dc": d.dc }}>
              <div className="deck-n">{d.n}</div>
              <div className="deck-m">{d.c}</div>
              <div className="deck-bar"><i style={{ width: (d.done / d.cards) * 100 + "%" }} /></div>
              <div className="deck-f">{d.done}/{d.cards} dominadas{d.due > 0 && <span className="due">{d.due} hoy</span>}</div>
            </a>
          ))}
        </div>
      </div>
    </div>
  );
}

function QuizzesTab({ plus, data = {} }) {
  const [quizzes, setQuizzes] = React.useState(data.quizzes || QUIZZES);
  const [mode, setMode] = React.useState("test");
  const [status, setStatus] = React.useState("");
  const fileInput = React.useRef(null);
  const done = quizzes.filter((q) => q.s != null);
  const acc = done.length ? Math.round(done.reduce((a, q) => a + q.s, 0) / done.length) : 0;
  const upload = async (file) => {
    if (!file || !data.live) return;
    setStatus("Leyendo material…");
    try {
      const form = new FormData(); form.append("file", file);
      const extracted = await fetch("/api/student/extract-file", { method: "POST", headers: { "X-CSRFToken": data.csrf || "" }, body: form });
      const source = await extracted.json();
      if (!extracted.ok) throw new Error(source.error || "No se pudo leer el archivo");
      setStatus("Generando quiz…");
      const queued = await fetch("/api/student/quizzes/generate-async", { method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": data.csrf || "" }, body: JSON.stringify({ source_text: source.text, title: source.title, difficulty: "medium", count: plus ? 20 : 10 }) });
      const result = await queued.json();
      if (!queued.ok) throw new Error(result.error || "No se pudo generar el quiz");
      // Runs in the worker: the student can navigate away and the watcher in
      // the topbar tells them when it lands.
      markGenerationQueued("quiz");
      setStatus("Quiz en proceso. Puedes seguir navegando: te avisamos cuando esté listo.");
    } catch (error) { setStatus(error.message || "No se pudo generar el quiz"); }
  };
  // A quiz built from material that never read properly is worth throwing away:
  // its score counts towards the accuracy above, so leaving it there misreports
  // how well the student actually knows the course.
  const removeQuiz = async (quiz) => {
    const scored = quiz.s == null ? "" : " Su nota deja de contar en tu precisión.";
    if (!confirm(`¿Eliminar "${quiz.n}"?${scored}`)) return;
    if (data.live && quiz.id) {
      const response = await fetch(`/api/student/quizzes/${quiz.id}`, { method: "DELETE", headers: { "X-CSRFToken": data.csrf || "" } });
      if (!response.ok) { alert("No se pudo eliminar el quiz."); return; }
    }
    setQuizzes((current) => current.filter((item) => item !== quiz));
  };
  return (
    <div className="sh-stage" style={{ display: "flex", flexDirection: "column", gap: 18 }}>
      <section className="pnl">
        <div className="pnl-h">
          <span className="ico-badge" style={{ background: "#EFE7FF" }}><IconSparkle size={17} /></span>
          <h3>Generar quiz con IA</h3>
          <span className="mono">{acc}% precisión global · {done.length} resueltos</span>
        </div>
        <p className="an-sub">Dos formas de crear un quiz. Elige una.</p>
        <div className="qz-modes">
          <button className={"qz-mode" + (mode === "test" ? " on" : "")} onClick={() => setMode("test")}>
            <div className="qz-mode-ic">📝</div>
            <div className="qz-mode-t">Prueba oficial</div>
            <div className="qz-mode-s">Sube una prueba pasada (PDF / DOCX). La transcribimos literal. Funciona mejor con selección múltiple.</div>
          </button>
          <button className={"qz-mode" + (mode === "notes" ? " on" : "")} onClick={() => setMode("notes")}>
            <div className="qz-mode-ic">📖</div>
            <div className="qz-mode-t">Apuntes</div>
            <div className="qz-mode-s">Sube tus apuntes (PDF / DOCX / TXT) y generamos preguntas desde ese material.</div>
          </button>
        </div>
        <input ref={fileInput} type="file" accept=".pdf,.docx,.txt" hidden onChange={(e) => { const file = e.target.files?.[0]; e.target.value = ""; upload(file); }} />
        <button className="drop" style={{ marginTop: 13 }} onClick={() => fileInput.current?.click()}><IconSparkle size={15} /> {mode === "test" ? "Subir prueba pasada" : "Subir apuntes"} — PDF, DOCX o TXT</button>
        {status && <p className="an-sub" role="status" style={{ marginTop: 10 }}>{status}</p>}
      </section>
      <section className="pnl">
        <div className="pnl-h">
          <span className="ico-badge" style={{ background: "#DCEFF6" }}><IconBrain size={17} /></span>
          <h3>Tus quizzes</h3>
          <span className="mono">{quizzes.length} generados</span>
        </div>
        <div className="qz-list">
          {quizzes.map((q) => (
            <div className="qz-row" key={q.id || q.n}>
              <div className={"qz-score " + (q.s == null ? "none" : q.s >= 80 ? "hi" : q.s >= 60 ? "mid" : "lo")}>{q.s == null ? "nuevo" : q.s + "%"}</div>
              <div className="qz-b"><div className="qz-n">{q.n}</div><div className="qz-m">{q.m}</div></div>
              <a href={q.href || "/student/quizzes"} className="btn btn-ghost btn-sm">{q.s == null ? "Empezar" : "Repetir"}</a>
              <button className="qz-x" onClick={() => removeQuiz(q)} aria-label={"Eliminar " + q.n}><IconClose size={13} /></button>
            </div>
          ))}
          {!quizzes.length && <div className="ex-mat-empty">Aún no hay quizzes. Genera uno desde tus apuntes o una prueba pasada.</div>}
        </div>
        {!plus && (
          <div className="rv-locked" style={{ marginTop: 16 }}>
            <IconLock size={15} /> Las explicaciones de cada respuesta incorrecta son una función Plus.
          </div>
        )}
      </section>
    </div>
  );
}

function ToolsHead() {
  return (
    <section className="st-head">
      <div>
        <div className="pl-kicker">Herramientas</div>
        <h1>Repasa, no releas.</h1>
        <p>Flashcards con repaso espaciado y quizzes generados desde tus pruebas pasadas o tus apuntes. Todo sale del material que subiste a cada evaluación.</p>
      </div>
      <a href="/student/courses" className="btn btn-ghost">Subir material</a>
    </section>
  );
}

function ToolsBoard({ plus, data = {} }) {
  const [tab, setTab] = React.useState(data.initial_tab || "cards");
  return (
    <React.Fragment>
      <div className="sh-tabs">
        <button className={tab === "cards" ? "on" : ""} onClick={() => setTab("cards")}><span>🗃</span>Flashcards</button>
        <button className={tab === "quiz" ? "on" : ""} onClick={() => setTab("quiz")}><span>🧠</span>Quizzes</button>
      </div>
      <div key={tab}>{tab === "cards" ? <FlashcardsTab data={data} /> : <QuizzesTab plus={plus} data={data} />}</div>
    </React.Fragment>
  );
}

Object.assign(window, { ToolsHead, ToolsBoard });
