/* Notas — planilla de notas (cálculo real, escala chilena 1,0–7,0) */
const PASS = 4.0, ROUND_PASS = 3.95;
const SEM_LABELS = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"];
const NT_DEMO_KEY = "mr_planilla_demo_v1";
const uid = () => "e" + Math.random().toString(36).slice(2, 9);
const num = (v) => { const n = parseFloat(String(v).replace(",", ".")); return isFinite(n) ? n : null; };
const f2 = (n) => n.toFixed(2).replace(".", ",");

function seedCourse(name, credits, evals) {
  return { id: uid(), name, credits, evals: evals.map(([n, p, g]) => ({ id: uid(), name: n, pct: p, grade: g })) };
}
function seedData() {
  const sems = SEM_LABELS.map((label) => ({ label, courses: [] }));
  sems[5].courses = [
    seedCourse("Cálculo II", 10, [["Prueba 1", 25, "5.2"], ["Prueba 2", 30, ""], ["Examen", 45, ""]]),
    seedCourse("Termodinámica", 10, [["Control 1", 15, "4.8"], ["Control 2", 15, "5.5"], ["Control 3", 15, ""], ["Examen", 55, ""]]),
    seedCourse("Química Orgánica", 8, [["Laboratorio 1", 10, "6.0"], ["Informe", 20, ""], ["Examen", 70, ""]]),
  ];
  sems[4].courses = [
    seedCourse("Cálculo I", 10, [["Prueba 1", 30, "5.0"], ["Prueba 2", 30, "4.4"], ["Examen", 40, "5.1"]]),
    seedCourse("Física General", 10, [["Prueba 1", 30, "3.8"], ["Prueba 2", 30, "4.9"], ["Examen", 40, "4.6"]]),
  ];
  return { current: 5, sems };
}

function courseCalc(c) {
  let earned = 0, gradedPct = 0, totalPct = 0;
  c.evals.forEach((e) => {
    const p = num(e.pct) || 0, g = num(e.grade);
    totalPct += p;
    if (g != null && g >= 1 && g <= 7) { earned += (g * p) / 100; gradedPct += p; }
  });
  const rest = Math.max(0, totalPct - gradedPct);
  const partial = gradedPct > 0 ? (earned * 100) / gradedPct : null;
  const need = rest > 0 ? (ROUND_PASS - earned) / (rest / 100) : null;
  const closed = rest === 0 && gradedPct > 0;
  return { earned, gradedPct, totalPct, rest, partial, need, closed, final: closed ? earned : null };
}

function NotasHead({ onExport, onImport, onReset }) {
  const fileIn = React.useRef(null);
  return (
    <section className="st-head">
      <div>
        <div className="pl-kicker">Planilla de notas</div>
        <h1>Cuánto necesitas para pasar.</h1>
        <p>Calcula tus promedios por semestre y la nota mínima que necesitas en lo que queda para aprobar cada ramo.</p>
      </div>
      <div className="cr-actions">
        <button className="btn btn-ghost btn-sm" onClick={onExport}>⬇ Exportar</button>
        <button className="btn btn-ghost btn-sm" onClick={() => fileIn.current.click()}>⬆ Importar</button>
        <input type="file" accept="application/json" hidden ref={fileIn} onChange={(e) => { const f = e.target.files && e.target.files[0]; e.target.value = ""; if (f) onImport(f); }} />
        <button className="btn btn-ghost btn-sm" onClick={onReset}>🗑 Reiniciar</button>
      </div>
    </section>
  );
}

