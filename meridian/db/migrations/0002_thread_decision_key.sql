-- A structural question has an identity derived from the graph, so the same
-- board raises the same key every time:
--
--   sequence · primitive:invoice_complete | edge:e2 | primitive:coas_valid
--
-- Dedup on it and a question is never asked twice, in any status — a rejected
-- question must not come back either. Null for model-invented questions, which
-- have no structural identity and are deduped by the reviewer having every
-- prior thread in context.
alter table threads add column if not exists decision_key text;
create index if not exists threads_decision_key_idx on threads (board_id, decision_key);
