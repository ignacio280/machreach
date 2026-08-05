"""Every asset URL has to change when the code behind it changes.

The service worker serves /static cache-first, so a URL that survives a deploy
is a promise to keep running the old file. That is how a shipped fix can be
invisible to the person who reported the bug.
"""
import re

import app as appmod
from student.app_design import render_live_page


def _asset_versions(markup):
    return set(re.findall(r"/static/machreach_[^\"']*?\?v=([^\"'&]+)", markup))


def test_the_app_bundles_are_keyed_on_the_deploy(client, make_user):
    from machreach_core.db import _exec, get_db

    client_id = make_user("Cache", "cache@assets.test")
    with get_db() as db:
        _exec(db, "UPDATE clients SET academic_setup_complete = 1 WHERE id = %s", (client_id,))
    with client.session_transaction() as session:
        session["client_id"] = client_id
        session["client_name"] = "Cache"
        session["account_type"] = "student"
        session["session_version"] = 0

    versions = _asset_versions(client.get("/student/courses").get_data(as_text=True))

    assert versions, "no versioned assets on the page"
    assert versions == {appmod.DEPLOY_VERSION}, (
        f"assets pinned to something other than the deploy: {versions}"
    )


def test_no_asset_carries_a_hand_written_version():
    """The bundles were pinned to a literal that was last touched by hand, so
    every deploy after it shipped the same URL and the cache kept winning."""
    source = open("student/app_design.py", encoding="utf-8").read()

    assert "DEPLOY_VERSION" in source
    assert not re.search(r'version[^=\n]*=\s*"20\d{6}', source), "a date literal is back"


def test_the_version_changes_when_the_deploy_does():
    first = appmod.resolve_deploy_version("abc123def456789")
    second = appmod.resolve_deploy_version("999888777666555")

    assert first != second
    assert first == "abc123def456"          # short commit, stable within a deploy
    assert appmod.resolve_deploy_version(None).startswith("dev-")


def test_the_shell_reuses_whatever_version_it_is_given():
    markup = render_live_page("cursos", {"live": True}, version="pinned-for-test")

    assert markup is not None
    assert _asset_versions(markup) == {"pinned-for-test"}


def test_the_service_worker_cache_is_renamed_every_deploy(client):
    """Belt to the URL's braces: the old cache is dropped on activate."""
    body = client.get("/sw.js").get_data(as_text=True)

    assert f"'{appmod.DEPLOY_VERSION}'" in body or f'"{appmod.DEPLOY_VERSION}"' in body
    assert "caches.delete" in body
