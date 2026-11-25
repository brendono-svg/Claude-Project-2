"""Tiny Markdown-to-HTML converter for the reports this package writes (stdlib only).

Covers what report.py and brief.py produce: headings, paragraphs, bullet lists, block
quotes, pipe tables, fenced code, **bold**, `code` and [links](url). Everything is escaped.
"""
from __future__ import annotations

import html
import re


def _inline(text: str) -> str:
    out = html.escape(text, quote=False)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)

    def link(m: re.Match) -> str:
        url = m.group(2)
        if not re.match(r"https?://", url):
            return m.group(1)
        return f'<a href="{html.escape(url)}" target="_blank" rel="noopener">{m.group(1)}</a>'
    return re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", link, out)


def to_html(md: str) -> str:
    lines = md.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("```"):
            j = i + 1
            while j < len(lines) and not lines[j].startswith("```"):
                j += 1
            code = "\n".join(lines[i + 1:j])
            out.append(f"<pre><code>{html.escape(code, quote=False)}</code></pre>")
            i = j + 1
            continue
        m = re.match(r"^(#{1,4})\s+(.*)$", line)
        if m:
            n = len(m.group(1))
            out.append(f"<h{n}>{_inline(m.group(2))}</h{n}>")
            i += 1
            continue
        if line.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            head, body = rows[0], [r for r in rows[1:] if not all(re.fullmatch(r":?-+:?", c) for c in r)]
            t = ["<div class=\"tbl\"><table><thead><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in head) + "</tr></thead><tbody>"]
            t += ["<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>" for r in body]
            out.append("".join(t) + "</tbody></table></div>")
            continue
        if line.startswith(">"):
            buf = []
            while i < len(lines) and lines[i].startswith(">"):
                buf.append(lines[i][1:].strip())
                i += 1
            out.append(f"<blockquote>{_inline(' '.join(buf))}</blockquote>")
            continue
        if re.match(r"^\s*- ", line):
            items = []
            while i < len(lines) and re.match(r"^\s*- ", lines[i]):
                items.append(re.sub(r"^\s*- ", "", lines[i]))
                i += 1
            out.append("<ul>" + "".join(f"<li>{_inline(x)}</li>" for x in items) + "</ul>")
            continue
        if not line.strip() or line.strip() == "---":
            i += 1
            continue
        buf = []
        while i < len(lines) and lines[i].strip() and not re.match(r"^(#|\||>|```|\s*- )", lines[i]):
            buf.append(lines[i])
            i += 1
        out.append(f"<p>{_inline(' '.join(buf))}</p>")
    return "\n".join(out)
