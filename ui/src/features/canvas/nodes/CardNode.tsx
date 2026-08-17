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
 * **Entities carry no handles at all.** They are referenced, never traversed —
 * a Check names them in `inputs` — so drawing a connectable port on one would
 * invite somebody to draw a transition into a thing, which the edge table has
 * no way to mean.
 */
import { memo } from 'react';
import { Handle, Position, type NodeProps } from '@xyflow/react';
import { Boxes, CircleDot, GitBranch, Zap } from 'lucide-react';
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
}

const KIND = {
  event: { icon: Zap, tone: 'text-[#0f766e]', edge: 'before:bg-[#0f766e]', label: 'Event' },
  action: { icon: CircleDot, tone: 'text-[#000000]', edge: 'before:bg-[#000000]', label: 'Action' },
  check: { icon: GitBranch, tone: 'text-[#52525b]', edge: 'before:bg-[#52525b]', label: 'Check' },
  entity: { icon: Boxes, tone: 'text-[#71717a]', edge: 'before:bg-[#000000]', label: 'Thing' },
} as const;

function CardNodeImpl({ data, selected }: NodeProps) {
  const card = data as CardData;
  const kind = KIND[card.primitiveType];
  const Icon = kind.icon;
  const isEntity = card.primitiveType === 'entity';
  const name = typeof card.config.name === 'string' ? card.config.name : null;

  return (
    <div
      className={cx(
        'relative w-[218px] overflow-hidden rounded-lg border text-left transition-colors',
        // The coloured spine, 2px on the left. Enough to read the type at a
        // glance without colouring the whole card, which would make a board of
        // eleven cards read as a chart.
        'before:absolute before:inset-y-0 before:left-0 before:w-[2px] before:content-[""]',
        kind.edge,
        isEntity
          ? 'border-dashed border-[#a1a1aa] bg-[#fafafa]'
          : 'border-(--color-line) bg-(--color-surface)',
        selected
          ? 'border-(--color-accent) ring-1 ring-(--color-accent)'
          : 'hover:border-(--color-line-strong)',
        card.worst === 'blocking' && !selected && 'border-[#000000]',
      )}
    >
      {/* Steps take an incoming transition; a thing never does. */}
      {!isEntity && <Handle type="target" position={Position.Left} />}

      <div className="px-3 py-2.5 pl-3.5">
        <div className="flex items-center gap-1.5">
          <Icon size={12} className={kind.tone} />
          <span
            className={cx(
              'font-mono text-[10px] tracking-wide uppercase',
              kind.tone,
              'opacity-80',
            )}
          >
            {kind.label}
          </span>

          {card.isTrigger && (
            <span
              title="Nothing leads here, so this is what starts the process"
              className="rounded border border-(--color-line-strong) px-1 font-mono text-[9px] text-(--color-ink-faint)"
            >
              START
            </span>
          )}
          {card.isTerminal && (
            <span
              title="A named end state"
              className="rounded border border-(--color-line-strong) px-1 font-mono text-[9px] text-(--color-ink-faint)"
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
                  card.worst === 'blocking' ? 'text-(--color-blocking)' : 'text-(--color-ink-faint)',
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

      {/* One row per outcome, one handle each, positioned to line up with it.
          A person reads the ways this can come out and sees which have a line. */}
      {card.outcomes.length > 0 && (
        <div className="border-t border-(--color-line) bg-[#fafafa]">
          {card.outcomes.map((outcome) => (
            <div
              key={outcome.name}
              className="relative flex h-[22px] items-center justify-between gap-2 px-3 pl-3.5"
            >
              <span
                className={cx(
                  'truncate font-mono text-[10px]',
                  outcome.wired ? 'text-(--color-ink-dim)' : 'text-(--color-blocking)',
                )}
              >
                {outcome.name}
              </span>
              {!outcome.wired && (
                <span
                  title="This outcome has no line out"
                  className="shrink-0 font-mono text-[9px] text-(--color-blocking)"
                >
                  no path
                </span>
              )}
              <Handle
                type="source"
                position={Position.Right}
                id={outcome.name}
                style={{
                  top: '50%',
                  background: outcome.wired ? undefined : 'var(--color-blocking)',
                }}
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
