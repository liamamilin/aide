"""Interruptible bridges and quieter gesture scheduling on real pet profiles."""

import json
import random
from pathlib import Path

import pytest

from ai_desktop.ui.pet_animator import PetAnimator
from ai_desktop.ui.pet_manifest import ManifestError, load_manifest

ROOT = Path(__file__).resolve().parents[1] / "ai_desktop/pets"
PATHS = [ROOT / "owl-v2/pet.json"] + [ROOT / f"petdex-profiles/{name}.json"
                                      for name in ("astra", "boba", "shinchan")]


@pytest.fixture(params=PATHS, ids=["owl", "astra", "boba", "shinchan"])
def animator(request):
    return PetAnimator(load_manifest(request.param), rng=random.Random(12))


def test_state_is_immediate_while_pose_bridges_then_sleeps(animator):
    initial = animator.snapshot().frame_index
    animator.set_state("working")
    assert animator.current_state == "working"
    assert animator.layer == "transition"
    assert animator.snapshot().frame_index == initial
    animator.tick(.6)
    assert animator.layer == "base"
    assert animator.next_wake_seconds is None
    focus = animator.snapshot().frame_index
    animator.set_state("waiting")
    assert animator.current_state == "waiting"
    assert animator.snapshot().frame_index == focus
    animator.tick(.6)
    assert animator.next_wake_seconds is None
    assert animator.snapshot().frame_index == initial or animator.manifest.name == "owl"


def test_alias_task_updates_keep_bridge_progress(animator):
    animator.set_state("working")
    animator.tick(.08)
    before = animator.snapshot(), animator.next_wake_seconds
    for state in ("searching", "executing", "working"):
        animator.set_state(state)
        assert animator.current_state == state
        assert (animator.snapshot(), animator.next_wake_seconds) == before
    animator.tick(.6)
    assert animator.next_wake_seconds is None


def test_reversal_uses_actual_visible_frame_and_cancels_old_destination(animator):
    animator.set_state("working")
    animator.tick(.14)
    visible = animator.snapshot().frame_index
    animator.set_state("cancelled")
    assert animator.current_state == "cancelled"
    assert animator.snapshot().frame_index == visible
    animator.tick(.6)
    assert animator.layer == "base"
    assert animator.next_wake_seconds is None
    assert animator.snapshot().frame_index == animator.manifest.animations["rest"].frames[0]


def test_completion_bridge_keeps_event_and_new_task_preempts_it(animator):
    animator.set_state("working", animate=False)
    focus = animator.snapshot().frame_index
    animator.trigger_event("success")
    animator.tick(0)
    assert animator.current_state == "success"
    assert animator.layer == "transition"
    assert animator.snapshot().frame_index == focus
    assert not animator.gesture("click")
    animator.tick(.6)
    assert animator.layer == "event"
    animator.set_state("working")
    animator.tick(2)
    assert animator.current_state == "working"
    assert animator.next_wake_seconds is None
    animator.trigger_event("success")
    animator.tick(2)
    assert animator.current_state == "idle"
    assert animator.layer == "base"


def test_grab_holds_without_a_timer_and_task_takes_ownership(animator):
    assert animator.gesture("press", hold=True)
    assert animator.next_wake_seconds is None
    assert animator.gesture("grab", hold=True)
    held = animator.snapshot()
    animator.tick(5)
    assert animator.snapshot() == held
    animator.set_state("working", animate=False)
    assert animator.layer == "base"
    assert not animator.gesture("release")
    assert animator.next_wake_seconds is None


def test_signature_is_short_returns_home_and_has_real_cooldown(animator):
    manifest = animator.manifest
    name = manifest.interactions["signature"]
    signature = manifest.animations[name]
    assert signature.family == "signature"
    assert signature.cooldown_ms >= 30000
    assert signature.weight < 1
    assert sum(signature.durations_ms) <= 1500
    assert signature.frames[0] == signature.frames[-1] == animator.snapshot().frame_index
    assert animator.gesture("signature")
    animator.tick(2)
    assert animator.layer == "base"
    # Direct and scheduled playback share the same family cooldown.
    assert not animator.gesture("signature")
    assert animator._choose([name], "ambient") is None
    assert animator._choose([name], "reaction") is None
    animator._elapse(signature.cooldown_ms / 1000)
    assert animator._choose([name], "ambient") == name


def test_scheduler_avoids_entire_previous_family(animator):
    choices = animator.manifest.states["idle"].ambient
    families = {animator.manifest.animations[name].family or name for name in choices}
    assert len(families) >= 3
    previous = None
    for _ in range(100):
        chosen = animator._choose(choices, "ambient")
        family = animator.manifest.animations[chosen].family or chosen
        assert family != previous
        previous = family
        animator._elapse(8)


@pytest.mark.parametrize("change", [
    {"transitions": []}, {"transitions": {"missing": ["blink"]}},
    {"transitions": {"focus": ["missing"]}}, {"transitions": {"focus": ["rest"]}},
    {"transitions": {"focus": ["blink"]}}, {"interactions": []},
    {"interactions": {"press": "rest"}}, {"interactions": {"press": "missing"}},
])
def test_invalid_motion_references_are_rejected(tmp_path, change):
    raw = json.loads(PATHS[0].read_text())
    raw.update(change)
    path = tmp_path / "pet.json"
    path.write_text(json.dumps(raw))
    with pytest.raises(ManifestError):
        load_manifest(path)


@pytest.mark.parametrize("field,value", [("weight", 0), ("weight", True), ("weight", float("nan")),
                                         ("cooldown_ms", -1), ("cooldown_ms", 1.5), ("family", [])])
def test_invalid_motion_timing_is_rejected(tmp_path, field, value):
    raw = json.loads(PATHS[0].read_text())
    raw["animations"]["blink"][field] = value
    path = tmp_path / "pet.json"
    path.write_text(json.dumps(raw))
    with pytest.raises(ManifestError):
        load_manifest(path)


def test_owl_directional_gaze_is_finite_and_distinct():
    animator = PetAnimator(load_manifest(PATHS[0]))
    frames = set()
    for direction in ("left", "right", "up", "down"):
        animator.set_state("hover", restart=True, animate=False)
        assert animator.react(direction)
        animator.tick(.35)
        frames.add(animator.snapshot().frame_index)
        animator.tick(2)
        assert animator.next_wake_seconds is None
    assert len(frames) == 4
