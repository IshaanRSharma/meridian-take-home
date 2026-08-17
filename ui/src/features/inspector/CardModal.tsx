/** Say what a card does, in your own words.
 *
 * The one LLM call on this screen. What comes back is what was **stored**, not
 * what was sent — the merge revalidates server-side, so the panel re-renders
 * from the truth and an optimistic form cannot diverge from it.
 *
 * Three things this deliberately does not do:
 *
 * **It never blocks.** A value the model gets wrong is dropped rather than
 * raised, so a bad field becomes a blank, a blank is a lint finding, and a lint
 * finding is already a question somebody was going to be asked. There is no
 * error dialog on the fill path at all.
 *
 * **It only fills blanks**, unless overwrite is asked for explicitly.
 * Overwriting what somebody typed is the point where an accelerator becomes an
 * authority.
 *
 * **It cannot set anything that has to resolve against the board** — criteria,
 * inputs, outcomes, field paths. Those are absent from the schemas the model
 * sees, so a plausible-but-wrong reference is unrepresentable rather than
 * filtered. Those fields are shown here as what they are: still to be chosen.
 */
import { useEffect, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Sparkles, Trash2, X } from 'lucide-react';
import { api, type Board, type Finding, type Primitive } from '@/lib/api';
import { Badge, Button, Problem, SeverityBadge, cx } from '@/components/ui';

/** What the fill stage may draft, per card type. Mirrors `authoring.ALLOWED`;
 *  shown so somebody can see why a field stayed blank. */
const PICKED_NOT_TYPED: Record<string, string[]> = {
  event: ['correlation_key', 'captures', 'outcomes'],
  action: ['inputs', 'produces', 'payload_fields', 'outcomes', 'idempotency_key', 'is_terminal'],
  check: ['criteria', 'inputs', 'outcomes', 'evidence', 'scope', 'quantifier', 'fills'],
  entity: ['fields', 'cardinality.per'],
};

