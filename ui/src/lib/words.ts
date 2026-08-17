/** Labels for stored config, in the owner's words. One table, one consumer.
 *
 * This started much larger and most of it was wrong. The reviewer already
 * produces language a process owner can read — a lint finding's `reason` is a
 * finished sentence, and a semantic question is written by the model for the
 * person answering. Where our vocabulary was showing through, the honest fix
 * was almost always to **delete a redundant display**, not to translate it:
 *
 *   the lint panel printed the schema field beside the reason, and no card has
 *   two findings sharing a reason — so the field said nothing the sentence had
 *   not already said
 *
 *   the question badge printed the reviewer's own taxonomy, and the question
 *   states its own subject
 *
 *   the editor listed the fields it cannot fill, where one sentence says the
 *   same thing better
 *
 * What survives is the one place a label is genuinely needed: the editor's
 * readout of what a description actually settled. That is a key/value view of
 * stored config, and a key/value view needs a key.
 *
 * **This is where it should stop growing, and it is not where it belongs.** A
 * second table of names is a second source of truth that drifts silently — add
 * a field to `CheckConfig` and this falls back to the raw key with no warning.
 * The right home is a `title=` beside the field in the Pydantic model, carried
 * through the OpenAPI document into `schema.d.ts`, so one name ships with the
 * thing it names. That is a change in `domain/primitives.py` and worth making.
 *
 * The spec view never uses this. It is the FDE's surface, and there the field
 * name *is* the useful word — the reader is about to open a generated file
 * that uses it.
 */

/** Config key → what it holds, in the owner's words.
 *
 * Only keys that appear as top-level config on a card. Anything that reaches a
 * person some other way — a finding, a question — already arrives as a
 * sentence and must not be looked up here.
 */
const FIELDS: Record<string, string> = {
  name: 'its name',
  instructions: 'your description',

  // event
  channel: 'how it reaches you',
  match_condition: 'how to recognise it',
  correlation_key: 'what to file it under',
  captures: 'what arrives with it',
  timing: 'when it happens',
  schedule: 'when it runs',

  // action
  effect: 'what the step does',
  system: 'which system',
  recipients: 'who hears about it',
  payload_fields: 'what the message says',
  idempotency_key: 'what stops it happening twice',
  timeout: 'how long to wait',
  on_failure: 'what happens if it fails',
  on_timeout: 'what happens if nobody answers',
  performed_by: 'who decides',
  produces: 'what it produces',
  is_terminal: 'whether it ends here',

  // check
  criteria: 'what it tests',
  inputs: 'what it reads',
  outcomes: 'the ways it can come out',
  scope: 'what it looks at each time',
  quantifier: 'whether all or any must hold',
  evidence: 'what it reports as proof',
  on_missing_input: 'what to do if it has not arrived',
  fills: 'what it fills in',

  // entity
  identified_by: 'how to recognise it',
  fields: 'what to read off it',
  cardinality: 'how many turn up',
  sample_path: 'a sample document',
};

/** What this config key holds. Falls back to the key with its underscores
 *  opened out — a word we forgot should look slightly wrong, not crash. */
export const fieldWord = (field: string): string =>
  FIELDS[field] ?? field.replace(/_/g, ' ');
