"""OPML documents as HyperScope statement trees.

Statement numbers alternate between decimal and alphabetic at each level,
the same way Augment and HyperScope 1.1 number an outline. A document with
a single top-level statement numbers that statement ``0``; a document with
several top-level statements numbers them ``1``, ``2``, ``3``, and so on.
"""

import math
import re
from pathlib import Path
from xml.etree import ElementTree as ET

from .errors import HyperscopeError
from .viewspec import NO_FILTERING, CurrentViewspecs

_ENTITY_FIX = re.compile(
    r"&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)"
)
_TAG_RE = re.compile(r"<[^>]+>")
_HREF_RE = re.compile(
    r"""<a\b[^>]*?\bhref\s*=\s*(['"])(.*?)\1""",
    re.IGNORECASE | re.DOTALL,
)
_NAME_RE = re.compile(
    r"""<a\b[^>]*?\bname\s*=\s*(['"])(.*?)\1[^>]*>\s*</a>""",
    re.IGNORECASE | re.DOTALL,
)


def _local(tag):
    if tag[:1] == "{":
        return tag[tag.find("}") + 1:]
    return tag


def _attr(element, name):
    for key, value in element.attrib.items():
        if _local(key) == name:
            return value
    return None


def _letters_for_offset(node_offset):
    """Turn a 1-based sibling index into Augment letters (1 → A, 27 → AA)."""
    node_offset = node_offset - 1
    stack = []
    while node_offset >= 26:
        stack.append(node_offset % 26)
        node_offset = node_offset / 26.0
        node_offset = node_offset - 1
    word = chr(int(math.floor(node_offset + 65)))
    while stack:
        word += chr(int(math.floor(stack.pop() + 65)))
    return word


def to_node_number(parent_number, node_offset):
    """Child statement number under ``parent_number`` at sibling ``node_offset``."""
    if parent_number is None or parent_number == "0":
        return str(int(node_offset))
    parent_number = parent_number.upper()
    if parent_number[-1].isdigit():
        return parent_number + _letters_for_offset(node_offset)
    return parent_number + str(int(node_offset))


def offset_pieces(number):
    """Child indexes encoded by a relative statement number such as ``2A3``.

    This follows the original tokenizer: a run of digits is that integer, and
    a run of letters is read as decimal digits (A = 1 … I = 9, J = 10).
    """
    if not number or number == "0":
        raise HyperscopeError("Invalid node number: " + str(number))
    pieces = []
    remaining = number.upper()
    while remaining:
        match = re.match(r"^([0-9]+)", remaining)
        if match:
            pieces.append(int(match.group(1)))
            remaining = remaining[match.end():]
            continue
        match = re.match(r"^([A-Z]+)", remaining)
        if not match:
            raise HyperscopeError("? " + number)
        stack = []
        for char in match.group(1):
            stack.append(((ord(char) - 65) % 10) + 1)
        result = 0
        scale = 1
        while stack:
            result += stack.pop() * scale
            scale *= 10
        pieces.append(result)
        remaining = remaining[match.end():]
    return pieces


def plain_text(value):
    """Visible text of a statement, with tags removed."""
    if not value:
        return ""
    text = re.sub(r"(?i)<br\s*/?>", "\n", value)
    text = re.sub(r"(?i)</(p|div|h[1-6]|li|tr)>", "\n", text)
    text = _TAG_RE.sub("", text)
    return (
        text.replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&apos;", "'")
        .replace("&amp;", "&")
        .replace("\xa0", " ")
    )


def link_hrefs(value):
    """``href`` values of ``<a>`` elements in a statement, in order."""
    if not value:
        return []
    return [match.group(2) for match in _HREF_RE.finditer(value)]


def anchor_names(value):
    """Named anchors (``<a name="…">``) in a statement."""
    if not value:
        return []
    return [match.group(2) for match in _NAME_RE.finditer(value)]


class Node:
    """One statement in an outline."""

    def __init__(self):
        self.parent = None
        self.children = []
        self.text = ""
        self.label = ""
        self.nid = ""
        self.level = 0
        self.number = "0"
        self.counter = 0
        self.created_by = ""
        self.created_on = ""
        self.included = False
        self.include_failed = False
        self.included_from = ""
        self.include_url = None
        self.include_type = "node"
        self.passes_filter = False

    def clone(self, parent=None, with_children=True):
        copy = Node()
        copy.parent = parent
        copy.text = self.text
        copy.label = self.label
        copy.nid = self.nid
        copy.level = self.level
        copy.number = self.number
        copy.counter = self.counter
        copy.created_by = self.created_by
        copy.created_on = self.created_on
        copy.included = self.included
        copy.include_failed = self.include_failed
        copy.included_from = self.included_from
        copy.include_url = self.include_url
        copy.include_type = self.include_type
        copy.passes_filter = False
        if with_children:
            copy.children = [
                child.clone(copy, True) for child in self.children
            ]
        return copy


