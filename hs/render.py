"""Turn a positioned document into HTML for the statement view."""

import re
import textwrap
from html import escape
from pathlib import Path
from urllib.parse import quote

from .model import plain_text
from .viewspec import (
    BRANCH_ONLY,
    FILTER_ALL,
    INDENT_OFF,
    INDENT_TO_CONTENT,
    MAX_LINE_CLIPPING,
    NEXT_FILTERED_NODE,
    NO_FILTERING,
    PLEX_ONLY,
    SHOW_NODE_ID,
)

_HREF_RE = re.compile(
    r"""(<a\b[^>]*?\bhref\s*=\s*)(['"])(.*?)(\2)""",
    re.IGNORECASE | re.DOTALL,
)
_SRC_RE = re.compile(
    r"""(<img\b[^>]*?\bsrc\s*=\s*)(['"])(.*?)(\2)""",
    re.IGNORECASE | re.DOTALL,
)
_LINE_WIDTH = 100


def render_document(document, mark_mode=False):
    """HTML for the statements the current viewspecs leave visible."""
    nodes = visible_nodes(document)
    if not nodes and document.viewspecs.filter_type != NO_FILTERING:
        return (
            "<p style='font-family: Georgia, serif; padding: 12px;'>"
            "The combination of content filter and viewspecs produces "
            "nothing to display. "
            "<a href='hs:/go/%s'>Turn the content filter off</a>."
            "</p>" % quote("#:j", safe="")
        )
    parts = [
        "<html><body style='font-family: Georgia, \"Times New Roman\", serif;"
        " font-size: 12pt; color: #1c1c1c; background: white;'>"
    ]
    for node in nodes:
        parts.append(_render_node(document, node, mark_mode))
    parts.append(
        "<p style='color:#888; font-size:9pt; margin-top:18px;'>"
        "%d of %d statements</p>" % (len(nodes), len(document.order))
    )
    parts.append("</body></html>")
    return "".join(parts)


def visible_nodes(document):
    """Statements left after structural clipping, level clipping, and filters."""
    view = document.viewspecs
    context = document.context
    if context is None:
        return []
    if view.structure == BRANCH_ONLY:
        chosen = [context] + document._descendants(context)
    elif view.structure == PLEX_ONLY:
        parent = context.parent
        if parent is None:
            chosen = list(document.order)
        else:
            chosen = []
            for sibling in parent.children:
                chosen.append(sibling)
                chosen.extend(document._descendants(sibling))
    else:
        chosen = list(document.order)

    filtering = view.filter_type in (FILTER_ALL, NEXT_FILTERED_NODE)
    visible = []
    for node in chosen:
        if filtering and not node.passes_filter and node is not context:
            continue
        if node.level <= view.level_clip or node is context:
            visible.append(node)
    return visible


