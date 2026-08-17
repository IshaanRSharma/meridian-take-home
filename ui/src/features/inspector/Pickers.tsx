/** The fields a description may never fill, chosen from the board instead.
 *
 * `interpret.ALLOWED` decides what a sentence can settle, and everything that
 * has to *point at something else* is deliberately outside it: which cards a
 * step reads, which field identifies a case, what the outcomes are called. The
 * reason is not that a model would find them hard — it is that a wrong answer
 * there resolves, lints clean and tests the wrong thing, which looks exactly
 * like a right one.
 *
 * So they are picked, and the list comes from the board. That is what makes a
 * reference impossible to invent rather than merely discouraged.
 *
 * `criteria` and `fills` are the two this deliberately does not attempt. A
 * criterion is a sentence with holes — *for every line item on the invoice, all
 * of these must be true* — and a row of three dropdowns for `op`/`operator`/
 * `operand` rebuilds SQL in a form, which is the failure the brief names. It
 * needs the sentence builder, and that is its own unit.
 */
import { useState } from 'react';
import { Check, Plus, X } from 'lucide-react';
import type { Board, Config, PrimitiveType } from '@/lib/api';
import { Button, cx, inputStyles } from '@/components/ui';
import FieldsEditor from './Fields';

/** Every addressable field on the board, as `entity.path`.
 *
 * Walks one level into an array of objects, because that is where the
 * interesting references live — `line_items[].hts_number` is the thing a check
 * tests, and the array itself is not. */
export function fieldPaths(board: Board): { entity: string; path: string; label: string }[] {
  const out: { entity: string; path: string; label: string }[] = [];
  for (const card of board.primitives) {
    if (card.primitive_type !== 'entity') continue;
    const fields = card.config.fields;
    if (!fields || typeof fields !== 'object') continue;
    for (const [name, shape] of Object.entries(fields as Record<string, unknown>)) {
      const spec = shape as { type?: unknown; items?: { properties?: Record<string, unknown> } };
      const nested = spec.items?.properties;
      if (nested) {
        for (const inner of Object.keys(nested)) {
          const path = `${name}[].${inner}`;
          out.push({ entity: card.key, path, label: `${card.key} · ${path}` });
        }
      } else {
        out.push({ entity: card.key, path: name, label: `${card.key} · ${name}` });
      }
    }
  }
  return out;
}

const entitiesOn = (board: Board) =>
  board.primitives
    .filter((card) => card.primitive_type === 'entity')
    .map((card) => ({
      key: card.key,
      name: typeof card.config.name === 'string' ? card.config.name : card.key,
    }));

type Ref = { entity: string; path: string };
const sameRef = (a: Ref, b: Ref) => a.entity === b.entity && a.path === b.path;
const asRefs = (value: unknown): Ref[] =>
  Array.isArray(value)
    ? value.filter((v): v is Ref => Boolean(v) && typeof v === 'object' && 'entity' in v)
    : [];

/** Which pickers a card of this type shows, in the order somebody fills them. */
const SHOWN: Record<PrimitiveType, string[]> = {
  event: ['captures', 'correlation_key', 'outcomes'],
  action: ['inputs', 'payload_fields', 'produces', 'idempotency_key', 'is_terminal', 'outcomes'],
  check: ['inputs', 'scope', 'quantifier', 'outcomes', 'evidence'],
  entity: ['fields', 'cardinality'],
};

