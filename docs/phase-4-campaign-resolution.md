# Phase 4: Quest, Clock, and Campaign Resolution

Phase 4 moves campaign progression into `ProgressionService`. The service owns
quest lifecycle, stage evaluation, clock trigger consequences, finale
activation, terminal outcomes, and epilogues. `WorldEngine` orchestrates it;
the director may propose evidence or commitments but cannot declare a quest or
campaign outcome.

## Typed quests

`QuestStage` replaces string-only stages. Each stage has a stable ID, title,
description, lifecycle status, and one or more engine-verifiable `Condition`
records. A stage advances only when every condition is true in authoritative
state. Legacy `progress` fields remain readable for save compatibility but are
not completion authority.

Supported conditions include:

- item acquired;
- exact NPC recruited with an expected disposition;
- fact discovered;
- stable encounter target defeated;
- stable location reached;
- object state reached;
- exact choice committed;
- clock threshold reached;
- prerequisite quest completed.

Generated quests reference actual location and NPC IDs. They form a
deterministic prerequisite chain rather than all attaching to the first two
entities. Completing a prerequisite makes the next quest available and the
engine activates the next eligible quest.

Dialogue beats can propose exact-ID NPC disposition changes and choice
commitments. Freeform action intents can propose `npc_disposition` and
`choice_commit` effects. The engine accepts them only when they match the
active stage. Unrelated facts and choices remain in world state but do not
advance the quest.

## Clocks

Clock triggers still fire exactly once, now with a broader structured
consequence vocabulary:

- add a fact;
- fail or activate a quest;
- change stability;
- start an encounter;
- change local-scene tension;
- start the finale;
- end the campaign in victory or defeat.

Trigger IDs and fired state persist. Re-evaluating a completed clock does not
repeat its effects.

## Campaign endings

`CampaignStatus` is persisted as `active`, `finale`, `victory`, `defeat`, or
`abandoned`. Completing every quest marked `required_for_finale` starts the
finale but does not grant automatic victory. `resolve finale` checks explicit
finale requirements and resolves a common engine-owned d20 check:

- success commits `campaign_victory`;
- failure commits `campaign_defeat`.

Both outcomes produce a persisted epilogue and replayable `TurnRecord`.
`abandon campaign` is the explicit non-final confrontation ending.

Victory, defeat, and abandonment reject ordinary gameplay commands. The
remaining meta commands are `campaign status`, `inventory`, and `quit`.
Ending a campaign also resolves any active encounter so a terminal save cannot
retain a derived movement lock.

## Save migration

Save schema version 4 adds typed stage payloads, quest prerequisites, campaign
status, main quest ID, finale requirements, ending details, and resolved
encounter IDs. The version-3 migration:

- converts string stages and parallel condition arrays into `QuestStage`
  records;
- maps legacy related location/NPC names to stable IDs when possible;
- creates condition-backed fallbacks for stages that had only counters;
- establishes a prerequisite chain and main quest;
- initializes an active campaign and explicit finale requirement.

Versions 0 through 3 migrate sequentially. Saving the loaded campaign writes
schema version 4.