def _render_node(document, node, mark_mode):
    view = document.viewspecs
    context = document.context
    is_context = node is context
    indent = _indent(view, node, context)
    gap = "14px" if view.show_blank_lines else "2px"
    background = "#e7f0ff" if is_context else "transparent"
    border = "3px solid #3d6fbf" if is_context else "3px solid transparent"
    number = _address_text(view, node)
    number_color = "#8a4b08" if node.included or node.include_failed else "#7a4ea3"
    content = _content_html(document, node, view)
    if mark_mode:
        target = "hs:/mark/%s" % quote(node.number, safe="")
        content = re.sub(r"(?i)</?a\b[^>]*>", "", content)
        content = (
            "<a href='%s' style='color:#1c1c1c; text-decoration:none;'>%s</a>"
            % (target, content)
        )
        number_href = target
    else:
        number_href = "hs:/jump/%s" % quote(node.number, safe="")

    number_html = ""
    if view.show_addressing and number:
        number_html = (
            "<a href='%s' style='color:%s; font-family: Verdana, sans-serif;"
            " font-size: 9pt; font-weight: bold; text-decoration: none;"
            " padding-right: 8px;'>(%s)</a>"
            % (number_href, number_color, escape(number))
        )
    failed = ""
    if node.include_failed:
        failed = " <span style='color:#a32020; font-size:9pt;'>(include failed)</span>"
    quick = ""
    if is_context and not mark_mode:
        quick = (
            "<span style='font-family: Verdana, sans-serif; font-size: 8pt;'>"
            "<a href='hs:/quick/out' style='color:#3d6fbf; text-decoration:none;'"
            " title='Zoom out'>&#9650;</a> "
            "<a href='hs:/quick/in' style='color:#3d6fbf; text-decoration:none;'"
            " title='Zoom in'>&#9660;</a> "
            "<a href='hs:/quick/all' style='color:#3d6fbf; text-decoration:none;'"
            " title='Show every line'>&#9776;</a> "
            "</span>"
        )
    left = ""
    right = ""
    if view.placement == "right":
        right = number_html
    else:
        left = number_html
    anchor = "<a name='stmt-%s'></a>" % escape(node.number)
    return (
        "<p style='margin: 0 0 %s 0; padding: 1px 6px 1px 4px;"
        " margin-left: %sem; background: %s; border-left: %s;'>"
        "%s%s%s%s%s%s</p>"
        % (gap, indent, background, border, anchor, quick, left, content, failed, right)
    )


def _indent(view, node, context):
    if view.indent == INDENT_OFF:
        return 0
    level = node.level
    if view.indent == INDENT_TO_CONTENT and context is not None:
        level = max(0, node.level - context.level)
    return level * 1.4


def _address_text(view, node):
    if view.address_type == SHOW_NODE_ID:
        return node.nid or node.number
    return node.number


def _content_html(document, node, view):
    html = node.text or ""
    base = Path(document.path).resolve().parent if document.path else None
    html = _rewrite_links(html)
    html = _rewrite_images(html, base)
    if view.line_clip < MAX_LINE_CLIPPING:
        html = _clip_lines(html, view.line_clip)
    prefix = ""
    if view.show_labels and node.label and not _label_already_visible(node):
        prefix = (
            "<span style='color:#666; font-size:10pt; font-style:italic;'>"
            "%s </span>" % escape(node.label)
        )
    suffix = ""
    if view.show_signatures and (node.created_by or node.created_on):
        bits = [bit for bit in (node.created_by, node.created_on) if bit]
        suffix = (
            " <span style='color:#777; font-size:9pt;'>— %s</span>"
            % escape(", ".join(bits))
        )
    return prefix + html + suffix


def _label_already_visible(node):
    label = plain_text(node.label).strip().strip("()").lower()
    text = plain_text(node.text).strip().lower()
    if not label:
        return True
    return text.startswith(label)


def _rewrite_links(html):
    def replace(match):
        href = match.group(3)
        if href.lower().startswith(("javascript:", "mailto:")):
            target = href
        else:
            target = "hs:/go/" + quote(href, safe="")
        return match.group(1) + match.group(2) + target + match.group(4)

    return _HREF_RE.sub(replace, html)


def _rewrite_images(html, base):
    def replace(match):
        src = match.group(3)
        if re.match(r"^[a-z]+:", src, re.IGNORECASE) or src.startswith("/"):
            target = src
        elif base is not None:
            target = (base / src).resolve().as_uri()
        else:
            target = src
        return match.group(1) + match.group(2) + target + match.group(4)

    return _SRC_RE.sub(replace, html)


def _clip_lines(html, count):
    text = plain_text(html).replace("\r", "")
    wrapped = []
    for paragraph in text.split("\n"):
        lines = textwrap.wrap(paragraph, _LINE_WIDTH) or [""]
        wrapped.extend(lines)
    if len([line for line in wrapped if line.strip()]) <= count and "<img" not in html.lower():
        # The statement already fits, so keep its links and emphasis.
        if len(wrapped) <= count:
            return html
    kept = wrapped[:count]
    clipped = "<br/>".join(escape(line) for line in kept)
    if len(wrapped) > count:
        clipped += " <span style='color:#888;'>…</span>"
    return clipped