export default function Pickers({
  board,
  card,
  onChange,
  busy,
}: {
  board: Board;
  card: { key: string; primitive_type: PrimitiveType; config: Config };
  onChange: (patch: Config) => void;
  busy: boolean;
}) {
  const shown = SHOWN[card.primitive_type];
  if (shown.length === 0) return null;

  const paths = fieldPaths(board);
  const entities = entitiesOn(board);
  const c = card.config;

  return (
    <section className="border-t border-(--color-line) px-5 py-4">
      <p className="font-mono text-[10px] tracking-wide text-(--color-ink-faint) uppercase">
        Chosen from the board
      </p>
      <p className="mt-1 mb-3 text-[11.5px] leading-relaxed text-(--color-ink-faint)">
        These point at other cards, so they are picked rather than written — a wrong answer here
        would resolve and quietly test the wrong thing.
      </p>

      <div className={cx('space-y-3.5', busy && 'pointer-events-none opacity-50')}>
        {shown.includes('fields') && (
          <FieldsEditor value={c.fields} onChange={(fields) => onChange({ fields })} />
        )}

        {shown.includes('cardinality') && (
          <OneOf
            label="How many turn up"
            options={[
              { value: 'one', label: 'one per case' },
              { value: 'many', label: 'several per case' },
              { value: 'one_per', label: 'one for each row on another card' },
            ]}
            chosen={
              (c.cardinality as { kind?: string } | null)?.kind ?? null
            }
            onChange={(kind) => onChange({ cardinality: kind ? { kind } : null })}
          />
        )}

        {shown.includes('captures') && (
          <EntityList
            label="What arrives with it"
            entities={entities}
            chosen={(c.captures as string[]) ?? []}
            onChange={(captures) => onChange({ captures })}
          />
        )}

        {shown.includes('inputs') && (
          <EntityList
            label="What it reads"
            entities={entities}
            chosen={(c.inputs as string[]) ?? []}
            onChange={(inputs) => onChange({ inputs })}
          />
        )}

        {shown.includes('correlation_key') && (
          <OneField
            label="What to file it under"
            hint="The field that says which case this belongs to — a container, an order, an application."
            paths={paths}
            chosen={(c.correlation_key as Ref | null) ?? null}
            onChange={(correlation_key) => onChange({ correlation_key })}
          />
        )}

        {shown.includes('produces') && (
          <OneOf
            label="What it produces"
            options={entities.map((e) => ({ value: e.key, label: e.name }))}
            chosen={(c.produces as string | null) ?? null}
            onChange={(produces) => onChange({ produces })}
          />
        )}

        {shown.includes('payload_fields') && (
          <ManyFields
            label="What the message says"
            paths={paths}
            chosen={asRefs(c.payload_fields)}
            onChange={(payload_fields) => onChange({ payload_fields })}
          />
        )}

        {shown.includes('evidence') && (
          <ManyFields
            label="What it reports as proof"
            paths={paths}
            chosen={asRefs(c.evidence)}
            onChange={(evidence) => onChange({ evidence })}
          />
        )}

        {shown.includes('scope') && (
          <OneOf
            label="What it looks at each time"
            options={[
              { value: 'per_case', label: 'the whole case' },
              { value: 'per_line_item', label: 'each line item' },
              { value: 'per_entity', label: 'each thing it reads' },
            ]}
            chosen={(c.scope as string | null) ?? null}
            onChange={(scope) => onChange({ scope })}
          />
        )}

        {shown.includes('quantifier') && (
          <OneOf
            label="How many must hold"
            options={[
              { value: 'all', label: 'all of them' },
              { value: 'any', label: 'any one of them' },
            ]}
            chosen={(c.quantifier as string | null) ?? null}
            onChange={(quantifier) => onChange({ quantifier })}
          />
        )}

        {shown.includes('outcomes') && (
          <Outcomes
            chosen={(c.outcomes as { name: string }[]) ?? []}
            onChange={(outcomes) => onChange({ outcomes })}
          />
        )}

        {shown.includes('idempotency_key') && (
          <Line
            label="What stops it happening twice"
            hint="A value that is the same every time this runs for the same case — usually an identifier off the document."
            value={(c.idempotency_key as string) ?? ''}
            onChange={(idempotency_key) => onChange({ idempotency_key: idempotency_key || null })}
          />
        )}

        {shown.includes('is_terminal') && (
          <label className="flex cursor-pointer items-center gap-2 text-[12.5px] text-(--color-ink)">
            <input
              type="checkbox"
              checked={c.is_terminal === true}
              onChange={(e) => onChange({ is_terminal: e.target.checked })}
              className="size-3.5 accent-(--color-accent)"
            />
            The process ends here
          </label>
        )}
      </div>
    </section>
  );
}

// ── controls ─────────────────────────────────────────────────────────────────

function Label({ children, hint }: { children: string; hint?: string }) {
  return (
    <>
      <span className="block text-[12px] font-medium text-(--color-ink)">{children}</span>
      {hint && (
        <span className="mt-0.5 mb-1 block text-[11px] leading-snug text-(--color-ink-faint)">
          {hint}
        </span>
      )}
    </>
  );
}

