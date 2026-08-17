-- What arrived and was not used.
--
-- Half of a "found nothing" failure is a document that was skipped rather than
-- a document that was absent, and the two have completely different fixes: one
-- is a recognition rule, the other is upstream of the agent entirely. Without
-- this the bundle can say a check counted zero certificates and cannot say
-- whether seven turned up and none was recognised.
--
-- On `runs` rather than in `run_steps` because a decline is not a step. It has
-- no sequence, no attempt and no outcome — it is a source and a reason — and
-- `run_steps.primitive_key` is the board_key domain, which "image001.gif" is
-- never going to satisfy.

alter table runs add column declined jsonb not null default '[]';

comment on column runs.declined is
  'sources this run skipped, as [{source, reason}] — harness runs only';
