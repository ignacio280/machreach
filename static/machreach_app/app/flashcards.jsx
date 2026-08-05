/* Flashcards — dentro del deck.
   El diseño vive en el proyecto Claude (MachReach Flashcards.html); el markup y
   las clases son las de ahí. Lo que cambia acá es el origen de los datos: el
   deck llega en el payload del servidor y cada calificación, edición, alta y
   baja de tarjeta se persiste contra la API en vez de quedarse en memoria. */

const FC_DECK = {
  title: "Termodinámica — Conceptos clave",
  course: "FIS-2103 Termodinámica",
  cards: [
    { id: 1, front: "Primera ley de la termodinámica", back: "ΔU = Q − W. La energía interna de un sistema cambia por el calor que absorbe menos el trabajo que realiza.", seen: 7, ok: 6 },
    { id: 2, front: "Proceso adiabático", back: "Proceso sin transferencia de calor (Q = 0). Todo el cambio de energía interna viene del trabajo: ΔU = −W.", seen: 5, ok: 2 },
    { id: 3, front: "Entalpía (H)", back: "H = U + PV. A presión constante, ΔH es igual al calor intercambiado con el entorno.", seen: 6, ok: 5 },
    { id: 4, front: "¿Por qué Cp > Cv?", back: "A presión constante el gas se expande y gasta parte del calor en trabajo, así que hace falta más calor para el mismo ΔT. Cp − Cv = nR.", seen: 4, ok: 1 },
    { id: 5, front: "Proceso isocórico", back: "Volumen constante. No hay trabajo de expansión (W = 0), así que ΔU = Q.", seen: 6, ok: 6 },
    { id: 6, front: "Función de estado", back: "Propiedad que solo depende del estado actual, no del camino recorrido. U, H, S y T son funciones de estado; Q y W no.", seen: 3, ok: 2 },
    { id: 7, front: "Reacción exotérmica", back: "El sistema libera calor al entorno, así que ΔH < 0.", seen: 5, ok: 5 },
    { id: 8, front: "Trabajo en un proceso isobárico", back: "W = P·ΔV. La presión es constante, así que el trabajo es el área bajo una recta horizontal en el diagrama P–V.", seen: 2, ok: 0 },
  ],
};

const FcCards = (p) => <Icon {...p}><rect x="3" y="6" width="13" height="14" rx="2.5" /><path d="M7 3h11a2 2 0 012 2v11" /></Icon>;
const FcPen = (p) => <Icon {...p}><path d="M4 20h4l11-11a2.5 2.5 0 00-3.5-3.5L4.5 16.5 4 20z" /></Icon>;
const FcRetry = (p) => <Icon {...p}><path d="M20 12a8 8 0 11-2.7-6M20 4v5h-5" /></Icon>;
const FcPlus = (p) => <Icon {...p}><path d="M12 5v14M5 12h14" /></Icon>;
const FcTrash = (p) => <Icon {...p}><path d="M4 7h16M9 7V4.5h6V7M6.5 7l1 13h9l1-13M10 11v6M14 11v6" /></Icon>;

/* Every write goes through here: live pages carry their CSRF token in the
   payload, and a study round must never lose a grade to a silent failure. */
function fcSend(path, { method = "POST", body = null } = {}) {
  const csrf = (window.__MACHREACH_APP__ || {}).csrf || "";
  return fetch(path, {
    method,
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
    body: body ? JSON.stringify(body) : undefined,
  }).then((r) => (r.ok ? r.json().catch(() => ({})) : Promise.reject(new Error(String(r.status)))));
}

function FcHead({ mode, setMode, count, deck, live }) {
  return (
    <div className="fc-head">
      <div>
        <a className="fc-back" href={live ? "/student/flashcards" : "MachReach Herramientas.html"}><IconArrow size={13} style={{ transform: "rotate(180deg)" }} />Volver a mis decks</a>
        <h1>{deck.title}</h1>
        <div className="fc-meta">
          {deck.course ? <span className="fc-pill"><IconBook size={13} />{deck.course}</span> : null}
          <span className="fc-pill">{count} tarjetas</span>
          <span className="fc-pill">Repaso espaciado activo</span>
        </div>
      </div>
      <div className="fc-modes">
        <button className={mode === "study" ? "on" : ""} onClick={() => setMode("study")}><FcCards size={15} />Estudiar</button>
        <button className={mode === "edit" ? "on" : ""} onClick={() => setMode("edit")}><FcPen size={15} />Editar</button>
      </div>
    </div>
  );
}

