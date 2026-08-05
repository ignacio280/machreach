/* Taking one quiz (/student/quizzes/<id>).

   The server hands the questions over in the page payload rather than a
   fetch, so the first paint already has them. Explanations arrive stripped
   for free accounts — the lock here is cosmetic, the real gate is that the
   text never leaves the server. */

const QZ_KEYS = ["a", "b", "c", "d"];

const QzLamp = (p) => <Icon {...p}><path d="M9 18h6M10 21h4M12 3a6 6 0 00-3.5 10.9V16h7v-2.1A6 6 0 0012 3z" /></Icon>;
const QzTarget = (p) => <Icon {...p}><circle cx="12" cy="12" r="8.5" /><circle cx="12" cy="12" r="4" /><circle cx="12" cy="12" r=".6" fill="currentColor" /></Icon>;
const QzSearch = (p) => <Icon {...p}><circle cx="11" cy="11" r="6.5" /><path d="M16 16l4 4" /></Icon>;
const QzRetry = (p) => <Icon {...p}><path d="M20 12a8 8 0 11-2.7-6M20 4v5h-5" /></Icon>;

const mmss = (s) => `${String(Math.floor(Math.max(0, s) / 60)).padStart(2, "0")}:${String(Math.max(0, s) % 60).padStart(2, "0")}`;

/* Server shape -> the shape this view reads. Kept in one place so the wire
   format can change without touching the markup. */
function qzNormalize(raw = {}) {
  const questions = (raw.questions || []).map((q, i) => ({
    id: Number(q.id ?? i + 1),
    topic: (q.topic || "").trim() || "General",
    q: q.question || "",
    a: q.option_a || "", b: q.option_b || "", c: q.option_c || "", d: q.option_d || "",
    correct: String(q.correct || "").toLowerCase(),
    exp: q.explanation || "",
  }));
  return {
    id: raw.id,
    title: raw.title || "Quiz",
    course: raw.course_name || "",
    difficulty: ["easy", "medium", "hard"].includes(raw.difficulty) ? raw.difficulty : "medium",
    attempts: Number(raw.attempts || 0),
    qs: questions,
  };
}

const QZ_DIFF_LABEL = { easy: "Dificultad fácil", medium: "Dificultad media", hard: "Dificultad difícil" };

function QuizHead({ quiz, total, timeLeft, timerOn }) {
  return (
    <div className="qz-head">
      <div>
        <a className="qz-back" href="/student/quizzes"><IconArrow size={13} style={{ transform: "rotate(180deg)" }} />Volver a quizzes</a>
        <h1>{quiz.title}</h1>
        <div className="qz-meta">
          {quiz.course && <span className="qz-pill"><IconBook size={13} />{quiz.course}</span>}
          <span className={"qz-pill " + quiz.difficulty}>{QZ_DIFF_LABEL[quiz.difficulty]}</span>
          <span className="qz-pill">{total} {total === 1 ? "pregunta" : "preguntas"}</span>
        </div>
      </div>
      {timerOn && (
        <div className={"qz-timer" + (timeLeft <= 60 ? " low" : "")}>
          <div className="t">{mmss(timeLeft)}</div>
          <div className="l">Restante</div>
        </div>
      )}
    </div>
  );
}

