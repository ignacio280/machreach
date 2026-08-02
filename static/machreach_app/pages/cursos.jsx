/* Page bootstrap — Mis cursos (cursos).
   Lifted from the design project's "MachReach Cursos.html" inline App(); the
   ReactDOM.createRoot call is appended by build-app.mjs, not written here. */

const PAGE_ID = "courses";
const PAGE_TITLE = "Mis cursos";
const PAGE_SUB = "Ramos del semestre";

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "theme": "light",
  "accent": "coral",
  "plus": true
}/*EDITMODE-END*/;

function App() {
  const data = window.__MACHREACH_APP__ || {};
  const [tweaks, setTweak] = useTweaks(TWEAK_DEFAULTS);
  const plus = data.live ? !!data.plus : !!tweaks.plus;
  const [list, setList] = React.useState(data.courses?.items || CRS);
  const [adding, setAdding] = React.useState(false);
  const [delTarget, setDelTarget] = React.useState(null);
  const addCourse = async (v) => {
    let id = null;
    if (data.live) {
      const response = await fetch("/api/student/courses/manual", { method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": data.csrf || "" }, body: JSON.stringify({ name: v.name, code: v.code }) });
      const body = await response.json();
      if (!response.ok) return alert(body.error || "No se pudo agregar el curso.");
      id = body.id;
    }
    setList((l) => [...l, { id, code: v.code || "SIN CÓDIGO", name: v.name, cc: v.color, origin: "Agregado a mano", sessions: 0, studied: "0h 00m", next: "Sin evaluaciones próximas", exams: [], bench: { hrs: "—", grade: "—", mine: "0h", delta: "—", n: 0 } }]);
  };
  const deleteCourse = async (course) => {
    if (data.live && course.id) {
      const response = await fetch("/api/student/courses/" + course.id, { method: "DELETE", headers: { "X-CSRFToken": data.csrf || "" } });
      if (!response.ok) return alert("No se pudo eliminar el curso.");
    }
    setList((l) => l.filter((x) => (course.id ? x.id !== course.id : x.code !== course.code)));
  };

  React.useEffect(() => {
    document.documentElement.dataset.theme = tweaks.theme || "light";
    document.documentElement.dataset.accent = tweaks.accent || "coral";
  }, [tweaks.theme, tweaks.accent]);

  return (
    <div className="app">
      <Sidebar active={PAGE_ID} plus={plus} />
      <div>
        <Topbar title={data.title || PAGE_TITLE} sub={data.sub || PAGE_SUB} streak={data.streak ?? "17"} xp={data.xp || "4.180"} coins={data.coins ?? "320"} avatar={data.avatar || "MR"} plus={plus} tweaks={tweaks} setTweak={setTweak} />
        <main className="page">
          <div className="col">
            <CoursesHead onAdd={() => setAdding(true)} data={data.courses} csrf={data.csrf} />
            <CoursesStats n={list.length} ex={list.reduce((a, c) => a + c.exams.length, 0)} data={data.courses} />
            <CoursesGrid plus={plus} list={list} onDelete={setDelTarget} />
            <ExtCard />
          </div>
        </main>
      </div>
      <TabBar active={PAGE_ID} />
      {adding && <AddCourseModal onClose={() => setAdding(false)} onAdd={addCourse} />}
      {delTarget && <DeleteCourseModal course={delTarget} onClose={() => setDelTarget(null)} onConfirm={deleteCourse} />}

      {!data.live && <TweaksPanel title="Tweaks">
        <PlanTweak plus={plus} setTweak={setTweak} />
        <TweakSection label="Tema">
          <TweakRadio label="Modo" value={tweaks.theme} onChange={(v) => setTweak("theme", v)}
            options={[{ value: "light", label: "Claro" }, { value: "dark", label: "Oscuro" }]} />
          <TweakSelect label="Acento" value={tweaks.accent} onChange={(v) => setTweak("accent", v)}
            options={[{ value: "coral", label: "Coral (marca)" }, { value: "lime", label: "Lima" }, { value: "violet", label: "Violeta" }, { value: "sky", label: "Cielo" }]} />
        </TweakSection>
      </TweaksPanel>}
    </div>
  );
}
