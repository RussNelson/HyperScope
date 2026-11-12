"""HyperScope addresses.

An address has a file, a sequence of statement moves (``2A``, ``023``,
``.u``, a label, a quoted search), a string of viewspecs after ``:``, and
an optional content filter between semicolons. Resolving an address loads
the file, moves to the statement, then applies the view.
"""

import re
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

from .errors import HyperscopeError
from .model import (
    Document,
    forget_cached,
    link_hrefs,
    load_cached,
    load_opml,
    parse_opml,
    plain_text,
    transclude,
)
from .viewspec import (
    FILTER_ALL,
    NEXT_FILTERED_NODE,
    NO_FILTERING,
    CurrentViewspecs,
)

_NODE_ID = re.compile(r"^\s*(0\d+)")
_NODE_NUMBER = re.compile(
    r"^\s*((?:0(?![0-9]+))|(?:\!?[1-9][0-9]*[a-z0-9]*))",
    re.IGNORECASE,
)
_LABEL = re.compile(r"^\s*((?:\!|\*|\$)?[a-z][a-z0-9_\-]*)", re.IGNORECASE)
_MARKER = re.compile(r"^\s*(@[a-z][a-z0-9_\-]*)", re.IGNORECASE)
_RELATIVE = re.compile(
    r"^\s*(\.[0-9nbudoelhtspcrf]+)",
    re.IGNORECASE,
)
_SEARCH = re.compile(
    r"""^\s*((?:"(?:\\"|[^"])*")|(?:/(?:\\/|[^/])*/[a-z]*))""",
    re.IGNORECASE,
)
_POSITION = re.compile(r"^\s*((?:\+|-)[0-9wcvilef+\-]*)", re.IGNORECASE)
_RELATIVE_PIECE = re.compile(r"^(\d*)([nbudoehtspcrl]f?)", re.IGNORECASE)
_VIEW_LETTER = re.compile(r"^\s*([a-zA-Q])")
# A relative move (``.u``, ``.2n``, ``.ub:wmh``), as opposed to a file name.
_RELATIVE_ADDRESS = re.compile(
    r"^\.(?:[0-9]*[nbudoelhtspcrl]f?)+",
    re.IGNORECASE,
)


class Piece:
    def apply(self, document):
        raise NotImplementedError


class NodeNumber(Piece):
    def __init__(self, number, is_offset=False):
        self.number = number.upper()
        self.is_offset = is_offset

    def apply(self, document):
        if self.is_offset:
            document.jump_offset(self.number)
        else:
            document.jump_number(self.number)

    def __str__(self):
        return ("!" if self.is_offset else "") + self.number


class NodeID(Piece):
    def __init__(self, nid):
        self.nid = nid

    def apply(self, document):
        document.jump_id(self.nid)

    def __str__(self):
        return self.nid


class NodeLabel(Piece):
    FIRST = "first"
    NEXT = "next"
    BRANCH = "branch"
    EXTERNAL = "external"

    def __init__(self, label, kind=FIRST):
        self.label = label
        self.kind = kind

    def apply(self, document):
        if self.kind == self.BRANCH:
            document.jump_branch_label(self.label)
        elif self.kind == self.NEXT:
            document.jump_label(self.label, first=False)
        elif self.kind == self.EXTERNAL:
            raise HyperscopeError("external labels are not supported")
        else:
            document.jump_label(self.label, first=True)

    def __str__(self):
        prefix = {self.BRANCH: "!", self.NEXT: "*", self.EXTERNAL: "$"}.get(
            self.kind, ""
        )
        return prefix + self.label


class Marker(Piece):
    def __init__(self, name):
        self.name = name

    def apply(self, document):
        document.jump_marker(self.name)

    def __str__(self):
        return "@" + self.name


