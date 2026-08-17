-- Three corrections found while planning the reviewer, all of them things the
-- schema promised and could not deliver.


-- ─────────────────────────────────────────────────────────────────────────
-- 1. A question about the whole process could not be stored
-- ─────────────────────────────────────────────────────────────────────────
--
-- `comment_anchors` carried two constraints that contradict each other:
--
--   primary key (thread_id, anchor_kind, anchor_key)       anchor_key NOT NULL
--   check ((anchor_kind = 'board') = (anchor_key is null))  anchor_key MUST be null
--
-- Every primary-key column is implicitly not-null, so a board anchor needed a
-- null and was forbidden one. "Is there another way information reaches this
-- process?" had nowhere to go.
--
-- The fix is the shape `assertions` already uses one table over: a surrogate
-- key, with the real business key expressed as a unique constraint. NULLS NOT
-- DISTINCT because here a null is a meaning — *this anchor is the board* —
-- rather than an unknown, so two of them are a duplicate rather than two
-- different rows.

alter table comment_anchors drop constraint comment_anchors_pkey;

-- Dropping the key does NOT drop the not-null it imposed — Postgres keeps the
-- column attribute after the constraint that created it is gone. Without this
-- line the whole migration applies cleanly and changes nothing.
alter table comment_anchors alter column anchor_key drop not null;

alter table comment_anchors
  add column if not exists id uuid not null default gen_random_uuid();

alter table comment_anchors add constraint comment_anchors_pkey primary key (id);

alter table comment_anchors add constraint comment_anchors_one_per_element
  unique nulls not distinct (thread_id, anchor_kind, anchor_key);


-- ─────────────────────────────────────────────────────────────────────────
-- 2. Why a question is being asked had nowhere to live
-- ─────────────────────────────────────────────────────────────────────────
--
-- `Thread.reason` exists on the domain type and in the reviewer payload, and
-- was dropped on the way to the database. It is kept apart from the question on
-- purpose: "What happens after the COA is reported?" reads very differently
-- beside "the SOP ends at reporting and never says what closes the shipment",
-- and a process owner answers the second one better.

alter table threads add column if not exists reason text;


-- ─────────────────────────────────────────────────────────────────────────
-- 3. A settled statement is superseded, never rewritten
-- ─────────────────────────────────────────────────────────────────────────
--
-- Only `specs` was enforced immutable. Assertions were append-only by
-- convention, and the convention is load-bearing: a frozen spec inlines the
-- statements behind it, so editing one in place would leave a checksummed
-- artifact quoting text that no longer exists anywhere, with nothing to notice.
--
-- Not a blanket ban, because superseding *is* an update — round two answers by
-- inserting a new row and pointing the old one at it. `thread_id` moves too,
-- when a conversation is deleted and the statement outlives it. Everything that
-- carries meaning is frozen; the two pointers are not.
--
-- DELETE stays legal: `board_id` cascades, and dropping a board should take its
-- assertions with it.

create or replace function forbid_assertion_rewrite() returns trigger
language plpgsql as $$
begin
  if new.board_id       is distinct from old.board_id
  or new.anchor_kind    is distinct from old.anchor_kind
  or new.anchor_key     is distinct from old.anchor_key
  or new.kind           is distinct from old.kind
  or new.statement      is distinct from old.statement
  or new.constraint_json is distinct from old.constraint_json
  or new.round          is distinct from old.round
  or new.created_at     is distinct from old.created_at then
    raise exception 'an assertion is superseded, never rewritten';
  end if;
  return new;
end $$;

drop trigger if exists assertions_supersede_only on assertions;

create trigger assertions_supersede_only before update on assertions
  for each row execute function forbid_assertion_rewrite();
