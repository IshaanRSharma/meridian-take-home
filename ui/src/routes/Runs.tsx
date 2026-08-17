/** What the pipeline did, and whether the answers were right.
 *
 * Two halves, and they are at very different stages, which this screen says
 * rather than hides.
 *
 * **The timeline is real.** Every review round and every freeze writes rows to
 * `events` keyed by `cycle_id`, so one end-to-end run reads back in order.
 *
 * **The scoreboard is expected-only, for now.** `fixtures/expected/shipments.json`
 * is ground truth supplied with the brief — five shipments, one row each, real
 * values. Nothing has produced an *actual* yet because codegen and the eval
 * sweep are not built. So the actual column is empty and says so. Inventing a
 * plausible number here would be lying about the one thing the product exists
 * to measure.
 */
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api, type CycleEvent, type Evals } from '@/lib/api';
import { Badge, Dot, Empty, Panel, PanelHeader, Problem, Spinner, cx } from '@/components/ui';

export default function Runs() {
  const [cycle, setCycle] = useState<string | null>(null);

  const events = useQuery({
    queryKey: ['events'],
    queryFn: () => api.observability.events(300),
    refetchInterval: 5_000,
  });
  const evals = useQuery({ queryKey: ['evals'], queryFn: api.observability.evals });

  // Grouped here rather than in SQL: the same rows also render as a flat feed,
  // and a shape forced on the server would rule that out.
  const cycles = groupByCycle(events.data ?? []);
  const shown = cycle ? cycles.find((one) => one.id === cycle) : cycles[0];

  return (
    <div className="mx-auto max-w-6xl px-6 py-8">
      <h1 className="text-[19px] font-semibold tracking-[-0.015em]">Runs</h1>
      <p className="mt-1 text-[13px] text-(--color-ink-dim)">
        What the pipeline did, and how it scored against real shipments.
      </p>

      <div className="mt-6 grid gap-5 lg:grid-cols-[280px_1fr]">
        <Panel className="overflow-hidden">
          <PanelHeader title="Cycles" hint={`${cycles.length}`} />
          {events.isLoading && <Spinner />}
          {events.error && (
            <div className="p-3">
              <Problem
                title="Could not load events"
                body={events.error instanceof Error ? events.error.message : undefined}
              />
            </div>
          )}
          {events.data && cycles.length === 0 && (
            <Empty
              title="Nothing has run yet"
              body="Run a review round or freeze a spec and it will appear here."
            />
          )}
          <ul className="divide-y divide-(--color-line-soft)">
            {cycles.map((one) => (
              <li key={one.id}>
                <button
                  onClick={() => setCycle(one.id)}
                  className={cx(
                    'flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left transition-colors hover:bg-(--color-raised)',
                    shown?.id === one.id && 'bg-(--color-raised)',
                  )}
                >
                  <Dot tone={one.failed ? 'blocking' : one.running ? 'running' : 'ok'} />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-[12.5px] text-(--color-ink)">{one.headline}</p>
                    <p className="font-mono text-[10.5px] text-(--color-ink-faint)">
                      {new Date(one.at).toLocaleTimeString()} · {one.events.length} step
                      {one.events.length === 1 ? '' : 's'}
                    </p>
                  </div>
                </button>
              </li>
            ))}
          </ul>
        </Panel>

        <div className="space-y-5">
          <Panel className="overflow-hidden">
            <PanelHeader
              title="Timeline"
              hint={shown ? shown.id.slice(0, 8) : 'nothing selected'}
            />
            {shown ? (
              <ol className="divide-y divide-(--color-line-soft)">
                {shown.events.map((event) => (
                  <TimelineRow key={event.id} event={event} />
                ))}
              </ol>
            ) : (
              <Empty title="Select a cycle" />
            )}
          </Panel>

          <Panel className="overflow-hidden">
            <PanelHeader
              title="Eval set"
              hint={evals.data ? evals.data.unit : undefined}
              right={
                evals.data && (
                  <Badge tone={evals.data.runs_recorded > 0 ? 'ok' : 'warn'}>
                    {evals.data.runs_recorded > 0
                      ? `${evals.data.runs_recorded} recorded`
                      : 'no build has run'}
                  </Badge>
                )
              }
            />
            {evals.isLoading && <Spinner />}
            {evals.error && (
              <div className="p-3">
                <Problem
                  title="Could not load the eval set"
                  body={evals.error instanceof Error ? evals.error.message : undefined}
                />
              </div>
            )}
            {evals.data && <EvalTable evals={evals.data} />}
          </Panel>
        </div>
      </div>
    </div>
  );
}

/* Phase by brightness, same rule as severity. The two that exist today are the
   two that stand out: review is the accent, compile is white because the freeze
   is the moment that matters most on any timeline. The rest are dim until they
   are built. */
const PHASE_TONE: Record<string, string> = {
  review: 'text-(--color-accent)',
  compile: 'text-(--color-ink)',
  codegen: 'text-(--color-ink-dim)',
  eval: 'text-(--color-ink-dim)',
  repair: 'text-(--color-ink-dim)',
  deploy: 'text-(--color-ink-faint)',
  prod: 'text-(--color-ink-faint)',
};