class Relative(Piece):
    def __init__(self, kind, offset=1):
        self.kind = kind
        self.offset = offset

    def apply(self, document):
        kind = self.kind.lower()
        if kind == "n":
            document.jump_next(self.offset)
        elif kind == "b":
            document.jump_back(self.offset)
        elif kind == "u":
            document.jump_up(self.offset)
        elif kind == "d":
            document.jump_down(self.offset)
        elif kind == "o":
            document.jump_origin()
        elif kind == "e":
            document.jump_branch_end()
        elif kind == "h":
            document.jump_plex_head()
        elif kind == "t":
            document.jump_plex_tail()
        elif kind == "s":
            document.jump_successor(self.offset)
        elif kind == "p":
            document.jump_predecessor(self.offset)
        elif kind in ("c", "r", "rf"):
            raise HyperscopeError("? ." + kind)
        else:
            raise HyperscopeError("? ." + kind)

    def __str__(self):
        prefix = "" if self.offset == 1 else str(self.offset)
        return prefix + self.kind


class IndirectLink(Piece):
    """``.l`` — follow a link inside the statement currently in view."""

    def __init__(self, offset=1):
        self.offset = offset

    def apply(self, document):
        raise HyperscopeError("indirect link must be resolved by the address")

    def __str__(self):
        return ("l" if self.offset == 1 else str(self.offset) + "l")


class StringSearch(Piece):
    def __init__(self, raw):
        self.raw = raw

    def apply(self, document):
        pattern = compile_search(self.raw)
        if pattern is not None:
            document._search(pattern, self.raw, first=False)
        else:
            document.jump_content(search_text(self.raw), first=False)

    def __str__(self):
        return self.raw


class StringPosition(Piece):
    def apply(self, document):
        raise HyperscopeError("string positioning is not supported")

    def __str__(self):
        return ""


class Address:
    def __init__(self):
        self.file = ""
        self.pieces = []
        self.viewspecs = ""
        self.content_filter = None

    def __str__(self):
        text = self.file or ""
        if self.pieces or self.viewspecs or self.content_filter:
            text += "#"
        previous = None
        for piece in self.pieces:
            if previous is not None:
                if not (
                    isinstance(previous, (Relative, IndirectLink))
                    and isinstance(piece, (Relative, IndirectLink))
                ):
                    text += " "
            if isinstance(piece, (Relative, IndirectLink)):
                if not isinstance(previous, (Relative, IndirectLink)):
                    text += "."
                text += str(piece).lstrip(".")
            else:
                text += str(piece)
            previous = piece
        if self.viewspecs:
            text += ":" + self.viewspecs
        if self.content_filter:
            text += ";" + self.content_filter + ";"
        return text


def search_text(raw):
    """The text a quoted search actually looks for."""
    raw = raw.strip()
    if raw.startswith('"') and raw.endswith('"') and len(raw) >= 2:
        return raw[1:-1].replace('\\"', '"')
    return raw


