# Frozen downstream model protocol

All arms used `openai-codex/gpt-5.6-sol` through subscription RLM calls. They were told not to
inspect files, repositories, other agents, prior conversations, or the web.

The three planners received the same target and output schema. Only analogue context differed:

- baseline: none;
- surface: the surface-selected alert-dashboard observation, with no transferable action;
- structural: the selected firebreak observation, transfer relation, and deterministic role map,
  explicitly described as an advisory hypothesis needing target-specific grounding.

The candidate judge received only the target and candidates Q7/M2/R9 in opaque order. It was not
told source identities, retrieval choices, structural scores, surface scores, or the hypothesis.
The judge had to require both an answer to the falsified sequential-cleanup assumption and a
credible payroll-availability story, and had to state a falsifier for its winner.

The exact target and outputs are stored beside this file. Baseline and surface returned final JSON
in their isolated session transcripts; structural and judge returned JSON through the agent
message bus. No output was regenerated after seeing another arm.
