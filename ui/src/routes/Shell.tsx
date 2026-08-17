/** The frame: a rail on the left, the pipeline across the top.
 *
 * The pipeline is shown as a path rather than a menu because the order is the
 * product — Whiteboard, then Review, then Spec, then Runs — and a menu implies
 * the steps are independent. Steps that are not reachable yet are shown and
 * disabled rather than hidden, so somebody can see what is coming.
 */
import type { ReactNode } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Activity, FileLock2, LayoutGrid, LogOut, PenLine } from 'lucide-react';
import { api } from '@/lib/api';
import { useAuth } from '@/features/auth/session';
import { Badge, cx } from '@/components/ui';

export default function Shell({ children, boardId }: { children: ReactNode; boardId?: string }) {
  const { email, unguarded, signOut } = useAuth();
  const { pathname } = useLocation();

  const { data: board } = useQuery({
    queryKey: ['board', boardId],
    queryFn: () => api.boards.get(boardId!),
    enabled: Boolean(boardId),
  });

  const { data: spec } = useQuery({
    queryKey: ['spec', boardId],
    queryFn: () => api.spec.get(boardId!).catch(() => null),
    enabled: Boolean(boardId),
  });

  const steps = boardId
    ? [
        { to: `/boards/${boardId}`, label: 'Whiteboard', icon: PenLine, on: true },
        {
          to: `/boards/${boardId}/spec`,
          label: 'Spec',
          icon: FileLock2,
          on: true,
          badge: spec ? `v${spec.version}` : undefined,
        },
        { to: '/runs', label: 'Runs', icon: Activity, on: true },
      ]
    : [];

  return (
    <div className="flex h-full flex-col">
      <header className="flex h-12 shrink-0 items-center gap-4 border-b border-(--color-line) bg-(--color-surface) px-4">
        <Link to="/boards" className="flex shrink-0 items-center gap-2.5">
          <div className="grid size-6 place-items-center rounded-[5px] border border-(--color-line-strong) bg-(--color-raised)">
            <div className="size-[7px] rounded-[2px] bg-(--color-accent)" />
          </div>
          <span className="text-[13.5px] font-semibold tracking-[-0.01em]">Meridian</span>
        </Link>

        {board && (
          <>
            <span className="text-(--color-ink-faint)">/</span>
            <div className="flex min-w-0 items-center gap-2">
              <span className="truncate text-[13px] text-(--color-ink)">{board.name}</span>
              <Badge tone={board.status === 'submitted' ? 'ok' : 'neutral'}>{board.status}</Badge>
              {board.review_round > 0 && <Badge mono>round {board.review_round}</Badge>}
            </div>
          </>
        )}

        <nav className="ml-auto flex items-center gap-1">
          {steps.map(({ to, label, icon: Icon, badge }) => {
            const active = pathname === to;
            return (
              <Link
                key={to}
                to={to}
                className={cx(
                  'flex h-8 items-center gap-2 rounded-md px-2.5 text-[12.5px] transition-colors',
                  active
                    ? 'bg-(--color-raised) text-(--color-ink)'
                    : 'text-(--color-ink-dim) hover:bg-(--color-raised) hover:text-(--color-ink)',
                )}
              >
                <Icon size={14} />
                {label}
                {badge && <Badge tone="accent" mono>{badge}</Badge>}
              </Link>
            );
          })}
          {!boardId && (
            <Link
              to="/runs"
              className={cx(
                'flex h-8 items-center gap-2 rounded-md px-2.5 text-[12.5px] transition-colors',
                pathname === '/runs'
                  ? 'bg-(--color-raised) text-(--color-ink)'
                  : 'text-(--color-ink-dim) hover:bg-(--color-raised) hover:text-(--color-ink)',
              )}
            >
              <Activity size={14} />
              Runs
            </Link>
          )}
          <Link
            to="/boards"
            className={cx(
              'flex h-8 items-center gap-2 rounded-md px-2.5 text-[12.5px] transition-colors',
              pathname === '/boards'
                ? 'bg-(--color-raised) text-(--color-ink)'
                : 'text-(--color-ink-dim) hover:bg-(--color-raised) hover:text-(--color-ink)',
            )}
          >
            <LayoutGrid size={14} />
            Boards
          </Link>

          <div className="ml-2 flex items-center gap-2 border-l border-(--color-line) pl-3">
            {unguarded ? (
              <span
                title="Supabase Auth is not configured — see ui/.env"
                className="font-mono text-[11px] text-(--color-important)"
              >
                unauthenticated
              </span>
            ) : (
              <>
                <span className="max-w-[160px] truncate text-[12px] text-(--color-ink-faint)">
                  {email}
                </span>
                <button
                  onClick={signOut}
                  title="Sign out"
                  className="grid size-7 place-items-center rounded-md text-(--color-ink-faint) transition-colors hover:bg-(--color-raised) hover:text-(--color-ink)"
                >
                  <LogOut size={13} />
                </button>
              </>
            )}
          </div>
        </nav>
      </header>

      <main className="min-h-0 flex-1">{children}</main>
    </div>
  );
}
