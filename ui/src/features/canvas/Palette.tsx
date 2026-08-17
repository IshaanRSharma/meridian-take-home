/** What you can put on the board, labelled by the question it answers.
 *
 * The label is the question, not the type name. "Event" means nothing to
 * somebody who runs a warehouse; *something happens* does. The type name stays
 * underneath in mono because it is what appears in the spec and in generated
 * filenames, and seeing it here is what connects the two.
 *
 * `entity` is called *a thing* rather than *entity* deliberately: the palette
 * label and the type name are different artifacts and need not agree. Tested
 * across five processes, most entities arrive from a lookup rather than as a
 * document, so `document` would have been a lie in the spec — but nobody draws
 * a box and calls it an entity either.
 */
import { useState } from 'react';
import { Boxes, ChevronDown, CircleDot, GitBranch, Plus, Zap } from 'lucide-react';
import type { PrimitiveType, Relation } from '@/lib/api';

const ITEMS: {
  type: PrimitiveType;
  label: string;
  question: string;
  icon: typeof Zap;
  tone: string;
}[] = [
  {
    type: 'event',
    label: 'Something happens',
    question: 'what starts or resumes this',
    icon: Zap,
    tone: 'text-[#0f766e]',
  },
  {
    type: 'action',
    label: 'Something gets done',
    question: 'a step somebody or something performs',
    icon: CircleDot,
    tone: 'text-[#000000]',
  },
  {
    type: 'check',
    label: 'Something is decided',
    question: 'a rule, and the ways it can come out',
    icon: GitBranch,
    tone: 'text-[#52525b]',
  },
  {
    type: 'entity',
    label: 'A thing you look at',
    question: 'a document, a record, a row',
    icon: Boxes,
    tone: 'text-[#71717a]',
  },
];

/** The three kinds of line.
 *
 * A relation is picked before the line is drawn rather than changed after,
 * because there is no endpoint to edit an edge in place — and that is not an
 * oversight. An edge key is what a thread anchors to with no foreign key, so
 * re-minting one to change its relation would silently orphan every comment
 * pinned to it.
 *
 * **`Pass` is a knowingly overloaded label.** `pass` is also an *outcome* name
 * on every Check the seed board draws, and a relation is not an outcome: the
 * relation says whether the process carries on, the outcome says which branch
 * it took. The board already has `normal` edges carrying `missing_information`,
 * so the two are genuinely orthogonal and one word now spans both. `Next` says
 * the same thing without the collision, and is one edit here if the overlap
 * ever bites. */
const LINES: { relation: Relation; label: string; hint: string }[] = [
  { relation: 'normal', label: 'Pass', hint: 'the process carries on to the next step' },
  { relation: 'exception', label: 'Exception', hint: 'something went wrong and this is where it goes' },
  { relation: 'repeat', label: 'Loop', hint: 'comes back round — the shape that proves this is not a one-way flowchart' },
];

export default function Palette({
  pending,
  drawing,
  onDrawing,
}: {
  pending: boolean;
  drawing: Relation;
  onDrawing: (relation: Relation) => void;
}) {
  // Collapsible because it sits on top of the board, and the entity lane is
  // laid out exactly where it covers. A palette you cannot move out of the way
  // is a palette that hides the drawing.
  const [open, setOpen] = useState(true);

  return (
    <div className="absolute top-4 left-4 w-[210px] overflow-hidden rounded-lg border border-(--color-line) bg-(--color-surface)/95 backdrop-blur">
      <button
        onClick={() => setOpen((was) => !was)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-(--color-raised)"
      >
        <Plus size={12} className="text-(--color-ink-faint)" />
        <span className="text-[11.5px] font-semibold text-(--color-ink-dim)">
          Drag onto the board
        </span>
        <ChevronDown
          size={13}
          className={`ml-auto text-(--color-ink-faint) transition-transform ${open ? '' : '-rotate-90'}`}
        />
      </button>
      <div className={open ? 'border-t border-(--color-line) p-1.5' : 'hidden'}>
        {ITEMS.map(({ type, label, question, icon: Icon, tone }) => (
          <div
            key={type}
            draggable={!pending}
            onDragStart={(event) => {
              event.dataTransfer.setData('primitive_type', type);
              event.dataTransfer.effectAllowed = 'move';
            }}
            className="group cursor-grab rounded-md px-2 py-1.5 transition-colors hover:bg-(--color-raised) active:cursor-grabbing"
          >
            <div className="flex items-center gap-2">
              <Icon size={12} className={tone} />
              <span className="text-[12px] text-(--color-ink)">{label}</span>
            </div>
            <p className="mt-0.5 pl-[20px] text-[10.5px] leading-tight text-(--color-ink-faint)">
              {question}
            </p>
          </div>
        ))}

        <div className="mt-1.5 border-t border-(--color-line-soft) px-2 pt-2 pb-1">
          <p className="mb-1.5 text-[10.5px] text-(--color-ink-faint)">
            Then drag between cards. This line is a…
          </p>
          <div className="flex overflow-hidden rounded-md border border-(--color-line)">
            {LINES.map(({ relation, label, hint }) => (
              <button
                key={relation}
                title={hint}
                onClick={() => onDrawing(relation)}
                className={
                  'flex-1 border-r border-(--color-line-soft) px-1 py-1 text-[10px] last:border-r-0 transition-colors ' +
                  (drawing === relation
                    ? 'bg-(--color-ink) text-white'
                    : 'text-(--color-ink-dim) hover:bg-(--color-raised)')
                }
              >
                {label}
              </button>
            ))}
          </div>
          <p className="mt-1.5 text-[10.5px] leading-tight text-(--color-ink-faint)">
            Click a line and press Delete to remove it.
          </p>
        </div>
      </div>
    </div>
  );
}
