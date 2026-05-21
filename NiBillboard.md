# NiBillboardNode

How the niftools addon imports/exports `NiBillboardNode`.

---

## Import (`.nif` → Blender)

In `io_scene_niftools/nif_import.py:248`, every imported node calls `import_billboard()`:

**`io_scene_niftools/modules/nif_import/object/types.py:76-92`**
- If the nif block is a `NiBillboardNode` and the Blender object is not a Bone:
  - Finds (or creates) a **Camera** object in the scene.
  - Adds a **`TRACK_TO`** constraint to the Blender object, targeting the camera.
  - Sets `track_axis = 'TRACK_Z'` and `up_axis = 'UP_Y'`.

> ⚠️ The `billboard_mode` enum value from the NIF is **ignored on import** — only the node type (`NiBillboardNode` vs `NiNode`) matters.

## Export (Blender → `.nif`)

**`io_scene_niftools/modules/nif_export/types.py:64-65`**
- `has_track(b_obj)` (line 77-83) checks if the object has a **`TRACK_TO`** constraint.
- If it does, the exported node type becomes `"NiBillboardNode"`.
- `billboard_mode` is **not configurable** — defaults to `ALWAYS_FACE_CAMERA` (0).

**`io_scene_niftools/modules/nif_export/object/__init__.py:218`**
- The object's **full transform** (translation + rotation + scale) is read from `b_obj.matrix_local` and written to the NIF node.
- **Problem**: The `Track To` constraint evaluates in Blender and **bakes a rotation** into `matrix_local`. This rotation (pointing at the camera at export time) gets written into the `NiBillboardNode`. In-game, the billboard effect applies on top of this baked rotation, causing it to face the wrong direction.
- **Fix (line 220-222)**: After setting the matrix, if the node is a `NiBillboardNode` (has `billboard_mode`), its rotation is reset to identity — only position and scale are kept.

---

## How to set up in Blender (for export)

1. Create an **Empty** object.
2. Add a **`Track To`** constraint in the **Object Constraints** panel.
3. **Target** it to a **Camera** object in the scene.
4. Leave `track_axis = Z` and `up_axis = Y` (the add-on hardcodes these on import, but export only checks for the **presence** of the constraint).

> The Empty's position determines the billboard's location in the NIF. The rotation from the `Track To` constraint is **stripped during export** so the game engine's billboard effect can freely face the camera.

---

## Limitations

- **`billboard_mode` not exposed**: The NIF supports modes like `ALWAYS_FACE_CAMERA` (0), `ROTATE_ABOUT_UP` (1), `RIGID_FACE_CAMERA` (2), `ALWAYS_FACE_CENTER` (3), etc., but the add-on doesn't expose them in the UI. Export always uses `ALWAYS_FACE_CAMERA`. Import does not read the field either.
