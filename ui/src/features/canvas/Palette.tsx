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
import { Boxes, CircleDot, GitBranch, Zap } from 'lucide-react';
import type { PrimitiveType } from '@/lib/api';

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
    tone: 'text-[#7ba0ff]',
  },
  {
    type: 'action',
    label: 'Something gets done',
    question: 'a step somebody or something performs',
    icon: CircleDot,
    tone: 'text-[#c9c9d1]',
  },
  {
    type: 'check',
    label: 'Something is decided',
    question: 'a rule, and the ways it can come out',
    icon: GitBranch,
    tone: 'text-[#e0b45c]',
  },
  {
    type: 'entity',
    label: 'A thing you look at',
    question: 'a document, a record, a row',
    icon: Boxes,
    tone: 'text-[#9d8cd4]',
  },
];

export default function Palette({ pending }: { pending: boolean }) {
  return (
    <div className="absolute top-4 left-4 w-[210px] overflow-hidden rounded-lg border border-(--color-line) bg-(--color-surface)/95 backdrop-blur">
      <div className="border-b border-(--color-line) px-3 py-2">
        <p className="text-[11.5px] font-semibold text-(--color-ink-dim)">Drag onto the board</p>
      </div>
      <div className="p-1.5">
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
      </div>
    </div>
  );
}
