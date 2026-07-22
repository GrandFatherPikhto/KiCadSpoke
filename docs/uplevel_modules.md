# Top-level Modules of KiCadSpoke (Current Version)

The `kicadspoke/` folder contains the core modules responsible for configuration loading, exception handling, logging, undo, validation, placement registries for vias and tracks, template extraction, and the CLI entry point. Each module addresses a specific task and interacts with others through well-defined interfaces.

---

## 1. `kicadspoke_cli.py` – CLI Entry Point

**Purpose:**  
Main executable script that processes command-line arguments, initialises logging, loads the configuration (with support for `templates_file`), connects to KiCad, runs pre-validation, invokes the planner and executor, and supports the `undo`, `extract`, `clone-extract` commands and the optional `--clone-placement` flag.

**Main functions:**

| Function | Description |
|----------|-------------|
| `setup_logging(verbose, log_file)` | Configures logging: level (INFO/DEBUG), output to console and/or file. |
| `cmd_apply(args)` | `apply` command: loads config, connects to KiCad, runs validation, planning, and **three-phase** execution (moves → refresh → vias → tracks). Supports `--dry-run` and `--clone-placement` for processing a single clone. |
| `cmd_extract(args)` | `extract` command: extracts a template from the current selection on the board (including **tracks**) and writes it as JSON or YAML. Supports `--param`, `--net-template`, `--origin-by-via-net`, `--origin-by-component-role`. The format is determined by the file extension; for `.json` the file is written as a flat dictionary without a `templates:` wrapper. |
| `cmd_undo(args)` | `undo` command: finds the latest JSON log in `logs/`, calls `undo_last_operation()` (removes created vias **and tracks**, restores components). |
| `main()` | Parses arguments (supports implicit `apply`), configures logging, invokes the appropriate command, catches exceptions. |

**Key dependencies:**  
`config.load_config`, `kicad.adapter.KiCadBoardAdapter`, `validation.run_all_checks`, `placement.planner.PlacementPlanner`, `placement.executor.BatchExecutor`, `registry.PlacementRegistry`, `registry.TrackRegistry`, `template_extraction.extract_template_from_selection`, `undo.undo_last_operation`, `cloner.extract.extract_channel`.

**Features:**  
- Supports four commands: `apply`, `undo`, `extract`, `clone-extract`.
- In `apply` mode, performs a three-phase process: moves → vias → tracks (with intermediate board reload).
- The `--clone-placement NAME` flag allows processing only one clone in selection mode (useful for debugging).
- In `extract` mode, uses the current selection in the PCB editor to create a template; extracts components, vias, and tracks.
- All exceptions are caught and logged; user‑defined (`PlacerError`) are printed without a stack trace.

---

## 2. `config.py` – Configuration Loading and Storage

**Purpose:**  
Defines all data structures (dataclasses) describing the placement configuration, and provides the `load_config()` function to load a YAML file into typed Python objects. Includes checking for unique roles inside a template (fatal error on duplicates), support for **external template files** (`templates_file`), **tracks** (`TemplateTrack`), and cross-validation of `layer`/`mirror` for `ClonePlacement`.

**Main dataclasses:**

| Class | Description |
|-------|-------------|
| `ThermalViaArrayConfig` | Thermal via array settings (now uses `anchor_ref` instead of `target_ref`). |
| `TemplateVia` | Via slot in a template (local coordinates, net, dimensions). |
| `TemplateTrack` | Straight track segment in a template: start/end points (local), width, net, optional layer. |
| `TemplateComponentSlot` | Component slot in a template: role, local coordinates, angle, list of vias, optional `net_template` and `layer`. |
| `SpokeTemplate` | Complete spoke template: name, list of vias, list of tracks, list of component slots, absolute `layer`. |
| `ManualSpoke` | Specific spoke: pad, template, shift, rotation, `enabled` flag. |
| `Rule` | Rule for one net: net name, list of spokes, `anchor_ref` (mandatory). |
| `ClonePlacement` | Cloned placement: name, template, absolute point or shift from anchor, angle, dicts `nets`, `params`, `net_overrides`, `layer`, `mirror`, `refs`. |
| `Config` | Main object: global `layer`, templates, thermal vias, rules, clones, flags. |

**Main functions:**

