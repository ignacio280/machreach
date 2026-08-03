/* Page bootstrap — Plan.
   Lifted from the design project's "MachReach Plan.html" inline App(); the
   ReactDOM.createRoot call is appended by build-app.mjs, not written here. */

const PAGE_ID = "plan";
const PAGE_TITLE = "Plan";
const PAGE_SUB = "Planificación semanal";

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "theme": "light",
  "accent": "coral",
  "plus": true
}/*EDITMODE-END*/;

function App() {
  const data = window.__MACHREACH_APP__ || {};
  const [tweaks, setTweak] = useTweaks(TWEAK_DEFAULTS);
  const plus = data.live ? !!data.plus : !!tweaks.plus;
  const [plan, setPlan] = React.useState(data.plan || null);
  // Week paging is read-only: only the live week (offset 0) can be generated.
  const [week, setWeek] = React.useState({ offset: 0, busy: false });
  const showWeek = async (offset) => {
    if (!data.live) return;
    setWeek({ offset, busy: true });
    try {
      const response = await fetch("/api/student/planner/week?offset=" + offset);
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || "No se pudo cargar la semana.");
      setPlan(body.plan || { live: true, week_start: body.week_start, days: [], exams: [], done: [], availability: data.plan?.availability || [] });
      setWeek({ offset: body.week_offset ?? offset, busy: false });
    } catch (error) {
      setWeek((w) => ({ ...w, busy: false }));
      alert(error.message || "No se pudo cargar la semana.");
    }
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
          {plus ? (
            <div className="dash">
              <div className="col">
                <PlanHero data={plan} />
                <Availability data={plan} csrf={data.csrf} onPlan={(p) => { setPlan(p); setWeek({ offset: 0, busy: false }); }} locked={week.offset !== 0} />
                <WeekGrid data={plan} csrf={data.csrf} week={data.live ? week : null} onWeek={data.live ? showWeek : null} />
              </div>
              <div className="col">
                <WhyPanel data={plan} />
                <RiskPanel data={plan} />
              </div>
            </div>
          ) : <PlanLocked />}
        </main>
      </div>
      <TabBar active={PAGE_ID} />

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
