from __future__ import annotations

from copy import deepcopy
from dataclasses import fields
from typing import Callable

from worldsim.models import (
    CheckResult,
    EffectCondition,
    Player,
    RejectedEffect,
    StateEffect,
    World,
)


EffectValidator = Callable[[StateEffect], str | None]
EffectCommitter = Callable[[StateEffect], None]


class StateReducer:
    """Validates a batch before committing it and rolls the batch back on error."""

    def apply(
        self,
        world: World,
        player: Player,
        effects: list[StateEffect],
        check: CheckResult | None,
        validate: EffectValidator,
        commit: EffectCommitter,
    ) -> tuple[list[StateEffect], list[RejectedEffect]]:
        applicable: list[StateEffect] = []
        rejected: list[RejectedEffect] = []
        succeeded = check is None or check.success

        for effect in effects:
            condition_error = self._condition_error(effect, succeeded)
            if condition_error is not None:
                rejected.append(RejectedEffect(effect=effect, reason=condition_error))
                continue
            validation_error = validate(effect)
            if validation_error is not None:
                rejected.append(RejectedEffect(effect=effect, reason=validation_error))
                continue
            applicable.append(effect)

        self.apply_accepted(world, player, applicable, commit)
        return applicable, rejected

    def apply_accepted(
        self,
        world: World,
        player: Player,
        effects: list[StateEffect],
        commit: EffectCommitter,
    ) -> None:
        """Replay or commit an already validated batch as one transaction."""

        world_snapshot = deepcopy(world)
        player_snapshot = deepcopy(player)
        try:
            for effect in effects:
                commit(effect)
        except Exception:
            self._restore(world, world_snapshot)
            self._restore(player, player_snapshot)
            raise

    def _condition_error(self, effect: StateEffect, succeeded: bool) -> str | None:
        if effect.condition == EffectCondition.SUCCESS and not succeeded:
            return "requires a successful check"
        if effect.condition == EffectCondition.FAILURE and succeeded:
            return "requires a failed check"
        return None

    def _restore(self, target: object, snapshot: object) -> None:
        for item in fields(target):
            setattr(target, item.name, deepcopy(getattr(snapshot, item.name)))