class Document:
    """A loaded outline and the statement that is currently in view."""

    def __init__(self):
        self.path = ""
        self.title = ""
        self.roots = []
        self.order = []
        self.by_number = {}
        self.by_nid = {}
        self.by_counter = {}
        self.context = None
        self.viewspecs = CurrentViewspecs()
        self.content_filter = None
        self.source_url = ""

    @property
    def origin(self):
        return self.roots[0] if self.roots else None

    def clone(self):
        """A fresh copy with default viewspecs and the origin in view."""
        copy = Document()
        copy.path = self.path
        copy.title = self.title
        copy.source_url = self.source_url
        copy.roots = [root.clone() for root in self.roots]
        copy.renumber()
        copy.viewspecs = CurrentViewspecs(context_level=copy.context.level)
        return copy

    def renumber(self):
        """Recompute levels, statement numbers, and the document-order index."""
        self.order = []
        self.by_number = {}
        self.by_nid = {}
        self.by_counter = {}
        counter = [0]

        def walk(nodes, level, parent_number):
            for offset, node in enumerate(nodes, start=1):
                counter[0] += 1
                node.level = level
                if level == 0:
                    node.number = "0"
                else:
                    node.number = to_node_number(parent_number, offset)
                node.counter = counter[0]
                self.order.append(node)
                self.by_number[node.number] = node
                if node.nid:
                    self.by_nid.setdefault(node.nid, node)
                self.by_counter[node.counter] = node
                walk(node.children, level + 1, node.number)

        if len(self.roots) == 1:
            walk(self.roots, 0, None)
        else:
            walk(self.roots, 1, None)
        self.context = self.origin
        if self.context is not None:
            self.viewspecs.context_level = self.context.level

    def location(self):
        """Short address of what is on screen, suitable for copying."""
        if self.context is None:
            return Path(self.path).name if self.path else ""
        name = Path(self.path).name if self.path else self.title or ""
        text = name + "#" + self.context.number
        spec = self.viewspecs.to_string()
        if spec:
            text += ":" + spec
        if self.content_filter and self.viewspecs.filter_type != NO_FILTERING:
            text += ";" + self.content_filter + ";"
        return text

    def set_context(self, node):
        self.context = node
        self.viewspecs.context_level = node.level if node else 0
        return node

    def jump_origin(self):
        if self.origin is None:
            raise HyperscopeError("No origin node in document")
        return self.set_context(self.origin)

    def jump_number(self, number):
        number = (number or "").upper()
        node = self.by_number.get(number)
        if node is None:
            raise HyperscopeError("? " + number)
        return self.set_context(node)

    def jump_id(self, nid):
        node = self.by_nid.get(nid)
        if node is None:
            raise HyperscopeError("? " + str(nid))
        return self.set_context(node)

    def jump_offset(self, number):
        """Move relative to the context by a statement number such as ``2A``."""
        parent = self.context
        for offset in offset_pieces(number):
            if parent is None or len(parent.children) < offset:
                raise HyperscopeError("? " + number)
            parent = parent.children[offset - 1]
        return self.set_context(parent)

    def jump_label(self, label, first=True):
        pattern = re.compile("^" + re.escape(label) + "$", re.IGNORECASE)
        start = self.origin if first else self.context
        for node in self._walk(start, include_start=first):
            if node.label and pattern.match(node.label):
                return self.set_context(node)
        raise HyperscopeError("? " + label)

    def jump_branch_label(self, label):
        pattern = re.compile("^" + re.escape(label) + "$", re.IGNORECASE)
        for node in self._descendants(self.context):
            if node.label and pattern.match(node.label):
                return self.set_context(node)
        raise HyperscopeError("? " + label)

    def jump_marker(self, name):
        pattern = re.compile(re.escape(name), re.IGNORECASE)
        for node in self._walk(self.context, include_start=True):
            for anchor in anchor_names(node.text):
                if pattern.fullmatch(anchor):
                    return self.set_context(node)
        raise HyperscopeError("? " + name)

    def jump_word(self, word, first=True):
        pattern = re.compile(
            r"(?:^|[ ])" + re.escape(word) + r"(?:$|[ ])",
            re.IGNORECASE,
        )
        return self._search(pattern, word, first)

    def jump_content(self, text, first=True):
        if text is None or str(text).strip() == "":
            raise HyperscopeError("? " + str(text))
        folded = text.lower()

        def tester(node):
            return folded in plain_text(node.text).lower()

        return self._search(tester, text, first)

    def jump_up(self, offset=1):
        ancestors = []
        parent = self.context.parent
        while parent is not None:
            ancestors.append(parent)
            parent = parent.parent
        if not ancestors:
            return self.context
        # ``ancestors[0]`` is the parent; the last entry is the outermost.
        ancestors.reverse()
        if offset > len(ancestors):
            chosen = ancestors[0]
        else:
            chosen = ancestors[len(ancestors) - offset]
        return self.set_context(chosen)

    def jump_down(self, offset=1):
        node = self.context
        remaining = offset
        while remaining > 0 and node.children:
            node = node.children[0]
            remaining -= 1
        return self.set_context(node)

    def jump_back(self, offset=1):
        target = self.context.counter - offset
        if target <= 0:
            return self.context
        node = self.by_counter.get(target)
        if node is None:
            return self.context
        return self.set_context(node)

    def jump_next(self, offset=1):
        target = self.context.counter + offset
        if target > len(self.order):
            target = len(self.order)
        node = self.by_counter.get(target, self.context)
        return self.set_context(node)

    def jump_branch_end(self):
        descendants = self._descendants(self.context)
        if not descendants:
            return self.context
        return self.set_context(descendants[-1])

    def jump_plex_head(self):
        siblings = self._siblings()
        index = siblings.index(self.context)
        if index == 0:
            return self.context
        return self.set_context(siblings[0])

    def jump_plex_tail(self):
        siblings = self._siblings()
        if siblings[-1] is self.context:
            return self.context
        return self.set_context(siblings[-1])

    def jump_successor(self, offset=1):
        siblings = self._siblings()
        index = siblings.index(self.context)
        following = siblings[index + 1:]
        if not following:
            return self.context
        if offset > len(following):
            return self.set_context(following[-1])
        return self.set_context(following[offset - 1])

    def jump_predecessor(self, offset=1):
        siblings = self._siblings()
        index = siblings.index(self.context)
        preceding = siblings[:index]
        if not preceding:
            return self.context
        if offset > len(preceding):
            return self.set_context(preceding[0])
        return self.set_context(preceding[-offset])

    def replace_node(self, node, replacements):
        """Replace ``node`` with ``replacements`` (already parented)."""
        container = self.roots if node.parent is None else node.parent.children
        index = container.index(node)
        container[index:index + 1] = replacements
        for child in replacements:
            child.parent = node.parent

    def _siblings(self):
        if self.context.parent is None:
            return self.roots
        return self.context.parent.children

    def _descendants(self, node):
        found = []

        def walk(current):
            for child in current.children:
                found.append(child)
                walk(child)

        walk(node)
        return found

    def _walk(self, start, include_start):
        if start not in self.order:
            return []
        index = self.order.index(start)
        if not include_start:
            index += 1
        return self.order[index:]

    def _search(self, tester, description, first):
        start = self.origin if first else self.context
        for node in self._walk(start, include_start=first):
            matched = tester(node) if callable(tester) else tester.search(
                plain_text(node.text)
            )
            if matched:
                return self.set_context(node)
        raise HyperscopeError("? " + str(description))