| Function | Description |
|----------|-------------|
| `load_config(path)` | Reads YAML, loads external template file (`templates_file`) if specified, merges with inline `templates` (inline take precedence). Parses all sections, returns a `Config` object. Checks for unique roles in templates and validity of `layer`/`mirror` in `ClonePlacement`. |
| `_load_template_via(data)` | Loads `TemplateVia`. Checks that `net` is a string (protection against accidental `net_overrides` nesting). |
| `_load_template_track(data)` | Loads `TemplateTrack`. Checks that `net` is a string. |
| `_load_template_component_slot(data)` | Loads `TemplateComponentSlot`. |
| `_load_spoke_template(name, data)` | Loads `SpokeTemplate` with role uniqueness check. |
| `_load_manual_spoke(data)` | Loads `ManualSpoke`. |
| `_load_clone_placement(data)` | Loads `ClonePlacement`. Checks that `anchor_ref` is present if `anchor_pad` is given, and that coordinates are mandatory if no anchor. |

**Features:**  
- **`templates_file`** – path to an external template file (JSON or YAML). Inline `templates` complement/override external ones.
- Role uniqueness check inside a template (duplicates are not allowed).
- Support for `net_template` for cloning (placeholders for nets).
- For `ClonePlacement`, two role resolution modes: "by selection" (no `nets`/`params`) and "by nets" (with `nets` or `params`).
- Net inheritance for vias and tracks: if `net` is omitted, it is taken from `rule.net` (for ManualSpoke) or is mandatory for ClonePlacement.
- Cross-validation of `layer`/`mirror`: `mirror` without a layer change, or a layer change without `mirror`, is a fatal error.
- Deprecated fields `target_ref` and `side` at the root of the config cause a fatal error.

---

## 3. `exceptions.py` – Exception Hierarchy

**Purpose:**  
Defines custom exceptions for the project and a common fatal error formatting function. All exceptions inherit from the base `PlacerError`.

**Exception classes:**

| Class | Purpose |
|-------|---------|
| `PlacerError` | Base exception for all placer errors. |
| `BoardNotFoundError` | Failed to obtain the board from KiCad. |
| `ComponentNotFoundError` | Component not found on the board. |
| `GeometryError` | Geometry calculation error. |
| `ValidationError` | Fatal pre‑validation error — the program stops before modifying the board. |

**Helper function:**

| Function | Description |
|----------|-------------|
| `format_fatal_error(title, problems)` | Formats a list of problems into a single multi‑line message with a border of `=`. Used both in `config.py` (checks at YAML load time) and `validation.py` (checks after connecting to KiCad). Lives here to avoid circular imports. |

---

## 4. `net_resolution.py` – Net Resolution for Cloned Templates

**Purpose:**  
Provides three‑layer net name resolution for `ClonePlacement` (TemplatePlacer). Allows substitution of placeholders from `params` and application of `net_overrides`. Also provides **reverse parametrisation** (`parametrize_net`) for `extract`.

**Main functions:**

| Function | Description |
|----------|-------------|
| `resolve_net(net_template, params, net_overrides)` | Takes a net name template (possibly with `{placeholder}`), a params dict for substitution, and a net_overrides dict. Returns the final net name. If a placeholder parameter is missing, raises `ValidationError`. |
| `parametrize_net(literal_net, net_template_map, params)` | Reverse operation for `extract`: given a real net name and a mapping from literal to pattern, reconstructs the pattern with placeholders. Performs a round‑trip check (resolving the pattern with `params` must yield the original literal). |

**How `resolve_net` works:**
1. If `net_template` has no placeholders, return as‑is.
2. Otherwise, do `str.format(**params)`.
3. Then apply `net_overrides.get(resolved, resolved)` for point overrides.

**Used in:** `placement/services/clone_role_resolver.py` (for role resolution in cloned placements) and `geometry/clone_geometry.py` (for via and track net resolution).

---

## 5. `registry.py` – Placement Registries for Vias and Tracks

**Purpose:**  
Ensures idempotency of via and track placement across runs. Stores information about created objects (UUID, position, parameters, net) in JSON files next to the config. On subsequent runs, reconciles planned objects against **real objects on the board** (`adapter.get_vias()`, `adapter.get_tracks()`), removes obsolete ones (prune), and creates only new or changed objects.

**Main classes and functions:**

| Class/Function | Description |
|----------------|-------------|
| `make_registry_key(anchor_id, template_name, role, via_index)` | Generates a composite key for the via registry. |
| `registry_path_for_config(config_path)` | Returns the path to the via registry file. |
| `track_registry_path_for_config(config_path)` | Returns the path to the track registry file (separate file). |
| `RegistryEntry` | Dataclass for vias: UUID, position, net, drill/diameter parameters. |
| `TrackRegistryEntry` | Dataclass for tracks: UUID, start/end coordinates, width, net, layer. |
| `PlacementRegistry` | Class managing the via registry. |
| `TrackRegistry` | Class managing the track registry. |
| `reconcile(planned_objects, known_clone_names)` | Compares planned objects against the registry and real objects on the board, removes obsolete entries, returns the list of objects to actually create. |
| `record_created(cmd, created_uuid)` | Records a created object in the registry. |

