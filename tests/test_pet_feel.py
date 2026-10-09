"""Gesture timing, continuation, and quiet elapsed clocks on all four pets."""

import json
import random
from pathlib import Path

import pytest

from ai_desktop.ui.pet_animator import PetAnimator
from ai_desktop.ui.pet_manifest import ManifestError, load_manifest

ROOT = Path(__file__).resolve().parents[1] / "ai_desktop/pets"
PATHS = [ROOT / "owl-v2/pet.json", *[ROOT / f"petdex-profiles/{name}.json"
                                   for name in ("astra", "boba", "shinchan")]]


@pytest.fixture(params=PATHS, ids=["owl", "astra", "boba", "shinchan"])
def animator(request):
    return PetAnimator(load_manifest(request.param), rng=random.Random(24))


def test_hover_crossings_keep_signature_progress_and_task_can_preempt(animator):
    assert animator.gesture("signature")
    animator.tick(.25)
    visible, timer, tempo = animator.snapshot(), animator._frame_timer, animator._tempo
    for state in ("hover", "idle", "hover"):
        animator.set_state(state)
        assert animator.snapshot() == visible
        assert animator._frame_timer == timer
        assert animator._tempo == tempo
        assert not animator.react("right")  # No second greeting cuts off a wave.
    animator.set_state("working")
    assert animator.current_state == "working"
    assert animator._tempo == 1.0
    animator.tick(.6)
    assert animator.layer == "base"
    assert animator.next_wake_seconds is None


def test_held_pointer_pose_survives_hover_until_explicit_release(animator):
    assert animator.gesture("press", hold=True)
    held = animator.snapshot()
    animator.set_state("hover")
    animator.settle_to("idle")
    animator.tick(5)
    assert animator.snapshot() == held
    assert animator.next_wake_seconds is None
    assert not animator.react()
    assert animator.gesture("click")
    animator.tick(2)
    assert animator.layer == "base"


def test_each_gesture_has_one_bounded_tempo_and_keeps_it_until_finish(animator):
    tempos = []
    for _ in range(6):
        assert animator.gesture("signature")
        tempo = animator._tempo
        tempos.append(tempo)
        assert .92 <= tempo <= 1.08
        animation = animator.manifest.animations[animator.current_animation]
        duration = sum(animation.duration(i) for i in range(len(animation.frames))) * tempo
        animator.tick(duration - .00001)
        assert animator.layer == "interaction" and animator._tempo == tempo
        animator.tick(.00002)
        assert animator.layer == "base" and animator._tempo == 1.0
        animator.tick(0, elapsed_seconds=60)
    assert len(set(tempos)) == len(tempos)


def test_static_time_expires_cooldown_without_replaying_old_gestures(animator):
    name = animator.manifest.interactions["signature"]
    assert animator.gesture("signature")
    animator.tick(2)
    animator.set_state("hover", animate=False)
    before = animator.snapshot()
    assert animator._choose([name], "ambient") is None
    animator.tick(0, elapsed_seconds=60)
    assert animator.snapshot() == before
    assert animator.next_wake_seconds is None
    assert animator._choose([name], "ambient") == name


def test_returning_hover_gesture_starts_at_the_current_pose(animator):
    animator.set_state("hover", animate=False)
    before = animator.snapshot().frame_index
    assert animator.react("left")
    assert animator.snapshot().frame_index == before
    animator.tick(2)
    assert animator.next_wake_seconds is None


def test_unattended_idle_gradually_leaves_longer_quiet_periods(animator):
    intervals = []
    for _ in range(3_000):
        was_quiet = animator.layer == "base" and not animator._secondary.active
        animator.tick(.1)
        if not was_quiet and animator.layer == "base" and not animator._secondary.active:
            intervals.append((animator._elapsed, animator.next_wake_seconds))
    early = [wait for elapsed, wait in intervals if elapsed < 60]
    late = [wait for elapsed, wait in intervals if elapsed >= 180]
    assert early and late
    assert all(5.8 <= wait <= 12 for wait in early)
    # Boba's tail can finish slightly after the body enters the base pose.
    assert all(17.5 <= wait <= 30 for wait in late)


@pytest.mark.parametrize("action", ["click", "release", "signature"])
def test_deliberate_interaction_restores_short_idle_intervals(animator, action):
    animator.tick(0, elapsed_seconds=300)
    assert animator.gesture(action)
    animator.tick(3)
    assert animator.layer == "base"
    assert 3 <= animator.next_wake_seconds <= 12


def test_return_from_task_renews_attention_without_an_extra_greeting(animator):
    animator.set_state("working", animate=False)
    animator.tick(0, elapsed_seconds=300)
    assert animator.next_wake_seconds is None
    animator.set_state("idle", animate=False)
    assert animator.layer == "base"
    assert 6 <= animator.next_wake_seconds <= 12


def test_sustained_hover_does_not_build_up_an_idle_animation_debt(animator):
    animator.set_state("hover", animate=False)
    animator.tick(20)
    assert animator.next_wake_seconds is None
    animator.set_state("idle", animate=False)
    assert 6 <= animator.next_wake_seconds <= 12


@pytest.mark.parametrize("value", [-.01, .16, True, float("nan"), float("inf"), "slow"])
def test_invalid_tempo_metadata_is_rejected(tmp_path, value):
    data = json.loads(PATHS[0].read_text())
    data["animations"]["blink"]["tempo_variation"] = value
    path = tmp_path / "pet.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ManifestError, match="tempo_variation"):
        load_manifest(path)