export default function CardModal({
  board,
  card,
  findings,
  onClose,
}: {
  board: Board;
  card: Primitive;
  findings: Finding[];
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [said, setSaid] = useState('');
  const [overwrite, setOverwrite] = useState(false);

  const existing = typeof card.config.instructions === 'string' ? card.config.instructions : '';

  useEffect(() => {
    const escape = (event: KeyboardEvent) => event.key === 'Escape' && onClose();
    window.addEventListener('keydown', escape);
    return () => window.removeEventListener('keydown', escape);
  }, [onClose]);

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['board', board.id] });
    queryClient.invalidateQueries({ queryKey: ['lint', board.id] });
  };

  const describe = useMutation({
    mutationFn: () => api.boards.describeCard(board.id, card.key, said.trim(), overwrite),
    onSuccess: () => {
      setSaid('');
      refresh();
    },
  });

  const remove = useMutation({
    mutationFn: () => api.boards.removeCard(board.id, card.key),
    onSuccess: () => {
      refresh();
      onClose();
    },
  });

  const filled = Object.entries(card.config).filter(
    ([key, value]) =>
      key !== 'instructions' &&
      value !== null &&
      value !== undefined &&
      !(Array.isArray(value) && value.length === 0),
  );
  const pickers = PICKED_NOT_TYPED[card.primitive_type] ?? [];

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-6 backdrop-blur-[2px]"
      onClick={onClose}
    >
      <div
        onClick={(event) => event.stopPropagation()}
        className="rise flex max-h-[86vh] w-full max-w-[620px] flex-col overflow-hidden rounded-lg border border-(--color-line) bg-(--color-surface) shadow-2xl shadow-black/50"
      >
        <header className="flex shrink-0 items-start justify-between gap-4 border-b border-(--color-line) px-5 py-3.5">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <Badge mono>{card.primitive_type}</Badge>
              <span className="truncate font-mono text-[11.5px] text-(--color-ink-faint)">
                {card.key}
              </span>
            </div>
            <h2 className="mt-1.5 truncate text-[15px] font-semibold">
              {typeof card.config.name === 'string' ? card.config.name : 'Untitled card'}
            </h2>
          </div>
          <button
            onClick={onClose}
            className="grid size-7 shrink-0 place-items-center rounded-md text-(--color-ink-faint) transition-colors hover:bg-(--color-raised) hover:text-(--color-ink)"
          >
            <X size={15} />
          </button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto">
          <section className="px-5 py-4">
            <label className="block">
              <span className="text-[12.5px] font-medium text-(--color-ink)">
                Describe this step in your own words
              </span>
              <span className="mt-1 block text-[11.5px] leading-relaxed text-(--color-ink-faint)">
                Write it the way you would explain it to a new colleague. Whatever you type is
                kept exactly as written — the fields below are filled in from it where they can
                be.
              </span>
              <textarea
                autoFocus
                rows={4}
                value={said}
                onChange={(event) => setSaid(event.target.value)}
                onKeyDown={(event) => {
                  if ((event.metaKey || event.ctrlKey) && event.key === 'Enter' && said.trim()) {
                    describe.mutate();
                  }
                }}
                placeholder="We email the receiving supervisor with the batch and invoice numbers. If they've not come back in two days it goes up to the ops manager."
                className={cx(
                  'mt-2.5 w-full resize-y rounded-md border border-(--color-line) bg-(--color-ground) px-3 py-2.5',
                  'text-[13px] leading-relaxed text-(--color-ink) placeholder:text-[#4a4a52]',
                  'focus:border-(--color-accent-dim) focus:ring-1 focus:ring-(--color-accent-dim) focus:outline-none',
                )}
              />
            </label>

            <div className="mt-2.5 flex items-center justify-between gap-3">
              <label className="flex cursor-pointer items-center gap-2 text-[11.5px] text-(--color-ink-faint)">
                <input
                  type="checkbox"
                  checked={overwrite}
                  onChange={(event) => setOverwrite(event.target.checked)}
                  className="size-3 accent-(--color-accent)"
                />
                Replace what I said before
              </label>
              <Button
                variant="primary"
                size="sm"
                busy={describe.isPending}
                disabled={!said.trim()}
                onClick={() => describe.mutate()}
              >
                <Sparkles size={13} /> Fill from this
              </Button>
            </div>

            {describe.error && (
              <div className="mt-3">
                <Problem
                  title="Could not read that"
                  body={
                    describe.error instanceof Error ? describe.error.message : String(describe.error)
                  }
                />
              </div>
            )}
          </section>

          {existing && (
            <section className="border-t border-(--color-line) px-5 py-4">
              <p className="font-mono text-[10px] tracking-wide text-(--color-ink-faint) uppercase">
                In your words
              </p>
              <p className="mt-2 text-[12.5px] leading-relaxed whitespace-pre-wrap text-(--color-ink-dim)">
                {existing}
              </p>
            </section>
          )}

          <section className="border-t border-(--color-line) px-5 py-4">
            <p className="font-mono text-[10px] tracking-wide text-(--color-ink-faint) uppercase">
              What that settled
            </p>
            {filled.length === 0 ? (
              <p className="mt-2 text-[12px] text-(--color-ink-faint) italic">
                Nothing yet.
              </p>
            ) : (
              <dl className="mt-2 space-y-1.5">
                {filled.map(([key, value]) => (
                  <div key={key} className="flex gap-3 text-[12px]">
                    <dt className="w-[130px] shrink-0 font-mono text-[11px] text-(--color-ink-faint)">
                      {key}
                    </dt>
                    <dd className="min-w-0 flex-1 break-words text-(--color-ink)">
                      {render(value)}
                    </dd>
                  </div>
                ))}
              </dl>
            )}
          </section>

          {findings.length > 0 && (
            <section className="border-t border-(--color-line) px-5 py-4">
              <p className="font-mono text-[10px] tracking-wide text-(--color-ink-faint) uppercase">
                Still missing
              </p>
              <ul className="mt-2 space-y-2">
                {findings.map((finding) => (
                  <li key={`${finding.anchor}.${finding.field}`} className="flex items-start gap-2.5">
                    <SeverityBadge severity={finding.severity} />
                    <div className="min-w-0">
                      <p className="font-mono text-[11px] text-(--color-ink-dim)">{finding.field}</p>
                      <p className="text-[12px] leading-snug text-(--color-ink)">{finding.reason}</p>
                    </div>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {pickers.length > 0 && (
            <section className="border-t border-(--color-line) px-5 py-4">
              <p className="font-mono text-[10px] tracking-wide text-(--color-ink-faint) uppercase">
                Chosen, not written
              </p>
              <p className="mt-1.5 text-[11.5px] leading-relaxed text-(--color-ink-faint)">
                These point at other things on the board, so they are picked from a list rather
                than read out of a sentence — a wrong answer here would look exactly like a right
                one.
              </p>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {pickers.map((field) => (
                  <span
                    key={field}
                    className="rounded border border-(--color-line) px-1.5 py-0.5 font-mono text-[10.5px] text-(--color-ink-faint)"
                  >
                    {field}
                  </span>
                ))}
              </div>
            </section>
          )}
        </div>

        <footer className="flex shrink-0 items-center justify-between border-t border-(--color-line) px-5 py-3">
          <Button
            variant="danger"
            size="sm"
            busy={remove.isPending}
            onClick={() => remove.mutate()}
          >
            <Trash2 size={13} /> Remove card
          </Button>
          <span className="font-mono text-[10.5px] text-(--color-ink-faint)">⌘↵ to fill</span>
        </footer>
      </div>
    </div>
  );
}

function render(value: unknown): string {
  if (typeof value === 'string') return value;
  if (typeof value === 'boolean' || typeof value === 'number') return String(value);
  return JSON.stringify(value);
}