function TimelineRow({ event }: { event: CycleEvent }) {
  const summary = Object.entries(event.detail ?? {})
    .filter(([key]) => key !== 'board_id')
    .map(([key, value]) => `${key} ${format(value)}`)
    .join('  ·  ');

  return (
    <li className="flex items-start gap-3 px-4 py-2.5">
      <span className="w-[52px] shrink-0 pt-0.5 font-mono text-[10px] text-(--color-ink-faint)">
        {new Date(event.at).toLocaleTimeString([], {
          hour: '2-digit',
          minute: '2-digit',
          second: '2-digit',
        })}
      </span>
      <span
        className={cx(
          'w-[62px] shrink-0 pt-0.5 font-mono text-[10px] uppercase',
          PHASE_TONE[event.phase] ?? 'text-(--color-ink-dim)',
        )}
      >
        {event.phase}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="font-mono text-[11.5px] text-(--color-ink)">{event.kind}</span>
          {event.status !== 'ok' && (
            <span
              className={cx(
                'font-mono text-[10px]',
                event.status === 'failed'
                  ? 'text-(--color-blocking)'
                  : event.status === 'started'
                    ? 'text-(--color-ink-faint)'
                    : 'text-(--color-important)',
              )}
            >
              {event.status}
            </span>
          )}
          {event.primitive_key && (
            <span className="font-mono text-[10px] text-(--color-ink-faint)">
              {event.primitive_key}
            </span>
          )}
          {event.duration_ms !== null && (
            <span className="ml-auto shrink-0 font-mono text-[10px] text-(--color-ink-faint)">
              {event.duration_ms}ms
            </span>
          )}
        </div>
        {summary && (
          <p className="mt-0.5 truncate font-mono text-[10.5px] text-(--color-ink-faint)">
            {summary}
          </p>
        )}
      </div>
    </li>
  );
}

/** The columns the brief's own eval table uses, in its order. */
const COLUMNS: [string, string][] = [
  ['invoices_successful', 'Inv ok'],
  ['invoices_failed', 'Inv fail'],
  ['coa_success', 'CoA ok'],
  ['coa_total', 'CoA total'],
  ['goods_failed', 'Goods fail'],
  ['invoices_mismatched_asn', 'ASN'],
  ['status', 'Status'],
];

function EvalTable({ evals }: { evals: Evals }) {
  return (
    <div className="overflow-x-auto">
      {evals.runs_recorded === 0 && (
        <p className="border-b border-(--color-line) px-4 py-2.5 text-[11.5px] leading-relaxed text-(--color-ink-faint)">
          Ground truth only. No agent has been generated or swept yet, so there is nothing to
          compare against — the actual column fills in once a build runs.
        </p>
      )}
      <table className="w-full text-left">
        <thead>
          <tr className="border-b border-(--color-line)">
            <th className="px-4 py-2 font-mono text-[10px] tracking-wide text-(--color-ink-faint) uppercase">
              Shipment
            </th>
            {COLUMNS.map(([key, label]) => (
              <th
                key={key}
                className="px-3 py-2 font-mono text-[10px] tracking-wide text-(--color-ink-faint) uppercase"
              >
                {label}
              </th>
            ))}
            <th className="px-3 py-2 font-mono text-[10px] tracking-wide text-(--color-ink-faint) uppercase">
              Agent
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-(--color-line-soft)">
          {evals.shipments.map((row) => (
            <tr key={String(row.expected.shipment_no)}>
              <td className="px-4 py-2 font-mono text-[11.5px] text-(--color-ink)">
                {String(row.expected.shipment_no)}
              </td>
              {COLUMNS.map(([key]) => (
                <td key={key} className="px-3 py-2 font-mono text-[11.5px] text-(--color-ink-dim)">
                  {String(row.expected[key] ?? '—')}
                </td>
              ))}
              <td className="px-3 py-2">
                {row.outcome ? (
                  <Badge tone={row.outcome === 'passed' ? 'ok' : 'warn'}>{row.outcome}</Badge>
                ) : (
                  <span className="font-mono text-[11px] text-(--color-ink-faint)">not run</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

interface Cycle {
  id: string;
  at: string;
  headline: string;
  events: CycleEvent[];
  failed: boolean;
  running: boolean;
}

function groupByCycle(events: CycleEvent[]): Cycle[] {
  const grouped = new Map<string, CycleEvent[]>();
  for (const event of events) {
    grouped.set(event.cycle_id, [...(grouped.get(event.cycle_id) ?? []), event]);
  }
  return [...grouped.entries()]
    .map(([id, rows]) => {
      // `recent` returns newest first; a timeline reads oldest first.
      const ordered = [...rows].sort((a, b) => a.id - b.id);
      const first = ordered[0]!;
      const closed = ordered.some(
        (row) => row.kind === first.kind && row.status !== 'started',
      );
      return {
        id,
        at: first.at,
        headline: headlineFor(ordered),
        events: ordered,
        failed: ordered.some((row) => row.status === 'failed'),
        running: !closed,
      };
    })
    .sort((a, b) => b.at.localeCompare(a.at));
}

function headlineFor(events: CycleEvent[]): string {
  const done = events.find((event) => event.status === 'ok' || event.status === 'failed');
  const anchor = done ?? events[0]!;
  const detail = anchor.detail ?? {};
  if (anchor.kind === 'round') {
    return `Review round ${detail.round ?? '?'} · ${detail.asked ?? 0} asked`;
  }
  if (anchor.kind === 'freeze') {
    return `Froze spec v${detail.version ?? '?'}`;
  }
  return `${anchor.phase} · ${anchor.kind}`;
}

function format(value: unknown): string {
  if (typeof value === 'string') return value.length > 22 ? `${value.slice(0, 22)}…` : value;
  return String(value);
}
