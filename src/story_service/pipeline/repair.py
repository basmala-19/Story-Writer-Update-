"""Scene-scoped repair for S4.

A retry that rewrites the whole script fixes one problem and often creates another. These helpers
let the loop (1) work out which scenes the failure is about and (2) put every other scene back
exactly as it was, so the writer can only change what the feedback is about.

Nothing here knows about a topic, a character or a story. It reads scene ids from failed checks and
Critic issues and compares two script dicts.
"""
from __future__ import annotations

import copy
import re
from typing import Any

_SCENE_ID = re.compile(r"\bs(\d{2})(?![0-9])")

# Failed code checks whose detail names the scenes/lines involved. Anything else that fails is a
# whole-script problem (length, narrator share, speakers, concept coverage, schema...) and the writer
# keeps full freedom.
SCOPED_CODE_CHECKS = {
    "lines_per_scene",
    "words_per_line",
    "spoken_no_digits",
    "spoken_no_latin",
    "no_emoji",
    "no_stage_markup",
    "near_duplicate_lines",
    "interaction_position",
    "spoken_display_similarity",
    "dialogue_self_stage_direction",
}

# Failed Critic/audit rules that mean the plot structure itself must change.
GLOBAL_CRITIC_CHECKS = {
    "critic_must_as_plot_tool",
    "critic_evidence",
    "critic_important_coverage",
    "critic_explanatory_ratio",
    "critic_schema",
}


def scene_ids_in(text: str) -> set[str]:
    """Every scene id mentioned in `text` (also inside line ids such as s03_l05)."""
    return {f"s{m}" for m in _SCENE_ID.findall(text or "")}


def _blocking(checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [c for c in checks if not c["passed"] and c["severity"] in ("blocker", "major")]


def affected_scene_ids(
    code_validation: dict[str, Any],
    critic_validation: dict[str, Any] | None,
    issues: list[dict[str, Any]],
) -> set[str] | None:
    """Scenes the failure is about, or None when the whole script may change.

    `issues` are Critic and continuity-audit issues (dicts with scene_id/line_id).
    """
    scenes: set[str] = set()
    for c in _blocking(code_validation.get("checks", [])):
        if c["name"] not in SCOPED_CODE_CHECKS:
            return None
        found = scene_ids_in(c.get("detail", ""))
        if not found:
            return None
        scenes |= found

    if critic_validation is not None:
        failed = _blocking(critic_validation.get("checks", []))
        if any(c["name"] in GLOBAL_CRITIC_CHECKS for c in failed):
            return None
        if failed:
            cited = set()
            for issue in issues:
                cited |= scene_ids_in(f"{issue.get('scene_id') or ''} {issue.get('line_id') or ''}")
            if not cited and not scenes:
                return None  # low scores with no cited place: nothing to anchor a local fix
            scenes |= cited
    return scenes or None


def expand_with_neighbours(scenes: set[str], all_ids: list[str]) -> set[str]:
    """A continuity fix often sits one scene before or after the cited line."""
    out = set(scenes)
    for sid in scenes:
        if sid in all_ids:
            i = all_ids.index(sid)
            out.update(all_ids[max(0, i - 1): i + 2])
    return out


def freeze_unaffected(
    new_raw: dict[str, Any], previous_raw: dict[str, Any] | None, editable: set[str] | None
) -> tuple[dict[str, Any], list[str]]:
    """Restore every scene outside `editable` from `previous_raw`.

    Returns (script, restored scene ids). The script is returned untouched when freezing would be
    unsafe: no editable set, scene lists differ, or a frozen scene needs a speaker the new draft
    dropped.
    """
    if not editable or not previous_raw:
        return new_raw, []
    new_scenes, old_scenes = new_raw.get("scenes"), previous_raw.get("scenes")
    if not isinstance(new_scenes, list) or not isinstance(old_scenes, list):
        return new_raw, []
    new_ids = [s.get("id") for s in new_scenes if isinstance(s, dict)]
    old_ids = [s.get("id") for s in old_scenes if isinstance(s, dict)]
    if new_ids != old_ids or len(new_ids) != len(new_scenes):
        return new_raw, []

    old_by_id = {s["id"]: s for s in old_scenes}
    keys = {c.get("key") for c in new_raw.get("characters", []) if isinstance(c, dict)}
    restore = [sid for sid in new_ids if sid not in editable]
    for sid in restore:
        speakers = {l.get("speaker") for l in old_by_id[sid].get("lines", []) if isinstance(l, dict)}
        if not speakers <= (keys | {"narrator"}):
            return new_raw, []

    merged = copy.deepcopy(new_raw)
    changed = []
    for i, sid in enumerate(new_ids):
        if sid in restore and merged["scenes"][i] != old_by_id[sid]:
            merged["scenes"][i] = copy.deepcopy(old_by_id[sid])
            changed.append(sid)
    if new_ids and new_ids[-1] in restore and "recap_line_ids" in previous_raw:
        merged["recap_line_ids"] = list(previous_raw["recap_line_ids"])
    return merged, changed


def scope_note(editable: set[str] | None, all_ids: list[str]) -> str:
    """Tell the writer what it may change. Empty when the whole script is open."""
    if not editable or set(all_ids) <= editable:
        return ""
    open_ids = [s for s in all_ids if s in editable]
    locked = [s for s in all_ids if s not in editable]
    return (
        "REPAIR SCOPE: only these scenes may change: "
        + ", ".join(open_ids)
        + ". These scenes are locked and are restored exactly as they were, whatever you write: "
        + ", ".join(locked)
        + ". Keep every scene id, line id, line count and character key unchanged, and make the "
        "scenes you edit agree with the locked scenes around them."
    )
