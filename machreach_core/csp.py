"""Nonce and extract legacy inline HTML so CSP can reject unsafe-inline."""
from __future__ import annotations

import hashlib
import html
import re
import secrets


_STYLE_ATTR = re.compile(r"\s+style\s*=\s*([\"'])(.*?)\1", re.IGNORECASE | re.DOTALL)
_EVENT_ATTR = re.compile(r"\s+on([a-z]+)\s*=\s*([\"'])(.*?)\2", re.IGNORECASE | re.DOTALL)
_CLASS_ATTR = re.compile(r"(\s+class\s*=\s*)([\"'])(.*?)\2", re.IGNORECASE | re.DOTALL)
_NONCE_ATTR = re.compile(r"\snonce\s*=", re.IGNORECASE)
_DYNAMIC_HANDLER = re.compile(r"(?:['\"]\s*\+|\+\s*['\"]|\\['\"])")


def new_nonce() -> str:
    return secrets.token_urlsafe(18)


def _identifier(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"


def _tag_end(document: str, start: int) -> int:
    quote = ""
    escaped = False
    for pos in range(start + 1, len(document)):
        char = document[pos]
        if escaped:
            escaped = False
            continue
        if char == "\\" and quote:
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = ""
            continue
        if char in ("'", '"'):
            quote = char
        elif char == ">":
            return pos
    return -1


def _add_class(attrs: str, class_name: str) -> str:
    match = _CLASS_ATTR.search(attrs)
    if not match:
        return attrs + f' class="{class_name}"'
    classes = (match.group(3) + " " + class_name).strip()
    replacement = f"{match.group(1)}{match.group(2)}{classes}{match.group(2)}"
    return attrs[:match.start()] + replacement + attrs[match.end():]


def harden_html(document: str, nonce: str) -> str:
    """Rewrite block/attribute inline code into nonce-authorized assets.

    Event listeners use delegation, so controls created later with innerHTML
    retain the behavior their former inline handler provided.
    """
    styles: dict[str, str] = {}
    handlers: dict[tuple[str, str], str] = {}
    dynamic_events: set[str] = set()
    out: list[str] = []
    cursor = 0
    length = len(document)

    def replace_event(match: re.Match) -> str:
        event_name = match.group(1).lower()
        code = html.unescape(match.group(3)).strip()
        # Some legacy HTML fragments are assembled inside JavaScript strings,
        # so their handler arguments are completed later (for example with a
        # course id). Compiling that unfinished expression into this response's
        # handler map would make the whole nonce script invalid. Preserve the
        # concatenation in a data attribute and execute only its final, narrow
        # function-call form through the dispatcher below.
        if _DYNAMIC_HANDLER.search(code):
            dynamic_events.add(event_name)
            return f' data-mr-csp-call-{event_name}={match.group(2)}{match.group(3)}{match.group(2)}'
        handler_id = _identifier("mr-csp-handler", event_name + "\0" + code)
        handlers[(event_name, handler_id)] = code
        return f' data-mr-csp-{event_name}="{handler_id}"'

    while cursor < length:
        start = document.find("<", cursor)
        if start < 0:
            out.append(document[cursor:])
            break
        out.append(document[cursor:start])
        if document.startswith("<!--", start):
            end_comment = document.find("-->", start + 4)
            if end_comment < 0:
                out.append(document[start:])
                break
            out.append(document[start:end_comment + 3])
            cursor = end_comment + 3
            continue

        end = _tag_end(document, start)
        if end < 0:
            out.append(document[start:])
            break
        raw = document[start:end + 1]
        opening = re.match(r"<([A-Za-z][A-Za-z0-9:-]*)(.*)>$", raw, re.DOTALL)
        if not opening:
            out.append(raw)
            cursor = end + 1
            continue

        tag_name = opening.group(1).lower()
        attrs = opening.group(2)
        if tag_name in {"script", "style"}:
            if not _NONCE_ATTR.search(attrs):
                attrs += f' nonce="{nonce}"'
            out.append(f"<{opening.group(1)}{attrs}>")
            cursor = end + 1
            continue

        style_match = _STYLE_ATTR.search(attrs)
        if style_match:
            css = html.unescape(style_match.group(2)).strip()
            attrs = attrs[:style_match.start()] + attrs[style_match.end():]
            if css:
                class_name = _identifier("mr-csp-style", css)
                styles[class_name] = css
                attrs = _add_class(attrs, class_name)

        attrs = _EVENT_ATTR.sub(replace_event, attrs)
        out.append(f"<{opening.group(1)}{attrs}>")
        cursor = end + 1

    hardened = "".join(out)

    # Raw HTML fragments embedded in JavaScript strings are not always reached
    # by the tag scanner (a comparison using < can look like a tag boundary).
    # Attribute-only fallbacks keep those future innerHTML nodes CSP-safe too.
    def replace_remaining_style(match: re.Match) -> str:
        css = html.unescape(match.group(2)).strip()
        if not css:
            return ""
        style_id = _identifier("mr-csp-style", css)
        styles[style_id] = css
        return f' data-mr-csp-style="{style_id}"'

    hardened = _STYLE_ATTR.sub(replace_remaining_style, hardened)
    hardened = _EVENT_ATTR.sub(replace_event, hardened)

    def add_remaining_nonce(match: re.Match) -> str:
        attrs = match.group(2)
        if _NONCE_ATTR.search(attrs):
            return match.group(0)
        return f'<{match.group(1)}{attrs} nonce="{nonce}">'

    hardened = re.sub(
        r"<(script|style)\b([^>]*)>",
        add_remaining_nonce,
        hardened,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if styles:
        rules = "".join(
            f".{style_id}{{{css}}}[data-mr-csp-style=\"{style_id}\"]{{{css}}}"
            for style_id, css in styles.items()
        ).replace("</style", "<\\/style")
        block = f'<style nonce="{nonce}" data-mr-csp-extracted>{rules}</style>'
        hardened = hardened.replace("</head>", block + "</head>", 1)

    if handlers or dynamic_events:
        by_event: dict[str, list[tuple[str, str]]] = {}
        for (event_name, handler_id), code in handlers.items():
            by_event.setdefault(event_name, []).append((handler_id, code))
        for event_name in dynamic_events:
            by_event.setdefault(event_name, [])
        registrations = []
        for event_name, event_handlers in by_event.items():
            function_map = ",".join(
                f'"{handler_id}":function(event){{{code}}}'
                for handler_id, code in event_handlers
            ).replace("</script", "<\\/script")
            registrations.append(
                "(function(){"
                f'var eventName="{event_name}",attr="data-mr-csp-{event_name}",'
                f'callAttr="data-mr-csp-call-{event_name}",handlers={{{function_map}}};'
                "function splitArgs(value){var out=[],part='',quote='',escaped=false,depth=0;"
                "for(var i=0;i<value.length;i++){var ch=value.charAt(i);"
                "if(escaped){part+=ch;escaped=false;continue;}"
                "if(quote){part+=ch;if(ch==='\\\\'){escaped=true;}else if(ch===quote){quote='';}continue;}"
                "if(ch==='\\\"'||ch===\"'\"){quote=ch;part+=ch;continue;}"
                "if(ch==='('||ch==='['||ch==='{'){depth++;part+=ch;continue;}"
                "if(ch===')'||ch===']'||ch==='}'){depth--;part+=ch;continue;}"
                "if(ch===','&&depth===0){out.push(part.trim());part='';continue;}part+=ch;}"
                "if(part.trim())out.push(part.trim());return out;}"
                "function valueOf(token,node,event){token=token.trim();"
                "if(token==='this')return node;if(token==='event')return event;"
                "if(token==='this.value')return node.value;if(token==='this.checked')return node.checked;"
                "if(token==='this.id')return node.id;if(token==='this.name')return node.name;"
                "var file=/^this\\.files\\[(\\d+)\\]$/.exec(token);"
                "if(file)return node.files&&node.files[Number(file[1])];"
                "if(token==='event.target')return event.target;"
                "if(token==='event.currentTarget')return event.currentTarget;"
                "if(token==='true')return true;if(token==='false')return false;if(token==='null')return null;"
                "if(/^[-+]?\\d+(?:\\.\\d+)?$/.test(token))return Number(token);"
                "if((token.charAt(0)==='\\\"'&&token.charAt(token.length-1)==='\\\"')||"
                "(token.charAt(0)===\"'\"&&token.charAt(token.length-1)===\"'\"))"
                "return token.slice(1,-1).replace(/\\\\([\\\\'\"])/g,'$1');"
                "throw new Error('Unsupported CSP handler argument');}"
                "function invoke(code,node,event){"
                "var closest=/^this\\.closest\\((['\\\"])(.*?)\\1\\)\\.hidden\\s*=\\s*true;?$/.exec(code);"
                "if(closest){var target=node.closest(closest[2]);if(target)target.hidden=true;return;}"
                "var match=/^\\s*([A-Za-z_$][\\w$]*(?:\\.[A-Za-z_$][\\w$]*)*)\\s*\\((.*)\\)\\s*;?\\s*$/.exec(code);"
                "if(!match)throw new Error('Unsupported CSP handler');var owner=window,parts=match[1].split('.');"
                "for(var i=0;i<parts.length-1;i++){owner=owner[parts[i]];}var fn=owner&&owner[parts[parts.length-1]];"
                "if(typeof fn!=='function')throw new Error('Unknown CSP handler');"
                "var args=match[2].trim()?splitArgs(match[2]).map(function(x){return valueOf(x,node,event);}):[];"
                "return fn.apply(node,args);}"
                "document.addEventListener(eventName,function(event){"
                "var node=event.target;"
                "while(node&&node!==document){"
                "var id=node.getAttribute&&node.getAttribute(attr);"
                "var call=node.getAttribute&&node.getAttribute(callAttr);var result;"
                "if(id&&handlers[id]){result=handlers[id].call(node,event);"
                "if(result===false){event.preventDefault();event.stopPropagation();}"
                "if(event.cancelBubble){break;}}else if(call){try{result=invoke(call,node,event);"
                "if(result===false){event.preventDefault();event.stopPropagation();}}"
                "catch(error){console.error('MachReach CSP handler failed',error);}}node=node.parentElement;"
                "}},true);"
                "})();"
            )
        script = (
            f'<script nonce="{nonce}" data-mr-csp-events>'
            + "".join(registrations)
            + "</script>"
        )
        hardened = hardened.replace("</body>", script + "</body>", 1)
    return hardened
