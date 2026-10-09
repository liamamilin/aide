"""Elapsed-time player with quiet base poses and interruptible one-shot gestures.

A task owns the semantic state. Ambient and hover gestures temporarily replace
its frame sequence; they never change task state. Timers can sleep until the
next meaningful frame instead of polling a motionless character.
"""

import math
import random
from dataclasses import dataclass

from ai_desktop.ui.pet_manifest import PetManifest
from ai_desktop.ui.pet_secondary import SecondaryPlayer


@dataclass(frozen=True)
class AnimatorState:
    frame_index: int
    animation_name: str
    layer: str
    secondary_angles: tuple[float, ...] = ()


class PetAnimator:
    def __init__(self, manifest: PetManifest, *, rng: random.Random | None = None) -> None:
        self._manifest = manifest
        self._rng = rng or random.Random()
        self._current_state = manifest.default_state
        self._current_anim = manifest.states[self._current_state].base
        self._layer = "base"
        self._frame_pos = 0
        self._frame_timer = 0.0
        self._tempo = 1.0
        self._elapsed = 0.0
        self._last_attention = 0.0
        self._ambient_delay = self._random_ambient_delay()
        self._event_queue: list[str] = []
        self._last_choice: dict[str, str] = {}
        self._return_state: str | None = None
        self._holding = False
        self._transition_return_layer = "base"
        self._last_family = ""
        self._family_ready: dict[str, float] = {}
        self._secondary = SecondaryPlayer(manifest.secondary_motion)

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
        secondary_wake = self._secondary.next_wake_seconds
        if not self._holding and secondary_wake is not None:
            waits.append(secondary_wake)
        if not self._holding and (len(anim.frames) > 1 or not anim.loop):
            waits.append(max(0.0, self._frame_duration() - self._frame_timer))
        if not self._holding and self._layer == "base" and self._manifest.states[self._current_state].ambient:
            waits.append(max(0.0, self._ambient_delay))
        return min(waits) if waits else None

    def snapshot(self) -> AnimatorState:
        frame = self._manifest.animations[self._current_anim].frames[self._frame_pos]
        return AnimatorState(frame, self._current_anim, self._layer, self._secondary.angles(frame))

    def clear_secondary_motion(self) -> None:
        self._secondary.clear()

    def set_state(self, state: str, *, preserve_events: bool = False, restart: bool = False,
                  animate: bool = True) -> None:
        if state not in self._manifest.states:
            return
        if not animate or state not in {"idle", "hover"}:
            self._secondary.clear()
        if not preserve_events:
            self._event_queue.clear()
        # Re-entering hover cancels a pending return without replaying the
        # greeting or resetting its progress.
        self._return_state = None
        if state == self._current_state and not restart:
            return
        frame = self.snapshot().frame_index
        previous_state = self._current_state
        previous_base = self._manifest.states[self._current_state].base
        self._current_state = state
        if state != previous_state and state in {"idle", "hover"}:
            self._last_attention = self._elapsed
        base = self._manifest.states[state].base
        # Crossing the silhouette must not cut off a wave, blink, or held paw.
        # At its end the gesture will bridge to the new hover/idle base.
        if (animate and not restart and {previous_state, state} <= {"idle", "hover"}
                and self._layer in {"ambient", "reaction", "interaction"}):
            return
        if animate and not restart and base == previous_base and self._layer in {"base", "transition"}:
            if state != previous_state:
                self._ambient_delay = self._random_ambient_delay()
            return
        if not animate or not self._start_transition(base, frame):
            self._start_animation(base, "base")
        self._ambient_delay = self._random_ambient_delay()

    def trigger_event(self, event_state: str) -> None:
        if event_state in self._manifest.states:
            anim = self._manifest.animations[self._manifest.states[event_state].base]
            if not anim.loop:
                # Coalesce repeated completion notifications; never build a backlog.
                self._event_queue[:] = [event_state]

    def react(self, direction: str | None = None) -> bool:
        """One response per pointer entry; avoid repeating the previous gesture."""
        if not self._can_interact() or self._layer not in {"base", "transition"} or self._secondary.active:
            return False
        if direction and self.gesture(f"look_{direction}"):
            self._layer = "reaction"
            return True
        frame = self.snapshot().frame_index
        choices = self._manifest.states[self._current_state].reactions
        compatible = [name for name in choices
                   if frame in self._manifest.animations[name].frames[:-1]
                   or self._manifest.animations[name].frames[0] == frame]
        choices = compatible or choices  # Legacy gestures need not include a lead-in base frame.
        if not choices:
            return False
        choice = self._choose(choices, "reaction")
        if choice is None:
            return False
        self._last_attention = self._elapsed
        self._start_animation(choice, "reaction")
        self._align_gesture(frame)
        self._ambient_delay = self._random_ambient_delay()
        return True

    def gesture(self, action: str, *, hold: bool = False) -> bool:
        """Immediate pointer feedback; task and result states always own the pose."""
        name = self._manifest.interactions.get(action)
        if name is None or not self._can_interact():
            return False
        soft = action == "signature" or action.startswith("look_")
        anim = self._manifest.animations[name]
        frame = self.snapshot().frame_index
        family = anim.family or name
        if soft and (self._layer not in {"base", "transition"}
                     or self._elapsed < self._family_ready.get(family, 0)
                     or frame not in anim.frames[:-1]):
            return False
        self._last_attention = self._elapsed
        self._return_state = None
        self._start_animation(name, "interaction")
        if action not in {"press", "grab"}:
            self._align_gesture(frame)
        if soft:
            self._last_family = family
            self._family_ready[family] = self._elapsed + anim.cooldown_ms / 1000
        self._holding = hold
        if hold:
            self._secondary.clear()
        self._ambient_delay = self._random_ambient_delay()
        return True

    def _can_interact(self) -> bool:
        return (self._current_state in {"idle", "hover"}
                and self._layer != "event" and not self._event_queue)

    def settle_to(self, state: str) -> None:
        """Finish the tiny current gesture before returning; tasks still preempt it."""
        if (self._layer in {"reaction", "ambient", "interaction"} and not self._holding
                and state in self._manifest.states):
            self._return_state = state
        else:
            self.set_state(state)

    def tick(self, delta_seconds: float, *, elapsed_seconds: float | None = None) -> AnimatorState:
        if not math.isfinite(delta_seconds) or delta_seconds < 0:
            return self.snapshot()
        if self._event_queue:
            state = self._event_queue.pop(0)
            self.set_state(state, preserve_events=True, restart=True)
            if self._layer == "transition":
                self._transition_return_layer = "event"
            else:
                self._layer = "event"
        # A delayed GUI callback must not replay a whole minute of gestures.
        remaining = min(delta_seconds, 30.0)
        # A sleeping GUI can skip visual catch-up while cooldowns still follow
        # real elapsed time. Never let a bad external clock poison scheduling.
        clock_delta = elapsed_seconds if elapsed_seconds is not None else delta_seconds
        if math.isfinite(clock_delta):
            skipped = max(0.0, clock_delta - remaining)
            self._elapsed += skipped
            self._secondary.advance(skipped)
        for _ in range(1024):
            wake = self.next_wake_seconds
            if wake is None or wake > remaining + 1e-9:
                self._elapse(remaining)
                break
            self._elapse(wake)
            remaining = max(0.0, remaining - wake)
            anim = self._manifest.animations[self._current_anim]
            if (not self._holding and (len(anim.frames) > 1 or not anim.loop)
                    and self._frame_timer + 1e-9 >= self._frame_duration()):
                self._frame_timer = max(0.0, self._frame_timer - self._frame_duration())
                self._frame_pos += 1
                if self._frame_pos >= len(anim.frames):
                    if anim.loop:
                        self._frame_pos = 0
                    else:
                        self._finish_animation()
            elif self._layer == "base" and self._ambient_delay <= 1e-9:
                choices = self._manifest.states[self._current_state].ambient
                choice = self._choose(choices, "ambient")
                if choice is None:
                    self._ambient_delay = self._random_ambient_delay()
                else:
                    self._start_animation(choice, "ambient")
            if remaining <= 1e-9:
                break
        return self.snapshot()

    def _elapse(self, seconds: float) -> None:
        self._elapsed += seconds
        self._secondary.advance(seconds)
        self._frame_timer += seconds
        if self._layer == "base" and self._manifest.states[self._current_state].ambient:
            self._ambient_delay -= seconds

    def _finish_animation(self) -> None:
        anim = self._manifest.animations[self._current_anim]
        self._frame_pos = len(anim.frames) - 1
        if self._layer == "base":
            self._holding = True
            return
        if self._layer == "transition":
            self._start_animation(self._manifest.states[self._current_state].base,
                                  self._transition_return_layer)
        elif self._return_state:
            self.set_state(self._return_state, preserve_events=True, restart=True)
        elif self._layer == "event" and anim.next_state:
            self.set_state(anim.next_state, preserve_events=True, restart=True)
        else:
            base = self._manifest.states[self._current_state].base
            if not self._start_transition(base, self.snapshot().frame_index):
                self._start_animation(base, "base")
        self._ambient_delay = self._random_ambient_delay()

    def _start_animation(self, name: str, layer: str) -> None:
        self._current_anim, self._layer = name, layer
        self._frame_pos, self._frame_timer = 0, 0.0
        self._holding = False
        variation = self._manifest.animations[name].tempo_variation
        self._tempo = (self._rng.uniform(1 - variation, 1 + variation)
                       if variation and layer in {"ambient", "reaction", "interaction"} else 1.0)
        if layer in {"ambient", "reaction", "interaction"}:
            self._secondary.trigger(name, self._manifest.animations[name].family)

    def _frame_duration(self) -> float:
        return self._manifest.animations[self._current_anim].duration(self._frame_pos) * self._tempo

    def _align_gesture(self, frame: int) -> None:
        frames = self._manifest.animations[self._current_anim].frames
        if frame in frames[:-1]:
            self._frame_pos = frames[:-1].index(frame)

    def _start_transition(self, target: str, frame: int) -> bool:
        if frame == self._manifest.animations[target].frames[0]:
            return False
        candidates = []
        for name in self._manifest.transitions.get(target, []):
            anim = self._manifest.animations[name]
            for pos, index in enumerate(anim.frames[:-1]):
                if index == frame:
                    duration = sum(anim.duration(i) for i in range(pos, len(anim.frames)))
                    candidates.append((duration, name, pos))
        if not candidates:
            return False
        _, name, pos = min(candidates)
        self._start_animation(name, "transition")
        self._frame_pos = pos
        self._transition_return_layer = "base"
        return True

    def _choose(self, choices: list[str], channel: str) -> str | None:
        def family(name):
            return self._manifest.animations[name].family or name

        available = [name for name in choices if self._elapsed >= self._family_ready.get(family(name), 0)]
        if not available:
            return None
        candidates = [name for name in available if family(name) != self._last_family] or available
        candidates = [name for name in candidates if name != self._last_choice.get(channel)] or candidates
        chosen = self._rng.choices(candidates, weights=[self._manifest.animations[n].weight for n in candidates])[0]
        self._last_choice[channel] = chosen
        self._last_family = family(chosen)
        self._family_ready[self._last_family] = self._elapsed + self._manifest.animations[chosen].cooldown_ms / 1000
        return chosen

    def _random_ambient_delay(self) -> float:
        # Become quieter over sustained inactivity; self-generated gestures
        # never renew attention. Real interactions and task returns do.
        quiet = min(1.0, max(0.0, (self._elapsed - self._last_attention - 60) / 120))
        return self._rng.uniform(6 + 12 * quiet, 12 + 18 * quiet)