function Planilla() {
  const liveGrades = (window.__MACHREACH_APP__ || {}).grades || {};
  const storageKey = liveGrades.storage_key || NT_DEMO_KEY;
  const [data, setData] = React.useState(() => {
    if (liveGrades.live && liveGrades.sheet) return liveGrades.sheet;
    try { const raw = localStorage.getItem(storageKey); if (raw) return JSON.parse(raw); } catch (e) {}
    return liveGrades.live ? { current: Math.max(0, Number(liveGrades.current_semester || 1) - 1), sems: SEM_LABELS.map((label) => ({ label, courses: [] })) } : seedData();
  });
  React.useEffect(() => {
    try { localStorage.setItem(storageKey, JSON.stringify(data)); } catch (e) {}
    if (!liveGrades.live) return;
    const timer = setTimeout(async () => {
      try {
        const response = await fetch("/api/student/grades/sheet", { method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": liveGrades.csrf || "" }, body: JSON.stringify({ sheet: data }) });
        if (!response.ok) throw new Error("No se pudo sincronizar la planilla");
      } catch (error) { console.error(error); }
    }, 650);
    return () => clearTimeout(timer);
  }, [data, storageKey]);

  const sem = data.sems[data.current];
  const up = (fn) => setData((d) => { const n = JSON.parse(JSON.stringify(d)); fn(n); return n; });

  const semStats = React.useMemo(() => {
    let pts = 0, cr = 0, allPts = 0, allCr = 0;
    data.sems.forEach((s, si) => {
      s.courses.forEach((c) => {
        const k = courseCalc(c);
        const v = k.closed ? k.final : k.partial;
        if (v == null) return;
        const credits = num(c.credits) || 0;
        allPts += v * credits; allCr += credits;
        if (si === data.current) { pts += v * credits; cr += credits; }
      });
    });
    return { semAvg: cr ? pts / cr : null, semCred: cr, carAvg: allCr ? allPts / allCr : null, carCred: allCr };
  }, [data]);

  const addCourse = () => up((d) => d.sems[d.current].courses.push(seedCourse("Curso nuevo", 10, [["Prueba 1", 30, ""], ["Prueba 2", 30, ""], ["Examen", 40, ""]])));
  const exportJson = () => {
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = "planilla_notas.json"; a.click();
  };
  const reset = () => { if (confirm("Esto borrará TODA la planilla. ¿Continuar?")) setData(liveGrades.live ? { current: Math.max(0, Number(liveGrades.current_semester || 1) - 1), sems: SEM_LABELS.map((label) => ({ label, courses: [] })) } : seedData()); };
  const importJson = (file) => {
    const r = new FileReader();
    r.onload = () => {
      try {
        const j = JSON.parse(r.result);
        if (!j || !Array.isArray(j.sems) || !j.sems.length) throw new Error("bad");
        const sems = j.sems.slice(0, SEM_LABELS.length).map((s, i) => ({
          label: s.label || SEM_LABELS[i],
          courses: (Array.isArray(s.courses) ? s.courses : []).map((c) => ({
            id: c.id || uid(), name: String(c.name || "Curso"), credits: c.credits != null ? c.credits : 10,
            evals: (Array.isArray(c.evals) ? c.evals : []).map((e) => ({ id: e.id || uid(), name: String(e.name || "Evaluación"), pct: e.pct != null ? e.pct : 0, grade: e.grade != null ? String(e.grade) : "" })),
          })),
        }));
        setData({ current: Math.min(Math.max(0, j.current | 0), sems.length - 1), sems });
      } catch (err) { alert("Ese archivo no es una planilla válida. No se cambió nada."); }
    };
    r.readAsText(file);
  };

  const card = (lbl, val, cls) => (
    <div className={"nt-card " + (cls || "")} style={{ background: cls === "pass" ? "#E1F3D6" : cls === "fail" ? "#FFDFE1" : "var(--surface)" }}>
      <div className="lbl">{lbl}</div><div className="val num">{val}</div>
    </div>
  );

  return (
    <React.Fragment>
      <NotasHead onExport={exportJson} onImport={importJson} onReset={reset} />
      <div className="nt-sum">
        {card("Promedio del semestre", semStats.semAvg == null ? "–" : f2(semStats.semAvg), semStats.semAvg == null ? "" : semStats.semAvg >= ROUND_PASS ? "pass" : "fail")}
        {card("Créditos del semestre", semStats.semCred)}
        {card("Promedio de carrera", semStats.carAvg == null ? "–" : f2(semStats.carAvg), semStats.carAvg == null ? "" : semStats.carAvg >= ROUND_PASS ? "pass" : "fail")}
        {card("Créditos de carrera", semStats.carCred)}
      </div>

      <div className="nt-tabs">
        {data.sems.map((s, i) => (
          <button key={s.label} className={(i === data.current ? "on " : "") + (s.courses.length ? "has" : "")}
            onClick={() => up((d) => { d.current = i; })}>{s.label}</button>
        ))}
      </div>

      <div>
        {sem.courses.map((c, ci) => {
          const k = courseCalc(c);
          const shown = k.closed ? k.final : k.partial;
          return (
            <div className="nt-course" key={c.id}>
              <div className="nt-ch">
                <input className="nt-name" value={c.name} onChange={(e) => up((d) => { d.sems[d.current].courses[ci].name = e.target.value; })} />
                <label className="nt-cred">Créditos
                  <input value={c.credits} onChange={(e) => up((d) => { d.sems[d.current].courses[ci].credits = e.target.value; })} />
                </label>
                <span className={"nt-avg " + (shown == null ? "none" : shown >= ROUND_PASS ? "pass" : "fail")}>
                  {shown == null ? "sin notas" : f2(shown)}
                </span>
                <button className="nt-del" aria-label="Eliminar ramo" onClick={() => up((d) => d.sems[d.current].courses.splice(ci, 1))}><IconClose size={14} /></button>
              </div>
              <div className="nt-evh"><span>Evaluación</span><span style={{ textAlign: "center" }}>Peso</span><span style={{ textAlign: "center" }}>Nota</span><span /></div>
              <div className="nt-evals">
                {c.evals.map((e, ei) => {
                  const g = num(e.grade);
                  return (
                    <div className="nt-ev" key={e.id}>
                      <input value={e.name} onChange={(ev) => up((d) => { d.sems[d.current].courses[ci].evals[ei].name = ev.target.value; })} />
                      <input className="pct" value={e.pct} onChange={(ev) => up((d) => { d.sems[d.current].courses[ci].evals[ei].pct = ev.target.value; })} />
                      <input className={"grade" + (g == null ? "" : g >= PASS ? " ok" : " bad")} placeholder="—" value={e.grade}
                        onChange={(ev) => up((d) => { d.sems[d.current].courses[ci].evals[ei].grade = ev.target.value; })} />
                      <button className="nt-del" aria-label="Quitar evaluación" onClick={() => up((d) => d.sems[d.current].courses[ci].evals.splice(ei, 1))}><IconClose size={13} /></button>
                    </div>
                  );
                })}
              </div>
              <div className="nt-foot">
                <button className="btn btn-ghost btn-sm" onClick={() => up((d) => d.sems[d.current].courses[ci].evals.push({ id: uid(), name: "Evaluación", pct: 0, grade: "" }))}>+ Evaluación</button>
                <span className="mono">{k.totalPct}% asignado{k.totalPct !== 100 ? " · debería sumar 100%" : ""}</span>
                {k.closed
                  ? <span className={"nt-need " + (k.final >= ROUND_PASS ? "ok" : "bad")}>{k.final >= ROUND_PASS ? "Aprobado con " + f2(k.final) : "Reprobado con " + f2(k.final)}</span>
                  : k.need == null ? null
                    : k.need <= 1 ? <span className="nt-need ok">Ya está aprobado, pase lo que pase</span>
                      : k.need > 7 ? <span className="nt-need bad">Ya no alcanza a aprobar</span>
                        : <span className={"nt-need " + (k.need > 5.5 ? "warn" : "")}>Necesitas <b>{f2(k.need)}</b> en el {k.rest}% restante</span>}
              </div>
            </div>
          );
        })}
        <button className="nt-add" style={{ marginTop: sem.courses.length ? 14 : 0 }} onClick={addCourse}>+ Agregar ramo al semestre {sem.label}</button>
      </div>
    </React.Fragment>
  );
}

Object.assign(window, { Planilla });