function EntityList({
  label,
  entities,
  chosen,
  onChange,
}: {
  label: string;
  entities: { key: string; name: string }[];
  chosen: string[];
  onChange: (next: string[]) => void;
}) {
  if (entities.length === 0) {
    return (
      <div>
        <Label>{label}</Label>
        <p className="text-[11.5px] text-(--color-ink-faint) italic">
          No things on the board yet — drag one on first.
        </p>
      </div>
    );
  }
  return (
    <div>
      <Label>{label}</Label>
      <div className="mt-1 flex flex-wrap gap-1.5">
        {entities.map((entity) => {
          const on = chosen.includes(entity.key);
          return (
            <button
              key={entity.key}
              onClick={() =>
                onChange(on ? chosen.filter((k) => k !== entity.key) : [...chosen, entity.key])
              }
              className={cx(
                'flex items-center gap-1.5 rounded-md border px-2 py-1 text-[11.5px] transition-colors',
                on
                  ? 'border-(--color-accent) bg-(--color-accent-wash) text-(--color-ink)'
                  : 'border-(--color-line-soft) text-(--color-ink-dim) hover:border-(--color-ink-faint)',
              )}
            >
              {on && <Check size={11} className="text-(--color-accent)" />}
              {entity.name}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function OneField({
  label,
  hint,
  paths,
  chosen,
  onChange,
}: {
  label: string;
  hint?: string;
  paths: { entity: string; path: string; label: string }[];
  chosen: Ref | null;
  onChange: (next: Ref | null) => void;
}) {
  const value = chosen ? `${chosen.entity}::${chosen.path}` : '';
  return (
    <div>
      <Label hint={hint}>{label}</Label>
      <select
        value={value}
        onChange={(e) => {
          if (!e.target.value) return onChange(null);
          const [entity, path] = e.target.value.split('::');
          onChange({ entity: entity!, path: path! });
        }}
        className={cx(inputStyles, 'mt-1')}
      >
        <option value="">— not chosen —</option>
        {paths.map((p) => (
          <option key={p.label} value={`${p.entity}::${p.path}`}>
            {p.label}
          </option>
        ))}
      </select>
    </div>
  );
}

function ManyFields({
  label,
  paths,
  chosen,
  onChange,
}: {
  label: string;
  paths: { entity: string; path: string; label: string }[];
  chosen: Ref[];
  onChange: (next: Ref[]) => void;
}) {
  const [adding, setAdding] = useState('');
  return (
    <div>
      <Label>{label}</Label>
      {chosen.length > 0 && (
        <ul className="mt-1 mb-1.5 flex flex-wrap gap-1.5">
          {chosen.map((ref) => (
            <li
              key={`${ref.entity}.${ref.path}`}
              className="flex items-center gap-1.5 rounded-md border border-(--color-line-soft) px-2 py-1 font-mono text-[11px] text-(--color-ink-dim)"
            >
              {ref.entity}.{ref.path}
              <button
                onClick={() => onChange(chosen.filter((r) => !sameRef(r, ref)))}
                className="text-(--color-ink-faint) hover:text-(--color-ink)"
              >
                <X size={11} />
              </button>
            </li>
          ))}
        </ul>
      )}
      <select
        value={adding}
        onChange={(e) => {
          const [entity, path] = e.target.value.split('::');
          if (entity && path && !chosen.some((r) => sameRef(r, { entity, path }))) {
            onChange([...chosen, { entity, path }]);
          }
          setAdding('');
        }}
        className={cx(inputStyles, 'mt-1')}
      >
        <option value="">+ add a field…</option>
        {paths.map((p) => (
          <option key={p.label} value={`${p.entity}::${p.path}`}>
            {p.label}
          </option>
        ))}
      </select>
    </div>
  );
}

function OneOf({
  label,
  options,
  chosen,
  onChange,
}: {
  label: string;
  options: { value: string; label: string }[];
  chosen: string | null;
  onChange: (next: string | null) => void;
}) {
  return (
    <div>
      <Label>{label}</Label>
      <select
        value={chosen ?? ''}
        onChange={(e) => onChange(e.target.value || null)}
        className={cx(inputStyles, 'mt-1')}
      >
        <option value="">— not chosen —</option>
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </div>
  );
}

function Line({
  label,
  hint,
  value,
  onChange,
}: {
  label: string;
  hint?: string;
  value: string;
  onChange: (next: string) => void;
}) {
  const [draft, setDraft] = useState(value);
  return (
    <div>
      <Label hint={hint}>{label}</Label>
      <input
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => draft !== value && onChange(draft.trim())}
        placeholder="e.g. invoice_no"
        className={cx(inputStyles, 'mt-1')}
      />
    </div>
  );
}

/** The ways a step can come out. Each one becomes a handle on the card, so
 *  adding a row here draws a port somebody has to connect. */
function Outcomes({
  chosen,
  onChange,
}: {
  chosen: { name: string }[];
  onChange: (next: { name: string }[]) => void;
}) {
  const [draft, setDraft] = useState('');
  const add = () => {
    const name = draft.trim().toLowerCase().replace(/\s+/g, '_');
    if (!name || chosen.some((o) => o.name === name)) return;
    onChange([...chosen, { name }]);
    setDraft('');
  };
  return (
    <div>
      <Label hint="Each one becomes a handle on the card that needs a line out.">
        The ways it can come out
      </Label>
      {chosen.length > 0 && (
        <ul className="mt-1 mb-1.5 space-y-1">
          {chosen.map((outcome) => (
            <li
              key={outcome.name}
              className="flex items-center gap-2 rounded-md border border-(--color-line-soft) px-2 py-1"
            >
              <span className="flex-1 font-mono text-[11.5px] text-(--color-ink-dim)">
                {outcome.name}
              </span>
              <button
                onClick={() => onChange(chosen.filter((o) => o.name !== outcome.name))}
                className="text-(--color-ink-faint) hover:text-(--color-ink)"
              >
                <X size={12} />
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="mt-1 flex gap-2">
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && add()}
          placeholder="e.g. missing information"
          className={inputStyles}
        />
        <Button size="sm" onClick={add} disabled={!draft.trim()}>
          <Plus size={12} /> Add
        </Button>
      </div>
    </div>
  );
}
