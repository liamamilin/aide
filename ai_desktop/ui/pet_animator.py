"""Elapsed-time player with quiet base poses and interruptible one-shot gestures.

A task owns the semantic state. Ambient and hover gestures temporarily replace
its frame sequence; they never change task state. Timers can sleep until the
next meaningful frame instead of polling a motionless character.
"""

import math
import random
from dataclasses import dataclass

from ai_desktop.ui.pet_manifest import PetManifest


@dataclass(frozen=True)
class AnimatorState:
    frame_index: int
    animation_name: str
    layer: str


class PetAnimator:
    def __init__(self, manifest: PetManifest, *, rng: random.Random | None = None) -> None:
        self._manifest = manifest
        self._rng = rng or random.Random()
        self._current_state = manifest.default_state
        self._current_anim = manifest.states[self._current_state].base
        self._layer = "base"
        self._frame_pos = 0
        self._frame_timer = 0.0
        self._ambient_delay = self._random_ambient_delay()
        self._event_queue: list[str] = []
        self._last_choice: dict[str, str] = {}
        self._return_state: str | None = None
        self._holding = False

    @property
    def manifest(self) -> PetManifest:
        return self._manifest

    @property
    def anchor(self):
        return self._manifest.anchor

    @property
    def current_state(self) -> str:
        return self._current_state

    @property
    def current_animation(self) -> str:
        return self._current_anim

    @property
    def layer(self) -> str:
        return self._layer

    @property
    def next_wake_seconds(self) -> float | None:
        """None means a completely static pose with no scheduled action."""
        if self._event_queue:
            return 0.0
        anim = self._manifest.animations[self._current_anim]
        waits = []
        if not self._holding and (len(anim.frames) > 1 or not anim.loop):
            waits.append(max(0.0, anim.duration(self._frame_pos) - self._frame_timer))
        if self._layer == "base" and self._manifest.states[self._current_state].ambient:
            waits.append(max(0.0, self._ambient_delay))
        return min(waits) if waits else None

    def snapshot(self) -> AnimatorState:
        return AnimatorState(
            self._manifest.animations[self._current_anim].frames[self._frame_pos],
            self._current_anim, self._layer,
        )

    def set_state(self, state: str, *, preserve_events: bool = False, restart: bool = False) -> None:
        if state not in self._manifest.states:
            return
        if not preserve_events:
            self._event_queue.clear()
        # Re-entering hover cancels a pending return without replaying the
        # greeting or resetting its progress.
        self._return_state = None
        if state == self._current_state and not restart:
            return
        self._current_state = state
        self._start_animation(self._manifest.states[state].base, "base")
        self._ambient_delay = self._random_ambient_delay()

    def trigger_event(self, event_state: str) -> None:
        if event_state in self._manifest.states:
            anim = self._manifest.animations[self._manifest.states[event_state].base]
            if not anim.loop:
                # Coalesce repeated completion notifications; never build a backlog.
                self._event_queue[:] = [event_state]

    def react(self) -> bool:
        """One response per pointer entry; avoid repeating the previous gesture."""
        choices = self._manifest.states[self._current_state].reactions
        if not choices or self._layer == "event":
            return False
        self._start_animation(self._choose(choices, "reaction"), "reaction")
        return True

    def settle_to(self, state: str) -> None:
        """Finish the tiny current gesture before returning; tasks still preempt it."""
        if self._layer in {"reaction", "ambient"} and state in self._manifest.states:
            self._return_state = state
        else:
            self.set_state(state, restart=True)

    def tick(self, delta_seconds: float) -> AnimatorState:
        if not math.isfinite(delta_seconds) or delta_seconds < 0:
            return self.snapshot()
        if self._event_queue:
            state = self._event_queue.pop(0)
            self.set_state(state, preserve_events=True, restart=True)
            self._layer = "event"
        # A delayed GUI callback must not replay a whole minute of gestures.
        remaining = min(delta_seconds, 30.0)
        for _ in range(1024):
            wake = self.next_wake_seconds
            if wake is None or wake > remaining + 1e-9:
                self._elapse(remaining)
                break
            self._elapse(wake)
            remaining = max(0.0, remaining - wake)
            anim = self._manifest.animations[self._current_anim]
            if (not self._holding and (len(anim.frames) > 1 or not anim.loop)
                    and self._frame_timer + 1e-9 >= anim.duration(self._frame_pos)):
                self._frame_timer = max(0.0, self._frame_timer - anim.duration(self._frame_pos))
                self._frame_pos += 1
                if self._frame_pos >= len(anim.frames):
                    if anim.loop:
                        self._frame_pos = 0
                    else:
                        self._finish_animation()
            elif self._layer == "base" and self._ambient_delay <= 1e-9:
                choices = self._manifest.states[self._current_state].ambient
                self._start_animation(self._choose(choices, "ambient"), "ambient")
            if remaining <= 1e-9:
                break
        return self.snapshot()

    def _elapse(self, seconds: float) -> None:
        self._frame_timer += seconds
        if self._layer == "base":
            self._ambient_delay -= seconds

    def _finish_animation(self) -> None:
        anim = self._manifest.animations[self._current_anim]
        if self._layer == "base":
            self._frame_pos = len(anim.frames) - 1
            self._holding = True
            return
        if self._return_state:
            self.set_state(self._return_state, preserve_events=True, restart=True)
        elif self._layer == "event" and anim.next_state:
            self.set_state(anim.next_state, preserve_events=True, restart=True)
        else:
            self._start_animation(self._manifest.states[self._current_state].base, "base")
        self._ambient_delay = self._random_ambient_delay()

    def _start_animation(self, name: str, layer: str) -> None:
        self._current_anim, self._layer = name, layer
        self._frame_pos, self._frame_timer = 0, 0.0
        self._holding = False

    def _choose(self, choices: list[str], channel: str) -> str:
        candidates = [name for name in choices if name != self._last_choice.get(channel)] or choices
        chosen = self._rng.choice(candidates)
        self._last_choice[channel] = chosen
        return chosen

    def _random_ambient_delay(self) -> float:
        return self._rng.uniform(4.8, 9.6)
