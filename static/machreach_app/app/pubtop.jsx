function PubTop({ tag, action, tweaks, setTweak }) {
  return (
    <header className="pub-top">
      <div className="pub-top-in">
        <a className="pub-brand" href="/"><Mark size={30} /><b>MachReach</b></a>
        {tag && <span className="pub-tag">{tag}</span>}
        <span className="pub-sp" />
        <div className="pub-act">
          {action}
          <button className="pub-ib" aria-label="Cambiar tema" onClick={() => setTweak("theme", tweaks.theme === "dark" ? "light" : "dark")}>
            {tweaks.theme === "dark" ? <IconSun size={17} /> : <IconMoon size={17} />}
          </button>
        </div>
      </div>
    </header>
  );
}

Object.assign(window, { PubTop });