function QuizSetup({ quiz, onStart }) {
  const [timer, setTimer] = React.useState(true);
  // A minute a question is the house default the legacy page used; snap it to
  // the nearest preset so the choice starts somewhere sensible.
  const suggested = [5, 10, 15, 20, 30].reduce(
    (best, m) => (Math.abs(m - quiz.qs.length) < Math.abs(best - quiz.qs.length) ? m : best), 10);
  const [mins, setMins] = React.useState(suggested);
  const [instant, setInstant] = React.useState(true);
  const total = quiz.qs.length;
  return (
    <div className="qz-card qz-setup">
      <span className="qz-topic"><IconBolt size={13} />Antes de empezar</span>
      <h2 style={{ fontSize: 25, lineHeight: 1.05, marginBottom: 7 }}>{total} {total === 1 ? "pregunta lista" : "preguntas listas"}</h2>
      <p style={{ color: "var(--ink-2)", fontSize: 14.5, fontWeight: 600, lineHeight: 1.5, maxWidth: "46ch" }}>
        {quiz.course
          ? `Generado desde tu material de ${quiz.course}. Puedes ponerle cronómetro para simular presión de examen.`
          : "Generado desde tu material. Puedes ponerle cronómetro para simular presión de examen."}
      </p>
      <div style={{ marginTop: 20 }}>
        <label className="row">
          <span className="tx"><b>Cronómetro</b><span>Simula el tiempo real de tu prueba</span></span>
          <input type="checkbox" checked={timer} onChange={(e) => setTimer(e.target.checked)} style={{ position: "absolute", opacity: 0, pointerEvents: "none" }} />
          <span className={"qz-sw" + (timer ? " on" : "")}><i /></span>
        </label>
        {timer && (
          <div className="qz-mins">
            {[5, 10, 15, 20, 30].map((m) => (
              <button key={m} className={m === mins ? "on" : ""} onClick={() => setMins(m)}>{m} min</button>
            ))}
          </div>
        )}
        <label className="row" style={{ marginTop: 14 }}>
          <span className="tx"><b>Corregir al instante</b><span>Ves si acertaste después de cada pregunta</span></span>
          <input type="checkbox" checked={instant} onChange={(e) => setInstant(e.target.checked)} style={{ position: "absolute", opacity: 0, pointerEvents: "none" }} />
          <span className={"qz-sw" + (instant ? " on" : "")}><i /></span>
        </label>
      </div>
      <div className="qz-nav">
        <span className="qz-count">Los quizzes son práctica: no dan XP ni monedas.</span>
        <span className="sp" />
        <button className="btn btn-primary btn-lg" onClick={() => onStart({ timer, mins, instant })}>Empezar quiz<IconArrow size={18} /></button>
      </div>
    </div>
  );
}

