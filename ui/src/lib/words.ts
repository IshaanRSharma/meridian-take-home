/** Our vocabulary, translated into theirs. One table, one direction.
 *
 * The whiteboard belongs to the person who runs the process. Everything they
 * read there has to be a sentence they would say — and the schema is full of
 * words that are exactly right for a compiler and mean nothing to a warehouse
 * supervisor: `idempotency_key`, `correlation_key`, `on_missing_input`,
 * `missing_context`.
 *
 * **This is a display layer and only a display layer.** Nothing here changes
 * what is stored, sent or checksummed. `criteria` is still `criteria` in the
 * config, in the spec and in generated code; it is only ever *shown* as "what
 * it tests". That split is deliberate: the schema being the single source of
 * truth is what makes lint, codegen and the spec agree, and a rename to suit a
 * panel would put a second name on the same thing.
 *
 * **The spec view is exempt.** It is the FDE's surface — bindings, conformance,
 * provenance — and there the field name *is* the useful word, because the
 * reader is about to open a generated file that uses it.
 *
 * Anything missing from a table falls back to the raw key with underscores
 * turned to spaces. A word we forgot to translate should look slightly wrong
 * rather than crash a panel.
 */

/** Schema field → what it is, in the owner's words.
 *
 * Phrased as a noun a sentence could be built around ("what it tests"), not as
 * a title ("Test Criteria"), because these appear inline after a card name:
 * *"Under fifty pounds · what it tests"*.
 */
const FIELDS: Record<string, string> = {
  // shared
  name: 'its name',
  instructions: 'your description',
  timing: 'when it happens',
  deadline: 'the time limit',
  outcomes: 'the ways it can come out',
  inputs: 'what it reads',
  condition: 'when this line is taken',

  // event
  correlation_key: 'what to file it under',
  match_condition: 'how to recognise it',
  captures: 'what arrives with it',
  channel: 'how it reaches you',

  // action
  effect: 'what the step does',
  recipients: 'who hears about it',
  payload_fields: 'what the message says',
  idempotency_key: 'what stops it happening twice',
  system: 'which system',
  timeout: 'how long to wait',
  on_timeout: 'what happens if nobody answers',
  on_failure: 'what happens if it fails',
  performed_by: 'who decides',
  produces: 'what it produces',
  is_terminal: 'whether it ends here',

  // check
  criteria: 'what it tests',
  scope: 'what it looks at each time',
  quantifier: 'whether all or any must hold',
  evidence: 'what it reports as proof',
  on_missing_input: 'what to do if it has not arrived',
  fills: 'what it fills in',
  left: 'which field it tests',
  right: 'what it compares against',
  operator: 'how the two are compared',
  value: 'what it compares against',
  field: 'which field it compares against',
  statement: 'what the test is',
  reads: 'which fields it uses',

  // entity
  identified_by: 'how to recognise it',
  fields: 'what to read off it',
  cardinality: 'how many turn up',
  per: 'what they are counted against',
  sample_path: 'a sample document',
  schedule: 'when it runs',

  // structural — these come from board rules rather than a card's own config
  incoming: 'what leads here',
  outgoing: 'where it goes next',
  from_key: 'where the line starts',
  to_key: 'where the line ends',
  on_outcomes: 'which outcome it carries',
  // Board-level: the whole drawing has no starting event.
  events: 'what starts it',
};

/** Reviewer category → why it is being asked.
 *
 * The taxonomy is real and it ranks the questions, but it is ours. A process
 * owner does not need to know a question came from `undefined_timing`; they
 * need to know it is about timing.
 */
const CATEGORIES: Record<string, string> = {
  missing_path: 'no path',
  ambiguous_rule: 'unclear rule',
  missing_context: 'not stated',
  undefined_exception: 'unhandled failure',
  undefined_timing: 'timing',
  redundancy: 'possibly redundant',
  spec_gap: 'needs a decision',
};

const plain = (key: string) => key.replace(/_/g, ' ');

/** What this field is, in the owner's words. */
export const fieldWord = (field: string): string => {
  if (FIELDS[field]) return FIELDS[field];
  // `cardinality.per` and friends: translate the leaf, keep the sense.
  const leaf = field.split('.').pop() ?? field;
  return FIELDS[leaf] ?? plain(field);
};

/** Why this question is being asked, in the owner's words. */
export const categoryWord = (category: string): string =>
  CATEGORIES[category] ?? plain(category);
