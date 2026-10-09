"""Tests for the animation state machine."""


from ai_desktop.ui.pet_animator import PetAnimator
from ai_desktop.ui.pet_manifest import (
    Anchor,
    AnimationDef,
    PetManifest,
    SpritesheetInfo,
    StateDef,
)


def _make_manifest(
    states: dict[str, StateDef] | None = None,
    animations: dict[str, AnimationDef] | None = None,
    default_state: str = "idle",
) -> PetManifest:
    if states is None:
        states = {
            "idle": StateDef(base="idle_base", ambient=["blink"]),
            "working": StateDef(base="coding"),
            "success": StateDef(base="success_anim"),
        }
    if animations is None:
        animations = {
            "idle_base": AnimationDef(name="idle_base", frames=[0, 1, 2, 3], fps=5, loop=True),
            "blink": AnimationDef(name="blink", frames=[4, 5, 4, 3], fps=10, loop=False),
            "coding": AnimationDef(name="coding", frames=[6, 7, 8, 9], fps=6, loop=True),
            "success_anim": AnimationDef(
                name="success_anim", frames=[10, 11, 12, 13, 14], fps=10, loop=False,
                next_state="idle",
            ),
        }
    return PetManifest(
        schema_version=1,
        name="test-pet",
        spritesheet=SpritesheetInfo(
            file="spritesheet.png",
            frame_width=512,
            frame_height=512,
            columns=4,
            frame_count=15,
        ),
        anchor=Anchor(x=0.5, y=0.95),
        default_state=default_state,
        default_fps=8,
        states=states,
        animations=animations,
    )


class TestPetAnimator:
    def test_starts_in_default_state(self):
        manifest = _make_manifest()
        animator = PetAnimator(manifest)

        assert animator.current_state == "idle"
        assert animator.current_animation == "idle_base"
        assert animator.layer == "base"

    def test_snapshot_returns_current_frame(self):
        manifest = _make_manifest()
        animator = PetAnimator(manifest)
        snap = animator.snapshot()

        assert snap.frame_index == 0
        assert snap.animation_name == "idle_base"
        assert snap.layer == "base"

    def test_set_state_changes_base_animation(self):
        manifest = _make_manifest()
        animator = PetAnimator(manifest)
        animator.set_state("working")

        assert animator.current_state == "working"
        assert animator.current_animation == "coding"
        assert animator.layer == "base"

    def test_set_state_ignores_unknown_state(self):
        manifest = _make_manifest()
        animator = PetAnimator(manifest)
        animator.set_state("nonexistent")

        assert animator.current_state == "idle"

    def test_trigger_event_queues_event(self):
        manifest = _make_manifest()
        animator = PetAnimator(manifest)
        animator.trigger_event("success")

        assert animator.current_state == "idle"
        assert animator.layer == "base"

    def test_tick_advances_frame(self):
        manifest = _make_manifest()
        animator = PetAnimator(manifest)
        animator.tick(0.0)

        snap = animator.tick(0.3)
        assert snap.frame_index == 1

    def test_tick_loops_base_animation(self):
        manifest = _make_manifest()
        animator = PetAnimator(manifest)
        animator.tick(0.0)

        for _ in range(3):
            animator.tick(0.2)
        snap = animator.tick(0.2)

        assert snap.frame_index == 0
        assert animator.layer == "base"

    def test_tick_triggers_queued_event(self):
        manifest = _make_manifest()
        animator = PetAnimator(manifest)
        animator.tick(0.0)
        animator.trigger_event("success")
        animator.tick(0.0)

        snap = animator.snapshot()
        assert snap.animation_name == "success_anim"
        assert animator.layer == "event"

    def test_event_transitions_to_next_state(self):
        manifest = _make_manifest()
        animator = PetAnimator(manifest)
        animator.tick(0.0)
        animator.trigger_event("success")

        for _ in range(10):
            animator.tick(0.1)

        assert animator.current_state == "idle"
        assert animator.current_animation == "idle_base"
        assert animator.layer == "base"

    def test_ambient_triggers_after_delay(self):
        manifest = _make_manifest()
        animator = PetAnimator(manifest)
        animator.tick(0.0)

        delay = animator._ambient_delay
        animator.tick(delay)
        assert animator.layer == "ambient"
        assert animator.current_animation == "blink"

    def test_ambient_returns_to_base(self):
        animator = PetAnimator(_make_manifest())
        animator.tick(animator._ambient_delay)
        assert animator.layer == "ambient"
        animator.tick(0.4)
        assert animator.layer == "base"
        assert animator.current_animation == "idle_base"

    def test_set_state_clears_event_queue(self):
        manifest = _make_manifest()
        animator = PetAnimator(manifest)
        animator.tick(0.0)
        animator.trigger_event("success")
        animator.set_state("working")

        snap = animator.snapshot()
        assert snap.animation_name == "coding"
        assert animator.layer == "base"

    def test_set_state_preserve_events_keeps_queue(self):
        manifest = _make_manifest()
        animator = PetAnimator(manifest)
        animator.tick(0.0)
        animator.trigger_event("success")
        animator.set_state("working", preserve_events=True)

        assert animator.current_state == "working"

    def test_trigger_event_ignores_looping_animation(self):
        states = {
            "idle": StateDef(base="idle_base"),
            "working": StateDef(base="coding"),
        }
        animations = {
            "idle_base": AnimationDef(name="idle_base", frames=[0, 1], fps=5, loop=True),
            "coding": AnimationDef(name="coding", frames=[2, 3], fps=5, loop=True),
        }
        manifest = _make_manifest(states=states, animations=animations)
        animator = PetAnimator(manifest)
        animator.tick(0.0)
        animator.trigger_event("working")

        snap = animator.snapshot()
        assert snap.animation_name == "idle_base"
        assert animator.layer == "base"


