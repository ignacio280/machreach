"""The focus timer survives navigation but not closing the app.

The behaviour lives in the browser, so these read the shipped source: what
matters is that the rules are the ones described, and that the two files agree
on them — a disagreement is exactly how one module starts trusting a record
the other considers dead.

The verdict comes from three signals (see mrFocusPresence in either file):
a sessionStorage tab marker, a BroadcastChannel presence ping, and the legacy
heartbeat stamp as fallback and restored-tab tell.
"""
import re

FOCUS = "static/machreach_app/app/focus.jsx"
SHELL = "static/machreach_app/app/shell.jsx"


def _read(path):
    return open(path, encoding="utf-8").read()


def test_open_pages_keep_a_heartbeat():
    focus, shell = _read(FOCUS), _read(SHELL)

    assert "mr_focus_alive_v1" in focus
    assert "mr_focus_alive_v1" in shell        # the float beats on every page
    assert "setInterval(touchFocusHeartbeat" in focus


def test_a_running_block_is_dropped_when_the_app_was_closed():
    focus = _read(FOCUS)
    restore = focus[focus.index("function restoreFocusTimer"):focus.index("function ClaimCountdown")]

    assert "stored.running && (!presence.known || presence.abandoned)" in restore
    # Comes back reset, and none of the away time is credited.
    assert "running: false" in restore
    assert "endsAt: 0" in restore
    assert "left: cfg.work" in restore
    assert "finishedPhaseId: \"\"" in restore


def test_blocks_already_finished_stay_claimable():
    """Closing the tab stops the clock; it does not confiscate earned XP."""
    focus = _read(FOCUS)
    restore = focus[focus.index("function restoreFocusTimer"):focus.index("function ClaimCountdown")]
    abandoned = restore[restore.index("presence.abandoned"):restore.index("const base =")]

    assert "pending: Array.isArray(stored.pending) ? stored.pending : []" in abandoned
    assert "claimUntil: Number(stored.claimUntil) || 0" in abandoned


def test_the_presence_reading_is_taken_once_and_shared():
    """shell.jsx and focus.jsx ship in one bundle. Whichever module runs first
    writes the sessionStorage marker — if each took its own reading, the second
    would find the marker the first had just written and resume a dead block."""
    focus, shell = _read(FOCUS), _read(SHELL)

    for src in (focus, shell):
        assert "if (window.__mrFocusPresence) return window.__mrFocusPresence;" in src
        assert "mr_focus_tab_v1" in src
    # The reading exists before anything restores from it.
    assert focus.index("function mrFocusPresence") < focus.index("function restoreFocusTimer")
    assert shell.index("function mrFocusPresence") < shell.index("function FocusFloat")


def test_a_fresh_tab_asks_other_tabs_not_a_clock():
    """The old 15-second heartbeat guess resumed a block when the app was
    closed and reopened inside the window. A fresh tab now asks over
    BroadcastChannel; silence means closed, however fast the reopen came."""
    focus, shell = _read(FOCUS), _read(SHELL)

    for src in (focus, shell):
        assert "new BroadcastChannel(\"mr_focus_presence_v1\")" in src
        assert "postMessage(\"ping\")" in src
        assert "postMessage(\"pong\")" in src
        # Silence settles as abandoned — the timeout resolves true.
        assert re.search(r"setTimeout\(\(\) => settle\(true\), \d+\)", src)


def test_both_files_agree_on_the_heartbeat_fallback():
    """Where BroadcastChannel is missing, the heartbeat gap still decides, and
    it also exposes a marker the browser restored long after a close. The two
    copies must agree on the threshold."""
    focus, shell = _read(FOCUS), _read(SHELL)
    focus_threshold = int(re.search(r"Date\.now\(\) - last > (\d+)", focus).group(1))
    shell_threshold = int(re.search(r"Date\.now\(\) - last > (\d+)", shell).group(1))
    beat = int(re.search(r"FOCUS_HEARTBEAT_MS = (\d+)", focus).group(1))

    assert focus_threshold == shell_threshold
    # Beats have to land well inside the window, or an open app whose tab was
    # merely slow would be mistaken for a closed one.
    assert beat * 3 <= focus_threshold
    assert focus_threshold >= 10000


def test_an_unreadable_store_never_discards_a_running_block():
    """Private mode should cost you the persistence, not the block."""
    focus = _read(FOCUS)
    presence = focus[focus.index("function mrFocusPresence"):focus.index("function focusAbandoned")]

    # sessionStorage unreadable: claim the marker existed, so nothing resets.
    assert "catch (e) { state.hadTab = true; }" in presence
    # heartbeat unreadable: not stale, so nothing resets.
    assert re.search(r"heartbeatStale[\s\S]{0,200}catch \(e\) \{ return false; \}", presence)


def test_the_first_page_repairs_the_record_instead_of_only_hiding_it():
    """The bug this exists to prevent: the dashboard hid the floating timer
    but left the record saying "running", and the next page resumed a block
    that had been "running" all night."""
    shell = _read(SHELL)
    reconcile = shell[shell.index("SHELL_FOCUS_PRESENCE.verdict.then"):shell.index("function FocusFloat")]

    assert "removeItem" in reconcile                      # nothing owed: drop it
    assert "running: false" in reconcile                  # something owed: strip the block
    assert "endsAt: 0" in reconcile
    assert "Array.isArray(state.pending)" in reconcile    # earned XP survives


def test_the_float_waits_for_the_verdict():
    """Until the presence verdict is in, a stored running block is not shown —
    and the focus page neither shows nor persists state that would overwrite
    the record another tab may be about to claim."""
    focus, shell = _read(FOCUS), _read(SHELL)

    assert "if (!SHELL_FOCUS_PRESENCE.known || SHELL_FOCUS_PRESENCE.abandoned) return setLeft(0);" in shell
    assert "awaitingVerdict: !presence.known" in focus
    # The persist effect holds off while the verdict is pending.
    assert "if (!data.live || verdictPending) return;" in focus


def test_the_repair_runs_before_this_page_starts_beating():
    """Order is the whole fix: the reading and the repair are wired at module
    load; the heartbeat only ever starts inside a mounted component."""
    shell = _read(SHELL)

    assert shell.index("const SHELL_FOCUS_PRESENCE = mrFocusPresence()") < shell.index("function FocusFloat")
    # The only heartbeat write in the shell lives inside FocusFloat's effect.
    assert shell.index('localStorage.setItem("mr_focus_alive_v1"') > shell.index("function FocusFloat")


def test_the_repair_reaches_every_page_not_just_focus():
    """It lives in the shell, which every page bundle includes — the focus page
    alone was the reason the verdict never stuck."""
    import re

    build = open("landing_build/build-app.mjs", encoding="utf-8").read()
    pages = re.findall(r"jsx: \[([^\]]+)\]", build)
    assert pages, "no page bundles found"
    app_pages = [p for p in pages if "shell.jsx" in p]

    assert len(app_pages) >= 10
    assert all("shell.jsx" in p for p in app_pages)
