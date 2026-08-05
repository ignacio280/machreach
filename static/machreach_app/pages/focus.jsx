/* Page bootstrap — Enfoque (focus).
   Lifted from the design project's "MachReach Focus.html" inline App(); the
   ReactDOM.createRoot call is appended by build-app.mjs, not written here. */

const PAGE_ID = "focus";
const PAGE_TITLE = "Enfoque";
const PAGE_SUB = "Sesión de estudio";

/* Focus needs the desktop extension, so phones and phone-sized touch screens
   (a landscape phone is wider than 760px) get the notice instead of a timer.
   The server also refuses on a mobile user agent. */
const PHONE_QUERY = "(max-width: 760px), (pointer: coarse) and (max-width: 1180px)";

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "theme": "light",
  "accent": "coral",
  "plus": true
}/*EDITMODE-END*/;

function App() {
  const data = window.__MACHREACH_APP__ || {};
  const [tweaks, setTweak] = useTweaks(TWEAK_DEFAULTS);
  const [scene, setScene] = React.useState("silence");
  const [running, setRunning] = React.useState(false);
  const [courseId, setCourseId] = React.useState(data.focus?.courses?.[0]?.id || "");
  // The claim animation throws orbs at the topbar chips; the chips have to
  // actually go up when they land, or the flourish is a lie. Server totals
  // only — the claim response is what moves these.
  const [earned, setEarned] = React.useState(null);
  const plus = data.live ? !!data.plus : !!tweaks.plus;
  const numeric = (value) => Number(String(value ?? "0").replace(/\./g, "")) || 0;
  const topXp = earned
    ? (numeric(data.xp) + earned.xp).toLocaleString("es-CL")
    : (data.xp || "4.180");
  const topCoins = earned ? numeric(data.coins) + earned.coins : (data.coins ?? "320");
  const topStreak = earned && earned.streak ? earned.streak : (data.streak ?? "17");
  const [phoneBlocked, setPhoneBlocked] = React.useState(() => typeof matchMedia === "function" && matchMedia(PHONE_QUERY).matches);

  React.useEffect(() => {
    document.documentElement.dataset.theme = tweaks.theme || "light";
    document.documentElement.dataset.accent = tweaks.accent || "coral";
  }, [tweaks.theme, tweaks.accent]);

  React.useEffect(() => {
    if (typeof matchMedia !== "function") return undefined;
    const query = matchMedia(PHONE_QUERY);
    const update = () => setPhoneBlocked(query.matches);
    update();
    query.addEventListener?.("change", update);
    return () => query.removeEventListener?.("change", update);
  }, []);

  return (
    <div className="app">
      <Sidebar active={PAGE_ID} plus={plus} />
      <div>
        <Topbar title={data.title || PAGE_TITLE} sub={data.sub || PAGE_SUB} streak={topStreak} xp={topXp} coins={topCoins} avatar={data.avatar || "MR"} plus={plus} tweaks={tweaks} setTweak={setTweak} />
        <main className={"page fx" + (running ? " zen" : "")} data-scene={scene}>
          {phoneBlocked ? <section className="focus-mobile-blocker">
            <span className="focus-mobile-icon"><IconTimer size={30} /></span>
            <div className="mono">Disponible en computador</div>
            <h1>Enfoque necesita una pantalla grande.</h1>
            <p>Para evitar sesiones interrumpidas y mantener el bloqueo de distracciones, usa Enfoque desde tu computador.</p>
            <a href="/student" className="btn btn-primary">Volver al inicio</a>
          </section> : <div className="col">
            <FocusHead scene={scene} data={data.focus} />
            <FocusNotes data={data.focus} />
            <div className="fx-grid">
              <Timer scene={scene} onRun={setRunning} onCourse={setCourseId}
                onReward={(r) => { if (r && !r.expired) setEarned(r); }} data={data.focus} />
              <div className="col fx-tools">
                <Ambience scene={scene} setScene={setScene} />
                <Guard live={!!data.live} />
                <Benchmark plus={plus} data={data.focus} courseId={courseId} />
              </div>
            </div>
          </div>}
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
