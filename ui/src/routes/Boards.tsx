/** Which process are you working on.
 *
 * The counts are the point of the row. "11 cards, 2 open questions, spec v1"
 * tells somebody which board is the one they were in the middle of, which a
 * list of names does not.
 */
import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowUpRight, Plus } from 'lucide-react';
import { api, type BoardSummary } from '@/lib/api';
import { Badge, Button, Empty, Panel, Problem, Spinner, inputStyles } from '@/components/ui';

export default function Boards() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [naming, setNaming] = useState(false);
  const [name, setName] = useState('');

  const { data, isLoading, error } = useQuery({
    queryKey: ['boards'],
    queryFn: api.boards.list,
  });

  const create = useMutation({
    mutationFn: () => api.boards.create(name.trim()),
    onSuccess: (board) => {
      queryClient.invalidateQueries({ queryKey: ['boards'] });
      navigate(`/boards/${board.id}`);
    },
  });

  return (
    <div className="mx-auto max-w-4xl px-6 py-10">
      <div className="flex items-end justify-between gap-4">
        <div>
          <h1 className="text-[19px] font-semibold tracking-[-0.015em]">Processes</h1>
          <p className="mt-1 text-[13px] text-(--color-ink-dim)">
            Each one is a whiteboard, a review, and a spec.
          </p>
        </div>
        {!naming && (
          <Button variant="primary" onClick={() => setNaming(true)}>
            <Plus size={14} /> New process
          </Button>
        )}
      </div>

      {naming && (
        <Panel className="mt-6 p-4">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (name.trim()) create.mutate();
            }}
            className="flex items-center gap-2"
          >
            <input
              autoFocus
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="What is the process called?"
              className={inputStyles}
            />
            <Button type="submit" variant="primary" busy={create.isPending} disabled={!name.trim()}>
              Create
            </Button>
            <Button
              variant="ghost"
              onClick={() => {
                setNaming(false);
                setName('');
              }}
            >
              Cancel
            </Button>
          </form>
          {create.error && (
            <div className="mt-3">
              <Problem title="Could not create the board" body={String(create.error)} />
            </div>
          )}
        </Panel>
      )}

      <div className="mt-6">
        {isLoading && <Spinner label="Loading boards…" />}
        {error && (
          <Problem
            title="Could not load boards"
            body={error instanceof Error ? error.message : String(error)}
          />
        )}
        {data && data.length === 0 && (
          <Panel>
            <Empty
              title="No processes yet"
              body="Start one, drop a few cards on the canvas, and describe each in your own words."
              action={
                <Button variant="primary" onClick={() => setNaming(true)}>
                  <Plus size={14} /> New process
                </Button>
              }
            />
          </Panel>
        )}
        {data && data.length > 0 && (
          <Panel className="divide-y divide-(--color-line) overflow-hidden">
            {data.map((board) => (
              <Row key={board.id} board={board} />
            ))}
          </Panel>
        )}
      </div>
    </div>
  );
}

function Row({ board }: { board: BoardSummary }) {
  return (
    <Link
      to={`/boards/${board.id}`}
      className="group flex items-center gap-4 px-4 py-3.5 transition-colors hover:bg-(--color-raised)"
    >
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate text-[13.5px] font-medium">{board.name}</span>
          <Badge tone={board.status === 'submitted' ? 'ok' : 'neutral'}>{board.status}</Badge>
          {board.spec_version !== null && (
            <Badge tone="accent" mono>
              spec v{board.spec_version}
            </Badge>
          )}
        </div>
        <div className="mt-1 flex items-center gap-3 font-mono text-[11.5px] text-(--color-ink-faint)">
          <span>
            {board.cards} card{board.cards === 1 ? '' : 's'}
          </span>
          <span>·</span>
          <span>
            {board.edges} connection{board.edges === 1 ? '' : 's'}
          </span>
          {board.open_threads > 0 && (
            <>
              <span>·</span>
              <span className="text-(--color-important)">
                {board.open_threads} open question{board.open_threads === 1 ? '' : 's'}
              </span>
            </>
          )}
        </div>
      </div>
      <ArrowUpRight
        size={15}
        className="shrink-0 text-(--color-ink-faint) transition-colors group-hover:text-(--color-ink)"
      />
    </Link>
  );
}
