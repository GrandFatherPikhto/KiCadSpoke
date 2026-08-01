# gui/docks/extract.py
"""
ExtractDock — build a Cell template from whatever's currently selected on
the board and write it into the file picked in the Files dock. Wraps
kicadstamp.template_extraction.extract_template_from_selection() plus
kicadstamp_cli.py's cmd_extract merge-into-existing-file behaviour — NOT
kicadstamp.author.dump_template(), which always overwrites the whole file
(fine for a script regenerating its own dedicated file, wrong here: a file
picked in the Files dock is very likely already home to other cells).

Fed by the same selection-watch timer as the tree/bulk-edit docks (see
MainWindow._poll_board_selection) — but unlike those, which only need
FootprintInstance refs, extraction needs the FULL raw selection (vias and
tracks too — a thermal via array or a decoupling-cap+via pattern has both),
so MainWindow passes this dock the raw get_selected_items() result
alongside the Selected-wrapped footprint list the other docks use.

Optionally also writes an extract_profiles: entry (the --profile mechanism
in kicadstamp_cli.py's extract command) — a replayable recipe (name/output/
params) so the same extraction can be re-run from the CLI later without
retyping the alias mapping by hand. This CANNOT go in the same file as the
cell output: cell_files/cells_file content is parsed as a flat
{cell_name: {...}} dict with no wrapper (see config/loader.py — every
top-level key is treated as a cell name), so an extract_profiles: sibling
key in that file would be misread as another cell. extract_profiles: lives
in a root/included profile config instead (alongside clone_placements:/
include:/cells_file: etc.) — a second, independent file target.

Net aliases feed params, NOT net_template_map, directly — the alias field
next to a net IS its params key (e.g. net '+2V5' aliased 'PWR_IN' becomes
params={'PWR_IN': '+2V5'}). extract_template_from_selection()'s own
auto-inference then derives net_template_map from params on its own
(net_resolution.py: any literal net equal to a param VALUE gets mapped to
that param's {key} automatically) — passing net_template_map directly
without matching params was an earlier bug here: parametrize_net() always
round-trip-checks pattern.format(**params) against the literal, and with
params=={} that check fails for every single alias (found live 2026-08-01,
"net '{X}' has a placeholder with no parameter" on every extract attempt
that used an alias).

Both the cell-output file and the extract_profiles file are chosen in the
Files dock now (its Cells/Extractor role slots — see file_picker.py),
pushed here via set_target_file()/set_profile_file(). This dock used to
have its own separate "Change profile file..." QFileDialog button for the
profile file alone, which meant two different ways to pick a file for two
closely related purposes — reported as confusing live 2026-08-01.

There's a third role, Placer (set_placer_file()) — the root config a
placement run would actually be pointed at. After a successful extract,
if a Placer file is assigned, this dock also makes sure that file's own
cell_files: list includes the Cells file and its include: list includes
the Extractor file (deduped by resolved path, never duplicated, every
other key in the file left alone — see _add_list_entry(), same read-
merge-write shape as _write_merged() but for a list section instead of a
dict one). Requested live 2026-08-01 ("это всё собралось вместе, и placer
— это точка сборки"): extracting a cell is meant to leave the Placer file
ready to use it, not just leave the cell sitting in a file nothing points
at yet. Skipped when Cells/Extractor already IS the Placer file (self-
reference is pointless — the file already effectively "has itself").

Net template role (a component whose pads touch TWO aliased nets — a
ferrite bead/inductor bridging two rails, e.g. a pi-filter's feedback
element): extract_template_from_selection() can't auto-decide which of
the two aliased nets becomes that role's net_template (see
template_extraction.py, "N nets from --net-template on pads"), so it's
left EMPTY there unless net_template_role={role: literal} says which one
— a mechanism that already existed backend/CLI-side (--net-template-role)
but had no GUI surface until now. The "Net template role:" section only
ever shows a role once 2+ of ITS pads' nets have a non-empty alias typed
next to them (checked live: this project's own board has exactly this
shape — a PI_FILTER_FB ferrite bead with '-2V5' on one pad and
'-2V5_DIRTY' on the other, both meant to be templated) — reactive to
alias typing, not a fixed list. Extraction is blocked until every such
row has a pick; there is no safe default to guess.

The "Existing cells:"/"Existing profiles:" lists read straight from
whatever those two files currently contain (top-level keys / the
extract_profiles: section's keys) and let a click reuse an existing name
outright. They also drive a quiet auto-fill: when the current selection is
a single Cluster and a slugified form of it matches an existing key, that
key is filled into the Cell name / Profile key fields (only if the field
is still empty — never stomps something the user already typed) and
highlighted in its list. Matching is a plain slug comparison (e.g. Cluster
'PWR/DAC0' -> 'pwr_dac0'), not a stored Cluster->name mapping — nothing in
the file formats records that association today, so this is a heuristic
that helps when it hits.

Cell name specifically ALSO falls back to the Cluster's own slug when
nothing existing matches (Profile key does not — it already defaults to
the cell name at extraction time, see _on_extract()). Requested live
2026-08-01: "если есть имя кластера, зачем придумывать что-то" — a first-
time extraction has no existing key to match yet, but the Cluster name is
already right there, so Cell name is never left blank purely because
nothing's been extracted from it before.
"""
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from kipy.board_types import Via
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDockWidget, QFormLayout,
                              QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                              QListWidget, QPushButton, QScrollArea,
                              QVBoxLayout, QWidget)

