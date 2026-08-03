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
  // Join a course that already exists in the university catalog instead of
  // creating a duplicate. Returns the new student_course id, or null if the
  // student is already in it (nothing to append to the grid).
  const joinCatalog = async (catalogId) => {
    const response = await fetch("/api/student/courses/catalog/" + catalogId + "/join", { method: "POST", headers: { "X-CSRFToken": data.csrf || "" } });
    const body = await response.json();
    if (!response.ok) { alert(body.error || "No se pudo agregar el curso."); return { failed: true }; }
    if (body.already_joined) { alert("Ya tienes este curso en tus ramos."); return { failed: true }; }
    return { id: body.id };
  };
  const addCourse = async (v) => {
    let id = null;
    let course = v;
    if (data.live) {
      if (v.catalogId) {
        const joined = await joinCatalog(v.catalogId);
        if (joined.failed) return;
        id = joined.id;
      } else {
        const response = await fetch("/api/student/courses/manual", { method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": data.csrf || "" }, body: JSON.stringify({ name: v.name, code: v.code }) });
        const body = await response.json();
        if (response.status === 409 && body.catalog_id) {
          // Someone at this university already created it — join theirs.
          const joined = await joinCatalog(body.catalog_id);
          if (joined.failed) return;
          id = joined.id;
          course = { ...v, name: body.course?.name || v.name, code: body.course?.code || v.code };
        } else if (!response.ok) {
          return alert(body.error || "No se pudo agregar el curso.");
        } else {
          id = body.id;
        }
      }
    }
    setList((l) => [...l, { id, code: course.code || "SIN CÓDIGO", name: course.name, cc: course.color, origin: "Agregado a mano", sessions: 0, studied: "0h 00m", next: "Sin evaluaciones próximas", exams: [], bench: { hrs: "—", grade: "—", mine: "0h", delta: "—", n: 0 } }]);
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
      {adding && <AddCourseModal onClose={() => setAdding(false)} onAdd={addCourse} live={!!data.live} />}
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
