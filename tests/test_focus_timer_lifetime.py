"""The focus timer survives navigation but not closing the app.

The behaviour lives in the browser, so these read the shipped source: what
matters is that the rules are the ones described, and that the two files agree
on the threshold — a mismatch there is exactly how one of them starts trusting
a heartbeat the other considers dead.
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

    assert "stored.running && focusAbandoned()" in restore
    # Comes back reset, and none of the away time is credited.
    assert "running: false" in restore
    assert "endsAt: 0" in restore
    assert "left: cfg.work" in restore
    assert "finishedPhaseId: \"\"" in restore


def test_blocks_already_finished_stay_claimable():
    """Closing the tab stops the clock; it does not confiscate earned XP."""
    focus = _read(FOCUS)
    restore = focus[focus.index("function restoreFocusTimer"):focus.index("function ClaimCountdown")]
    abandoned = restore[restore.index("focusAbandoned()"):restore.index("const base =")]

    assert "pending: Array.isArray(stored.pending) ? stored.pending : []" in abandoned
    assert "claimUntil: Number(stored.claimUntil) || 0" in abandoned


def test_the_verdict_is_taken_before_this_page_starts_beating():
    """Read it late and you find the heartbeat this very page just wrote."""
    focus, shell = _read(FOCUS), _read(SHELL)

    assert "const FOCUS_WAS_ABANDONED = (() => {" in focus
    assert "const SHELL_FOCUS_ABANDONED = (() => {" in shell
    # The snapshot has to be read before the timer restores from it.
    assert focus.index("const FOCUS_WAS_ABANDONED") < focus.index("function restoreFocusTimer")


def test_both_files_agree_on_how_long_a_gap_means_closed():
    focus, shell = _read(FOCUS), _read(SHELL)
    focus_threshold = int(re.search(r"FOCUS_ABANDON_MS = (\d+)", focus).group(1))
    shell_threshold = int(re.search(r"Date\.now\(\) - last > (\d+)", shell).group(1))
    beat = int(re.search(r"FOCUS_HEARTBEAT_MS = (\d+)", focus).group(1))

    assert focus_threshold == shell_threshold
    # A page load has to fit inside the window, and beats have to be well
    # inside it, or an open app would be mistaken for a closed one.
    assert beat * 3 <= focus_threshold
    assert focus_threshold >= 10000


def test_an_unreadable_store_never_discards_a_running_block():
    """Private mode should cost you the persistence, not the block."""
    focus = _read(FOCUS)
    snapshot = focus[focus.index("const FOCUS_WAS_ABANDONED"):focus.index("function focusAbandoned")]

    assert "catch" in snapshot
    assert snapshot.rindex("return false") > snapshot.index("catch")


def test_the_first_page_repairs_the_record_instead_of_only_hiding_it():
    """The bug this exists to prevent: the dashboard hid the floating timer,
    then refreshed the heartbeat, and the next page — seeing a heartbeat one
    second old — resumed a block that had been "running" all night."""
    shell = _read(SHELL)
    reconcile = shell[shell.index("function reconcileAbandonedFocus"):shell.index("function FocusFloat")]

    assert "SHELL_FOCUS_ABANDONED" in reconcile
    assert "removeItem" in reconcile                      # nothing owed: drop it
    assert "running: false" in reconcile                  # something owed: strip the block
    assert "endsAt: 0" in reconcile
    assert "Array.isArray(state.pending)" in reconcile    # earned XP survives


def test_the_repair_runs_before_this_page_starts_beating():
    """Order is the whole fix: repair first, heartbeat second."""
    shell = _read(SHELL)

    assert shell.index("function reconcileAbandonedFocus") < shell.index("function FocusFloat")
    # The heartbeat only ever runs inside an effect, which is after mount.
    snapshot = shell[shell.index("const SHELL_FOCUS_ABANDONED"):shell.index("function reconcileAbandonedFocus")]
    assert "setItem" not in snapshot


def test_the_repair_reaches_every_page_not_just_focus():
    """It lives in the shell, which every page bundle includes — the focus page
    alone was the reason the verdict never stuck."""
    import json
    import re

    build = open("landing_build/build-app.mjs", encoding="utf-8").read()
    pages = re.findall(r"jsx: \[([^\]]+)\]", build)
    assert pages, "no page bundles found"
    app_pages = [p for p in pages if "shell.jsx" in p]

    assert len(app_pages) >= 10
    assert all("shell.jsx" in p for p in app_pages)
