/* Reviews — reseñas anónimas de ramos */
const REVIEWS = [
  { c: "Cálculo II", code: "MAT1620", u: "PUC", m: "Ing. Civil", d: 4, q: 3, t: "Las pruebas pesan mucho más que las ayudantías. Si no haces las guías completas, la P2 te pasa por encima.", g: "4,8", h: 62 },
  { c: "Química Orgánica", code: "QIM100E", u: "U. de Chile", m: "Ing. Civil", d: 5, q: 4, t: "El laboratorio se lleva la mitad del tiempo y no lo dicen al principio. Ordénate con los informes desde la semana 1.", g: "4,2", h: 88 },
  { c: "Termodinámica", code: "FIS1523", u: "USM", m: "Ing. Civil", d: 3, q: 5, t: "Profesor explica muy bien y las clases se siguen sin apuntes ajenos. Los controles son parecidos a las guías.", g: "5,6", h: 41 },
  { c: "Álgebra Lineal", code: "MAT1203", u: "PUC", m: "Ing. Civil", d: 2, q: 4, t: "Ramo amable si haces los ejercicios. El examen repite estructura de los años anteriores casi textual.", g: "6,1", h: 30 },
  { c: "Introducción a la Programación", code: "IIC1103", u: "UdeC", m: "Ing. Comercial", d: 3, q: 4, t: "Las tareas toman más tiempo del estimado. Empieza el día que las publican y no el fin de semana.", g: "5,3", h: 54 },
  { c: "Cálculo II", code: "MAT1620", u: "USM", m: "Ing. Civil", d: 5, q: 2, t: "Mucha materia en poco tiempo y las ayudantías no alcanzan a cubrirla. Busca guías de años anteriores.", g: "4,0", h: 95 },
];
const UNIS = ["Todas las universidades", "PUC", "U. de Chile", "USM", "UdeC"];
const MAJORS = ["Todas las carreras", "Ing. Civil", "Ing. Comercial"];
const stars = (n) => "★".repeat(n) + "☆".repeat(5 - n);

function ReviewsHead({ onWrite }) {
  return (
    <section className="st-head">
      <div>
        <div className="pl-kicker">Reviews</div>
        <h1>Lo que nadie te dice del ramo.</h1>
        <p>Explora dificultad y calidad por universidad y carrera. Las reviews son anónimas; nota final y horas estudiadas solo aparecen para usuarios Plus.</p>
      </div>
      <button className="btn btn-primary" onClick={onWrite}><IconStar size={16} /> Escribir una review</button>
    </section>
  );
}

function ReviewsBoard({ plus, data = {} }) {
  const [rows, setRows] = React.useState(data.live ? (data.items || []) : REVIEWS);
  React.useEffect(() => {
    if (!data.live) return;
    fetch("/api/student/reviews").then((r) => r.json()).then((body) => setRows((body.reviews || []).map((r) => ({ c: r.course_name, code: r.course_code, u: r.university || "Universidad", m: r.major || "Carrera", d: r.difficulty_rating, q: r.quality_rating, t: r.review_text, g: r.final_grade, h: r.total_hours }))));
  }, [data.live]);
  const unis = [UNIS[0], ...Array.from(new Set(rows.map((r) => r.u).filter(Boolean)))];
  const majors = [MAJORS[0], ...Array.from(new Set(rows.map((r) => r.m).filter(Boolean)))];
  const [uni, setUni] = React.useState(UNIS[0]);
  const [maj, setMaj] = React.useState(MAJORS[0]);
  const [sort, setSort] = React.useState("dif");
  let list = rows.filter((r) => (uni === UNIS[0] || r.u === uni) && (maj === MAJORS[0] || r.m === maj));
  list = [...list].sort((a, b) => (sort === "dif" ? b.d - a.d : b.q - a.q));
  return (
    <section className="pnl">
      <div className="pnl-h">
        <span className="ico-badge" style={{ background: "#FFF3CF" }}><IconStar size={17} /></span>
        <h3>Reseñas de la comunidad</h3>
        <span className="mono">{list.length} de {rows.length}</span>
      </div>
      <div className="rv-filters" style={{ marginBottom: 16 }}>
        <select value={uni} onChange={(e) => setUni(e.target.value)}>{unis.map((u) => <option key={u}>{u}</option>)}</select>
        <select value={maj} onChange={(e) => setMaj(e.target.value)}>{majors.map((m) => <option key={m}>{m}</option>)}</select>
        <select value={sort} onChange={(e) => setSort(e.target.value)}>
          <option value="dif">Más difíciles primero</option>
          <option value="cal">Mejor calidad primero</option>
        </select>
      </div>
      {list.length ? (
        <div className="rv-grid" key={uni + maj + sort}>
          {list.map((r, i) => (
            <article className="rvc" key={r.code + i} style={{ animation: "rowIn .5s var(--spring) both", animationDelay: i * 60 + "ms" }}>
              <div className="rvc-meta">Anónimo · {r.u} · {r.m}</div>
              <h3>{r.c}</h3>
              <div className="rvc-code">{r.code}</div>
              <div className="rvc-ratings">
                <span className="rv-pill">Dificultad <b className="rv-stars">{stars(r.d)}</b></span>
                <span className="rv-pill">Calidad <b className="rv-stars">{stars(r.q)}</b></span>
              </div>
              <p>{r.t}</p>
              {plus ? (
                <div className="rv-data">
                  <div><span>Nota final</span>{r.g}</div>
                  <div><span>Horas estudiadas</span>{r.h}h</div>
                </div>
              ) : (
                <div className="rv-locked"><IconLock size={14} /> Nota y horas: solo Plus</div>
              )}
            </article>
          ))}
        </div>
      ) : <div className="fr-empty">Ninguna review coincide con esos filtros</div>}
    </section>
  );
}

function ReviewModal({ data, onClose }) {
  const courses = data.courses || [];
  const [form, setForm] = React.useState({ course_id: courses[0]?.id || "", difficulty_rating: 3, quality_rating: 3, review_text: "" });
  const save = async () => {
    const response = await fetch("/api/student/reviews", { method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": (window.__MACHREACH_APP__ || {}).csrf || "" }, body: JSON.stringify(form) });
    const body = await response.json();
    if (!response.ok) return alert(body.error || "No se pudo publicar la review.");
    location.reload();
  };
  return <Modal title="Escribir una review" sub="Tu identidad nunca aparece en la publicación." onClose={onClose}
    foot={<><button className="btn btn-ghost btn-sm" onClick={onClose}>Cancelar</button><button className="btn btn-primary btn-sm" onClick={save} disabled={!form.course_id}>Publicar</button></>}>
    <div className="mdl-fields">
      <label>Ramo<select value={form.course_id} onChange={(e) => setForm({ ...form, course_id: e.target.value })}>{courses.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label>
      <label>Dificultad<select value={form.difficulty_rating} onChange={(e) => setForm({ ...form, difficulty_rating: Number(e.target.value) })}>{[1,2,3,4,5].map((n) => <option key={n} value={n}>{n}/5</option>)}</select></label>
      <label>Calidad<select value={form.quality_rating} onChange={(e) => setForm({ ...form, quality_rating: Number(e.target.value) })}>{[1,2,3,4,5].map((n) => <option key={n} value={n}>{n}/5</option>)}</select></label>
      <label>Tu experiencia<textarea rows="5" value={form.review_text} onChange={(e) => setForm({ ...form, review_text: e.target.value })} /></label>
    </div>
  </Modal>;
}

Object.assign(window, { ReviewsHead, ReviewsBoard, ReviewModal });