function QuizTaking({ quiz, cfg, plus, answers, setAnswers, idx, setIdx, onFinish }) {
  const total = quiz.qs.length;
  const q = quiz.qs[idx];
  const picked = answers[q.id];
  const graded = cfg.instant && picked;
  const [left, setLeft] = React.useState(cfg.mins * 60);
  const finish = React.useRef(onFinish);
  finish.current = onFinish;
  React.useEffect(() => {
    if (!cfg.timer) return undefined;
    const t = setInterval(() => setLeft((v) => {
      if (v <= 1) { clearInterval(t); finish.current(); return 0; }
      return v - 1;
    }), 1000);
    return () => clearInterval(t);
  }, [cfg.timer]);

  const pick = (k) => { if (graded) return; setAnswers({ ...answers, [q.id]: k }); };
  const done = Object.keys(answers).length;

  return (
    <React.Fragment>
      <QuizHead quiz={quiz} total={total} timeLeft={left} timerOn={cfg.timer} />
      <div className="qz-progress">
        <div className="qz-track"><span className="qz-fill" style={{ width: `${(done / total) * 100}%` }} /></div>
        <span className="qz-count">Pregunta {idx + 1} de {total} · {done} respondidas</span>
      </div>

      <div className="qz-card" key={q.id}>
        <span className="qz-topic"><QzTarget size={13} />{q.topic}</span>
        <p className="qz-q">{q.q}</p>
        <div className="qz-opts">
          {QZ_KEYS.filter((k) => q[k]).map((k) => {
            const isCorrect = q.correct === k, isPicked = picked === k;
            let cls = "qz-opt";
            if (graded) {
              if (isCorrect) cls += " ok";
              else if (isPicked) cls += " bad";
              else cls += " dim";
            } else if (isPicked) cls += " sel";
            return (
              <button key={k} className={cls} onClick={() => pick(k)} disabled={!!graded}>
                <span className="k">{k.toUpperCase()}</span>
                <span className="tx">{q[k]}</span>
                {graded && (isCorrect || isPicked) && <span className="st">{isCorrect ? <IconCheck size={19} /> : <IconClose size={17} />}</span>}
              </button>
            );
          })}
        </div>

        {graded && (plus ? (
          <div className="qz-exp">
            <QzLamp size={19} />
            <div>
              <h5>{picked === q.correct ? "Correcto" : "Por qué la respuesta es " + q.correct.toUpperCase()}</h5>
              <p>{q.exp || (picked === q.correct ? "Bien resuelto." : `La alternativa correcta es ${q.correct.toUpperCase()}.`)}</p>
            </div>
          </div>
        ) : (
          <div className="qz-exp lock">
            <IconLock size={18} />
            <div>
              <h5>Explicación con Plus</h5>
              <p>Plus te dice por qué fallaste cada pregunta, no solo cuál era la correcta.</p>
              <a className="btn btn-primary btn-sm" href="/student/shop">Desbloquear Plus</a>
            </div>
          </div>
        ))}

        <div className="qz-nav">
          <button className="btn btn-ghost" disabled={idx === 0} onClick={() => setIdx(idx - 1)}>Anterior</button>
          <span className="sp" />
          <div className="qz-dots">
            {quiz.qs.map((x, i) => {
              const a = answers[x.id];
              const cls = i === idx ? "on" : a ? (cfg.instant ? (a === x.correct ? "ok" : "bad") : "ans") : "";
              return <button key={x.id} className={cls} onClick={() => setIdx(i)}>{i + 1}</button>;
            })}
          </div>
          <span className="sp" />
          {idx === total - 1 ? (
            <button className="btn btn-primary" onClick={onFinish}>Ver resultado<IconArrow size={17} /></button>
          ) : (
            <button className="btn btn-primary" onClick={() => setIdx(idx + 1)}>Siguiente<IconArrow size={17} /></button>
          )}
        </div>
      </div>
      <p className="qz-count" style={{ textAlign: "center" }}>Puedes salir y volver: tus respuestas se guardan solas.</p>
    </React.Fragment>
  );
}

