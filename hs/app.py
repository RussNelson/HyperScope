"""PySide2 interface for HyperScope.

Browser mode offers Help, Jump, and Viewspecs. Turbo mode replaces Jump
and Viewspecs with the Augment command line (type ``j`` then ``i``, an
address, and Enter).
"""

import sys
from pathlib import Path
from urllib.parse import unquote

from PySide2.QtCore import Qt, QUrl
from PySide2.QtGui import QDesktopServices, QFont, QKeySequence, QIcon
from PySide2.QtWidgets import (
    QAction,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QShortcut,
    QSizePolicy,
    QSpinBox,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .address import apply_view, parse_address, resolve
from .commands import CommandSession
from .errors import HyperscopeError
from .model import Document
from .render import render_document
from .viewspec import (
    MAX_LEVEL_CLIPPING,
    MAX_LINE_CLIPPING,
    NO_FILTERING,
    CurrentViewspecs,
)

_HERE = Path(__file__).resolve().parent.parent
_DEMOS = _HERE / "data" / "demos"


def demo_directory():
    return _DEMOS


def default_document():
    preferred = _DEMOS / "tutor-hyperscope.opml"
    if preferred.is_file():
        return preferred
    return _DEMOS / "tutor.opml"


class StatementView(QTextBrowser):
    """The outline. Link clicks are handed to the main window."""

    def __init__(self, window):
        super().__init__(window)
        self._window = window
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.setStyleSheet("QTextBrowser { background: white; border: 0; }")
        self.anchorClicked.connect(window.follow_anchor)

    def keyPressEvent(self, event):
        if self._window.handle_command_key(event):
            return
        super().keyPressEvent(event)


class JumpDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Jump (to) Item")
        self.setModal(True)
        self.resize(460, 120)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Click a statement number, or enter its address:"))
        self.address = QLineEdit()
        self.address.setPlaceholderText("2, 2A, 023, INTRODUCTION, .u, 2:m")
        layout.addWidget(self.address)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        ok = QPushButton("OK")
        ok.setDefault(True)
        cancel.clicked.connect(self.reject)
        ok.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(ok)
        layout.addLayout(buttons)
        self.address.returnPressed.connect(self.accept)

    def value(self):
        return self.address.text().strip()


class ViewspecDialog(QDialog):
    """The Set View panel: what to show, and how deep the outline goes."""

    def __init__(self, parent, document):
        super().__init__(parent)
        self.setWindowTitle("Set View")
        self.setModal(True)
        self.resize(640, 420)
        self._level = document.context.level if document.context else 0
        self._filter_text = document.content_filter or ""
        view = document.viewspecs
        layout = QVBoxLayout(self)

        show = QHBoxLayout()
        self.blank = QCheckBox("Blank lines  (y / z)")
        self.numbers = QCheckBox("Numbering  (m / n)")
        self.blank.setChecked(view.show_blank_lines)
        self.numbers.setChecked(view.show_addressing)
        show.addWidget(self.blank)
        show.addWidget(self.numbers)
        layout.addLayout(show)

        outline = QHBoxLayout()
        self.first = QCheckBox("1st line and level  (x)")
        self.all_lines = QCheckBox("All lines and levels  (w)")
        self.first.setChecked(view.level_clip == 1 and view.line_clip == 1)
        self.all_lines.setChecked(
            view.level_clip >= MAX_LEVEL_CLIPPING
            and view.line_clip >= MAX_LINE_CLIPPING
        )
        outline.addWidget(self.first)
        outline.addWidget(self.all_lines)
        layout.addLayout(outline)

        self._more_box = QWidget()
        more = QVBoxLayout(self._more_box)
        more.setContentsMargins(0, 8, 0, 0)

        place = QHBoxLayout()
        place.addWidget(QLabel("Numbers"))
        self.place_left = QRadioButton("Left (H)")
        self.place_right = QRadioButton("Right (G)")
        group = QButtonGroup(self)
        group.addButton(self.place_left)
        group.addButton(self.place_right)
        self.place_left.setChecked(view.placement != "right")
        self.place_right.setChecked(view.placement == "right")
        place.addWidget(self.place_left)
        place.addWidget(self.place_right)
        self.ids = QCheckBox("Node ids instead of statement numbers (I / J)")
        self.ids.setChecked(view.address_type == "id")
        place.addWidget(self.ids)
        place.addStretch(1)
        more.addLayout(place)

        clips = QHBoxLayout()
        clips.addWidget(QLabel("Levels (a / b)"))
        self.level_spin = QSpinBox()
        self.level_spin.setRange(0, MAX_LEVEL_CLIPPING)
        self.level_spin.setValue(min(view.level_clip, MAX_LEVEL_CLIPPING))
        self.level_spin.setSpecialValueText("0")
        clips.addWidget(self.level_spin)
        clips.addSpacing(16)
        clips.addWidget(QLabel("Lines (q / r)"))
        self.line_spin = QSpinBox()
        self.line_spin.setRange(1, MAX_LINE_CLIPPING)
        self.line_spin.setValue(min(max(view.line_clip, 1), MAX_LINE_CLIPPING))
        clips.addWidget(self.line_spin)
        clips.addStretch(1)
        more.addLayout(clips)

        flags = QHBoxLayout()
        self.labels = QCheckBox("Labels (C / D)")
        self.signatures = QCheckBox("Signatures (K / L)")
        self.includes = QCheckBox("Includes on (O / P)")
        self.labels.setChecked(view.show_labels)
        self.signatures.setChecked(view.show_signatures)
        self.includes.setChecked(view.sequence_generators)
        flags.addWidget(self.labels)
        flags.addWidget(self.signatures)
        flags.addWidget(self.includes)
        flags.addStretch(1)
        more.addLayout(flags)

        structure = QHBoxLayout()
        structure.addWidget(QLabel("Structure"))
        self.structure = QComboBox()
        self.structure.addItem("All branches (h)", "none")
        self.structure.addItem("This branch only (g)", "branch")
        self.structure.addItem("This plex only (l)", "plex")
        index = {"none": 0, "branch": 1, "plex": 2}.get(view.structure, 0)
        self.structure.setCurrentIndex(index)
        structure.addWidget(self.structure)
        structure.addWidget(QLabel("Indent"))
        self.indent = QComboBox()
        self.indent.addItem("On (A)", "on")
        self.indent.addItem("Off (B)", "off")
        self.indent.addItem("From this statement (Q)", "to_context_node")
        self.indent.setCurrentIndex(
            {"on": 0, "off": 1, "to_context_node": 2}.get(view.indent, 0)
        )
        structure.addWidget(self.indent)
        structure.addStretch(1)
        more.addLayout(structure)

        filt = QHBoxLayout()
        self.filter_on = QCheckBox("Apply filter (i / j)")
        self.filter_on.setChecked(view.filter_type != NO_FILTERING)
        self.filter_text = QLineEdit(self._filter_text.strip('"'))
        self.filter_text.setPlaceholderText('word or /regex/i')
        filt.addWidget(self.filter_on)
        filt.addWidget(self.filter_text, 1)
        more.addLayout(filt)
        layout.addWidget(self._more_box)
        self._more_box.setVisible(False)

        letters = QHBoxLayout()
        letters.addWidget(QLabel("Selected viewspecs:"))
        self.letters = QLineEdit(view.to_string())
        letters.addWidget(self.letters, 1)
        layout.addLayout(letters)
        self.error = QLabel("")
        self.error.setStyleSheet("color: #a32020;")
        layout.addWidget(self.error)

        buttons = QHBoxLayout()
        self.more_button = QPushButton("More")
        reset = QPushButton("Reset")
        cancel = QPushButton("Cancel")
        apply_button = QPushButton("Apply")
        apply_button.setDefault(True)
        buttons.addWidget(self.more_button)
        buttons.addStretch(1)
        buttons.addWidget(reset)
        buttons.addWidget(cancel)
        buttons.addWidget(apply_button)
        layout.addLayout(buttons)

        self.more_button.clicked.connect(self._toggle_more)
        reset.clicked.connect(self._reset)
        cancel.clicked.connect(self.reject)
        apply_button.clicked.connect(self._apply)
        self.first.toggled.connect(self._first_toggled)
        self.all_lines.toggled.connect(self._all_toggled)
        self.level_spin.valueChanged.connect(self._clips_edited)
        self.line_spin.valueChanged.connect(self._clips_edited)
        for widget in (
            self.blank, self.numbers, self.labels, self.signatures,
            self.includes, self.ids, self.place_left, self.place_right,
            self.filter_on,
        ):
            widget.toggled.connect(self._refresh_letters)
        self.structure.currentIndexChanged.connect(self._refresh_letters)
        self.indent.currentIndexChanged.connect(self._refresh_letters)
        self.letters.textEdited.connect(self._letters_edited)
        self._refresh_letters()

    def _toggle_more(self):
        show = not self._more_box.isVisible()
        self._more_box.setVisible(show)
        self.more_button.setText("Less" if show else "More")
        self.adjustSize()

    def _first_toggled(self, checked):
        if checked:
            self.all_lines.blockSignals(True)
            self.all_lines.setChecked(False)
            self.all_lines.blockSignals(False)
            self.level_spin.setValue(1)
            self.line_spin.setValue(1)
        self._refresh_letters()

    def _all_toggled(self, checked):
        if checked:
            self.first.blockSignals(True)
            self.first.setChecked(False)
            self.first.blockSignals(False)
            self.level_spin.setValue(MAX_LEVEL_CLIPPING)
            self.line_spin.setValue(MAX_LINE_CLIPPING)
        self._refresh_letters()

    def _clips_edited(self):
        level = self.level_spin.value()
        line = self.line_spin.value()
        self.first.blockSignals(True)
        self.all_lines.blockSignals(True)
        self.first.setChecked(level == 1 and line == 1)
        self.all_lines.setChecked(
            level >= MAX_LEVEL_CLIPPING and line >= MAX_LINE_CLIPPING
        )
        self.first.blockSignals(False)
        self.all_lines.blockSignals(False)
        self._refresh_letters()

    def _reset(self):
        fresh = CurrentViewspecs(context_level=self._level)
        self.blank.setChecked(fresh.show_blank_lines)
        self.numbers.setChecked(fresh.show_addressing)
        self.labels.setChecked(fresh.show_labels)
        self.signatures.setChecked(fresh.show_signatures)
        self.includes.setChecked(False)
        self.ids.setChecked(False)
        self.place_left.setChecked(True)
        self.level_spin.setValue(MAX_LEVEL_CLIPPING)
        self.line_spin.setValue(MAX_LINE_CLIPPING)
        self.structure.setCurrentIndex(0)
        self.indent.setCurrentIndex(0)
        self.filter_on.setChecked(False)
        self.filter_text.clear()
        self._refresh_letters()

    def _letters_edited(self):
        text = "".join(ch for ch in self.letters.text() if not ch.isspace())
        try:
            CurrentViewspecs(text, context_level=self._level)
        except HyperscopeError as exc:
            self.error.setText(str(exc))
            return
        self.error.setText("")

    def _refresh_letters(self):
        view = self._capture()
        self.letters.blockSignals(True)
        self.letters.setText(view.to_string())
        self.letters.blockSignals(False)
        self.error.setText("")

    def _capture(self):
        view = CurrentViewspecs(context_level=self._level)
        view.show_blank_lines = self.blank.isChecked()
        view.show_addressing = self.numbers.isChecked()
        view.show_labels = self.labels.isChecked()
        view.show_signatures = self.signatures.isChecked()
        view.sequence_generators = self.includes.isChecked()
        view.placement = "right" if self.place_right.isChecked() else "left"
        view.address_type = "id" if self.ids.isChecked() else "number"
        view.structure = self.structure.currentData()
        view.indent = self.indent.currentData()
        view.level_clip = self.level_spin.value()
        view.line_clip = self.line_spin.value()
        view.filter_type = "all" if self.filter_on.isChecked() else NO_FILTERING
        return view

    def _apply(self):
        text = "".join(ch for ch in self.letters.text() if not ch.isspace())
        try:
            CurrentViewspecs(text, context_level=self._level)
        except HyperscopeError as exc:
            self.error.setText(str(exc))
            return
        self._letters = text
        self._accepted_filter = None
        if self.filter_on.isChecked():
            raw = self.filter_text.text().strip()
            if raw and not raw.startswith('"') and not raw.startswith("/"):
                raw = '"' + raw + '"'
            self._accepted_filter = raw or None
            if "i" not in text and "k" not in text:
                self._letters = text + "i"
        self.accept()

    def result_view(self):
        return getattr(self, "_letters", ""), getattr(self, "_accepted_filter", None)


class HelpDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("HyperScope Help")
        self.resize(560, 420)
        layout = QVBoxLayout(self)
        text = QTextBrowser()
        text.setOpenExternalLinks(True)
        text.setHtml(
            "<h2>HyperScope</h2>"
            "<p>HyperScope is a thought processor for reading an outline. "
            "Every statement has an address. The view — how much of the "
            "outline you see — is a set of letters called viewspecs.</p>"
            "<p><b>Jump</b> (Ctrl+J) moves to a statement. "
            "<code>2</code> is the second statement, <code>2A</code> is its "
            "first child, <code>023</code> is a node id, and "
            "<code>.u</code> moves up. Add viewspecs after a colon: "
            "<code>2:x</code> shows one line of one level at statement 2.</p>"
            "<p><b>Viewspecs</b> (Ctrl+Shift+V) change the view. "
            "<code>m</code> shows statement numbers, <code>n</code> hides them, "
            "<code>y</code> puts a blank line between statements, "
            "<code>z</code> takes it away, <code>w</code> shows everything, "
            "and <code>x</code> shows the first line of the first level. "
            "The purple number beside a statement is its address; click it "
            "to bring that statement to the top.</p>"
            "<p><b>Turbo mode</b> is the Augment command line. "
            "<code>j</code> <code>i</code> then an address and Enter jumps. "
            "<code>j</code> <code>o</code> Enter Enter returns to the origin. "
            "A leading space selects the less common command: "
            "space <code>se</code> <code>v</code> sets viewspecs, and "
            "<code>res</code> <code>v</code> Enter resets them. "
            "Escape clears the line. While the prompt says <code>M/A</code>, "
            "click a statement to point at it.</p>"
            "<p>The link button copies the address of the statement in view. "
            "Open any OPML file from the File menu; the tutorial and the "
            "other sample documents are under Open Demo.</p>"
            "<p>This is a PySide2 desktop port of HyperScope 1.1, "
            "Doug Engelbart's outline viewer.</p>"
        )
        layout.addWidget(text)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        layout.addWidget(close, alignment=Qt.AlignRight)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("HyperScope")
        self.resize(1080, 740)
        self.document = None
        self.turbo = False
        self.commands = CommandSession()
        self.history = []
        self.history_at = -1
        self._showing = False

        self._build_actions()
        self._build_toolbar()
        self._build_menus()
        self.view = StatementView(self)
        self.setCentralWidget(self.view)
        self.status = self.statusBar()
        self.status.showMessage("Open an OPML document to begin.")
        QShortcut(QKeySequence("Ctrl+J"), self, self.jump)
        QShortcut(QKeySequence("Ctrl+Shift+V"), self, self.viewspecs)

    def _build_actions(self):
        self.open_action = QAction("Open…", self)
        self.open_action.setShortcut(QKeySequence.Open)
        self.open_action.triggered.connect(self.open_file)
        self.quit_action = QAction("Quit", self)
        self.quit_action.setShortcut(QKeySequence.Quit)
        self.quit_action.triggered.connect(self.close)
        self.back_action = QAction("Back", self)
        self.back_action.setShortcut(QKeySequence.Back)
        self.back_action.triggered.connect(self.back)
        self.forward_action = QAction("Forward", self)
        self.forward_action.setShortcut(QKeySequence.Forward)
        self.forward_action.triggered.connect(self.forward)
        self.origin_action = QAction("Origin", self)
        self.origin_action.triggered.connect(lambda: self.go("#.o"))
        self.jump_action = QAction("Jump to Item…", self)
        self.jump_action.triggered.connect(self.jump)
        self.view_action = QAction("Viewspecs…", self)
        self.view_action.triggered.connect(self.viewspecs)
        self.reset_view_action = QAction("Reset Viewspecs", self)
        self.reset_view_action.triggered.connect(self.reset_view)
        self.turbo_action = QAction("Turbo Mode", self)
        self.turbo_action.triggered.connect(lambda: self.set_turbo(True))
        self.browser_action = QAction("Browser Mode", self)
        self.browser_action.triggered.connect(lambda: self.set_turbo(False))
        self.copy_action = QAction("Copy Address", self)
        self.copy_action.setShortcut(QKeySequence("Ctrl+L"))
        self.copy_action.triggered.connect(self.copy_address)
        self.help_action = QAction("Help", self)
        self.help_action.setShortcut(QKeySequence.HelpContents)
        self.help_action.triggered.connect(self.help)
        self.about_action = QAction("About HyperScope", self)
        self.about_action.triggered.connect(self.about)

    def _build_menus(self):
        file_menu = self.menuBar().addMenu("File")
        file_menu.addAction(self.open_action)
        self.demo_menu = file_menu.addMenu("Open Demo")
        self._fill_demos()
        file_menu.addSeparator()
        file_menu.addAction(self.quit_action)
        go_menu = self.menuBar().addMenu("Navigate")
        go_menu.addAction(self.jump_action)
        go_menu.addAction(self.origin_action)
        go_menu.addSeparator()
        go_menu.addAction(self.back_action)
        go_menu.addAction(self.forward_action)
        view_menu = self.menuBar().addMenu("View")
        view_menu.addAction(self.view_action)
        view_menu.addAction(self.reset_view_action)
        view_menu.addSeparator()
        view_menu.addAction(self.turbo_action)
        view_menu.addAction(self.browser_action)
        view_menu.addSeparator()
        view_menu.addAction(self.copy_action)
        help_menu = self.menuBar().addMenu("Help")
        help_menu.addAction(self.help_action)
        help_menu.addAction(self.about_action)

    def _fill_demos(self):
        if not _DEMOS.is_dir():
            return
        for path in sorted(_DEMOS.glob("*.opml")):
            action = QAction(path.name, self)
            action.triggered.connect(
                lambda checked=False, filename=str(path): self.open_path(filename)
            )
            self.demo_menu.addAction(action)

    def _build_toolbar(self):
        bar = self.addToolBar("HyperScope")
        bar.setMovable(False)
        bar.setStyleSheet(
            "QToolBar { background: #2c3a4a; spacing: 6px; padding: 4px; }"
            "QToolButton { color: white; font-weight: bold; padding: 4px 8px; }"
            "QLabel { color: white; }"
        )
        logo = QLabel("  HyperScope")
        logo.setFont(QFont("Georgia", 16, QFont.Bold, True))
        logo.setStyleSheet("color: #f4f1ea; padding-right: 8px;")
        bar.addWidget(logo)
        self.help_button = QPushButton("Help")
        self.jump_button = QPushButton("Jump")
        self.view_button = QPushButton("Viewspecs")
        self.turbo_button = QPushButton("Turbo")
        self.browser_button = QPushButton("Browser")
        self.link_button = QPushButton("Link")
        for button in (
            self.help_button, self.jump_button, self.view_button,
            self.turbo_button, self.browser_button, self.link_button,
        ):
            button.setStyleSheet(
                "QPushButton { color: #f4f1ea; background: #3d5166;"
                " border: 1px solid #223140; padding: 4px 10px; }"
                "QPushButton:hover { background: #4e6780; }"
            )
        self.help_button.clicked.connect(self.help)
        self.jump_button.clicked.connect(self.jump)
        self.view_button.clicked.connect(self.viewspecs)
        self.turbo_button.clicked.connect(lambda: self.set_turbo(True))
        self.browser_button.clicked.connect(lambda: self.set_turbo(False))
        self.link_button.clicked.connect(self.copy_address)
        bar.addWidget(self.help_button)
        self.command_label = QLabel("BASE C: ")
        self.command_label.setFont(QFont("Courier", 11, QFont.Bold))
        self.command_label.setStyleSheet(
            "background: #e7f1fb; color: black; padding: 3px 8px;"
            " border: 1px inset #8aa;"
        )
        self.command_label.setMinimumWidth(280)
        bar.addWidget(self.command_label)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        bar.addWidget(spacer)
        bar.addWidget(self.jump_button)
        bar.addWidget(self.view_button)
        bar.addWidget(self.turbo_button)
        bar.addWidget(self.browser_button)
        bar.addWidget(self.link_button)
        self.browser_button.hide()
        self.command_label.hide()

    def set_turbo(self, enabled):
        self.turbo = enabled
        self.command_label.setVisible(enabled)
        self.browser_button.setVisible(enabled)
        self.turbo_button.setVisible(not enabled)
        self.jump_button.setVisible(not enabled)
        self.view_button.setVisible(not enabled)
        if enabled:
            prompt, _action = self.commands.reset()
            self.command_label.setText(prompt)
            self.status.showMessage(
                "Turbo mode. j i jumps, Escape clears the command."
            )
        else:
            self.status.showMessage("Browser mode.")

    def handle_command_key(self, event):
        if not self.turbo or self.document is None:
            return False
        if event.modifiers() & Qt.ControlModifier and event.key() == Qt.Key_B:
            self._apply_command(*self.commands.repeat())
            return True
        if event.modifiers() & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier):
            return False
        key = event.key()
        if key == Qt.Key_Escape:
            self._apply_command(*self.commands.reset())
            return True
        if key == Qt.Key_Backspace:
            self._apply_command(*self.commands.backspace())
            return True
        if key in (Qt.Key_Return, Qt.Key_Enter):
            self._apply_command(*self.commands.enter())
            return True
        text = event.text()
        if text and text.isprintable():
            self._apply_command(*self.commands.type_char(text))
            return True
        return False

    def keyPressEvent(self, event):
        if self.handle_command_key(event):
            return
        super().keyPressEvent(event)

    def _apply_command(self, prompt, action):
        self.command_label.setText(prompt)
        if self.commands.marking():
            self.show_document()
        if action:
            self._run_action(action)
            self.command_label.setText("BASE C: ")

    def _run_action(self, action):
        method = action["method"]
        view = action.get("viewspecs") or ""
        try:
            if method == "jump_item":
                self.go(self._located(action))
            elif method == "jump_link":
                self.go(action.get("typein") or "#")
            elif method == "jump_file":
                name = action.get("typein") or ""
                if name and not name.startswith(("./", "../", "/")) and "://" not in name:
                    name = "./" + name
                if view:
                    name += "#:" + view
                self.go(name or "#")
            elif method == "jump_origin":
                self.go(self._with_move(action.get("typein") or "#", "o", view))
            elif method == "jump_next":
                self.go(self._with_move(self._located(action, include_view=False), "n", view))
            elif method == "jump_back":
                self.go(self._with_move(self._located(action, include_view=False), "b", view))
            elif method == "jump_down":
                self.go(self._with_move(self._located(action, include_view=False), "d", view))
            elif method == "jump_up":
                self.go(self._with_move(self._located(action, include_view=False), "u", view))
            elif method == "jump_successor":
                self.go(self._with_move(self._located(action, include_view=False), "s", view))
            elif method == "jump_predecessor":
                self.go(self._with_move(self._located(action, include_view=False), "p", view))
            elif method == "jump_head":
                self.go(self._with_move(self._located(action, include_view=False), "h", view))
            elif method == "jump_tail":
                self.go(self._with_move(self._located(action, include_view=False), "t", view))
            elif method == "jump_end_branch":
                self.go(self._with_move(self._located(action, include_view=False), "e", view))
            elif method == "jump_end_plex":
                self.go(self._with_move(self._located(action, include_view=False), "t", view))
            elif method in ("jump_content_first", "jump_content_next",
                            "jump_word_first", "jump_word_next",
                            "jump_name_first", "jump_name_next"):
                self._search_command(action)
            elif method == "reset_viewspecs":
                self.go("#:" + "hjmpuwyACHJLP")
            elif method == "set_viewspecs":
                self.go("#:" + view)
            else:
                self.report("Unknown command " + method)
        except HyperscopeError as exc:
            self.report(str(exc))

    def _located(self, action, include_view=True):
        if action.get("marked"):
            raw = "#" + str(action.get("location") or "")
        else:
            raw = action.get("location") or "#"
        if include_view and action.get("viewspecs"):
            address = parse_address(raw)
            address.viewspecs += action["viewspecs"]
            return address
        return raw

    def _with_move(self, raw, kind, view):
        address = raw if hasattr(raw, "pieces") else parse_address(raw)
        from .address import Relative
        address.pieces.append(Relative(kind))
        if view:
            address.viewspecs += view
        return address

    def _search_command(self, action):
        text = action.get("typein") or ""
        if not text:
            raise HyperscopeError("? ")
        method = action["method"]
        first = method.endswith("first")
        if "content" in method:
            self.document.jump_content(text, first=first)
        elif "word" in method:
            self.document.jump_word(text, first=first)
        else:
            self.document.jump_label(text, first=first)
        view = action.get("viewspecs") or ""
        self.go("#" + self.document.context.number + ((":" + view) if view else ""))

    def open_file(self):
        path, _selected = QFileDialog.getOpenFileName(
            self, "Open OPML", str(_DEMOS if _DEMOS.is_dir() else Path.home()),
            "OPML documents (*.opml *.xml);;All files (*)",
        )
        if path:
            self.open_path(path)

    def open_path(self, path):
        self.history = []
        self.history_at = -1
        try:
            document = resolve(str(Path(path).resolve()), fresh=True)
        except HyperscopeError as exc:
            self.report(str(exc))
            return
        self._adopt(document, record=True)

    def go(self, url):
        if self.document is None and not str(url).startswith(("/", ".")) and "://" not in str(url):
            self.report("Open a document first.")
            return
        try:
            document = resolve(url, relative_to=self.document)
        except HyperscopeError as exc:
            self.report(str(exc))
            return
        self._adopt(document, record=True)

    def _adopt(self, document, record):
        self.document = document
        self._showing = True
        self.show_document()
        title = document.title or Path(document.path).name or "HyperScope"
        self.setWindowTitle(title + " — HyperScope")
        self.status.showMessage(self.address_text())
        if record:
            snap = self._snapshot()
            if self.history_at < len(self.history) - 1:
                self.history = self.history[:self.history_at + 1]
            if not self.history or self.history[-1] != snap:
                self.history.append(snap)
                self.history_at = len(self.history) - 1
        self._update_history_actions()

    def show_document(self):
        if self.document is None:
            return
        html = render_document(self.document, mark_mode=self.turbo and self.commands.marking())
        self.view.setHtml(html)
        if self.document.context is not None:
            self.view.scrollToAnchor("stmt-" + self.document.context.number)

    def _snapshot(self):
        document = self.document
        return (
            document.source_url or str(Path(document.path).resolve()),
            document.context.number if document.context else "0",
            document.viewspecs.to_string(),
            document.content_filter if document.viewspecs.filter_type != NO_FILTERING else None,
        )

    def _restore(self, snap):
        path, number, letters, content_filter = snap
        url = path + "#" + number
        if letters:
            url += ":" + letters
        if content_filter:
            url += ";" + content_filter + ";"
        try:
            document = resolve(url)
        except HyperscopeError as exc:
            self.report(str(exc))
            return
        self._adopt(document, record=False)

    def back(self):
        if self.history_at <= 0:
            return
        self.history_at -= 1
        self._restore(self.history[self.history_at])

    def forward(self):
        if self.history_at >= len(self.history) - 1:
            return
        self.history_at += 1
        self._restore(self.history[self.history_at])

    def _update_history_actions(self):
        self.back_action.setEnabled(self.history_at > 0)
        self.forward_action.setEnabled(self.history_at < len(self.history) - 1)

    def address_text(self):
        if self.document is None:
            return ""
        return self.document.location()

    def follow_anchor(self, qurl):
        # Use QUrl's path rather than urlparse. A percent-encoded "#" is kept
        # inside the path by Qt, and urlparse would treat the decoded "#" as
        # the start of a fragment.
        scheme = (qurl.scheme() or "").lower()
        path = unquote(qurl.path() or "")
        if scheme == "hs" and path.startswith("/mark/"):
            number = path[len("/mark/"):]
            prompt, action = self.commands.mark(number)
            self._apply_command(prompt, action)
            return
        if scheme == "hs" and path.startswith("/jump/"):
            number = path[len("/jump/"):]
            if self.turbo and self.commands.marking():
                prompt, action = self.commands.mark(number)
                self._apply_command(prompt, action)
            else:
                self.go("#" + number)
            return
        if scheme == "hs" and path.startswith("/go/"):
            target = path[len("/go/"):]
            if target.startswith(("http://", "https://")) and ".opml" not in target.lower().split("#", 1)[0]:
                QDesktopServices.openUrl(QUrl(target))
                return
            self.go(target)
            return
        if scheme == "hs" and path.startswith("/quick/"):
            which = path[len("/quick/"):]
            context = self.document.context if self.document is not None else None
            number = context.number if context is not None else "0"
            if which == "out":
                if context is not None and context.parent is not None:
                    parent = context.parent.number
                else:
                    parent = "0"
                self.go("#" + parent + ":ebthz")
            elif which == "in":
                self.go("#" + number + ":ebthz")
            else:
                self.go("#" + number + ":wyh")
            return
        if scheme in ("http", "https"):
            QDesktopServices.openUrl(qurl)

    def jump(self):
        if self.document is None:
            self.report("Open a document first.")
            return
        dialog = JumpDialog(self)
        if dialog.exec_() != QDialog.Accepted:
            return
        text = dialog.value()
        if not text:
            return
        self.go(text)

    def viewspecs(self):
        if self.document is None:
            self.report("Open a document first.")
            return
        dialog = ViewspecDialog(self, self.document)
        if dialog.exec_() != QDialog.Accepted:
            return
        letters, content_filter = dialog.result_view()
        try:
            document = apply_view(self.document, letters, content_filter)
        except HyperscopeError as exc:
            self.report(str(exc))
            return
        self._adopt(document, record=True)

    def reset_view(self):
        if self.document is None:
            return
        try:
            document = apply_view(self.document, "", None)
        except HyperscopeError as exc:
            self.report(str(exc))
            return
        self._adopt(document, record=True)

    def copy_address(self):
        if self.document is None:
            return
        text = self.address_text()
        QApplication.clipboard().setText(text)
        self.status.showMessage("Copied " + text)

    def help(self):
        HelpDialog(self).exec_()

    def about(self):
        QMessageBox.about(
            self,
            "About HyperScope",
            "HyperScope is Doug Engelbart's outline viewer, ported here "
            "to a PySide2 desktop application.\n\n"
            "The original HyperScope 1.1 was written by Brad Neuberg and "
            "the Bootstrap Alliance. This port reads the same OPML documents "
            "and viewspecs.",
        )

    def report(self, message):
        self.status.showMessage(str(message))


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    app = QApplication(argv)
    app.setApplicationName("HyperScope")
    window = MainWindow()
    window.setWindowIcon(QIcon("HyperScope.ico"))
    path = argv[1] if len(argv) > 1 else str(default_document())
    if path and Path(path).is_file():
        window.open_path(path)
    window.show()
    return app.exec_()