function FcStudy({ cards, plus, onDone, srs, live }) {
  const [i, setI] = React.useState(0);
  const [flip, setFlip] = React.useState(false);
  const [log, setLog] = React.useState({});
  const card = cards[i];
  const started = React.useRef(Date.now()).current;

  const mark = React.useCallback((ok, quality) => {
    if (!card) return;
    if (live) {
      // Practice progress: a dropped request costs the student their history,
      // so it is fired per card rather than batched at the end of the round.
      const payload = quality === undefined
        ? { card_id: card.id, correct: !!ok }
        : { card_id: card.id, quality };
      fcSend("/api/student/flashcards/progress", { body: payload }).catch(() => {});
    }
    setLog((l) => ({ ...l, [card.id]: ok }));
    setFlip(false);
    if (i + 1 >= cards.length) onDone({ ...log, [card.id]: ok }, Math.round((Date.now() - started) / 1000));
    else setTimeout(() => setI(i + 1), 160);
  }, [card, i, cards.length, log, onDone, live, started]);

  React.useEffect(() => {
    const k = (e) => {
      if (e.target && /^(INPUT|TEXTAREA)$/.test(e.target.tagName)) return;
      if (e.code === "Space") { e.preventDefault(); setFlip((f) => !f); }
      if (e.key === "1") mark(false, srs && plus ? 0 : undefined);
      if (e.key === "2") mark(true, srs && plus ? 4 : undefined);
    };
    addEventListener("keydown", k);
    return () => removeEventListener("keydown", k);
  }, [mark, srs, plus]);

  if (!card) return null;
  const done = Object.keys(log).length;
  return (
    <>
      <div className="fc-progress">
        <div className="fc-track"><span className="fc-fill" style={{ width: `${((i + 1) / cards.length) * 100}%` }} /></div>
        <span className="fc-count">Tarjeta {i + 1} de {cards.length} · {done} calificadas</span>
      </div>

      <div className="fc-stage">
        <div className="fc-stack" aria-hidden="true"><i /><i /></div>
        <button className={"fc-card" + (flip ? " flip" : "")} onClick={() => setFlip(!flip)} aria-label="Girar tarjeta">
          <div className="fc-face">
            <span className="fc-side">Pregunta</span>
            <span className="fc-seen">Vista {card.seen}× · {card.seen ? Math.round((card.ok / card.seen) * 100) : 0}% ok</span>
            <p className="fc-text">{card.front}</p>
            <span className="fc-tap">Clic o espacio para girar</span>
          </div>
          <div className="fc-face back">
            <span className="fc-side">Respuesta</span>
            <p className="fc-text">{card.back}</p>
            <span className="fc-tap">¿La sabías?</span>
          </div>
        </button>
      </div>

      {srs && plus ? (
        <div className="fc-srs">
          <button className="q0" onClick={() => mark(false, 0)}><b>Otra vez</b><span>&lt; 1 min</span></button>
          <button className="q3" onClick={() => mark(true, 3)}><b>Difícil</b><span>en 2 días</span></button>
          <button onClick={() => mark(true, 4)}><b>Bien</b><span>en 5 días</span></button>
          <button className="q5" onClick={() => mark(true, 5)}><b>Fácil</b><span>en 12 días</span></button>
        </div>
      ) : (
        <div className="fc-acts">
          <button className="fc-btn no" onClick={() => mark(false)}><IconClose size={17} />No la sabía<kbd>1</kbd></button>
          <button className="fc-btn yes" onClick={() => mark(true)}><IconCheck size={19} />La sabía<kbd>2</kbd></button>
        </div>
      )}

      <p className="fc-keys"><kbd>Espacio</kbd> girar · <kbd>1</kbd> fallé · <kbd>2</kbd> acerté</p>
    </>
  );
}