def test_nonuniform_frame_times_are_respected():
    animations = {"idle_base": AnimationDef("idle_base", [0, 1, 2], 8, True,
                                           durations_ms=[280, 40, 140])}
    animator = PetAnimator(_make_manifest(states={"idle": StateDef("idle_base")}, animations=animations))
    assert animator.tick(.279).frame_index == 0
    assert animator.tick(.001).frame_index == 1
    assert animator.tick(.04).frame_index == 2
    assert animator.tick(.14).frame_index == 0


def test_duplicate_task_updates_do_not_restart_pose():
    animator = PetAnimator(_make_manifest())
    animator.set_state("working")
    animator.tick(.2)
    before = animator.snapshot()
    animator.set_state("working")
    assert animator.snapshot() == before


def test_task_preempts_a_pending_result_and_ambient():
    animator = PetAnimator(_make_manifest())
    animator.tick(animator._ambient_delay)
    assert animator.layer == "ambient"
    animator.trigger_event("success")
    animator.set_state("working")
    assert animator.tick(.01).animation_name == "coding"
    assert animator.layer == "base"


def test_static_hover_sleeps_and_reactions_do_not_repeat():
    import random
    animations = {"rest": AnimationDef("rest", [0], 8, True),
                  "a": AnimationDef("a", [1, 0], 10, False),
                  "b": AnimationDef("b", [2, 0], 10, False)}
    states = {"idle": StateDef("rest"), "hover": StateDef("rest", reactions=["a", "b"])}
    animator = PetAnimator(_make_manifest(states=states, animations=animations), rng=random.Random(3))
    animator.set_state("hover")
    assert animator.next_wake_seconds is None
    chosen = []
    for _ in range(6):
        assert animator.react()
        chosen.append(animator.current_animation)
        animator.tick(.2)
        assert animator.next_wake_seconds is None
    assert all(a != b for a, b in zip(chosen, chosen[1:]))
    animator.react()
    animator.settle_to("idle")
    animator.tick(.2)
    assert animator.current_state == "idle"


def test_static_idle_wakes_for_ambient_without_continuous_frame_loop():
    animations = {"rest": AnimationDef("rest", [0], 8, True),
                  "blink": AnimationDef("blink", [1, 0], 10, False)}
    animator = PetAnimator(_make_manifest(states={"idle": StateDef("rest", ["blink"])}, animations=animations))
    wake = animator.next_wake_seconds
    assert 6 <= wake <= 12
    animator.tick(wake)
    assert animator.layer == "ambient"
    assert animator.next_wake_seconds == .1
    animator.tick(.2)
    assert animator.snapshot().frame_index == 0
    assert animator.next_wake_seconds >= 4.8


def test_nonloop_base_holds_last_pose_instead_of_replaying_forever():
    animations = {"intro": AnimationDef("intro", [0, 1, 2], 10, False)}
    animator = PetAnimator(_make_manifest(states={"idle": StateDef("intro")}, animations=animations))
    assert animator.tick(.5).frame_index == 2
    assert animator.next_wake_seconds is None
    assert animator.tick(10).frame_index == 2