**Features:**
- **Reconciliation against live board objects** – source of truth, not just JSON. This prevents desynchronisation due to manual deletions or crashes between registry write and board commit.
- Registry keys follow the pattern: `anchor_id|template_name|role|via_index` (similar for tracks).
- `anchor_id` for ManualSpoke is `f"pad:{pad}"`, for ClonePlacement `f"name:{name}"`.
- `role` for spoke‑level vias is `__spoke__`.
- Position tolerance: 0.01 mm.
- Support for `known_clone_names` – when using `--clone-placement`, vias/tracks of other clones are not pruned.
- Separate registries for vias and tracks (different files and record structures).

**Used in:** `kicadspoke_cli.py` (during `apply`), `executor/via_executor.py`, and `executor/track_executor.py`.

---

## 6. `template_extraction.py` – Template Extraction from Selection

**Purpose:**  
Implements the `extract` command: from the current selection in the KiCad PCB editor, extracts a spoke template (components, vias, **and tracks**) and builds a structure ready for file output. Supports net parametrisation via `--net-template` and origin selection via `--origin-by-via-net` or `--origin-by-component-role`.

**Main functions:**

| Function | Description |
|----------|-------------|
| `extract_template_from_selection(adapter, name, params, net_template_map, origin_via_net, origin_component_role)` | Main function. Reads the selection (expanding groups), filters tracks (only those whose both ends match pads, vias, or other tracks in the selection), checks for presence and uniqueness of roles, computes origin (bbox or specific element), builds lists of components, vias, and tracks, returns a dictionary for writing. |
| `_bbox_origin(footprints, vias)` | Computes the lower‑left corner of the selection bounding box (min_x, max_y). |
| `_find_origin(...)` | Determines origin based on given parameters (via_net, component_role, or bbox). |
| `_filter_tracks_within_selection(...)` | Filters out tracks where at least one end does not match anything else in the selection (protection against capturing long traces). |

**Algorithm:**
1. Retrieves selected objects via `adapter.get_selected_items()`.
2. Splits into `FootprintInstance`, `Via`, `Track`; ignores the rest.
3. Filters tracks (`_filter_tracks_within_selection`), keeping only those that are self‑contained within the selection.
4. Checks that every component has a `Role` field and roles are unique.
5. Determines origin (via `--origin-by-via-net`, `--origin-by-component-role`, or bbox).
6. For each component, computes `along/across`, stores angle, role, and optional `layer`.
7. For each via and track, computes local coordinates, stores `net` (with parametrisation via `net_template_map`), via/track parameters, and layer.
8. Returns a dictionary `{name: {"vias": [...], "components": [...], "tracks": [...], "layer": ...}}`, ready for JSON or YAML output.

**Used in:** `kicadspoke_cli.py` (`extract` command).

---

## 7. `undo.py` – Undo Last Operation

**Purpose:**  
Implements the `undo` command, which restores the board state before the last placement operation. Uses JSON logs created by `executor/operation_logger.py` on every successful application of changes.

**Main function:**

| Function | Description |
|----------|-------------|
| `undo_last_operation(json_path)` | Loads the JSON log; for each moved component, determines the original layer (from string), flips if necessary, then restores position and angle. For each created via and track, deletes it by UUID. After successful undo, deletes the JSON file. |

**Layer restoration algorithm:**
- `original_layer` is stored in the log as `"F.Cu"` or `"B.Cu"`.
- If the current footprint layer differs, `adapter.flip_selected([fp])` is called, then the footprint is re‑fetched via `adapter.get_footprint(ref)`.
- Then position and angle are restored.

**Used in:** `kicadspoke_cli.py` (`undo` command).

---

## 8. `validation.py` – Pre‑validation Checks

**Purpose:**  
Performs fatal checks on the configuration **before** any board modifications. If a problem is found, the program stops with a detailed list of errors, leaving the board untouched.

**Main functions:**