function QuizResult({ quiz, answers, plus, elapsed, onRestart, onWrongOnly }) {
  const [open, setOpen] = React.useState(null);
  const [plan, setPlan] = React.useState(null);      // null = pending, [] = unavailable
  const total = quiz.qs.length;
  const right = quiz.qs.filter((q) => answers[q.id] === q.correct);
  const wrong = quiz.qs.filter((q) => answers[q.id] !== q.correct);
  const score = total ? Math.round((right.length / total) * 100) : 0;
  const topics = [...new Set(quiz.qs.map((q) => q.topic))].map((t) => {
    const qs = quiz.qs.filter((q) => q.topic === t);
    const ok = qs.filter((q) => answers[q.id] === q.correct).length;
    return { t, ok, n: qs.length, pct: Math.round((ok / qs.length) * 100) };
  }).sort((a, b) => a.pct - b.pct);
  const weakest = topics[0] || { t: "el material", ok: 0, n: 0 };

  // Score first (it is what the account keeps), then the model's read on it.
  React.useEffect(() => {
    const log = quiz.qs.map((q) => ({
      q_id: q.id,
      selected: answers[q.id] || "",
      is_correct: answers[q.id] === q.correct,
      topic: q.topic,
      question: q.q,
    }));
    qzPost(`/api/student/quizzes/${quiz.id}/score`, { answers: log.map((a) => ({ q_id: a.q_id, selected: a.selected })) })
      .catch(() => {});
    if (!plus) { setPlan([]); return; }
    // The 30-minute plan is already Plus-gated server-side; an empty list here
    // means the model had nothing useful, and the static fallback takes over.
    qzPost(`/api/student/quizzes/${quiz.id}/analyze`, { answers: log })
      .then((body) => {
        const steps = ((body && body.plus_report && body.plus_report.study_plan_30min) || [])
          .map((s) => ({ m: Number(s && s.minutes) || 0, task: (s && s.task) || "" }))
          .filter((s) => s.task);
        setPlan(steps);
      })
      .catch(() => setPlan([]));
  }, []);

  return (
    <React.Fragment>
      <QuizHead quiz={quiz} total={total} timerOn={false} />
      <div className="qz-card">
        <div className="qz-score">
          <div>
            <div className="big" style={{ color: score >= 70 ? "var(--good)" : score >= 50 ? "var(--ink)" : "var(--bad)" }}>{score}%</div>
            <div className="of">{right.length} de {total} correctas</div>
          </div>
          <div className="msg">
            <h2>{score >= 90 ? "Dominado" : score >= 70 ? "Vas bien, falta afinar" : score >= 50 ? "A medio camino" : "Toca repasar la base"}</h2>
            <p>{score >= 70
              ? `Tu punto flojo es ${weakest.t.toLowerCase()} (${weakest.ok}/${weakest.n}). Con un repaso corto lo cierras.`
              : `Falta base en ${weakest.t.toLowerCase()}. Repasa la teoría antes de volver a intentarlo.`}</p>
          </div>
        </div>
        <div className="qz-kpis">
          <div className="qz-kpi"><div className="n">{right.length}</div><div className="l">Correctas</div></div>
          <div className="qz-kpi"><div className="n">{wrong.length}</div><div className="l">Falladas</div></div>
          <div className="qz-kpi"><div className="n">{mmss(elapsed)}</div><div className="l">Tiempo usado</div></div>
          <div className="qz-kpi"><div className="n">{quiz.attempts + 1}º</div><div className="l">Intento</div></div>
        </div>
      </div>

      <div className="qz-sec">
        <h3><QzSearch size={19} />Dónde se te escapa</h3>
        <div className="qz-topics">
          {topics.map((t) => (
            <div className="qz-trow" key={t.t}>
              <span className="nm">{t.t}</span>
              <span className="bar"><i className={t.pct >= 70 ? "" : t.pct >= 40 ? "mid" : "low"} style={{ width: `${Math.max(t.pct, 4)}%` }} /></span>
              <span className="v">{t.ok}/{t.n} · {t.pct}%</span>
            </div>
          ))}
        </div>
      </div>

      <div className="qz-sec">
        <h3><QzTarget size={19} />Haz esto ahora</h3>
        {plus ? (
          plan === null
            ? <p className="qz-count">Analizando tus respuestas…</p>
            : plan.length
              ? <ol className="qz-plan">{plan.map((step, i) => <li key={i}><span className="m">{step.m ? `${step.m} min` : `Paso ${i + 1}`}</span><span>{step.task}</span></li>)}</ol>
              : (
                <ol className="qz-plan">
                  <li><span className="m">10 min</span><span>Relee la sección de <b>{weakest.t.toLowerCase()}</b> en tu material y reescribe lo clave de memoria.</span></li>
                  {wrong.length > 0 && <li><span className="m">12 min</span><span>Resuelve otra vez las {wrong.length} preguntas falladas, escribiendo el razonamiento antes de elegir.</span></li>}
                  <li><span className="m">8 min</span><span>Crea 3 flashcards con lo que más te costó de {weakest.t.toLowerCase()}.</span></li>
                </ol>
              )
        ) : (
          <div className="qz-exp lock" style={{ marginTop: 0 }}>
            <IconLock size={18} />
            <div>
              <h5>Plan de 30 minutos con Plus</h5>
              <p>Plus analiza tus fallos y te arma el repaso paso a paso después de cada quiz.</p>
              <a className="btn btn-primary btn-sm" href="/student/shop">Desbloquear Plus</a>
            </div>
          </div>
        )}
      </div>

      <div className="qz-sec">
        <h3><IconBook size={19} />Pregunta por pregunta</h3>
        <div className="qz-rev">
          {quiz.qs.map((q) => {
            const a = answers[q.id], ok = a === q.correct, isOpen = open === q.id;
            return (
              <div className="qz-rev-row" key={q.id}>
                <button onClick={() => setOpen(isOpen ? null : q.id)}>
                  <span className={"ix " + (ok ? "ok" : "bad")}>{ok ? <IconCheck size={13} /> : <IconClose size={12} />}</span>
                  <span className="qt">{q.q}</span>
                  <span className="tp">{q.topic}</span>
                  <IconArrow size={15} style={{ transform: isOpen ? "rotate(90deg)" : "rotate(90deg) rotate(180deg)", color: "var(--ink-3)", flex: "none" }} />
                </button>
                {isOpen && (
                  <div className="qz-rev-body">
                    <div className="ln"><span className="k">Tu resp.</span><span>{a ? <b>{a.toUpperCase()}. {q[a]}</b> : <b>Sin responder</b>}</span></div>
                    <div className="ln"><span className="k">Correcta</span><span><b>{q.correct.toUpperCase()}. {q[q.correct]}</b></span></div>
                    <div className="ln"><span className="k">Por qué</span><span>{plus ? (q.exp || "—") : "Las explicaciones son parte de Plus."}</span></div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      <div className="qz-acts">
        <button className="btn btn-primary" onClick={onRestart}><QzRetry size={17} />Repetir quiz</button>
        {wrong.length > 0 && <button className="btn btn-ghost" onClick={() => onWrongOnly(wrong.map((q) => q.id))}>Solo las {wrong.length} falladas</button>}
        <span className="sp" />
        <a className="btn btn-ghost" href="/student/quizzes">Volver a quizzes</a>
      </div>
    </React.Fragment>
  );
}

async function qzPost(path, payload) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRFToken": (window.__MACHREACH_APP__ || {}).csrf || "" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw new Error("request failed");
  return response.json();
}

function QuizPage({ data = {}, plus = true }) {
  const quiz = React.useMemo(() => qzNormalize(data.quiz || {}), [data.quiz]);
  // A retake of the wrong ones only narrows the deck; everything else reads
  // the same, so the whole view just runs against a shorter question list.
  const [only, setOnly] = React.useState(null);
  const deck = React.useMemo(
    () => (only ? { ...quiz, qs: quiz.qs.filter((q) => only.includes(q.id)) } : quiz),
    [quiz, only],
  );
  const [phase, setPhase] = React.useState("setup");
  const [cfg, setCfg] = React.useState({ timer: true, mins: 10, instant: true });
  const [answers, setAnswers] = React.useState({});
  const [idx, setIdx] = React.useState(0);
  const [startedAt, setStartedAt] = React.useState(0);
  const [elapsed, setElapsed] = React.useState(0);

  if (!quiz.qs.length) {
    return (
      <div className="qz-wrap">
        <div className="qz-card">
          <span className="qz-topic"><IconBolt size={13} />Sin preguntas</span>
          <p className="qz-q">Este quiz no tiene preguntas.</p>
          <div className="qz-nav"><span className="sp" /><a className="btn btn-primary" href="/student/quizzes">Volver a quizzes</a></div>
        </div>
      </div>
    );
  }

  const begin = (config) => { setCfg(config); setStartedAt(Date.now()); setPhase("taking"); };
  const finish = () => { setElapsed(Math.round((Date.now() - startedAt) / 1000)); setPhase("result"); };
  const restart = (ids) => {
    setOnly(ids || null);
    setAnswers({}); setIdx(0); setElapsed(0);
    setPhase("setup");
  };

  return (
    <div className="qz-wrap">
      {phase === "setup" && <QuizSetup quiz={deck} onStart={begin} />}
      {phase === "taking" && (
        <QuizTaking quiz={deck} cfg={cfg} plus={plus} answers={answers} setAnswers={setAnswers}
          idx={Math.min(idx, deck.qs.length - 1)} setIdx={setIdx} onFinish={finish} />
      )}
      {phase === "result" && (
        <QuizResult quiz={deck} answers={answers} plus={plus} elapsed={elapsed}
          onRestart={() => restart(null)} onWrongOnly={(ids) => restart(ids)} />
      )}
    </div>
  );
}

Object.assign(window, { QuizPage });