from kicadstamp.exceptions import PlacerError
from kicadstamp.explore import Selected
from kicadstamp.i18n import _
from kicadstamp.template_extraction import extract_template_from_selection

from .. import yaml_io
from ..worker import start_long_op
from ._common import (ERROR_STYLE as _ERROR_STYLE, SUCCESS_STYLE as _SUCCESS_STYLE,
                      WARN_STYLE as _WARN_STYLE, add_list_entry, display_path,
                      merge_write, set_combo_items, show_message)

logger = logging.getLogger(__name__)


class ExtractDock(QDockWidget):
    def __init__(self, main_window, connection=None):
        super().__init__(_("Extract"), main_window)
        self._main_window = main_window
        # Injected BoardConnection — falls back to the owning window's when
        # not passed explicitly (keeps direct-construction callers, e.g.
        # tests that mutate main_window.connection.board, working).
        self._connection = connection if connection is not None else main_window.connection
        # The currently running long op (gui/worker.py) — held so the
        # parent-less QThread isn't garbage-collected mid-run.
        self._active_op: Optional[Any] = None
        self._raw_items: List[Any] = []
        self._selected_footprints: List[Selected] = []
        self._target_path: Optional[Path] = None
        self._profile_path: Optional[Path] = None
        self._placer_path: Optional[Path] = None
        self._net_alias_edits: Dict[str, QLineEdit] = {}
        self._net_template_role_edits: Dict[str, QComboBox] = {}
        self._last_autofill_key: Optional[Tuple[frozenset, Optional[Path], Optional[Path]]] = None

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)

        self.selection_label = QLabel(_("Nothing selected"))
        self.selection_label.setWordWrap(True)
        layout.addWidget(self.selection_label)

        self.cluster_warning_label = QLabel("")
        self.cluster_warning_label.setWordWrap(True)
        self.cluster_warning_label.setStyleSheet(_WARN_STYLE)
        layout.addWidget(self.cluster_warning_label)

        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(_("cell name (key under cells:)"))
        form.addRow(_("Cell name:"), self.name_edit)
        layout.addLayout(form)

        self.target_label = QLabel(_("No target file picked (pick one in Files)"))
        self.target_label.setWordWrap(True)
        layout.addWidget(self.target_label)

        origin_form = QFormLayout()
        self.origin_mode_combo = QComboBox()
        self.origin_mode_combo.addItems(
            [_("Bounding box (default)"), _("Component role"), _("Via net")])
        self.origin_mode_combo.currentIndexChanged.connect(self._on_origin_mode_changed)
        origin_form.addRow(_("Origin:"), self.origin_mode_combo)
        layout.addLayout(origin_form)

        self._origin_role_row = QWidget()
        role_row = QHBoxLayout(self._origin_role_row)
        role_row.setContentsMargins(0, 0, 0, 0)
        self.origin_role_combo = QComboBox()
        self.origin_role_combo.setEditable(True)
        role_row.addWidget(QLabel(_("Role:")))
        role_row.addWidget(self.origin_role_combo, 1)
        self.origin_pad_edit = QLineEdit()
        self.origin_pad_edit.setPlaceholderText(_("pad (optional)"))
        role_row.addWidget(QLabel(_("Pad:")))
        role_row.addWidget(self.origin_pad_edit)
        layout.addWidget(self._origin_role_row)
        self._origin_role_row.setVisible(False)

        self._origin_via_row = QWidget()
        via_row = QHBoxLayout(self._origin_via_row)
        via_row.setContentsMargins(0, 0, 0, 0)
        self.origin_via_net_combo = QComboBox()
        self.origin_via_net_combo.setEditable(True)
        via_row.addWidget(QLabel(_("Net:")))
        via_row.addWidget(self.origin_via_net_combo, 1)
        layout.addWidget(self._origin_via_row)
        self._origin_via_row.setVisible(False)

        layout.addWidget(QLabel(_("Net aliases (blank = keep literal):")))
        self._nets_container = QWidget()
        self._nets_layout = QGridLayout(self._nets_container)
        self._nets_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._nets_container)
        layout.addWidget(scroll, 1)

        self._role_net_section = QWidget()
        role_net_section_layout = QVBoxLayout(self._role_net_section)
        role_net_section_layout.setContentsMargins(0, 0, 0, 0)
        role_net_section_layout.addWidget(
            QLabel(_("Net template role (bridging component — pick which aliased net is the template):")))
        self._role_net_layout = QGridLayout()
        self._role_net_layout.setContentsMargins(0, 0, 0, 0)
        role_net_section_layout.addLayout(self._role_net_layout)
        layout.addWidget(self._role_net_section)
        self._role_net_section.setVisible(False)

        layout.addWidget(QLabel(_("Existing (click to reuse a name):")))
        existing_row = QHBoxLayout()
        cells_col = QVBoxLayout()
        cells_col.addWidget(QLabel(_("Cells:")))
        self.cells_list = QListWidget()
        self.cells_list.setMaximumHeight(80)
        self.cells_list.itemClicked.connect(self._on_cell_item_clicked)
        cells_col.addWidget(self.cells_list)
        existing_row.addLayout(cells_col)

        profiles_col = QVBoxLayout()
        profiles_col.addWidget(QLabel(_("Profiles:")))
        self.profiles_list = QListWidget()
        self.profiles_list.setMaximumHeight(80)
        self.profiles_list.itemClicked.connect(self._on_profile_item_clicked)
        profiles_col.addWidget(self.profiles_list)
        existing_row.addLayout(profiles_col)
        layout.addLayout(existing_row)

        self.save_profile_checkbox = QCheckBox(_("Also save as extract_profile"))
        layout.addWidget(self.save_profile_checkbox)

        profile_form = QFormLayout()
        self.profile_key_edit = QLineEdit()
        self.profile_key_edit.setPlaceholderText(_("profile key (defaults to cell name)"))
        profile_form.addRow(_("Profile key:"), self.profile_key_edit)
        layout.addLayout(profile_form)

        self.profile_target_label = QLabel(_("No profile file picked (pick one in Files)"))
        self.profile_target_label.setWordWrap(True)
        layout.addWidget(self.profile_target_label)

        self.placer_target_label = QLabel(_("No placer file picked (pick one in Files, optional)"))
        self.placer_target_label.setWordWrap(True)
        layout.addWidget(self.placer_target_label)

        self.extract_button = QPushButton(_("Extract to file"))
        self.extract_button.setEnabled(False)
        self.extract_button.clicked.connect(self._on_extract)
        layout.addWidget(self.extract_button)

        self.message_label = QLabel("")
        self.message_label.setWordWrap(True)
        layout.addWidget(self.message_label)

        self.setWidget(container)

    def set_board_selection(self, raw_items: List[Any], selected_footprints: List[Selected]) -> None:
        """Called every selection-watch tick — see module docstring for why
        this needs the raw mixed list, not just the Selected-footprint one
        the tree/bulk-edit docks use."""
        self._raw_items = raw_items
        self._selected_footprints = selected_footprints
        self._update_selection_label()
        self._update_cluster_warning()
        self._rebuild_net_aliases()
        self._update_origin_choices()
        self._autofill_from_cluster()
        self._update_button_state()

    def _on_origin_mode_changed(self) -> None:
        mode = self.origin_mode_combo.currentIndex()
        self._origin_role_row.setVisible(mode == 1)
        self._origin_via_row.setVisible(mode == 2)

    def _update_origin_choices(self) -> None:
        """Populates the Role/Via-net combos from what's actually in the
        current selection — picking an origin from outside the selection
        makes no sense (extract_template_from_selection fatals on it
        anyway: 'role not found in selection' / 'no such via in selection'),
        so there's no point offering it."""
        roles = sorted({s.role for s in self._selected_footprints if s.role})
        set_combo_items(self.origin_role_combo, roles)

        via_nets = sorted({item.net.name for item in self._raw_items
                            if isinstance(item, Via) and item.net and item.net.name})
        set_combo_items(self.origin_via_net_combo, via_nets)

    def set_target_file(self, path: Optional[Path]) -> None:
        """Called by MainWindow whenever the Files dock's Cells-role file
        changes (wired via FilePickerDock's cells_file_changed signal)."""
        self._target_path = path
        self.target_label.setText(
            _("Target: {path}").format(path=path) if path is not None
            else _("No target file picked (pick one in Files)"))
        self._refresh_existing_lists()
        self._update_button_state()

    def set_profile_file(self, path: Optional[Path]) -> None:
        """Called by MainWindow whenever the Files dock's Extractor-role
        file changes (wired via FilePickerDock's extractor_file_changed
        signal) —
        replaces this dock's former standalone file-dialog button, so all
        three file roles (Cells/Extractor/Placer) are picked from one
        place (see file_picker.py)."""
        self._profile_path = path
        self.profile_target_label.setText(
            _("Profile file: {path}").format(path=path) if path is not None
            else _("No profile file picked (pick one in Files)"))
        self._refresh_existing_lists()

    def set_placer_file(self, path: Optional[Path]) -> None:
        """Called by MainWindow whenever the Files dock's Placer-role file
        changes (wired via FilePickerDock's placer_file_changed signal).
        Optional — extraction works the same without one, it just skips
        the cell_files:/include: wiring described in the module
        docstring."""
        self._placer_path = path
        self.placer_target_label.setText(
            _("Placer file: {path}").format(path=path) if path is not None
            else _("No placer file picked (pick one in Files, optional)"))

    @staticmethod
    def _slugify(text: str) -> str:
        return re.sub(r"[^0-9a-zA-Z]+", "_", text.strip().lower()).strip("_")

    _load_data = staticmethod(yaml_io.load_data)
    _existing_keys = staticmethod(yaml_io.existing_keys)

    def _refresh_existing_lists(self) -> None:
        self.cells_list.clear()
        self.cells_list.addItems(sorted(self._existing_keys(self._target_path)))
        self.profiles_list.clear()
        self.profiles_list.addItems(sorted(self._existing_keys(self._profile_path, section="extract_profiles")))
        self._last_autofill_key = None  # force _autofill_from_cluster to re-check against the new content

    @staticmethod
    def _select_list_item(list_widget: QListWidget, text: Optional[str]) -> None:
        list_widget.clearSelection()
        if text is None:
            return
        items = list_widget.findItems(text, Qt.MatchFlag.MatchExactly)
        if items:
            list_widget.setCurrentItem(items[0])

    def _autofill_from_cluster(self) -> None:
        """If the selection is a single Cluster and a slugified form of it
        (or its last '/'-segment) matches an existing Cells/Extractor key,
        fill that key into Cell name / Profile key — but only into a field
        that's still empty, so this never overwrites something already
        typed. Also highlights the match in its list either way, so a hit
        is visible even when the field was left untouched. No match ->
        silently does nothing (see module docstring).

        A matched profile's own params: (alias -> net literal, the same
        shape _on_extract() writes) are pulled into the net-alias fields
        too — reported live 2026-08-01 as missing ("алиасы сетей не
        подтянул"): reusing a profile's name without its aliases just
        means retyping them by hand every time. Tries an exact net-literal
        match first (the profile is being re-run on the SAME nets); a
        param whose literal isn't present in the current selection falls
        back to filling the next still-empty row, in declared order — the
        common case this covers is reusing a profile for an analogous
        Cluster on a different rail (found live 2026-08-01: a '-2V5'/
        '-2V5_DIRTY' selection matching a profile whose params were
        recorded against '+2V5'/'+2V5_DIRTY' — no literal in common, but
        the two aliases still belong in the same two rows). It's a
        heuristic, not a guarantee — same empty-field-only rule as the
        name fields, so a bad guess is just as easy to overtype as a
        blank field would have been."""
        clusters = frozenset(s.cluster for s in self._selected_footprints if s.cluster)
        key = (clusters, self._target_path, self._profile_path)
        if key == self._last_autofill_key:
            return
        self._last_autofill_key = key

        matched_cell = matched_profile = None
        if len(clusters) == 1:
            cluster = next(iter(clusters))
            candidates = [self._slugify(cluster)]
            if "/" in cluster:
                candidates.append(self._slugify(cluster.rsplit("/", 1)[-1]))

            cell_keys = self._existing_keys(self._target_path)
            matched_cell = next((c for c in candidates if c in cell_keys), None)
            if not self.name_edit.text().strip():
                # An existing key wins if there is one (it reflects
                # whatever naming was actually chosen last time); otherwise
                # the Cluster's own slug is still a perfectly good default
                # — no reason to leave the field blank just because nothing
                # was extracted under that name yet (2026-08-01: "если есть
                # имя кластера, зачем придумывать что-то").
                self.name_edit.setText(matched_cell or candidates[0])

            profile_keys = self._existing_keys(self._profile_path, section="extract_profiles")
            matched_profile = next((c for c in candidates if c in profile_keys), None)
            if matched_profile:
                if not self.profile_key_edit.text().strip():
                    self.profile_key_edit.setText(matched_profile)
                self._apply_profile_entry(matched_profile)

        self._select_list_item(self.cells_list, matched_cell)
        self._select_list_item(self.profiles_list, matched_profile)

    def _apply_profile_entry(self, profile_key: str) -> None:
        """Pulls one extract_profiles entry's params/net_template_role/
        origin_by_* into the alias/role/Origin fields — shared by the
        cluster auto-match above and by explicitly clicking a profile in
        the "Existing" list (see __init__): a manual pick deserves exactly
        the same pull an automatic match gets, not just the name (reported
        live 2026-08-01: names were "picked up" — via clicking, since this
        board's real Cluster names don't slugify to match its cell/profile
        keys at all, so the auto-match path above never actually fires here
        — but the alias fields, and then the Origin combo too, stayed
        untouched, because back then this pull only lived inside the
        auto-match branch and didn't cover Origin at all)."""
        profile_entry = self._load_data(self._profile_path).get("extract_profiles", {}).get(profile_key, {})
        if not profile_entry:
            return

        unmatched_aliases = []
        for alias, net_literal in (profile_entry.get("params") or {}).items():
            edit = self._net_alias_edits.get(net_literal)
            if edit is None:
                unmatched_aliases.append(alias)
            elif not edit.text().strip():
                edit.setText(alias)
        if unmatched_aliases:
            empty_edits = [e for e in self._net_alias_edits.values() if not e.text().strip()]
            for alias, edit in zip(unmatched_aliases, empty_edits):
                edit.setText(alias)

        # net_template_role is role -> literal, and role IS stable across a
        # rail swap (unlike the literal itself) — so rather than reusing
        # the old literal directly, look up which ALIAS it had back then
        # and find today's candidate net carrying that same alias (just
        # filled in above).
        old_params = profile_entry.get("params") or {}
        alias_for_old_literal = {v: k for k, v in old_params.items()}
        for role, old_literal in (profile_entry.get("net_template_role") or {}).items():
            combo = self._net_template_role_edits.get(role)
            wanted_alias = alias_for_old_literal.get(old_literal)
            if combo is None or combo.currentText().strip() or not wanted_alias:
                continue
            for i in range(combo.count()):
                candidate_net = combo.itemText(i)
                edit = self._net_alias_edits.get(candidate_net)
                if edit is not None and edit.text().strip() == wanted_alias:
                    combo.setCurrentText(candidate_net)
                    break

        # Origin — only applied while still at the untouched default (index
        # 0, bbox): same empty-field-only spirit as the alias/role pulls
        # above, so this never yanks the Origin selection out from under
        # something the user (or an earlier pull) already set.
        if self.origin_mode_combo.currentIndex() == 0:
            origin_role = profile_entry.get("origin_by_component_role")
            origin_via = profile_entry.get("origin_by_via_net")
            if origin_role:
                self.origin_mode_combo.setCurrentIndex(1)
                self.origin_role_combo.setCurrentText(origin_role)
                origin_pad = profile_entry.get("origin_by_component_pad")
                if origin_pad:
                    self.origin_pad_edit.setText(str(origin_pad))
            elif origin_via:
                self.origin_mode_combo.setCurrentIndex(2)
                self.origin_via_net_combo.setCurrentText(origin_via)

    def _find_profile_key_for_cell(self, cell_name: str) -> Optional[str]:
        """A profile entry's own key and the cell name it writes can differ
        (profile_key defaults to the cell name but can be overridden — see
        _on_extract()'s entry['name'] — this project's own real data has
        exactly this: profile key 'n2v5_adj_pi_filter', cell name
        '2v5_adj_pi_filter'). Used when the Cells list is clicked, so that
        click can find and pull the matching profile's aliases too, not
        just the ones the Profiles list itself was clicked for."""
        profiles = self._load_data(self._profile_path).get("extract_profiles", {}) or {}
        return next((key for key, entry in profiles.items()
                     if (entry.get("name") or key) == cell_name), None)

    def _on_cell_item_clicked(self, item) -> None:
        cell_name = item.text()
        self.name_edit.setText(cell_name)
        profile_key = self._find_profile_key_for_cell(cell_name)
        if profile_key is not None:
            if not self.profile_key_edit.text().strip():
                self.profile_key_edit.setText(profile_key)
            self._apply_profile_entry(profile_key)

    def _on_profile_item_clicked(self, item) -> None:
        profile_key = item.text()
        self.profile_key_edit.setText(profile_key)
        self._apply_profile_entry(profile_key)

    def _update_net_template_role_rows(self) -> None:
        """A role needs an explicit net_template_role pick exactly when 2+
        of ITS pads' nets currently have a non-empty alias next to them
        (see module docstring) — recomputed live as alias text changes
        (connected to each net-alias edit's textChanged), not just on
        selection changes, since typing an alias is exactly what turns a
        previously-unambiguous role into an ambiguous one."""
        ambiguous: Dict[str, List[str]] = {}
        for s in self._selected_footprints:
            if not s.role:
                continue
            distinct_nets = set(s.nets.values())
            aliased = sorted(n for n in distinct_nets
                              if self._net_alias_edits.get(n) and self._net_alias_edits[n].text().strip())
            if len(aliased) >= 2:
                ambiguous[s.role] = aliased

        if set(ambiguous) == set(self._net_template_role_edits):
            return

        previous = {role: combo.currentText() for role, combo in self._net_template_role_edits.items()}
        while self._role_net_layout.count():
            item = self._role_net_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        self._net_template_role_edits = {}
        for row, (role, nets) in enumerate(sorted(ambiguous.items())):
            self._role_net_layout.addWidget(QLabel(role), row, 0)
            combo = QComboBox()
            combo.addItem("")
            combo.addItems(nets)
            combo.setCurrentText(previous.get(role, ""))
            self._role_net_layout.addWidget(combo, row, 1)
            self._net_template_role_edits[role] = combo

        self._role_net_section.setVisible(bool(ambiguous))

    def _update_selection_label(self) -> None:
        if not self._raw_items:
            self.selection_label.setText(_("Nothing selected"))
            return
        fp_count = len(self._selected_footprints)
        other_count = len(self._raw_items) - fp_count
        if other_count:
            self.selection_label.setText(
                _("{fp} component(s), {other} via/track(s) selected")
                .format(fp=fp_count, other=other_count))
        else:
            self.selection_label.setText(_("{fp} component(s) selected").format(fp=fp_count))

    def _update_cluster_warning(self) -> None:
        clusters = {s.cluster for s in self._selected_footprints}
        if len(clusters) > 1:
            shown = ", ".join(repr(c) for c in sorted(clusters, key=lambda c: c or ""))
            self.cluster_warning_label.setText(
                _("Selection spans multiple Clusters: {clusters}").format(clusters=shown))
        else:
            self.cluster_warning_label.setText("")

    def _rebuild_net_aliases(self) -> None:
        """One row per distinct net found on the selected components' pads.
        Preserves whatever the user already typed for a net that's still
        present — the selection-watch tick fires every ~400ms, so without
        this, in-progress typing would be wiped just like the tree/bulk-edit
        docks had to guard against."""
        nets = sorted({net for s in self._selected_footprints for net in s.nets.values()})
        previous = {net: edit.text() for net, edit in self._net_alias_edits.items()}
        if set(nets) == set(previous):
            return

        while self._nets_layout.count():
            item = self._nets_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        self._net_alias_edits = {}
        for row, net in enumerate(nets):
            self._nets_layout.addWidget(QLabel(net), row, 0)
            edit = QLineEdit()
            edit.setPlaceholderText(_("alias, e.g. PWR_IN"))
            edit.setText(previous.get(net, ""))
            edit.textChanged.connect(self._update_net_template_role_rows)
            self._nets_layout.addWidget(edit, row, 1)
            self._net_alias_edits[net] = edit
        self._update_net_template_role_rows()

    def _update_button_state(self) -> None:
        self.extract_button.setEnabled(bool(self._raw_items) and self._target_path is not None)

    def _show_message(self, text: str, style: str = "") -> None:
        """Sets the inline status label AND mirrors it into the Log dock
        (see gui/docks/log_panel.py) at the matching level, so error/
        warning messages survive after the label itself gets overwritten
        by the next action — requested live 2026-08-01 ("для списка
        ошибок сделать внизу отдельное окошко")."""
        show_message(self.message_label, text, style, logger)

    def _on_extract(self) -> None:
        """Extract button handler — form collection (validation + widget
        reads) runs on the UI thread; the board IPC + file writes run on a
        worker thread (see gui/worker.py), so the GUI stays responsive while
        the shared socket stays exclusively ours (connection.long_op_active
        pauses the polling timers for the duration)."""
        self._show_message("")
        payload = self._collect_extract_inputs()
        if payload is None:
            return
        self._start_extract_op(payload)

    def _collect_extract_inputs(self) -> Optional[Dict[str, Any]]:
        """UI thread: read every widget + run every validation that can
        reject the request up front. Returns a plain-data payload for the
        worker (no widget references), or None after showing the error."""
        name = self.name_edit.text().strip()
        if not name:
            self._show_message(_("Cell name is required."), _ERROR_STYLE)
            return None
        if not self._raw_items or self._target_path is None:
            return None
        save_profile = self.save_profile_checkbox.isChecked()
        if save_profile and self._profile_path is None:
            self._show_message(
                _("'Also save as extract_profile' is checked, but no profile file is picked."),
                _ERROR_STYLE)
            return None

        board = self._connection.board
        if board is None:
            self._show_message(_("Not connected."), _ERROR_STYLE)
            return None

        params: Dict[str, str] = {}
        for net_literal, edit in self._net_alias_edits.items():
            alias = edit.text().strip()
            if not alias:
                continue
            if alias in params:
                self._show_message(
                    _("Alias {alias!r} used for both {a!r} and {b!r} — each alias needs a "
                      "distinct net.").format(alias=alias, a=params[alias], b=net_literal),
                    _ERROR_STYLE)
                return None
            params[alias] = net_literal

        origin_kwargs: Dict[str, str] = {}
        origin_mode = self.origin_mode_combo.currentIndex()
        if origin_mode == 1:  # component role (+ optional pad)
            role = self.origin_role_combo.currentText().strip()
            if not role:
                self._show_message(_("Origin: pick a component role."), _ERROR_STYLE)
                return None
            origin_kwargs["origin_component_role"] = role
            pad = self.origin_pad_edit.text().strip()
            if pad:
                origin_kwargs["origin_component_pad"] = pad
        elif origin_mode == 2:  # via net
            net = self.origin_via_net_combo.currentText().strip()
            if not net:
                self._show_message(_("Origin: pick a via net."), _ERROR_STYLE)
                return None
            origin_kwargs["origin_via_net"] = net

        net_template_role: Dict[str, str] = {}
        for role, combo in self._net_template_role_edits.items():
            literal = combo.currentText().strip()
            if not literal:
                self._show_message(
                    _("Net template role: role {role!r} bridges 2+ aliased nets — pick which one "
                      "is the template.").format(role=role),
                    _ERROR_STYLE)
                return None
            net_template_role[role] = literal

        return {
            "name": name,
            "raw_items": self._raw_items,
            "target_path": self._target_path,
            "save_profile": save_profile,
            "profile_key": self.profile_key_edit.text().strip() or name,
            "profile_path": self._profile_path,
            "placer_path": self._placer_path,
            "params": params,
            "origin_kwargs": origin_kwargs,
            "net_template_role": net_template_role,
            "board": board,
        }

    def _run_extract(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Worker thread: board IPC + file writes only — never touches a
        widget. Returns {"messages": [...], "annotations": [...]} on
        success, or {"error": str} for the expected failure modes (an
        unexpected exception is caught by _LongOpWorker and reported through
        the failed signal instead)."""
        name = payload["name"]
        annotations: List[Tuple[str, str, str]] = []
        try:
            template_dict = extract_template_from_selection(
                payload["board"].adapter, name, params=payload["params"],
                items=payload["raw_items"], net_template_role=payload["net_template_role"],
                annotations=annotations, **payload["origin_kwargs"])
        except PlacerError as e:
            return {"error": str(e)}

        try:
            cell_overwritten = merge_write(payload["target_path"], template_dict)
        except OSError as e:
            return {"error": _("Write failed: {error}").format(error=e)}

        messages = [_("{action} {name!r} in {path}").format(
            action=_("Overwrote") if cell_overwritten else _("Wrote"),
            name=name, path=payload["target_path"])]

        if payload["save_profile"]:
            profile_key = payload["profile_key"]
            entry: Dict[str, Any] = {"output": display_path(payload["target_path"])}
            if profile_key != name:
                entry["name"] = name
            if payload["params"]:
                entry["params"] = payload["params"]
            if payload["net_template_role"]:
                entry["net_template_role"] = payload["net_template_role"]
            for key, value in payload["origin_kwargs"].items():
                # Function kwargs (origin_component_role) vs. profile YAML
                # keys (origin_by_component_role) differ by "by_" — see
                # kicadstamp_cli.py's cmd_extract profile branch.
                entry[f"origin_by_{key[len('origin_'):]}"] = value
            try:
                profile_overwritten = merge_write(
                    payload["profile_path"],
                    {"extract_profiles": {profile_key: entry}}, section="extract_profiles")
            except OSError as e:
                return {"error": _("Cell written, but profile write failed: {error}").format(error=e)}
            messages.append(_("{action} profile {key!r} in {path}").format(
                action=_("overwrote") if profile_overwritten else _("wrote"),
                key=profile_key, path=payload["profile_path"]))

        placer_path = payload["placer_path"]
        if placer_path is not None:
            try:
                if payload["target_path"] != placer_path:
                    rel = Path(os.path.relpath(payload["target_path"], placer_path.parent)).as_posix()
                    if add_list_entry(placer_path, "cell_files", rel):
                        messages.append(_("added {rel!r} to cell_files: in {path}").format(
                            rel=rel, path=placer_path))
                if payload["save_profile"] and payload["profile_path"] != placer_path:
                    bad_keys = self._non_includable_keys(payload["profile_path"])
                    if bad_keys:
                        messages.append(
                            _("skipped adding to include: — {path} has root-config-only key(s) "
                              "{keys} that include: can't merge (move them to the Placer file "
                              "itself, or point Placer at this same file)")
                            .format(path=display_path(payload["profile_path"]), keys=sorted(bad_keys)))
                    else:
                        rel = Path(os.path.relpath(payload["profile_path"], placer_path.parent)).as_posix()
                        if add_list_entry(placer_path, "include", rel):
                            messages.append(_("added {rel!r} to include: in {path}").format(
                                rel=rel, path=placer_path))
            except OSError as e:
                messages.append(_("placer file wiring failed: {error}").format(error=e))

        return {"messages": messages, "annotations": annotations}

    def _finish_extract(self, result: Dict[str, Any]) -> None:
        """UI thread: reflect the worker's result into the message label and
        refresh the existing-lists widgets."""
        if result.get("error"):
            self._show_message(result["error"], _ERROR_STYLE)
            return
        messages = result["messages"]
        annotations = result["annotations"]
        if annotations:
            messages.append(_("{count} field(s) could not be determined automatically: {details}")
                             .format(count=len(annotations),
                                     details="; ".join(f"{role}/{field}: {hint}"
                                                        for role, field, hint in annotations)))
            self._show_message("; ".join(messages), _WARN_STYLE)
        else:
            self._show_message("; ".join(messages), _SUCCESS_STYLE)
        self._refresh_existing_lists()

    def _start_extract_op(self, payload: Dict[str, Any]) -> None:
        self._active_op = start_long_op(
            self._connection, (self.extract_button,),
            self._run_extract, self._finish_extract, self._on_extract_failed, payload)

    def _on_extract_failed(self, message: str) -> None:
        self._show_message(_("Extract failed: {error}").format(error=message), _ERROR_STYLE)

    def _do_extract(self) -> None:
        """Synchronous composition of collect + run + finish — the same
        behaviour the async button path would produce, kept for tests and
        any caller that must not return until the extract is complete."""
        payload = self._collect_extract_inputs()
        if payload is None:
            return
        result = self._run_extract(payload)
        self._finish_extract(result)

    # include: only ever merges these top-level keys from an included file
    # (config/includes.py's _LIST_SECTIONS/_DICT_SECTIONS) — everything
    # else is fatal there (no defined multi-file merge behaviour). A file
    # assigned the Extractor role can perfectly well ALSO be a full root
    # config in its own right (registry_path/cell_files/schematic_dir/...)
    # if it was set up that way before being pointed at by this role —
    # found live 2026-08-01: writing include: blindly in that case leaves
    # the Placer file unloadable the next time anything reads it.
    _INCLUDABLE_KEYS = frozenset(
        {"rules", "clone_placements", "cells", "points", "extract_profiles", "clone_profiles", "include"})

    @classmethod
    def _non_includable_keys(cls, path: Path) -> set:
        return set(cls._load_data(path).keys()) - cls._INCLUDABLE_KEYS
