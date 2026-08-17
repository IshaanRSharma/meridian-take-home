/** The notation key.
 *
 * This is a notation, and a notation has a key — sheet music has one, a circuit
 * diagram has one. The brief grades "simple enough for a non-technical user to
 * pick up without training", and a legend is the cheapest possible answer to
 * that.
 *
 * Drawn with the same components the canvas uses, at the same sizes, so the key
 * cannot drift from the thing it explains: a dashed red line here is the same
 * dashed red line out there.
 */
import { useState } from 'react';
import { ChevronDown, HelpCircle } from 'lucide-react';
import { cx } from '@/components/ui';

export default function Legend({
  showDataLinks,
  onToggleDataLinks,
}: {
  showDataLinks: boolean;
  onToggleDataLinks: () => void;
}) {
  const [open, setOpen] = useState(false);

  return (
    <div className="absolute top-4 right-4 w-[268px]">
      <button
        onClick={() => setOpen((was) => !was)}
        className={cx(
          'flex w-full items-center gap-2 rounded-lg border border-(--color-line) bg-(--color-surface)/95 px-3 py-2 backdrop-blur',
          'text-[12px] text-(--color-ink-dim) transition-colors hover:text-(--color-ink)',
          open && 'rounded-b-none',
        )}
      >
        <HelpCircle size={13} />
        How to read this board
        <ChevronDown
          size={13}
          className={cx('ml-auto transition-transform', open && 'rotate-180')}
        />
      </button>

      {open && (
        <div className="rise max-h-[calc(100vh-140px)] overflow-y-auto rounded-b-lg border border-t-0 border-(--color-line) bg-(--color-surface)/95 backdrop-blur">
          <Section title="Cards">
            <Row swatch={<Spine tone="bg-[#4d7cfe]" />} name="Event">
              Something arrives or a clock goes off. Marked <Chip>START</Chip> when nothing
              leads to it.
            </Row>
            <Row swatch={<Spine tone="bg-[#7c7c88]" />} name="Action">
              A step that gets done — tell someone, write it down, look it up.
            </Row>
            <Row swatch={<Spine tone="bg-[#d9a441]" />} name="Check">
              A rule. Its outcomes are listed underneath, one line out per outcome.
            </Row>
            <Row swatch={<Spine tone="bg-[#8b7ac4]" dashed />} name="Thing">
              A document or record the steps read. It sits in its own column and is never
              connected to — steps <em>reference</em> it.
            </Row>
          </Section>

          <Section title="Lines">
            <Row swatch={<Line />} name="Normal">
              The process moves on. The label is which outcome it carries.
            </Row>
            <Row swatch={<Line tone="#e5484d" dashed />} name="Exception">
              Something went wrong and this is where it goes.
            </Row>
            <Row swatch={<Line curved />} name="Repeat">
              Loops back. Routes around the graph, which is what shows this is not a
              one-way flowchart.
            </Row>
            <Row swatch={<Line tone="#4a4356" dotted />} name="Reads">
              Drawn, never stored — a step naming a thing it reads. You cannot draw one;
              it comes from the card.
            </Row>
          </Section>

          <Section title="Marks">
            <Row swatch={<span className="size-1.5 rounded-full bg-(--color-accent)" />} name="Blue dot">
              An open question is anchored on this card.
            </Row>
            <Row swatch={<span className="font-mono text-[10px] text-(--color-blocking)">3</span>} name="Red count">
              Blanks that stop this being a workable process.
            </Row>
            <Row
              swatch={<span className="font-mono text-[9px] text-(--color-blocking)">no path</span>}
              name="No path"
            >
              This outcome is declared and has no line out.
            </Row>
          </Section>

          <div className="border-t border-(--color-line) p-3">
            <label className="flex cursor-pointer items-center gap-2 text-[11.5px] text-(--color-ink-dim)">
              <input
                type="checkbox"
                checked={showDataLinks}
                onChange={onToggleDataLinks}
                className="size-3 accent-(--color-accent)"
              />
              Show which steps read which things
            </label>
          </div>
        </div>
      )}
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="border-t border-(--color-line) first:border-t-0">
      <p className="px-3 pt-2.5 pb-1 font-mono text-[10px] tracking-wide text-(--color-ink-faint) uppercase">
        {title}
      </p>
      <div className="pb-1">{children}</div>
    </div>
  );
}

function Row({
  swatch,
  name,
  children,
}: {
  swatch: React.ReactNode;
  name: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex gap-2.5 px-3 py-1.5">
      <div className="flex w-6 shrink-0 items-center justify-center pt-0.5">{swatch}</div>
      <div className="min-w-0">
        <p className="text-[11.5px] font-medium text-(--color-ink)">{name}</p>
        <p className="text-[11px] leading-snug text-(--color-ink-faint)">{children}</p>
      </div>
    </div>
  );
}

/** A miniature of the card, so the key cannot drift from the thing it explains.
 *
 * The spine is a child element rather than a `before:` pseudo, because Tailwind
 * scans source text for class names — a composed `before:${tone}` is invisible
 * to it and would silently produce a colourless swatch. */
function Spine({ tone, dashed }: { tone: string; dashed?: boolean }) {
  return (
    <span
      className={cx(
        'relative block h-5 w-4 overflow-hidden rounded-[3px] border bg-(--color-surface)',
        dashed ? 'border-dashed border-[#3a3547]' : 'border-(--color-line)',
      )}
    >
      <span className={cx('absolute inset-y-0 left-0 w-[2px]', tone)} />
    </span>
  );
}

function Line({
  tone = '#52525b',
  dashed,
  dotted,
  curved,
}: {
  tone?: string;
  dashed?: boolean;
  dotted?: boolean;
  curved?: boolean;
}) {
  return (
    <svg width="22" height="12" viewBox="0 0 22 12" fill="none">
      <path
        d={curved ? 'M20 9 C 14 9, 14 2, 2 2' : 'M1 6 H21'}
        stroke={tone}
        strokeWidth="1.4"
        strokeDasharray={dashed ? '4 3' : dotted ? '2 3' : undefined}
      />
    </svg>
  );
}

function Chip({ children }: { children: React.ReactNode }) {
  return (
    <span className="rounded border border-(--color-line-strong) px-1 font-mono text-[9px] text-(--color-ink-faint)">
      {children}
    </span>
  );
}
