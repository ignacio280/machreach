/* v6 — Quiz demo + FAQ (exact live copy) */

function QuizDemo() {
  const questions = [
    { q: "¿Cuál es el límite de sin(x)/x cuando x → 0?", opts: ["0", "1", "∞", "no existe"], correct: 1, ramo: "Cálculo I" },
    { q: "Una matriz cuadrada es invertible si...", opts: ["su determinante es 0", "su determinante es ≠ 0", "es simétrica", "tiene filas iguales"], correct: 1, ramo: "Álgebra Lineal" },
    { q: "La aceleración en MAS es máxima en...", opts: ["el equilibrio", "los extremos", "la mitad", "es constante"], correct: 1, ramo: "Física II" },
  ];
  const [i, setI] = React.useState(0);
  const [picked, setPicked] = React.useState(null);
  const [boom, setBoom] = React.useState(0);
  const q = questions[i];
  const choose = (k) => {
    if (picked !== null) return;
    setPicked(k);
    if (k === q.correct) setBoom((b) => b + 1);
  };
  return (
    <section className="band band-top">
      <div className="container">
        <div className="sec-head">
          <h2><Lines lines={["Quizzes con IA, hechos", "por tu propia universidad."]} /></h2>
          <Reveal as="p" d={220}>Practica con cuestionarios generados a partir del material real de tus cursos. Son herramientas privadas de estudio, no una red comunitaria.</Reveal>
        </div>
        <div className="quiz-grid">
          <Reveal>
            <h3 style={{ fontSize: 27, marginBottom: 14 }}>Material desordenado entra. Quiz ordenado sale.</h3>
            <p style={{ color: "var(--ink-2)", marginBottom: 26, fontWeight: 600 }}>Sube un PDF o tus propios apuntes. La IA genera preguntas relevantes para tu próxima prueba en segundos.</p>
            <div className="qfeat">
              {[[<IconBook />, "Aprende del material real, no de internet aleatorio"], [<IconPeople />, "Mantén tus materiales y preguntas dentro de tu cuenta"], [<IconCoin />, "El progreso real viene del Focus, no de farmear quizzes"]].map(([ic, t], k) => (
                <div key={k} className="qfeat-row"><span>{React.cloneElement(ic, { size: 21 })}</span>{t}</div>
              ))}
            </div>
          </Reveal>
          <Reveal d={140} variant="pop" className="quiz-card card-2">
            <Confetti fire={boom} />
            <div className="quiz-top">
              <span className="chip-brand">{q.ramo}</span>
              <div className="quiz-prog"><i style={{ width: ((i + 1) / questions.length) * 100 + "%" }} /></div>
              <span className="mono num quiz-count">{i + 1}/{questions.length}</span>
            </div>
            <div style={{ padding: 26 }}>
              <div key={i} style={{ fontFamily: "var(--font-display)", fontWeight: 800, fontSize: 24, marginBottom: 20, lineHeight: 1.22, animation: "rise .45s var(--ease) both" }}>{q.q}</div>
              <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                {q.opts.map((o, k) => {
                  const state = picked === null ? "" : k === q.correct ? " ok" : picked === k ? " no" : " dim";
                  return (
                    <button key={String(i) + k} className={"quiz-opt" + state} onClick={() => choose(k)} style={{ animationDelay: `${k * 60}ms` }}>
                      <span className="quiz-key">{["A", "B", "C", "D"][k]}</span>
                      <span>{o}</span>
                      {picked !== null && k === q.correct && <IconCheck size={19} strokeWidth={3.2} />}
                      {picked !== null && picked === k && k !== q.correct && <IconClose size={19} strokeWidth={3.2} />}
                    </button>
                  );
                })}
              </div>
              <div className="quiz-foot">
                <span className="fb" style={{ color: picked === null ? "var(--ink-3)" : picked === q.correct ? "var(--good)" : "var(--bad)" }}>
                  {picked === null ? "Elige una respuesta" : picked === q.correct ? "✓ Correcto" : "Revisa la respuesta correcta"}
                </span>
                <button className="btn btn-ghost btn-sm" onClick={() => { setPicked(null); setI((i + 1) % questions.length); }}>Siguiente <IconArrow size={14} /></button>
              </div>
            </div>
          </Reveal>
        </div>
      </div>
    </section>
  );
}

function FAQ() {
  const [open, setOpen] = React.useState(0);
  const faqs = [
    { q: "¿Por qué usar MachReach si ya tengo mis apuntes?", a: "MachReach no solo guarda material: mide tu estudio por curso, te da Focus con XP, quizzes IA, flashcards, analytics y rankings en un mismo lugar." },
    { q: "¿Mi universidad usa Canvas?", a: "La mayoría de las grandes en Chile sí: UC, UDP, UAndes, UAI, USACH, USS, PUCV. Si la tuya no, igual puedes subir cursos a mano y usar el resto de la app." },
    { q: "¿Es seguro conectar mi cuenta Canvas?", a: "Sí. La extensión de MachReach detecta tus cursos desde tu sesión abierta de Canvas. No pedimos tu contraseña ni tocamos tus tareas, notas o contenido." },
    { q: "¿Qué pasa con mis datos de estudio?", a: "Son tuyos. No vendemos datos a terceros. Puedes exportar todo o borrar tu cuenta en cualquier momento." },
    { q: "¿Cómo funciona la economía de monedas?", a: "Ganas monedas estudiando con Focus. Las gastas en cosméticos, banners y temas. Pura cosa estética — no afecta tu desempeño académico." },
    { q: "¿Puedo cancelar la suscripción cuando quiera?", a: "Sí. Sin contratos ni letra chica. La cancelas con un click y mantienes acceso hasta el fin del ciclo pagado." },
  ];
  return (
    <section id="faq">
      <div className="container container-narrow">
        <div className="sec-head">
          <h2><Lines lines={["Lo que preguntan tus", "compañeros antes de entrar."]} /></h2>
        </div>
        <div className="faq">
          {faqs.map((f, i) => (
            <Reveal key={i} d={i * 60} className={"faq-item" + (open === i ? " open" : "")}>
              <button onClick={() => setOpen(open === i ? -1 : i)} aria-expanded={open === i}>
                <span>{f.q}</span>
                <i><IconChevron size={17} strokeWidth={3} /></i>
              </button>
              <div className="faq-body"><div><p>{f.a}</p></div></div>
            </Reveal>
          ))}
        </div>
      </div>
    </section>
  );
}

Object.assign(window, { QuizDemo, FAQ });
