/** What is still missing, and how much each one matters.
 *
 * Not an error list. `blocking` means the drawing does not work as a process —
 * a reference that does not resolve, an outcome with no line, a step it stops
 * at without saying so. Everything else is a question for the review, and a
 * missing *value* is never blocking: who receives a report is knowledge only
 * the process owner has, so it is asked, never refused.
 *
 * That split is why this is a panel and not a modal. Nothing blocks while
 * somebody is drawing; blocking bites once, at submit.
 */
import { ChevronRight } from 'lucide-react';
import type { Finding, Severity } from '@/lib/api';
import { Empty, SeverityBadge, cx } from '@/components/ui';

const ORDER: Severity[] = ['blocking', 'important', 'minor'];

export default function LintPanel({
  findings,
  onGoTo,
  selected,
}: {
  findings: Finding[];
  onGoTo: (key: string) => void;
  selected: string | null;
}) {
  if (findings.length === 0) {
    return (
      <Empty
        title="Nothing missing"
        body="Every card says what it needs to. This board is ready for a review round."
      />
    );
  }

  const sorted = [...findings].sort(
    (a, b) => ORDER.indexOf(a.severity) - ORDER.indexOf(b.severity),
  );

  return (
    <ul className="divide-y divide-(--color-line-soft)">
      {sorted.map((finding) => {
        const key = finding.anchor.includes(':')
          ? finding.anchor.split(':')[1]!
          : finding.anchor;
        const isBoard = finding.anchor === 'board' || !finding.anchor.includes(':');
        return (
          <li key={`${finding.anchor}.${finding.field}`}>
            <button
              disabled={isBoard}
              onClick={() => onGoTo(key)}
              className={cx(
                'group flex w-full items-start gap-2.5 px-4 py-2.5 text-left transition-colors',
                !isBoard && 'hover:bg-(--color-raised)',
                selected === key && 'bg-(--color-raised)',
                isBoard && 'cursor-default',
              )}
            >
              <SeverityBadge severity={finding.severity} />
              <div className="min-w-0 flex-1">
                <p className="text-[12.5px] leading-snug text-(--color-ink)">{finding.reason}</p>
                <p className="mt-0.5 truncate font-mono text-[10.5px] text-(--color-ink-faint)">
                  {isBoard ? 'board' : key} · {finding.field}
                </p>
              </div>
              {!isBoard && (
                <ChevronRight
                  size={14}
                  className="mt-0.5 shrink-0 text-transparent transition-colors group-hover:text-(--color-ink-faint)"
                />
              )}
            </button>
          </li>
        );
      })}
    </ul>
  );
}
