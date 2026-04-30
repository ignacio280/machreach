"""Serve the Claude-exported MachReach landing exactly.

The export lives in ``static/machreach_landing``. We only rewrite relative asset
URLs so Flask can serve the files from /static.
"""
from __future__ import annotations

from pathlib import Path


_ROOT = Path(__file__).resolve().parent.parent
_LANDING_DIR = _ROOT / "static" / "machreach_landing"
_LANDING_HTML = _LANDING_DIR / "MachReach Landing.html"
_STATIC_PREFIX = "/static/machreach_landing/"


def render_landing_page(lang: str = "es") -> str:
    html = _LANDING_HTML.read_text(encoding="utf-8")
    replacements = {
        'href="styles.css"': f'href="{_STATIC_PREFIX}styles.css"',
        '<script src="https://unpkg.com/react@18.3.1/umd/react.development.js" integrity="sha384-hD6/rw4ppMLGNu3tX5cjIb+uRZ7UkRJ6BPkLpg4hAu/6onKUg4lLsHAs9EBPT82L" crossorigin="anonymous"></script>': f'<script src="{_STATIC_PREFIX}vendor/react.development.js"></script>',
        '<script src="https://unpkg.com/react-dom@18.3.1/umd/react-dom.development.js" integrity="sha384-u6aeetuaXnQ38mYT8rp6sbXaQe3NL9t+IBXmnYxwkUI2Hw4bsp2Wvmx4yRQF1uAm" crossorigin="anonymous"></script>': f'<script src="{_STATIC_PREFIX}vendor/react-dom.development.js"></script>',
        '<script src="https://unpkg.com/@babel/standalone@7.29.0/babel.min.js" integrity="sha384-m08KidiNqLdpJqLq95G/LEi8Qvjl/xUYll3QILypMoQ65QorJ9Lvtp2RXYGBFj1y" crossorigin="anonymous"></script>': f'<script src="{_STATIC_PREFIX}vendor/babel.min.js"></script>',
        'href="#" className="logo"': 'href="/" className="logo"',
    }
    for old, new in replacements.items():
        html = html.replace(old, new)
    for filename in (
        "icons.jsx",
        "tweaks-panel.jsx",
        "hero.jsx",
        "features.jsx",
        "sections.jsx",
        "pricing.jsx",
    ):
        code = (_LANDING_DIR / filename).read_text(encoding="utf-8")
        html = html.replace(
            f'<script type="text/babel" src="{filename}"></script>',
            f'<script type="text/babel">\n{code}\n</script>',
        )
    return html
