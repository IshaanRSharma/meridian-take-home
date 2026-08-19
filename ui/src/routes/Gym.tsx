/** How many eval cases the agent gets right, per turn of the loop.
 *
 * That is the whole screen, and deliberately. A repair either turned a shipment
 * green or it did not; every other number is a way of not saying that. The
 * earlier version of this page carried four score tiles, a per-column heatmap,
 * a reward curve, an attempts table and a beliefs list, and none of them
 * answered *is it working* faster than a count of passing cases does.
 *
 * Two columns carry the argument. **Written by** separates the generated build
 * from the repairs, because "build" stops being the right word once a repair
 * has rewritten the code. **Passing** is counted per shipment rather than per
 * column, because a column total can move while no shipment changes state.
 *
 * The grid says WHICH shipments, since a count that sits at 5 across two turns
 * is a different story if two cases swapped places underneath it.
 *
 * The screen reads and never writes, except for the trigger — which asks the
 * agent to process mail that arrived rather than changing anything about it.
 */
import { useEffect, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';

import { api, type Gym, type TriggerRun } from '@/lib/api';
import { Badge, Button, Empty, Panel, PanelHeader, Problem, Spinner } from '@/components/ui';

export default function GymPage() {
  const boards = useQuery({ queryKey: ['boards'], queryFn: api.boards.list });
  const [picked, setPicked] = useState<string>('');
  const boardId = picked || boards.data?.[0]?.id || '';

  const gym = useQuery({
    queryKey: ['gym', boardId],
    queryFn: () => api.observability.gym(boardId),
    enabled: Boolean(boardId),
  });

  if (boards.isLoading) return <Spinner label="loading boards" />;
  if (boards.isError) return <Problem title="could not load boards" />;

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-5 px-6 py-8">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-[22px] font-semibold text-(--color-ink)">Gym</h1>
          <p className="mt-1 text-[13px] text-(--color-ink-dim)">
            Eval cases passing, per iteration of the repair loop.
          </p>
        </div>
        <select
          value={boardId}
          onChange={(event) => setPicked(event.target.value)}
          className="rounded-md border border-(--color-line) bg-(--color-surface) px-3 py-2 text-[13px]"
        >
          {boards.data?.map((board) => (
            <option key={board.id} value={board.id}>
              {board.name}
            </option>
          ))}
        </select>
      </header>

      {gym.isLoading && <Spinner label="loading" />}
      {gym.isError && <Problem title="nothing frozen or swept on this board yet" />}
      {gym.data && (
        <>
          <Progress gym={gym.data} />
          <TriggerPanel boardId={boardId} />
        </>
      )}
    </div>
  );
}

