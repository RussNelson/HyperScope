"""View specifications.

These are the single-letter Augment/HyperScope view controls. The default
set and the meaning of each letter follow release 1.1
(``hs.filter.CurrentViewspecs``). Letters are applied left to right, so a
later letter overrides an earlier one.
"""

from .errors import HyperscopeError

DEFAULT_VIEWSPECS = "hjmpuwyACHJLP"
MAX_LEVEL_CLIPPING = 63
MAX_LINE_CLIPPING = 63

LEFT = "left"
RIGHT = "right"
SHOW_NODE_ID = "id"
SHOW_NODE_NUMBER = "number"
BRANCH_ONLY = "branch"
PLEX_ONLY = "plex"
NO_STRUCTURAL_CLIPPING = "none"
NO_FILTERING = "none"
FILTER_ALL = "all"
NEXT_FILTERED_NODE = "next_node"
INDENT_ON = "on"
INDENT_OFF = "off"
INDENT_TO_CONTENT = "to_context_node"

# Upper-case viewspecs stop at Q, matching the original tokenizer.
_VALID = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQ")


class CurrentViewspecs:
    """The view state produced by a string of viewspec letters."""

    def __init__(self, letters="", context_level=0, apply_defaults=True):
        self.context_level = context_level or 0
        self.level_clip = 1
        self.line_clip = 1
        self.structure = NO_STRUCTURAL_CLIPPING
        self.show_blank_lines = False
        self.show_addressing = False
        self.show_labels = False
        self.show_frozen = False
        self.show_signatures = False
        self.placement = LEFT
        self.address_type = SHOW_NODE_NUMBER
        self.filter_type = NO_FILTERING
        self.indent = INDENT_ON
        self.sequence_generators = False

        sequence = []
        if apply_defaults:
            sequence.extend(DEFAULT_VIEWSPECS)
        if letters:
            sequence.extend(str(letters))
        for letter in sequence:
            self.add(letter)

    def add(self, letter, context_level=None):
        """Apply one viewspec letter. Raises ``HyperscopeError`` if unknown."""
        letter = str(letter)
        if len(letter) != 1 or letter not in _VALID:
            raise HyperscopeError("? " + letter)
        if context_level is not None:
            self.context_level = context_level
        level = self.context_level

        if letter == "a":
            if self.level_clip > 0:
                self.level_clip -= 1
        elif letter == "b":
            if self.level_clip < MAX_LEVEL_CLIPPING:
                self.level_clip += 1
        elif letter == "c":
            self.level_clip = MAX_LEVEL_CLIPPING
        elif letter == "d":
            self.level_clip = 1
        elif letter == "e":
            self.level_clip = level
        elif letter == "f":
            pass
        elif letter == "g":
            self.structure = BRANCH_ONLY
        elif letter == "h":
            self.structure = NO_STRUCTURAL_CLIPPING
        elif letter == "i":
            self.filter_type = FILTER_ALL
        elif letter == "j":
            self.filter_type = NO_FILTERING
        elif letter == "k":
            self.filter_type = NEXT_FILTERED_NODE
        elif letter == "l":
            self.structure = PLEX_ONLY
        elif letter == "m":
            self.show_addressing = True
        elif letter == "n":
            self.show_addressing = False
        elif letter == "o":
            self.show_frozen = True
        elif letter == "p":
            self.show_frozen = False
        elif letter == "q":
            if self.line_clip > 1:
                self.line_clip -= 1
        elif letter == "r":
            if self.line_clip < MAX_LINE_CLIPPING:
                self.line_clip += 1
        elif letter == "s":
            self.line_clip = MAX_LINE_CLIPPING
        elif letter == "t":
            self.line_clip = 1
        elif letter in ("u", "v"):
            pass
        elif letter == "w":
            self.line_clip = MAX_LINE_CLIPPING
            self.level_clip = MAX_LEVEL_CLIPPING
        elif letter == "x":
            self.line_clip = 1
            self.level_clip = 1
        elif letter == "y":
            self.show_blank_lines = True
        elif letter == "z":
            self.show_blank_lines = False
        elif letter == "A":
            self.indent = INDENT_ON
        elif letter == "B":
            self.indent = INDENT_OFF
        elif letter == "C":
            self.show_labels = True
        elif letter == "D":
            self.show_labels = False
        elif letter in ("E", "F"):
            pass
        elif letter == "G":
            self.placement = RIGHT
        elif letter == "H":
            self.placement = LEFT
        elif letter == "I":
            self.address_type = SHOW_NODE_ID
        elif letter == "J":
            self.address_type = SHOW_NODE_NUMBER
        elif letter == "K":
            self.show_signatures = True
        elif letter == "L":
            self.show_signatures = False
        elif letter == "O":
            self.sequence_generators = True
        elif letter == "P":
            self.sequence_generators = False
        elif letter == "Q":
            self.indent = INDENT_TO_CONTENT
        else:
            raise HyperscopeError("? " + letter)

    def to_string(self):
        """Letters that differ from the default view, in canonical order."""
        results = ""
        results = self._write_level_and_line(results)
        results = self._write_structure(results)
        results = self._write_filter(results)
        results = self._write_metadata(results)
        results = self._write_appearance(results)
        results += "O" if self.sequence_generators else "P"
        for default in DEFAULT_VIEWSPECS:
            results = results.replace(default, "")
        return results

    def _write_level_and_line(self, results):
        if self.line_clip == 1 and self.level_clip == 1:
            return results + "x"
        if (self.line_clip == MAX_LINE_CLIPPING
                and self.level_clip == MAX_LEVEL_CLIPPING):
            return results + "w"
        return self._write_level(self._write_line(results))

    def _write_line(self, results):
        if self.line_clip == 1:
            return results + "t"
        if self.line_clip == MAX_LINE_CLIPPING:
            return results + "s"
        distance = MAX_LINE_CLIPPING - self.line_clip
        if distance <= self.line_clip:
            return results + "s" + ("q" * distance)
        return results + "t" + ("r" * (self.line_clip - 1))

    def _write_level(self, results):
        ctxt = self.context_level
        if self.level_clip == 1:
            return results + "d"
        if self.level_clip == MAX_LEVEL_CLIPPING:
            return results + "c"
        if self.level_clip == 0:
            return results + "da"
        if self.level_clip >= MAX_LEVEL_CLIPPING - 20:
            distance = MAX_LEVEL_CLIPPING - self.level_clip
            return results + "c" + ("a" * distance)
        if ctxt != 0 and self.level_clip >= ctxt:
            return results + "e" + ("b" * (self.level_clip - ctxt))
        if ctxt != 0 and self.level_clip < ctxt:
            distance = ctxt - self.level_clip
            if distance >= self.level_clip:
                return results + "d" + ("b" * (self.level_clip - 1))
            return results + "e" + ("a" * distance)
        # Context is the origin. Reach level 0, then climb back.
        return results + "da" + ("b" * self.level_clip)

    def _write_structure(self, results):
        return results + {"branch": "g", "plex": "l", "none": "h"}[self.structure]

    def _write_filter(self, results):
        return results + {
            NO_FILTERING: "j",
            FILTER_ALL: "i",
            NEXT_FILTERED_NODE: "k",
        }[self.filter_type]

    def _write_metadata(self, results):
        results += "m" if self.show_addressing else "n"
        results += "C" if self.show_labels else "D"
        results += "K" if self.show_signatures else "L"
        results += "G" if self.placement == RIGHT else "H"
        results += "I" if self.address_type == SHOW_NODE_ID else "J"
        return results

    def _write_appearance(self, results):
        results += "o" if self.show_frozen else "p"
        results += "y" if self.show_blank_lines else "z"
        results += {"on": "A", "off": "B", "to_context_node": "Q"}[self.indent]
        return results
