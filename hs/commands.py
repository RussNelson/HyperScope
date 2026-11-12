"""Augment-style command line used in turbo mode.

Typing follows the base subsystem from HyperScope 1.1. A letter selects
the verb or noun (``j`` becomes "Jump (to)"), Enter accepts the current
field, and Escape clears the line. The leading space on ``Next`` and
``Set`` is how Augment distinguishes them from ``Name`` and other ``s``
commands.
"""


# Each command is (activator, output, children-or-inputs, method).
# An input is ("input", type). A method is ("method", name).

def _inputs(*types, method):
    node = ("method", method)
    for kind in reversed(types):
        node = ("input", kind, node)
    return node


_JUMP = ("verb", "j", "Jump (to) ", [
    ("noun", "i", "Item ", _inputs("location", "viewspecs", method="jump_item")),
    ("noun", "l", "Link ", _inputs("typein", method="jump_link")),
    ("noun", "o", "Origin (of file) ", _inputs("address", "viewspecs", method="jump_origin")),
    ("noun", "f", "File (named) ", _inputs("typein", "viewspecs", method="jump_file")),
    ("noun", " n", "Next (from) ", _inputs("location", "viewspecs", method="jump_next")),
    ("noun", "b", "Back (from) ", _inputs("location", "viewspecs", method="jump_back")),
    ("noun", "d", "Down (from) ", _inputs("location", "viewspecs", method="jump_down")),
    ("noun", "u", "Up (from) ", _inputs("location", "viewspecs", method="jump_up")),
    ("noun", "s", "Successor (of) ", _inputs("location", "viewspecs", method="jump_successor")),
    ("noun", "p", "Predecessor (of) ", _inputs("location", "viewspecs", method="jump_predecessor")),
    ("noun", "h", "Head (of plex at) ", _inputs("location", "viewspecs", method="jump_head")),
    ("noun", "t", "Tail (of plex at) ", _inputs("location", "viewspecs", method="jump_tail")),
    ("noun", "e", "End (of) ", [
        ("noun", "b", "Branch (at) ", _inputs("location", "viewspecs", method="jump_end_branch")),
        ("noun", "p", "Plex (at) ", _inputs("location", "viewspecs", method="jump_end_plex")),
    ]),
    ("noun", "c", "Content ", [
        ("noun", "f", "First ", _inputs("persisted", "viewspecs", method="jump_content_first")),
        ("noun", "n", "Next ", _inputs("persisted", "viewspecs", method="jump_content_next")),
    ]),
    ("noun", "w", "Word ", [
        ("noun", "f", "First ", _inputs("persisted", "viewspecs", method="jump_word_first")),
        ("noun", "n", "Next ", _inputs("persisted", "viewspecs", method="jump_word_next")),
    ]),
    ("noun", "n", "Name ", [
        ("noun", "f", "First ", _inputs("persisted", "viewspecs", method="jump_name_first")),
        ("noun", "n", "Next ", _inputs("persisted", "viewspecs", method="jump_name_next")),
    ]),
])

_COMMANDS = [
    _JUMP,
    ("verb", "res", "Reset ", [
        ("noun", "v", "Viewspecs (to defaults) ", _inputs("ok", method="reset_viewspecs")),
    ]),
    ("verb", " se", "Set ", [
        ("noun", "v", "Viewspecs ", _inputs("viewspecs", method="set_viewspecs")),
    ]),
]


def _nouns(node):
    spec = node[3]
    if isinstance(spec, list):
        return spec
    return []


def _input_of(node):
    """The input chain hanging off a verb or noun, if it has one."""
    spec = node[3]
    if isinstance(spec, tuple) and spec and spec[0] == "input":
        return spec
    return None


def _prompt_for(kind, persisted, first_persist):
    if kind == "location":
        return "M/A: "
    if kind == "viewspecs":
        return "V: "
    if kind == "ok":
        return "OK: "
    if kind == "typein":
        return "T: "
    if kind == "address":
        return "A: "
    if kind == "persisted":
        if persisted and not first_persist:
            return '"%s" RC/T: ' % persisted
        return "T: "
    return ""