const fcMMSS = (s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;

function FcDone({ log, cards, plus, onAgain, onWrong, seconds, live }) {
  const ok = Object.values(log).filter(Boolean).length;
  const bad = cards.length - ok;
  const pct = cards.length ? Math.round((ok / cards.length) * 100) : 0;
  const weak = cards.filter((c) => log[c.id] === false);
  return (
    <>
      <div className="fc-done">
        <div className="ring"><IconCheck size={34} /></div>
        <h2>{pct >= 80 ? "Deck casi dominado" : pct >= 50 ? "Buena pasada" : "Toca repetir pronto"}</h2>
        <p>{bad > 0
          ? `Fallaste ${bad} tarjeta${bad > 1 ? "s" : ""}. Volverán a salir antes que el resto.`
          : "Acertaste todas. Este deck vuelve en 12 días."}</p>
        <div className="fc-kpis">
          <div className="fc-kpi"><div className="n">{ok}</div><div className="l">Acertadas</div></div>
          <div className="fc-kpi"><div className="n">{bad}</div><div className="l">Falladas</div></div>
          <div className="fc-kpi"><div className="n">{fcMMSS(Math.max(0, Number(seconds) || 0))}</div><div className="l">Tiempo</div></div>
        </div>
      </div>

      {weak.length > 0 && (
        <div className="fc-done" style={{ textAlign: "left", padding: "20px 22px" }}>
          <h3 style={{ fontSize: 17, marginBottom: 12, display: "flex", gap: 8, alignItems: "center" }}><IconBrain size={19} color="var(--brand-2)" />Las que se te escapan</h3>
          <div className="fc-editor">
            {weak.map((c) => (
              <div key={c.id} style={{ display: "flex", gap: 10, alignItems: "center", padding: "10px 12px", border: "2px solid var(--ink)", borderRadius: 12, background: "var(--surface-2)", boxShadow: "0 2px 0 var(--ink)" }}>
                <span style={{ fontFamily: "var(--font-display)", fontWeight: 800, fontSize: 14, flex: 1 }}>{c.front}</span>
                <span className="fc-count">{c.seen ? Math.round((c.ok / c.seen) * 100) : 0}% histórico</span>
              </div>
            ))}
          </div>
          {!plus && <p className="fc-count" style={{ marginTop: 12 }}>Con Plus el repaso espaciado decide solo cuándo volver a mostrártelas.</p>}
        </div>
      )}

      <div className="fc-editbar">
        <button className="btn btn-primary" onClick={onAgain}><FcRetry size={17} />Repasar deck completo</button>
        {weak.length > 0 && <button className="btn btn-ghost" onClick={onWrong}>Solo las {weak.length} falladas</button>}
        <span className="sp" />
        <a className="btn btn-ghost" href={live ? "/student/flashcards" : "MachReach Herramientas.html"}>Volver a mis decks</a>
      </div>
    </>
  );
}

function FcEdit({ cards, setCards, deckId, live }) {
  const [saving, setSaving] = React.useState("");
  const upd = (id, k, v) => setCards(cards.map((c) => (c.id === id ? { ...c, [k]: v } : c)));

  /* The design promises "se guardan al salir del campo", so blur is the commit
     point. A card the student just added has no server id yet: the first blur
     with both sides filled creates it and adopts the real id. */
  const commit = (card) => {
    if (!live) return;
    const front = (card.front || "").trim();
    const back = (card.back || "").trim();
    if (!front || !back) return;
    setSaving(String(card.id));
    const finish = () => setSaving("");
    if (card.isNew) {
      fcSend("/api/student/flashcards/add", { body: { front, back, deck_id: deckId } })
        .then((body) => {
          if (body && body.card_id) {
            setCards((list) => list.map((c) => (c.id === card.id ? { ...c, id: body.card_id, isNew: false } : c)));
          }
          finish();
        })
        .catch(finish);
      return;
    }
    fcSend(`/api/student/flashcards/${card.id}`, { method: "PUT", body: { front, back, deck_id: deckId } })
      .then(finish).catch(finish);
  };

  const remove = (card) => {
    setCards(cards.filter((x) => x.id !== card.id));
    if (live && !card.isNew) {
      fcSend(`/api/student/flashcards/${card.id}?deck_id=${deckId}`, { method: "DELETE" }).catch(() => {});
    }
  };

  return (
    <>
      <p className="fc-count">Los cambios se guardan al salir del campo. {cards.length} tarjetas en el deck.</p>
      <div className="fc-editor">
        {cards.map((c, i) => (
          <div className="fc-erow" key={c.id}>
            <span className="ix">{i + 1}</span>
            <div className="pair">
              <label><span className="k">Pregunta</span>
                <textarea className="fc-in" rows="1" value={c.front} onChange={(e) => upd(c.id, "front", e.target.value)} onBlur={() => commit(c)} />
              </label>
              <label><span className="k">Respuesta</span>
                <textarea className="fc-in" rows="2" value={c.back} onChange={(e) => upd(c.id, "back", e.target.value)} onBlur={() => commit(c)} />
              </label>
            </div>
            <div className="fc-erow-side">
              <span className="rate">{saving === String(c.id) ? "Guardando…" : c.seen ? Math.round((c.ok / c.seen) * 100) + "% ok" : "Nueva"}</span>
              <button className="fc-del" aria-label="Borrar tarjeta" onClick={() => remove(c)}><FcTrash size={15} /></button>
            </div>
          </div>
        ))}
        <button className="fc-add" onClick={() => setCards([...cards, { id: "new-" + Date.now(), front: "", back: "", seen: 0, ok: 0, isNew: true }])}><FcPlus size={17} />Agregar tarjeta</button>
      </div>
    </>
  );
}

function FlashcardsPage({ view = "study", plus = true, srs = true, data = {} }) {
  const live = !!data.live;
  const deck = live
    ? { title: data.deck?.title || "Deck", course: data.deck?.course || "", id: data.deck?.id }
    : { title: FC_DECK.title, course: FC_DECK.course, id: 0 };
  const initial = live ? (data.deck?.cards || []) : FC_DECK.cards;

  const [mode, setMode] = React.useState(view === "edit" ? "edit" : "study");
  const [cards, setCards] = React.useState(initial);
  const [log, setLog] = React.useState(
    view === "done" && !live ? { 1: true, 2: false, 3: true, 4: false, 5: true, 6: true, 7: true, 8: false } : null
  );
  const [seconds, setSeconds] = React.useState(252);
  const [round, setRound] = React.useState(0);
  const [pool, setPool] = React.useState(initial);
  // Spaced repetition is the Plus behaviour; free accounts grade with two buttons.
  const srsOn = live ? !!plus : srs;

  const finish = (l, secs) => { setLog(l); if (secs !== undefined) setSeconds(secs); };

  return (
    <div className="fc-wrap">
      <FcHead mode={mode} setMode={setMode} count={cards.length} deck={deck} live={live} />
      {mode === "edit" ? (
        <FcEdit cards={cards} setCards={setCards} deckId={deck.id} live={live} />
      ) : cards.length === 0 ? (
        <div className="fc-done">
          <h2>Este deck está vacío</h2>
          <p>Agrega tarjetas desde el modo Editar y vuelve a estudiar.</p>
          <div className="fc-editbar" style={{ justifyContent: "center", marginTop: 18 }}>
            <button className="btn btn-primary" onClick={() => setMode("edit")}><FcPen size={17} />Editar tarjetas</button>
          </div>
        </div>
      ) : log ? (
        <FcDone log={log} cards={pool} plus={plus} seconds={seconds} live={live}
          onAgain={() => { setPool(cards); setLog(null); setRound(round + 1); }}
          onWrong={() => { setPool(pool.filter((c) => log[c.id] === false)); setLog(null); setRound(round + 1); }} />
      ) : (
        <FcStudy key={round} cards={pool} plus={plus} srs={srsOn} live={live} onDone={finish} />
      )}
    </div>
  );
}

Object.assign(window, { FlashcardsPage, FC_DECK });
