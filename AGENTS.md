# Project Agent Guide

## Project summary
- Blender add-on that imports/exports NetImmerse/Gamebryo formats (`.nif`, `.kf`, `.egm`).
- Core add-on code lives in `io_scene_niftools/` and registers Blender operators, UI, and properties.
- Documentation is in `docs/` and built with Sphinx.
- Release history and user-facing changes are tracked in `CHANGELOG.rst`.
- Build, install, and distribution helpers live in `install/`, `bin/`, `dist_obfuscated/`, and `dependencies/`.

## How to answer questions
- Gather evidence from project files first; rely on blender 5.0's documentation or online niftools documentation only when necessary.
- Explain conclusions by citing the most relevant file paths.
- Provide the correct reference point for each claim (file path, and line/section when helpful).
- Prefer primary sources: `io_scene_niftools/` for behavior, `docs/` for docs, and `CHANGELOG.rst` for history.

## Quick pointers
- Add-on entry point and metadata: `io_scene_niftools/__init__.py`
- Main add-on packages: `io_scene_niftools/`
- Docs root: `docs/index.rst`
- Contribution rules: `CONTRIBUTING.rst`
- Release notes: `CHANGELOG.rst`

## Main functionality: import/export entry points
- NIF import main file: `io_scene_niftools/nif_import.py` (`NifImport.execute` loads NIFs via `NifFile.load_nif`, sets helpers, and walks root blocks through `import_root`/`import_branch` using `io_scene_niftools/modules/nif_import/` helpers).
- NIF export main file: `io_scene_niftools/nif_export.py` (`NifExport.execute` gathers exportable objects, builds the root block via `Object.export_root_node`, applies post-processing, then writes the NIF/EGM output).
- KF import main file: `io_scene_niftools/kf_import.py` (`KfImport.execute` loads .kf files via `NifFile.load_nif`, sets FPS, and imports keyframes via `TransformAnimation.import_kf_root`).
- KF export main file: `io_scene_niftools/kf_export.py` (`KfExport.execute` builds the keyframe tree with `TransformAnimation.export_kf_root` and writes the .kf output).
- Operators wiring: `io_scene_niftools/operators/nif_import_op.py`, `io_scene_niftools/operators/nif_export_op.py`, `io_scene_niftools/operators/kf_import_op.py`, and `io_scene_niftools/operators/kf_export_op.py` instantiate the corresponding classes and call `execute` from the Blender UI.

## Contribution and coding conventions
- Code changes should be clean, commented where complex, include required docstrings, keep tests green, and update documentation when behavior changes (see `CONTRIBUTING.rst`).
- Follow PEP 8 and use naming conventions: Blender instances prefixed `b_`, NIF instances prefixed `n_` (see `docs/development/design/conventions.rst`).
- Development methodology is feature-oriented TDD; write/run tests first, fix iteratively, and keep documentation current (see `docs/development/design/overview.rst` and `docs/development/design/workflow.rst`).
- Git workflow follows a gitflow-like model: feature branches off `develop`, rebase when `develop` moves, PRs target `develop` (see `docs/development/design/overview.rst`).
- Documentation uses Sphinx/reST heading conventions (#, *, =, -, ^) and is built with `make html` from `docs/` (see `docs/development/docs.rst` and `docs/development/design/workflow.rst`).
- Tests run against the installed addon; set `BLENDER_HOME` and `BLENDER_ADDONS_DIR`, then use `testframework/blender-nosetests.[bat|sh]` (see `docs/development/testframework/index.rst`).
- Integration tests should follow the `test.SingleNif` template with `n_create_data`, `n_check_data`, `b_create_data`, and `b_check_data`, and document features while writing Blender creation steps (see `docs/development/testframework/design/integration.rst`).
- Configure git `core.autocrlf` to avoid line-ending churn across platforms (see `docs/development/setup/sourcecode.rst`).
- Prefer `Operator.report` over raising exceptions in operators so tests can catch failures (see `docs/development/design/issues.rst`).

## Blender 5.0 compatibility

- **Minimum Blender version**: `io_scene_niftools/__init__.py:51` requires `(5, 0, 0)`.
- **Dict-style property access removed**: Properties defined by `bpy.props` no longer support `obj["key"]` or `obj.get("key")`. Use `setattr`/`getattr` instead. Fixed in:
  - `modules/nif_import/property/shader/__init__.py:143` — `b_mat.niftools_shader[name] = True` → `setattr`
  - `modules/nif_export/property/shader/__init__.py:168` — `b_mat.niftools_shader.get(sf_flag)` → `getattr`
- **`use_nodes` always True/deprecated**: Always check `mat.node_tree` directly instead of `mat.use_nodes and mat.node_tree`. Fixed in:
  - `nif_export/object/__init__.py:134` — `_is_obj_transparent`
  - `nif_export/property/texture/__init__.py:150` — `get_used_textslots`
  - `nif_export/property/object/__init__.py:164` — `export_alpha_property`
  - `ui/material.py:107` — panel poll
  - `modules/nif_import/property/nodes_wrapper/__init__.py:158` — removed `use_nodes = True` set (no-op)

## NiBillboardNode behavior

- **Export detection** (`io_scene_niftools/modules/nif_export/types.py:64-65`): Objects with a `TRACK_TO` constraint are exported as `NiBillboardNode`.
- **Export rotation fix** (`io_scene_niftools/modules/nif_export/object/__init__.py:221-228`): NiBillboardNode rotation is zeroed to identity during export. The Track To constraint in Blender bakes a rotation to face the camera, but the game engine's billboard effect handles this dynamically. Only translation and scale are preserved from the Blender object matrix. This prevents billboards from pointing at the scene root in-game.
- **Import**: `io_scene_niftools/modules/nif_import/object/types.py:76-92` creates a `TRACK_TO` constraint on the imported object targeting a Camera. `billboard_mode` field is not imported/exported — defaults to `ALWAYS_FACE_CAMERA`.
