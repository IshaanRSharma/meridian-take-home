/** A transition, with its outcome on it and a way to remove it.
 *
 * Custom rather than the built-in `smoothstep` for one reason: a line needs a
 * visible control. Delete-on-keypress works and is undiscoverable — somebody
 * has to already know the line can be selected before the keyboard is any use,
 * and nothing on the canvas says so.
 *
 * The button only appears on hover or when the edge is selected. A × sitting
 * permanently on every line turns a process diagram into a toolbar, and the
 * thing being read here is the shape.
 *
 * **Removal still goes through the same change handler as the keyboard**, not
 * through a direct call, so there is one path to the API and one place that can
 * be wrong.
 */
import { memo, useState } from 'react';
import {
  BaseEdge,
  EdgeLabelRenderer,
  getSmoothStepPath,
  useReactFlow,
  type EdgeProps,
} from '@xyflow/react';
import { X } from 'lucide-react';
import type { Relation } from '@/lib/api';
import { cx } from '@/components/ui';

export interface FlowEdgeData extends Record<string, unknown> {
  relation: Relation;
  outcomes: string[];
}

const STROKE: Record<Relation, string> = {
  normal: '#71717a',
  exception: '#000000',
  repeat: '#71717a',
};

function FlowEdgeImpl({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  data,
  selected,
}: EdgeProps) {
  const { deleteElements } = useReactFlow();
  const [over, setOver] = useState(false);
  const { relation = 'normal', outcomes = [] } = (data ?? {}) as FlowEdgeData;

  const [path, labelX, labelY] = getSmoothStepPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
    borderRadius: 10,
    // A repeat routes around the graph rather than through it, which is the one
    // relation that shows on sight that this is not a one-way flowchart.
    offset: relation === 'repeat' ? 60 : 20,
  });

  const shown = over || selected;

  return (
    <>
      <BaseEdge
        id={id}
        path={path}
        style={{
          stroke: selected ? '#0f766e' : STROKE[relation],
          strokeWidth: selected ? 2 : 1.4,
          strokeDasharray: relation === 'exception' ? '5 3' : undefined,
        }}
      />
      {/* A transparent overlay, wide enough to hover. The drawn line is 1.4px
          and nobody can reliably point at that. */}
      <path
        d={path}
        fill="none"
        strokeWidth={18}
        stroke="transparent"
        onMouseEnter={() => setOver(true)}
        onMouseLeave={() => setOver(false)}
        style={{ pointerEvents: 'stroke' }}
      />

      <EdgeLabelRenderer>
        <div
          onMouseEnter={() => setOver(true)}
          onMouseLeave={() => setOver(false)}
          style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)` }}
          className="pointer-events-auto absolute flex items-center gap-1"
        >
          {outcomes.length > 0 && (
            <span
              className={cx(
                'rounded border px-1 py-0.5 font-mono text-[9.5px] whitespace-nowrap',
                'border-(--color-line-soft) bg-(--color-surface)',
                selected ? 'text-(--color-accent)' : 'text-(--color-ink-faint)',
              )}
            >
              {outcomes.join(' / ')}
            </span>
          )}
          {shown && (
            <button
              title="Remove this line"
              onClick={(event) => {
                event.stopPropagation();
                // Through the change handler, so the keyboard and the button
                // reach the API by exactly one path.
                deleteElements({ edges: [{ id }] });
              }}
              className={cx(
                'grid size-[18px] place-items-center rounded-full border',
                'border-(--color-line) bg-(--color-surface) text-(--color-ink-faint)',
                'transition-colors hover:text-(--color-ink)',
              )}
            >
              <X size={10} />
            </button>
          )}
        </div>
      </EdgeLabelRenderer>
    </>
  );
}

export const FlowEdge = memo(FlowEdgeImpl);
