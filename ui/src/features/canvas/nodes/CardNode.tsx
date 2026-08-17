/** One card on the canvas, for all four kinds.
 *
 * One component rather than four, because the four differ in a colour, an icon
 * and which handles they carry — not in structure. Four files would be four
 * copies of the same layout drifting apart.
 *
 * **One handle per outcome.** This is the detail that makes an unwired outcome
 * visible on the canvas rather than only in a lint panel: three outcomes draw
 * three handles, and wiring two leaves the third sitting there unconnected.
 *
 * **Nothing here may clip.** React Flow positions a handle with
 * `translate(50%, -50%)`, so half of it deliberately overhangs the card edge —
 * and that overhang is most of the grab target. An `overflow-hidden` on this
 * card (which is how the coloured spine used to be trimmed to the rounded
 * corner) cut every handle in half and made edges practically undrawable. The
 * spine is a 2px left border now: the border radius trims it for free, and
 * nothing gets clipped.
 *
 * **Entities carry no handles at all.** They are referenced, never traversed —
 * a Check names them in `inputs` — so drawing a connectable port on one would
 * invite somebody to draw a transition into a thing, which the edge table has
 * no way to mean.
 */
import { memo } from 'react';
import { Handle, Position, type NodeProps } from '@xyflow/react';
import { Boxes, CircleDot, GitBranch, X, Zap } from 'lucide-react';
import type { Config, PrimitiveType, Severity } from '@/lib/api';
import { cx } from '@/components/ui';

export interface CardData extends Record<string, unknown> {
  primitiveKey: string;
  primitiveType: PrimitiveType;
  config: Config;
  outcomes: { name: string; wired: boolean }[];
  /** Worst severity among this card's blanks, or null when it has none. */
  worst: Severity | null;
  findingCount: number;
  /** This card is anchored by at least one open question. */
  hasOpenThread: boolean;
  isTrigger: boolean;
  isTerminal: boolean;
  onRemove?: (key: string) => void;
}

const KIND = {
  event: { icon: Zap, tone: 'text-(--color-accent)', spine: '#0f766e', label: 'Event' },
  action: { icon: CircleDot, tone: 'text-(--color-ink)', spine: '#000000', label: 'Action' },
  check: { icon: GitBranch, tone: 'text-[#52525b]', spine: '#52525b', label: 'Check' },
  entity: { icon: Boxes, tone: 'text-[#71717a]', spine: '#a1a1aa', label: 'Thing' },
} as const;

function CardNodeImpl({ data, selected }: NodeProps) {
  const card = data as CardData;
  const kind = KIND[card.primitiveType];
  const Icon = kind.icon;
  const isEntity = card.primitiveType === 'entity';
  const name = typeof card.config.name === 'string' ? card.config.name : null;

  return (
    <div
      style={{ borderLeftColor: kind.spine }}
      className={cx(
        // No overflow-hidden. See the module note — it halves every handle.
        'group relative w-[218px] rounded-lg border border-l-2 text-left transition-colors',
        isEntity ? 'border-dashed bg-[#fafafa]' : 'bg-(--color-surface)',
        selected
          ? 'border-(--color-accent) ring-1 ring-(--color-accent)'
          : 'border-(--color-line-soft) hover:border-(--color-ink-faint)',
        card.worst === 'blocking' && !selected && 'border-(--color-ink)',
      )}
    >
      {/* Steps take an incoming transition; a thing never does. */}
      {!isEntity && <Handle type="target" position={Position.Left} />}

      {card.onRemove && (
        <button
          title="Remove this card"
          onClick={(event) => {
            // Without this the click also selects the card and opens the editor
            // for something that is on its way out.
            event.stopPropagation();
            card.onRemove?.(card.primitiveKey);
          }}
          className={cx(
            'absolute -top-2 -right-2 z-10 grid size-5 place-items-center rounded-full',
            'border border-(--color-line) bg-(--color-surface) text-(--color-ink-faint)',
            'opacity-0 transition-opacity group-hover:opacity-100 hover:text-(--color-ink)',
            'focus-visible:opacity-100',
          )}
        >
          <X size={11} />
        </button>
      )}

      <div className="px-3 py-2.5">
        <div className="flex items-center gap-1.5">
          <Icon size={12} className={kind.tone} />
          <span className={cx('font-mono text-[10px] tracking-wide uppercase', kind.tone)}>
            {kind.label}
          </span>

          {card.isTrigger && (
            <span
              title="Nothing leads here, so this is what starts the process"
              className="rounded border border-(--color-line-soft) px-1 font-mono text-[9px] text-(--color-ink-faint)"
            >
              START
            </span>
          )}
          {card.isTerminal && (
            <span
              title="A named end state"
              className="rounded border border-(--color-line-soft) px-1 font-mono text-[9px] text-(--color-ink-faint)"
            >
              END
            </span>
          )}

          <span className="ml-auto flex items-center gap-1.5">
            {card.hasOpenThread && (
              <span
                title="An open question is anchored here"
                className="size-1.5 rounded-full bg-(--color-accent)"
              />
            )}
            {card.findingCount > 0 && (
              <span
                title={`${card.findingCount} thing${card.findingCount === 1 ? '' : 's'} not filled in`}
                className={cx(
                  'font-mono text-[10px]',
                  card.worst === 'blocking'
                    ? 'font-semibold text-(--color-ink)'
                    : 'text-(--color-ink-faint)',
                )}
              >
                {card.findingCount}
              </span>
            )}
          </span>
        </div>

        <p
          className={cx(
            'mt-1.5 text-[12.5px] leading-snug',
            name ? 'text-(--color-ink)' : 'text-(--color-ink-faint) italic',
          )}
        >
          {name ?? 'Untitled — click to describe'}
        </p>

        <p className="mt-1 truncate font-mono text-[10px] text-(--color-ink-faint)">
          {card.primitiveKey}
        </p>
      </div>

      {/* One row per outcome, one handle each, lined up with it. A person reads
          the ways this can come out and sees which have a line. */}
      {card.outcomes.length > 0 && (
        <div className="rounded-b-[7px] border-t border-(--color-line-soft) bg-[#fafafa]">
          {card.outcomes.map((outcome) => (
            <div
              key={outcome.name}
              className="relative flex h-[22px] items-center justify-between gap-2 px-3"
            >
              <span
                className={cx(
                  'truncate font-mono text-[10px]',
                  outcome.wired ? 'text-(--color-ink-dim)' : 'font-semibold text-(--color-ink)',
                )}
              >
                {outcome.name}
              </span>
              {!outcome.wired && (
                <span
                  title="This outcome has no line out"
                  className="shrink-0 font-mono text-[9px] text-(--color-ink)"
                >
                  no path
                </span>
              )}
              <Handle
                type="source"
                position={Position.Right}
                id={outcome.name}
                style={{ top: '50%', background: outcome.wired ? undefined : '#000000' }}
              />
            </div>
          ))}
        </div>
      )}

      {/* A step with no declared outcomes still needs one way out. */}
      {!isEntity && card.outcomes.length === 0 && !card.isTerminal && (
        <Handle type="source" position={Position.Right} />
      )}
    </div>
  );
}

export const CardNode = memo(CardNodeImpl);
