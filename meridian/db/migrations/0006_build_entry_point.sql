-- Where the sweep starts an agent.
--
-- `file_map` is `{primitive_key: path}` and the localiser reads it that way, so
-- the entry point cannot live in it: a key that is not a primitive would resolve
-- to a file for a card that does not exist, and every consumer of the map would
-- need to know to skip it.
--
-- It is a column rather than a lookup in `agents/<slug>/build.json` because a
-- build is a claim about code at a sha. Reading the current file to find out how
-- to run an earlier build answers a different question, and answers it silently.

alter table agent_builds add column entry_point text;

comment on column agent_builds.entry_point is
  'module path under agents/<slug>/ exposing run_case(case) -> CaseOutcome';