def _sanitize(text):
    return _ENTITY_FIX.sub("&amp;", text)


def _derive_label(text, left, right):
    if not left and not right:
        return ""
    plain = plain_text(text).strip()
    if right and right in plain:
        return plain[:plain.find(right) + len(right)].strip()
    if left and left in plain:
        return plain[plain.find(left):].split()[0] if plain else ""
    return ""


def parse_opml(source, path=""):
    """Parse OPML text or a file path into a numbered ``Document``."""
    if isinstance(source, (bytes, bytearray)):
        raw = bytes(source)
        match = re.match(br"<\?xml[^>]*encoding=['\"]([^'\"]+)", raw)
        encoding = match.group(1).decode("ascii", "replace") if match else "utf-8"
        text = raw.decode(encoding, "replace")
    elif path and not str(source).lstrip().startswith("<"):
        file_path = Path(source)
        raw = file_path.read_bytes()
        match = re.match(br"<\?xml[^>]*encoding=['\"]([^'\"]+)", raw)
        encoding = match.group(1).decode("ascii", "replace") if match else "utf-8"
        text = raw.decode(encoding, "replace")
        path = str(file_path)
    else:
        text = str(source)

    if text.startswith("\ufeff"):
        text = text[1:]
    try:
        root = ET.fromstring(_sanitize(text))
    except ET.ParseError as exc:
        raise HyperscopeError("Invalid OPML document: %s" % exc) from exc

    if _local(root.tag) != "opml":
        raise HyperscopeError("Invalid OPML document, no opml root")

    head = None
    body = None
    for child in list(root):
        name = _local(child.tag)
        if name == "head":
            head = child
        elif name == "body":
            body = child
    if body is None:
        raise HyperscopeError("Invalid OPML document, no outline nodes")

    left = _attr(head, "left-label-delim") if head is not None else None
    right = _attr(head, "right-label-delim") if head is not None else None
    title = ""
    if head is not None:
        for child in list(head):
            if _local(child.tag) == "title" and child.text:
                title = plain_text(child.text).strip()

    def build(element, inherited_left, inherited_right):
        node = Node()
        node.text = _attr(element, "text") or ""
        node.nid = _attr(element, "nid") or ""
        node.created_by = _attr(element, "createdBy") or ""
        node.created_on = _attr(element, "createdOn") or ""
        node_left = _attr(element, "left-label-delim")
        node_right = _attr(element, "right-label-delim")
        if node_left is None:
            node_left = inherited_left
        if node_right is None:
            node_right = inherited_right
        label = _attr(element, "label")
        if label is None:
            label = _derive_label(node.text, node_left or "", node_right or "")
        node.label = label or ""
        if (_attr(element, "type") or "").lower() == "include":
            node.include_url = _attr(element, "url") or ""
            node.include_type = (
                _attr(element, "include-type") or "node"
            ).lower()
        for child in list(element):
            if _local(child.tag) == "outline":
                built = build(child, node_left, node_right)
                built.parent = node
                node.children.append(built)
        return node

    document = Document()
    document.path = path
    document.title = title
    for child in list(body):
        if _local(child.tag) == "outline":
            document.roots.append(build(child, left, right))
    if not document.roots:
        raise HyperscopeError("Invalid OPML document, no outline nodes")
    document.renumber()
    document.viewspecs = CurrentViewspecs(
        context_level=document.context.level if document.context else 0
    )
    return document