def compile_search(raw):
    """A ``/regex/flags`` search, or None when ``raw`` is a quoted string."""
    raw = (raw or "").strip()
    if not raw.startswith("/"):
        return None
    match = re.match(r"^/(.*)/([a-z]*)$", raw, re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    flags = re.IGNORECASE if "i" in match.group(2).lower() else 0
    try:
        return re.compile(match.group(1), flags)
    except re.error as exc:
        raise HyperscopeError("? " + raw) from exc


def compile_filter(raw):
    """A content filter, either ``"word"`` or ``/regex/flags``."""
    raw = raw.strip()
    if raw.startswith('"') and raw.endswith('"'):
        word = raw[1:-1].replace('\\"', '"')
        if not word:
            raise HyperscopeError("? " + raw)
        return re.compile(r"(?:^|[ ])" + re.escape(word) + r"(?:$|[ ])", re.I)
    match = re.match(r"^/(.*)/([a-z]*)$", raw, re.IGNORECASE | re.DOTALL)
    if not match or raw == "//":
        raise HyperscopeError("? " + raw)
    flags = 0
    if "i" in match.group(2).lower():
        flags |= re.IGNORECASE
    try:
        return re.compile(match.group(1), flags)
    except re.error as exc:
        raise HyperscopeError("? " + raw) from exc


def normalize_user_address(url):
    """Treat a bare statement address as ``#address``.

    File names, ``./`` and ``../``, absolute paths, and URLs are left alone.
    A relative move such as ``.u`` is an address, not a file name.
    """
    url = (url or "").strip()
    if url == "":
        return "#"
    if url.startswith("#"):
        return url
    if url.startswith("./") or url.startswith("../") or url.startswith("/"):
        return url
    if re.match(r"^[^:]*:/", url):
        return url
    lowered = url.lower()
    if ".opml" in lowered or "/" in url:
        return url
    if url.startswith(".") and not _RELATIVE_ADDRESS.match(url):
        return url
    return "#" + url


def parse_address(url):
    """Parse an address string into an ``Address``."""
    url = unquote((url or "").strip())
    url = normalize_user_address(url)
    address = Address()
    if "#" in url:
        file_part, anchor = url.split("#", 1)
    else:
        file_part, anchor = url, ""
    address.file = file_part.strip()
    if anchor == "":
        return address

    node_text, rest = _split_node_text(anchor)
    view_text, filter_text = _split_view_and_filter(rest)
    address.pieces = _parse_nodes(node_text) if node_text.strip() else []
    address.viewspecs = _parse_viewspecs(view_text)
    if filter_text:
        address.content_filter = _parse_filter(filter_text)
    return address


def _split_node_text(anchor):
    """Statement moves stop at ``:`` or ``;`` (unless escaped)."""
    characters = []
    escaped = False
    for char in anchor:
        if escaped:
            characters.append(char)
            escaped = False
            continue
        if char == "\\":
            characters.append(char)
            escaped = True
            continue
        if char in ":;":
            return "".join(characters), anchor[len(characters):]
        characters.append(char)
    return "".join(characters), ""


def _split_view_and_filter(rest):
    if not rest:
        return "", ""
    semicolon = rest.find(";")
    if semicolon == -1:
        return rest, ""
    return rest[:semicolon], rest[semicolon:]


def _parse_viewspecs(text):
    text = text.strip()
    if not text:
        return ""
    if text[0] != ":":
        raise HyperscopeError("? " + text)
    text = text[1:]
    letters = []
    while text.strip():
        match = _VIEW_LETTER.match(text)
        if not match:
            raise HyperscopeError("? " + text.strip())
        letters.append(match.group(1))
        text = text[match.end():]
    return "".join(letters)


def _parse_filter(text):
    match = re.match(r"^\s*;\s*(.*?)\s*;\s*$", text, re.DOTALL)
    if not match:
        raise HyperscopeError("? " + text)
    body = match.group(1).strip()
    if not body or body[0] not in "\"/":
        raise HyperscopeError("? " + text)
    # Validate now so a bad filter is reported before the file is moved.
    compile_filter(body)
    return body


def _parse_nodes(text):
    pieces = []
    while text.strip():
        matched = None
        for pattern, parser in (
            (_NODE_ID, _take_id),
            (_NODE_NUMBER, _take_number),
            (_LABEL, _take_label),
            (_MARKER, _take_marker),
            (_RELATIVE, _take_relative),
            (_SEARCH, _take_search),
            (_POSITION, _take_position),
        ):
            match = pattern.match(text)
            if match:
                matched = (match, parser)
                break
        if matched is None:
            raise HyperscopeError("? " + text.strip())
        match, parser = matched
        token = match.group(1)
        # A trailing ``@`` is how several converted Augment links were written
        # (``Figure-13@``). It is not part of the label.
        pieces.extend(parser(token))
        text = text[match.end():]
        if text.startswith("@"):
            text = text[1:]
    return pieces


def _take_id(token):
    return [NodeID(token.strip())]


def _take_number(token):
    token = token.strip()
    is_offset = token.startswith("!")
    if is_offset:
        token = token[1:]
    return [NodeNumber(token, is_offset)]


def _take_label(token):
    token = token.strip()
    kind = NodeLabel.FIRST
    if token.startswith("!"):
        kind = NodeLabel.BRANCH
        token = token[1:]
    elif token.startswith("*"):
        kind = NodeLabel.NEXT
        token = token[1:]
    elif token.startswith("$"):
        kind = NodeLabel.EXTERNAL
        token = token[1:]
    return [NodeLabel(token, kind)]


def _take_marker(token):
    return [Marker(token.strip()[1:])]


def _take_relative(token):
    body = token.strip()[1:]
    pieces = []
    while body:
        match = _RELATIVE_PIECE.match(body)
        if not match or not match.group(2):
            raise HyperscopeError("? " + token)
        offset = int(match.group(1) or "1")
        if offset <= 0:
            raise HyperscopeError("? " + token)
        letter = match.group(2).lower()
        if letter == "l":
            pieces.append(IndirectLink(offset))
        else:
            pieces.append(Relative(letter, offset))
        body = body[match.end():]
    return pieces


def _take_search(token):
    return [StringSearch(token.strip())]


def _take_position(token):
    return [StringPosition()]


def same_file(address, document):
    if document is None:
        return False
    return _resolve_file(address.file, document) == _identity(document)


def _identity(document):
    if document.source_url:
        return document.source_url
    if document.path:
        return str(Path(document.path).resolve())
    return ""


def _resolve_file(file_part, document):
    """Absolute path or URL this file portion refers to."""
    file_part = (file_part or "").strip()
    if not file_part or file_part in (".", "./"):
        return _identity(document) if document else ""
    if re.match(r"^https?:", file_part, re.IGNORECASE):
        return file_part
    if file_part.startswith("file://"):
        parsed = urlparse(file_part)
        return str(Path(unquote(parsed.path)).resolve())
    base = None
    if document is not None and document.path:
        base = Path(document.path).resolve().parent
    candidate = Path(file_part)
    if not candidate.is_absolute():
        if base is None:
            raise HyperscopeError("? " + file_part)
        candidate = base / candidate
    return str(candidate.resolve())


def _load_location(location, fresh=False):
    if re.match(r"^https?:", location or "", re.IGNORECASE):
        return _load_remote(location)
    if fresh:
        forget_cached(location)
    return load_cached(location)


def _load_remote(url):
    try:
        request = Request(url, headers={"User-Agent": "HyperScope/1.1"})
        with urlopen(request, timeout=8) as response:
            payload = response.read()
    except Exception as exc:
        raise HyperscopeError("? " + url + " (" + str(exc) + ")") from exc
    document = parse_opml(payload, path="")
    document.source_url = url
    document.title = document.title or url
    return document


def _expand(address, document):
    """Fill in the current file, statement, view, and filter when omitted."""
    expanded = Address()
    expanded.file = address.file
    expanded.pieces = list(address.pieces)
    expanded.viewspecs = address.viewspecs
    expanded.content_filter = address.content_filter
    if document is None:
        return expanded
    if not same_file(address, document):
        return expanded
    if not expanded.pieces or not _anchors_context(expanded.pieces[0]):
        number = document.context.number if document.context else "0"
        expanded.pieces = [NodeNumber(number)] + expanded.pieces
    old = document.viewspecs.to_string()
    expanded.viewspecs = old + expanded.viewspecs
    if expanded.content_filter is None and document.content_filter:
        if document.viewspecs.filter_type != NO_FILTERING:
            expanded.content_filter = document.content_filter
    return expanded


def _anchors_context(piece):
    """True when this piece already chooses a statement without the context."""
    if isinstance(piece, NodeID):
        return True
    if isinstance(piece, NodeNumber) and piece.is_offset:
        return True
    return False


def resolve(url, relative_to=None, inclusion=False, stack=None, fresh=False):
    """Resolve ``url`` into a document positioned and viewed as the address says.

    ``relative_to`` is the document already on screen. Relative addresses
    (``#.u``, ``#:z``) keep that document's statement and view. ``fresh``
    reloads the file from disk.
    """
    if isinstance(url, Address):
        address = url
    else:
        address = parse_address(url)
    stack = list(stack or [])
    if relative_to is not None:
        address = _expand(address, relative_to)
    location = _resolve_file(address.file, relative_to)
    if not location:
        raise HyperscopeError("? " + (address.file or str(url)))
    # A file already being expanded (a transclusion of itself, or a cycle)
    # is positioned but not expanded again.
    expanding = location in stack

    document = _load_location(location, fresh=fresh)
    try:
        _apply_pieces(address, document, relative_to, inclusion, stack + [location])
    except _Followed as followed:
        return followed.document
    # Viewspecs are applied after the move, so a letter such as ``e``
    # (clip to the statement in view) sees the statement we landed on.
    document.viewspecs = CurrentViewspecs(
        address.viewspecs,
        context_level=document.context.level if document.context else 0,
    )
    if not expanding and (document.viewspecs.sequence_generators or inclusion):
        remembered = document.context

        def resolve_include(include_url):
            return resolve(
                include_url,
                relative_to=document,
                inclusion=True,
                stack=stack + [location],
            )

        transclude(document, resolve_include)
        if remembered in document.order:
            document.set_context(remembered)
    _apply_content_filter(address, document)
    document.content_filter = address.content_filter
    return document


def _apply_pieces(address, document, relative_to, inclusion, stack):
    for index, piece in enumerate(address.pieces):
        if isinstance(piece, IndirectLink):
            hrefs = link_hrefs(document.context.text)
            if piece.offset > len(hrefs):
                raise HyperscopeError(
                    "No indirect link for " + document.context.number
                )
            merged = parse_address(normalize_user_address(hrefs[piece.offset - 1]))
            merged.viewspecs = merged.viewspecs + address.viewspecs
            merged.pieces = merged.pieces + address.pieces[index + 1:]
            if address.content_filter and merged.content_filter is None:
                merged.content_filter = address.content_filter
            followed = resolve(
                merged,
                relative_to=document,
                inclusion=inclusion,
                stack=stack,
            )
            # Hand the followed document back by raising a private signal
            # the caller converts. Using an exception keeps the signature
            # of apply() simple for every other piece.
            raise _Followed(followed)
        if isinstance(piece, StringPosition):
            piece.apply(document)
        else:
            piece.apply(document)
            document.viewspecs.context_level = document.context.level


class _Followed(Exception):
    def __init__(self, document):
        super().__init__("followed")
        self.document = document


def _apply_content_filter(address, document):
    if not address.content_filter:
        return
    kind = document.viewspecs.filter_type
    if kind == NO_FILTERING:
        return
    pattern = compile_filter(address.content_filter)
    if kind == FILTER_ALL:
        nodes = document.order
    elif kind == NEXT_FILTERED_NODE:
        nodes = document._walk(document.context, include_start=False)
    else:
        return
    matches = []
    for node in nodes:
        if pattern.search(plain_text(node.text)):
            node.passes_filter = True
            matches.append(node)
    if not matches:
        raise HyperscopeError("? " + address.content_filter)
    context = document.context
    if context not in matches:
        # Keep the match nearest the statement already in view. A statement
        # sitting past every match lands on the last one; anything earlier
        # lands on the first.
        existing = context.counter if context is not None else 0
        first = matches[0]
        last = matches[-1]
        if existing >= last.counter:
            chosen = last
        else:
            chosen = first
        document.set_context(chosen)


def apply_view(document, letters, content_filter=None):
    """Show ``document`` again with exactly this view, keeping its statement.

    Unlike a relative ``#:`` address, this does not keep the previous view.
    It is what the view controls do when Apply is pressed.
    """
    number = document.context.number if document.context else "0"
    address = Address()
    address.file = document.source_url or document.path
    address.pieces = [NodeNumber(number)]
    address.viewspecs = letters or ""
    address.content_filter = content_filter
    # Load absolutely: do not prepend the view already on screen.
    loaded = _load_location(address.file or _identity(document))
    if number in loaded.by_number:
        loaded.set_context(loaded.by_number[number])
    loaded.viewspecs = CurrentViewspecs(
        address.viewspecs,
        context_level=loaded.context.level if loaded.context else 0,
    )
    if loaded.viewspecs.sequence_generators:
        def resolve_include(include_url):
            return resolve(include_url, relative_to=loaded, inclusion=True,
                           stack=[_identity(loaded)])
        transclude(loaded, resolve_include)
        if number in loaded.by_number:
            loaded.set_context(loaded.by_number[number])
    _apply_content_filter(address, loaded)
    loaded.content_filter = content_filter
    return loaded
