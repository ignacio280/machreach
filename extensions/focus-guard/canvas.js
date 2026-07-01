// Runs on the student's Canvas.
// Offers a movable course-sync control until MachReach confirms that the
// current student has already imported Canvas courses.

(function () {
  if (window.__mrCanvasSyncInjected) return;
  window.__mrCanvasSyncInjected = true;

  const POSITION_KEY = "mrCanvasWidget:" + location.origin;
  const SYNC_EVENT_KEY = "mrCanvasSyncCompletedAt";
  const EDGE_GAP = 12;
  let host = null;
  let shadow = null;
  let minimized = false;
  let storedPosition = null;
  let miniWasDragged = false;

  function sendMessage(message) {
    return new Promise((resolve) => {
      chrome.runtime.sendMessage(message, (response) => {
        if (chrome.runtime.lastError) {
          resolve({ ok: false, error: chrome.runtime.lastError.message });
          return;
        }
        resolve(response || { ok: false });
      });
    });
  }

  function clamp(value, min, max) {
    return Math.min(Math.max(value, min), Math.max(min, max));
  }

  function clampToViewport(x, y) {
    if (!host) return { x, y };
    const rect = host.getBoundingClientRect();
    return {
      x: clamp(x, EDGE_GAP, window.innerWidth - rect.width - EDGE_GAP),
      y: clamp(y, EDGE_GAP, window.innerHeight - rect.height - EDGE_GAP)
    };
  }

  function setPosition(x, y) {
    if (!host) return;
    const next = clampToViewport(x, y);
    host.style.left = next.x + "px";
    host.style.top = next.y + "px";
    host.style.right = "auto";
    host.style.bottom = "auto";
  }

  async function saveWidgetState() {
    if (!host) return;
    const rect = host.getBoundingClientRect();
    storedPosition = { x: rect.left, y: rect.top };
    await chrome.storage.local.set({
      [POSITION_KEY]: {
        x: storedPosition.x,
        y: storedPosition.y,
        minimized
      }
    });
  }

  function removeWidget() {
    if (host) host.remove();
    host = null;
    shadow = null;
  }

  function setMessage(text, kind) {
    if (!shadow) return;
    const message = shadow.getElementById("message");
    if (!message) return;
    message.textContent = text;
    message.className = "message visible" + (kind ? " " + kind : "");
    requestAnimationFrame(() => {
      if (!host) return;
      const rect = host.getBoundingClientRect();
      setPosition(rect.left, rect.top);
    });
  }

  function setBusy(busy) {
    if (!shadow) return;
    const syncButton = shadow.getElementById("sync");
    const minimizeButton = shadow.getElementById("minimize");
    if (syncButton) syncButton.disabled = busy;
    if (minimizeButton) minimizeButton.disabled = busy;
  }

  function setMinimized(next) {
    if (!host || !shadow) return;
    const rect = host.getBoundingClientRect();
    minimized = next;
    shadow.getElementById("panel").hidden = minimized;
    shadow.getElementById("mini").hidden = !minimized;
    requestAnimationFrame(() => {
      setPosition(rect.left, rect.top);
      saveWidgetState();
    });
  }

  function enableDrag(handle, isMini) {
    let drag = null;

    handle.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return;
      const rect = host.getBoundingClientRect();
      drag = {
        pointerId: event.pointerId,
        startX: event.clientX,
        startY: event.clientY,
        left: rect.left,
        top: rect.top,
        moved: false
      };
      try {
        handle.setPointerCapture(event.pointerId);
      } catch (_) {}
      handle.classList.add("dragging");
      event.preventDefault();
    });

    window.addEventListener("pointermove", (event) => {
      if (!drag || drag.pointerId !== event.pointerId) return;
      const dx = event.clientX - drag.startX;
      const dy = event.clientY - drag.startY;
      if (Math.abs(dx) + Math.abs(dy) > 4) drag.moved = true;
      setPosition(drag.left + dx, drag.top + dy);
    });

    function finishDrag(event) {
      if (!drag || drag.pointerId !== event.pointerId) return;
      if (isMini) miniWasDragged = drag.moved;
      drag = null;
      handle.classList.remove("dragging");
      try {
        handle.releasePointerCapture(event.pointerId);
      } catch (_) {}
      saveWidgetState();
    }

    window.addEventListener("pointerup", finishDrag);
    window.addEventListener("pointercancel", finishDrag);
  }

  async function syncCourses() {
    setBusy(true);
    setMessage("Leyendo tus cursos...");
    try {
      const response = await fetch(
        location.origin + "/api/v1/courses?per_page=100&enrollment_state=active&include[]=term",
        { credentials: "same-origin", headers: { Accept: "application/json" } }
      );
      if (!response.ok) {
        setMessage(
          response.status === 401 || response.status === 403
            ? "Inicia sesión en Canvas y vuelve a intentarlo."
            : "No pude leer tus cursos desde Canvas.",
          "error"
        );
        setBusy(false);
        return;
      }

      const data = JSON.parse((await response.text()).replace(/^while\(1\);/, ""));
      const courses = (Array.isArray(data) ? data : [])
        .filter((course) => course && course.id && !course.access_restricted_by_date)
        .map((course) => ({
          id: course.id,
          name: course.name,
          course_code: course.course_code,
          term: course.term ? { name: course.term.name } : null
        }));

      if (!courses.length) {
        setMessage("No encontré cursos activos en tu Canvas.", "error");
        setBusy(false);
        return;
      }

      setMessage("Enviando " + courses.length + " cursos a MachReach...");
      const result = await sendMessage({ type: "mrImportCourses", courses });
      if (result.ok) {
        setMessage("Listo: " + (result.saved || 0) + " cursos sincronizados.", "success");
        window.setTimeout(removeWidget, 1400);
      } else if (result.noToken) {
        setMessage("Abre MachReach con tu sesión iniciada y reintenta.", "error");
        setBusy(false);
      } else {
        setMessage("No se pudo importar. Inténtalo de nuevo.", "error");
        setBusy(false);
      }
    } catch (error) {
      setMessage("Error inesperado: " + String(error), "error");
      setBusy(false);
    }
  }

  async function mountWidget() {
    if (!document.body || host) return;

    const stored = await chrome.storage.local.get(POSITION_KEY);
    const state = stored[POSITION_KEY] || {};
    storedPosition = Number.isFinite(state.x) && Number.isFinite(state.y)
      ? { x: state.x, y: state.y }
      : null;
    minimized = !!state.minimized;

    host = document.createElement("div");
    host.id = "machreach-canvas-sync";
    host.style.cssText =
      "position:fixed;z-index:2147483647;left:0;top:0;" +
      "font-family:Nunito,Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;";
    shadow = host.attachShadow({ mode: "open" });
    shadow.innerHTML = `
      <style>
        * { box-sizing: border-box; }
        button { font: inherit; }
        .panel {
          width: min(352px, calc(100vw - 24px));
          overflow: hidden;
          color: #fff;
          background: #ff7a3d;
          border: 1px solid rgba(117, 45, 13, .2);
          border-radius: 14px;
          box-shadow: 0 8px 28px rgba(20, 18, 30, .28);
        }
        .row {
          display: grid;
          grid-template-columns: 34px minmax(0, 1fr) 36px;
          align-items: stretch;
          min-height: 50px;
        }
        .drag, .minimize, .mini {
          display: grid;
          place-items: center;
          border: 0;
          color: inherit;
          background: transparent;
          cursor: pointer;
          touch-action: none;
        }
        .drag {
          cursor: grab;
          color: rgba(255, 255, 255, .84);
          font-size: 17px;
          letter-spacing: -2px;
        }
        .drag.dragging, .mini.dragging { cursor: grabbing; }
        .sync {
          min-width: 0;
          border: 0;
          padding: 0 8px;
          color: #fff;
          background: transparent;
          font-weight: 900;
          font-size: 14px;
          line-height: 1.2;
          text-align: left;
          cursor: pointer;
        }
        .sync:disabled { opacity: .72; cursor: wait; }
        .minimize {
          font-size: 23px;
          line-height: 1;
        }
        .drag:hover, .minimize:hover, .drag:focus-visible, .minimize:focus-visible {
          background: rgba(91, 34, 9, .16);
          outline: none;
        }
        .message {
          display: none;
          margin: 0;
          padding: 10px 13px;
          color: #fff8e1;
          background: #1a1a1f;
          font-size: 12px;
          font-weight: 800;
          line-height: 1.4;
        }
        .message.visible { display: block; }
        .message.success { background: #1e6b43; }
        .message.error { background: #9b2f25; }
        .mini {
          width: 50px;
          height: 50px;
          border: 1px solid rgba(117, 45, 13, .2);
          border-radius: 50%;
          color: #fff;
          background: #ff7a3d;
          box-shadow: 0 8px 28px rgba(20, 18, 30, .28);
          font-size: 21px;
          cursor: grab;
        }
        .mini:hover, .mini:focus-visible {
          background: #e9652b;
          outline: 3px solid rgba(255, 122, 61, .24);
          outline-offset: 2px;
        }
        [hidden] { display: none !important; }
      </style>
      <div id="panel" class="panel">
        <div class="row">
          <button id="drag" class="drag" type="button" aria-label="Mover sincronización" title="Mover">⠿</button>
          <button id="sync" class="sync" type="button">📚 Sincronizar cursos con MachReach</button>
          <button id="minimize" class="minimize" type="button" aria-label="Minimizar" title="Minimizar">−</button>
        </div>
        <p id="message" class="message" role="status" aria-live="polite"></p>
      </div>
      <button id="mini" class="mini" type="button" aria-label="Mostrar sincronización" title="Mostrar sincronización">📚</button>
    `;

    document.body.appendChild(host);

    const panel = shadow.getElementById("panel");
    const mini = shadow.getElementById("mini");
    panel.hidden = minimized;
    mini.hidden = !minimized;

    shadow.getElementById("sync").addEventListener("click", syncCourses);
    shadow.getElementById("minimize").addEventListener("click", () => setMinimized(true));
    mini.addEventListener("click", () => {
      if (miniWasDragged) {
        miniWasDragged = false;
        return;
      }
      setMinimized(false);
    });

    enableDrag(shadow.getElementById("drag"), false);
    enableDrag(mini, true);

    requestAnimationFrame(() => {
      const rect = host.getBoundingClientRect();
      if (storedPosition) {
        setPosition(storedPosition.x, storedPosition.y);
      } else {
        setPosition(window.innerWidth - rect.width - 18, window.innerHeight - rect.height - 18);
      }
    });
  }

  async function initialize() {
    const status = await sendMessage({ type: "mrCanvasSyncStatus" });
    if (status.ok && status.synced) return;
    if (document.body) {
      mountWidget();
    } else {
      document.addEventListener("DOMContentLoaded", mountWidget, { once: true });
    }
  }

  chrome.storage.onChanged.addListener((changes, areaName) => {
    if (areaName === "local" && changes[SYNC_EVENT_KEY] && changes[SYNC_EVENT_KEY].newValue) {
      removeWidget();
    }
  });

  window.addEventListener("resize", () => {
    if (!host) return;
    const rect = host.getBoundingClientRect();
    setPosition(rect.left, rect.top);
  });

  initialize();
})();