| Function | Description |
|----------|-------------|
| `check_templates_and_pads_exist(adapter, cfg)` | Ensures that every enabled spoke references an existing template and a valid pad of the target component (anchor). Skips disabled spokes (`enabled=False`). |
| `check_role_pool_sufficiency(adapter, cfg)` | For each rule, builds a `ComponentPool` and checks that the required number of components for each role is available. Reports all shortages at once. |
| `check_clone_templates_exist(cfg)` | Checks that every `ClonePlacement` references an existing template (config‑only check, no KiCad connection). |
| `check_clone_nets_exist_on_board(adapter, cfg)` | Resolves `via.net` and `track.net` for each `ClonePlacement` and checks the result against actual board nets (`adapter.get_all_nets()`). Catches typos in `params` and `net_overrides`. |
| `check_single_selection_based_clone(cfg)` | Ensures that no more than one `ClonePlacement` is in selection mode (without `nets`/`params`), because KiCad supports only one selection at a time. Suggests using `--clone-placement` for debugging. |
| `run_all_checks(adapter, cfg)` | Runs all checks in order: `check_clone_templates_exist`, `check_single_selection_based_clone`, `check_templates_and_pads_exist`, `check_role_pool_sufficiency`, `check_clone_nets_exist_on_board`. |

**Features:**
- Collects all problems rather than stopping at the first one.
- Uses `ComponentPool` in `check_role_pool_sufficiency` for each net.
- For `ClonePlacement`, checks that no more than one clone is in selection mode (otherwise fatal).
- `check_clone_nets_exist_on_board` is a new check that guarantees that resolved via and track nets actually exist on the board.
- Error formatting via `format_fatal_error()` from `exceptions.py`.

**Used in:** `kicadspoke_cli.py` (before planning).

---

## 9. `constants.py` – Global Constants

**Purpose:**  
Holds global constants used across various modules, making them easy to change and maintain.

| Constant | Value | Usage |
|----------|-------|-------|
| `ROLE_FIELD_NAME` | `"Role"` | Name of the custom field for roles in the schematic (used in `component_pool.py`, `template_extraction.py`, `clone_role_resolver.py`). |
| `POSITION_TOLERANCE_NM` | `10_000` (0.01 mm) | Position tolerance for "already in place" checks (used in `planner.py`). |
| `ANGLE_TOLERANCE_DEG` | `0.1` | Angle tolerance for "already in place" checks (used in `planner.py`). |
| `POSITION_TOLERANCE_MM` | `0.01` | Position tolerance in millimetres for the registry (used in `registry.py`). |
| `DEFAULT_BATCH_SIZE` | `10` | Default batch size for transactions (used in `executor/batch_executor.py` and `kicadspoke_cli.py`). |
| `DEFAULT_TIMEOUT_MS` | `20000` | Default IPC timeout (used in `kicad/adapter.py` and `kicadspoke_cli.py`). |
| `DEFAULT_LOG_DIR` | `"logs"` | Default log directory (used in `executor/operation_logger.py`). |
| `SPOKE_LEVEL_ROLE_PLACEHOLDER` | `"__spoke__"` | Placeholder for spoke‑level vias in the registry (used in `registry.py`). |

---

## Module Interconnections

```mermaid
graph TD
    CLI[kicadspoke_cli.py] --> Config[config.py]
    CLI --> Adapter[kicad/adapter.py]
    CLI --> Validation[validation.py]
    CLI --> Planner[placement/planner.py]
    CLI --> Executor[placement/executor/batch_executor.py]
    CLI --> ViaRegistry[registry.PlacementRegistry]
    CLI --> TrackRegistry[registry.TrackRegistry]
    CLI --> Extract[template_extraction.py]
    CLI --> Undo[undo.py]
    CLI --> Constants[constants.py]

    Config --> Exceptions[exceptions.py]
    Config --> TemplatesFile[templates_file (external JSON/YAML)]

    Validation --> Config
    Validation --> ComponentPool[placement/services/component_pool.py]
    Validation --> Exceptions
    Validation --> Adapter

    ViaRegistry --> Config
    ViaRegistry --> Adapter
    ViaRegistry --> Exceptions

    TrackRegistry --> Config
    TrackRegistry --> Adapter
    TrackRegistry --> Exceptions

    Extract --> Adapter
    Extract --> Config
    Extract --> Exceptions

    Undo --> Adapter
    Undo --> Exceptions

    NetResolution[net_resolution.py] --> Exceptions
    NetResolution --> Config (used by ClonePlacement)
    NetResolution --> Extract (parametrize_net)
```

Each module addresses a specific task and interacts with others through clearly defined interfaces, ensuring modularity and testability. Thanks to centralised constants, a unified error formatter, and support for external template files, the project is easy to maintain and extend.