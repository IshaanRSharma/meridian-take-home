/** What to read off a thing.
 *
 * Without this an entity cannot be authored at all — every reference on the
 * board resolves against these names, so a board with no way to add a field is
 * a board where half the pickers are permanently empty.
 *
 * **Everything is nullable, and that is deliberate.** A field a Check requires
 * has to be able to come back absent, or the Check can never detect the failure
 * it exists to detect: if extraction refused to return a line item missing its
 * HTS number, "this line has no HTS number" would be unrepresentable. The one
 * exception is a field named as the thing's own identifier, which is not
 * something this editor knows about — so it errs toward nullable everywhere and
 * lets lint ask.
 *
 * Two shapes, because the documents have two. A plain field is one value on the
 * page. A **list of rows** is the repeating block — line items on an invoice —
 * and references address into it as `line_items[].hts_number`, which is what a
 * per-line check iterates. Nesting stops there on purpose: a list inside a list
 * has not come up, and a general schema editor is a JSON editor with extra
 * steps, which is the thing a non-technical surface exists to avoid.
 */
import { useState } from 'react';
import { Plus, X } from 'lucide-react';
import { Button, cx, inputStyles } from '@/components/ui';

/** JSON Schema fragment as the backend stores it. */
type Shape = { type: unknown; items?: { type: string; properties?: Record<string, Shape> } };
type Fields = Record<string, Shape>;

const KINDS = [
  { value: 'text', label: 'Text' },
  { value: 'number', label: 'A number' },
  { value: 'yesno', label: 'Yes or no' },
  { value: 'rows', label: 'A list of rows' },
] as const;
type Kind = (typeof KINDS)[number]['value'];

const shapeFor = (kind: Kind): Shape =>
  kind === 'rows'
    ? { type: 'array', items: { type: 'object', properties: {} } }
    : kind === 'number'
      ? { type: ['number', 'null'] }
      : kind === 'yesno'
        ? { type: ['boolean', 'null'] }
        : { type: ['string', 'null'] };

function kindOf(shape: Shape): Kind {
  if (shape.type === 'array') return 'rows';
  const types = Array.isArray(shape.type) ? shape.type : [shape.type];
  if (types.includes('number') || types.includes('integer')) return 'number';
  if (types.includes('boolean')) return 'yesno';
  return 'text';
}

/** Names become field paths, so they follow the same rule as a card key. */
const slug = (raw: string) =>
  raw
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '');

export default function FieldsEditor({
  value,
  onChange,
}: {
  value: unknown;
  onChange: (fields: Fields) => void;
}) {
  const fields: Fields = value && typeof value === 'object' ? (value as Fields) : {};
  const entries = Object.entries(fields);

  const put = (name: string, shape: Shape) => onChange({ ...fields, [name]: shape });
  const drop = (name: string) => {
    const next = { ...fields };
    delete next[name];
    onChange(next);
  };

  return (
    <div>
      <span className="block text-[12px] font-medium text-(--color-ink)">What to read off it</span>
      <span className="mt-0.5 mb-1.5 block text-[11px] leading-snug text-(--color-ink-faint)">
        Every rule on the board points at one of these, so anything a step needs has to be here.
      </span>

      {entries.length > 0 && (
        <ul className="mb-2 space-y-1.5">
          {entries.map(([name, shape]) => (
            <li key={name} className="rounded-md border border-(--color-line-soft) px-2 py-1.5">
              <div className="flex items-center gap-2">
                <span className="flex-1 truncate font-mono text-[11.5px] text-(--color-ink)">
                  {name}
                </span>
                <span className="text-[10.5px] text-(--color-ink-faint)">
                  {KINDS.find((k) => k.value === kindOf(shape))?.label}
                </span>
                <button
                  onClick={() => drop(name)}
                  className="text-(--color-ink-faint) hover:text-(--color-ink)"
                >
                  <X size={12} />
                </button>
              </div>

              {kindOf(shape) === 'rows' && (
                <SubFields
                  properties={shape.items?.properties ?? {}}
                  onChange={(properties) =>
                    put(name, { type: 'array', items: { type: 'object', properties } })
                  }
                  parent={name}
                />
              )}
            </li>
          ))}
        </ul>
      )}

      <AddField taken={Object.keys(fields)} onAdd={(name, kind) => put(name, shapeFor(kind))} />
    </div>
  );
}

function SubFields({
  properties,
  onChange,
  parent,
}: {
  properties: Record<string, Shape>;
  onChange: (next: Record<string, Shape>) => void;
  parent: string;
}) {
  const names = Object.keys(properties);
  return (
    <div className="mt-1.5 border-l border-(--color-line-soft) pl-2.5">
      {names.length > 0 && (
        <ul className="mb-1.5 space-y-0.5">
          {names.map((name) => (
            <li key={name} className="flex items-center gap-2">
              <span className="flex-1 truncate font-mono text-[10.5px] text-(--color-ink-dim)">
                {parent}[].{name}
              </span>
              <button
                onClick={() => {
                  const next = { ...properties };
                  delete next[name];
                  onChange(next);
                }}
                className="text-(--color-ink-faint) hover:text-(--color-ink)"
              >
                <X size={10} />
              </button>
            </li>
          ))}
        </ul>
      )}
      {/* No `rows` inside rows — a list within a list has not come up, and the
          reference syntax has no way to address it. */}
      <AddField
        small
        taken={names}
        kinds={KINDS.filter((k) => k.value !== 'rows')}
        onAdd={(name, kind) => onChange({ ...properties, [name]: shapeFor(kind) })}
      />
    </div>
  );
}

function AddField({
  taken,
  onAdd,
  small,
  kinds = KINDS,
}: {
  taken: string[];
  onAdd: (name: string, kind: Kind) => void;
  small?: boolean;
  kinds?: readonly { value: Kind; label: string }[];
}) {
  const [name, setName] = useState('');
  const [kind, setKind] = useState<Kind>('text');

  const add = () => {
    const key = slug(name);
    if (!key || taken.includes(key)) return;
    onAdd(key, kind);
    setName('');
    setKind('text');
  };

  return (
    <div className="flex gap-1.5">
      <input
        value={name}
        onChange={(e) => setName(e.target.value)}
        onKeyDown={(e) => e.key === 'Enter' && add()}
        placeholder={small ? 'another column…' : 'e.g. container number'}
        className={cx(inputStyles, small && 'py-1 text-[11.5px]')}
      />
      <select
        value={kind}
        onChange={(e) => setKind(e.target.value as Kind)}
        className={cx(inputStyles, 'w-[128px] shrink-0', small && 'py-1 text-[11.5px]')}
      >
        {kinds.map((k) => (
          <option key={k.value} value={k.value}>
            {k.label}
          </option>
        ))}
      </select>
      <Button size="sm" onClick={add} disabled={!slug(name)}>
        <Plus size={12} />
      </Button>
    </div>
  );
}