def load_opml(path):
    """Load an OPML file from disk."""
    file_path = Path(path)
    if not file_path.is_file():
        raise HyperscopeError("? " + str(path))
    return parse_opml(file_path, path=str(file_path))


_CACHE = {}


def load_cached(path):
    """Load ``path``, reusing a parsed copy for the rest of the session."""
    file_path = Path(path).resolve()
    key = str(file_path)
    if key not in _CACHE:
        _CACHE[key] = load_opml(file_path)
    return _CACHE[key].clone()


def forget_cached(path):
    _CACHE.pop(str(Path(path).resolve()), None)


def mark_included(node, url):
    node.included = True
    node.included_from = url
    node.nid = ""
    node.include_url = None
    for child in node.children:
        mark_included(child, url)


def transclude(document, resolve_include):
    """Replace ``type="include"`` statements. ``resolve_include(url)`` returns a document.

    Returns True when the tree changed and was renumbered.
    """
    includes = [node for node in document.order if node.include_url]
    if not includes:
        return False
    changed = False
    for node in includes:
        container = document.roots if node.parent is None else node.parent.children
        if node not in container:
            continue
        url = node.include_url
        try:
            included = resolve_include(url)
            target = included.context
            if target is None:
                raise HyperscopeError("No origin node in document")
        except Exception as exc:
            node.include_failed = True
            node.text = "Include failed for url %s, error: %s" % (url, exc)
            node.include_url = None
            changed = True
            continue
        kind = node.include_type or "node"
        if kind == "branch":
            copy = target.clone(with_children=True)
            mark_included(copy, url)
            document.replace_node(node, [copy])
        elif kind == "plex":
            siblings = (
                target.parent.children if target.parent is not None
                else included.roots
            )
            copies = []
            for sibling in siblings:
                copy = sibling.clone(with_children=True)
                mark_included(copy, url)
                copies.append(copy)
            document.replace_node(node, copies)
        else:
            copy = target.clone(with_children=False)
            mark_included(copy, url)
            document.replace_node(node, [copy])
        changed = True
    if changed:
        document.renumber()
    return changed