function Progress({ gym }: { gym: Gym }) {
  const turns = gym.iterations ?? [];
  if (turns.length === 0) return <Empty title="nothing swept yet" />;

  const shipments = Array.from(new Set(turns.flatMap((turn) => turn.cases))).sort();
  const widest = Math.max(...turns.map((turn) => turn.total), 1);

  return (
    <Panel>
      <PanelHeader
        title="Cases passing"
        hint={`${turns.length} iteration${turns.length === 1 ? '' : 's'}`}
      />
      <div className="overflow-x-auto">
        <table className="w-full text-[13px]">
          <thead>
            <tr className="border-b border-(--color-line) text-(--color-ink-faint)">
              <th className="px-4 py-2 text-left font-mono text-[10px] tracking-wide uppercase">
                Iter
              </th>
              <th className="px-2 py-2 text-left font-mono text-[10px] tracking-wide uppercase">
                Written by
              </th>
              <th className="px-2 py-2 text-left font-mono text-[10px] tracking-wide uppercase">
                Passing
              </th>
              {shipments.map((shipment) => (
                <th
                  key={shipment}
                  title={shipment}
                  className="px-1.5 py-2 text-center font-mono text-[9.5px]"
                >
                  {shipment.slice(0, 4)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-(--color-line-soft)">
            {turns.map((turn) => (
              <tr key={turn.iteration}>
                <td className="px-4 py-3 font-mono">{turn.iteration}</td>
                <td className="px-2 py-3 text-[12.5px] text-(--color-ink-dim)">
                  {turn.by === 'codegen' ? 'generated' : turn.by}
                </td>
                <td className="px-2 py-3 whitespace-nowrap">
                  <span className="font-mono">
                    {turn.passing}/{turn.total}
                  </span>
                  <span
                    className="ml-2 inline-block h-1.5 rounded-full bg-(--color-ok) align-middle"
                    style={{ width: `${Math.round((turn.passing / widest) * 80)}px` }}
                  />
                </td>
                {shipments.map((shipment) => (
                  <td key={shipment} className="px-1.5 py-3 text-center font-mono text-[11px]">
                    {!turn.cases.includes(shipment) ? (
                      <span className="text-(--color-ink-faint)">·</span>
                    ) : turn.green.includes(shipment) ? (
                      <span className="text-(--color-ok)">ok</span>
                    ) : (
                      <span className="text-(--color-ink-faint)">—</span>
                    )}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {gym.blocked.length > 0 && (
        <p className="border-t border-(--color-line) px-4 py-2.5 text-[12px] text-(--color-ink-faint)">
          A case passes when every column a card fills is right.{' '}
          <span className="font-mono">{gym.blocked.join(', ')}</span>{' '}
          {gym.blocked.length === 1 ? 'is' : 'are'} on no card, so nothing can produce{' '}
          {gym.blocked.length === 1 ? 'it' : 'them'}.
        </p>
      )}
    </Panel>
  );
}

function TriggerPanel({ boardId }: { boardId: string }) {
  // How many runs there were when Run was pressed. The trigger answers 202 the
  // instant it is asked and does the work afterwards, so refetching on success
  // reads the mailbox as it was BEFORE the run and then never looks again —
  // which is a button that appears to do nothing. Polling until the count moves
  // past this mark is what turns the answer into something you can watch.
  const [awaiting, setAwaiting] = useState<number | null>(null);

  const triggered = useQuery({
    queryKey: ['triggered', boardId],
    queryFn: () => api.observability.triggered(boardId),
    enabled: Boolean(boardId),
    refetchInterval: awaiting === null ? false : 3000,
  });
  const fire = useMutation({
    mutationFn: () => api.pipeline.trigger(boardId),
    onSuccess: () => setAwaiting(triggered.data?.runs.length ?? 0),
  });

  const runs = triggered.data?.runs ?? [];
  const running = awaiting !== null && runs.length <= awaiting;

  // Stopping the poll is an effect, not a render-time assignment: setting state
  // while rendering re-enters the render and React warns about it.
  useEffect(() => {
    if (awaiting !== null && runs.length > awaiting) setAwaiting(null);
  }, [awaiting, runs.length]);

  return (
    <Panel>
      <PanelHeader
        title="Trigger"
        hint="live mail, no expected answer"
        right={
          <div className="flex items-center gap-2.5">
            {running && (
              <span className="font-mono text-[11.5px] text-(--color-ink-faint)">
                reading the mailbox…
              </span>
            )}
            <Button onClick={() => fire.mutate()} disabled={fire.isPending || running}>
              {running ? 'running' : 'Run'}
            </Button>
          </div>
        }
      />
      {runs.length === 0 ? (
        <Empty title={running ? 'reading the mailbox…' : 'nothing triggered yet'} />
      ) : (
        <ul className="divide-y divide-(--color-line-soft)">
          {runs.map((run) => (
            <TriggerRow key={run.run_id} run={run} />
          ))}
        </ul>
      )}
    </Panel>
  );
}

function TriggerRow({ run }: { run: TriggerRun }) {
  const unkeyed = run.state === 'needs_correlation';
  const row = Object.entries(run.row ?? {}).filter(([key]) => !key.startsWith('_'));

  return (
    <li className="px-4 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-[13px]">
          {unkeyed ? 'No shipment number' : run.shipment}
        </span>
        {unkeyed ? (
          <Badge tone="warn">not processed</Badge>
        ) : (
          <Badge tone={run.trustworthy ? 'ok' : 'warn'}>
            {run.trustworthy ? 'ok' : 'check it'}
          </Badge>
        )}
      </div>

      {unkeyed && run.finding && (
        <p className="mt-1.5 text-[12px] leading-relaxed text-(--color-ink-faint)">
          {run.finding.subject}
          <br />
          {run.finding.reason}
        </p>
      )}

      {!unkeyed && row.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 font-mono text-[12px] text-(--color-ink-dim)">
          {row.map(([key, value]) => (
            <span key={key}>
              {key.replace(/_/g, ' ')} <span className="text-(--color-ink)">{String(value)}</span>
            </span>
          ))}
        </div>
      )}
    </li>
  );
}