class CommandSession:
    """Keystrokes collected since Escape, and the prompt they produce."""

    def __init__(self):
        self.buffer = []
        self.persisted = None
        self.first_persist = True
        self.last_buffer = None
        self._fields = {}

    def reset(self):
        self.buffer = []
        self._fields = {}
        return self.prompt(), None

    def backspace(self):
        if self.buffer:
            self.buffer.pop()
        return self.prompt(), None

    def enter(self):
        self.buffer.append({"enter": True, "value": "", "mark": None})
        return self._evaluate(commit=True)

    def type_char(self, character):
        self.buffer.append({"enter": False, "value": character, "mark": None})
        return self._evaluate(commit=True)

    def mark(self, number):
        """The statement the user pointed at, while a location is expected."""
        self.buffer.append({"enter": False, "value": "", "mark": number})
        return self._evaluate(commit=True)

    def repeat(self):
        """Ctrl+B. Repeat the previous type-in, or the previous command."""
        if self.buffer:
            self.buffer.append({"enter": False, "value": "", "mark": None, "repeat": True})
        elif self.last_buffer:
            self.buffer = list(self.last_buffer)
        return self._evaluate(commit=True)

    def marking(self):
        """True when the next click should point at a statement."""
        _prompt, action, kind = self._walk(list(self.buffer))
        return action is None and kind == "location"

    def prompt(self):
        prompt, _action, _kind = self._walk(list(self.buffer))
        return prompt

    def _evaluate(self, commit):
        prompt, action, _kind = self._walk(list(self.buffer))
        if action is not None:
            self.last_buffer = list(self.buffer[:-1]) if self.buffer else []
            if action.get("typein"):
                self.persisted = action["typein"]
                self.first_persist = False
            self.buffer = []
            prompt = "BASE C: "
        return prompt, action

    def _walk(self, events):
        """Replay ``events``. Returns ``(prompt, action or None, input kind)``."""
        self._fields = {}
        prompt = "BASE "
        if not events:
            return prompt + "C: ", None, None

        index = 0
        spec = None
        choices = _COMMANDS
        while True:
            if spec is None or spec[0] in ("verb", "noun"):
                if spec is not None:
                    choices = _nouns(spec)
                if index >= len(events):
                    return prompt + "C: ", None, None
                letters = ""
                matched = None
                partial = False
                while index < len(events):
                    event = events[index]
                    index += 1
                    if event.get("enter") or event.get("mark") or event.get("repeat"):
                        self._drop_last()
                        return prompt + "?", None, None
                    letters += event.get("value") or ""
                    partial = False
                    matched = None
                    for child in choices:
                        activator = child[1]
                        if letters.lower() == activator:
                            matched = child
                            break
                        if activator.startswith(letters.lower()):
                            partial = True
                    if matched is not None or not partial:
                        break
                if matched is None and partial:
                    return prompt + letters.upper(), None, None
                if matched is None:
                    self._drop_last()
                    return prompt + "?", None, None
                spec = matched
                prompt += spec[2]
                if _nouns(spec):
                    if index >= len(events):
                        return prompt + "C: ", None, None
                    continue
                spec = _input_of(spec)
                if spec is None:
                    self._drop_last()
                    return prompt + "?", None, None

            kind = spec[1]
            if index >= len(events):
                prompt += _prompt_for(kind, self.persisted, self.first_persist)
                return prompt, None, kind
            prompt += _prompt_for(kind, self.persisted, self.first_persist)
            typed = []
            done = False
            marked = None
            while index < len(events) and not done:
                event = events[index]
                index += 1
                if event.get("mark") and kind == "location":
                    marked = event["mark"]
                    prompt += "! "
                    done = True
                elif event.get("repeat") and kind == "persisted" and self.persisted:
                    typed = list(self.persisted)
                    prompt += self.persisted + " ! "
                    done = True
                elif event.get("enter"):
                    if (kind == "persisted" and not typed
                            and self.persisted and not self.first_persist):
                        typed = list(self.persisted)
                    prompt += "! "
                    done = True
                else:
                    value = event.get("value") or ""
                    typed.append(value)
                    prompt += value
            if not done:
                return prompt, None, kind
            self._store(kind, "".join(typed), marked)
            spec = spec[2]
            if spec[0] == "method":
                return prompt, self._action(spec[1]), None

    def _drop_last(self):
        if self.buffer:
            self.buffer.pop()

    def _store(self, kind, text, marked):
        if kind == "location":
            self._fields["location"] = marked if marked is not None else text
            self._fields["marked"] = marked is not None
        elif kind == "viewspecs":
            self._fields["viewspecs"] = text
        elif kind in ("typein", "persisted", "address"):
            self._fields["typein"] = text

    def _action(self, method):
        fields = self._fields
        return {
            "method": method,
            "location": fields.get("location", ""),
            "marked": fields.get("marked", False),
            "viewspecs": fields.get("viewspecs", ""),
            "typein": fields.get("typein", ""),
        }
