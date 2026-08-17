/** The conversation, per question.
 *
 * A thread is not a comment. It is a question the reviewer could not answer
 * from the drawing, anchored on the cards it is about, and it has exactly two
 * ways to end:
 *
 *   answered   the knowledge exists. It becomes a statement at the next settle
 *              and reaches the one card it is about.
 *   dismissed  not a delete. It compiles into the spec as negative knowledge,
 *              so a later round does not re-ask and codegen knows the case was
 *              considered.
 *
 * **`resolved` is deliberately not a button.** Answering says the knowledge
 * exists; that the drawing *shows* it is proved by the next round re-running
 * the walk that raised the question. Nobody gets to declare their own answer
 * resolved — that is what makes resolution provable rather than asserted.
 */
import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { CornerDownRight, MessageSquare } from 'lucide-react';
import { api, type Thread } from '@/lib/api';
import { Badge, Button, Empty, SeverityBadge, cx } from '@/components/ui';

const STATUS_TONE: Record<Thread['status'], string> = {
  open: 'border-(--color-accent-dim) bg-(--color-accent-wash) text-[#0f766e]',
  answered: 'border-[#5eead4] bg-[#0f766e14] text-(--color-ok)',
  rejected: 'border-(--color-line-strong) text-(--color-ink-faint)',
  resolved: 'border-[#5eead4] bg-[#0f766e14] text-(--color-ok)',
};

export default function ThreadPanel({
  boardId,
  threads,
  onGoTo,
  selected,
}: {
  boardId: string;
  threads: Thread[];
  onGoTo: (key: string) => void;
  selected: string | null;
}) {
  if (threads.length === 0) {
    return (
      <Empty
        title="No questions yet"
        body="Run a review round and the reviewer will ask what the drawing does not say."
      />
    );
  }

  const open = threads.filter((thread) => thread.status === 'open');
  const rest = threads.filter((thread) => thread.status !== 'open');

  return (
    <div className="divide-y divide-(--color-line-soft)">
      {[...open, ...rest].map((thread) => (
        <ThreadCard
          key={thread.id}
          boardId={boardId}
          thread={thread}
          onGoTo={onGoTo}
          selected={selected}
        />
      ))}
    </div>
  );
}

function ThreadCard({
  boardId,
  thread,
  onGoTo,
  selected,
}: {
  boardId: string;
  thread: Thread;
  onGoTo: (key: string) => void;
  selected: string | null;
}) {
  const queryClient = useQueryClient();
  const [answer, setAnswer] = useState('');
  const [replying, setReplying] = useState(thread.status === 'open');

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['threads', boardId] });
    queryClient.invalidateQueries({ queryKey: ['board', boardId] });
  };

  const respond = useMutation({
    mutationFn: ({ kind }: { kind: 'answer' | 'reject' }) =>
      kind === 'answer'
        ? api.review.answer(thread.id, answer.trim())
        : api.review.reject(thread.id, answer.trim() || 'Not applicable to this process.'),
    onSuccess: () => {
      setAnswer('');
      setReplying(false);
      refresh();
    },
  });

  const anchors = thread.anchors.filter((anchor) => anchor.key);
  const isHere = anchors.some((anchor) => anchor.key === selected);

  return (
    <div className={cx('px-4 py-3.5 transition-colors', isHere && 'bg-(--color-raised)')}>
      <div className="flex items-center gap-2">
        <span
          className={cx(
            'rounded border px-1.5 py-0.5 font-mono text-[10px] uppercase',
            STATUS_TONE[thread.status],
          )}
        >
          {thread.status}
        </span>
        <SeverityBadge severity={thread.severity} />
        <Badge mono>{thread.category.replace(/_/g, ' ')}</Badge>
        <span className="ml-auto font-mono text-[10px] text-(--color-ink-faint)">
          r{thread.round}
        </span>
      </div>

      <p className="mt-2.5 text-[13px] leading-relaxed text-(--color-ink)">{thread.question}</p>

      {thread.reason && (
        <p className="mt-1.5 text-[11.5px] leading-relaxed text-(--color-ink-faint)">
          {thread.reason}
        </p>
      )}

      {anchors.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {anchors.map((anchor) => (
            <button
              key={`${anchor.kind}:${anchor.key}`}
              onClick={() => onGoTo(anchor.key!)}
              className="rounded border border-(--color-line) px-1.5 py-0.5 font-mono text-[10px] text-(--color-ink-faint) transition-colors hover:border-(--color-accent-dim) hover:text-[#0f766e]"
            >
              {anchor.key}
            </button>
          ))}
        </div>
      )}

      {thread.messages.length > 1 && (
        <div className="mt-3 space-y-2 border-l border-(--color-line) pl-3">
          {thread.messages.slice(1).map((message) => (
            <div key={message.seq} className="flex gap-2">
              <CornerDownRight size={12} className="mt-0.5 shrink-0 text-(--color-ink-faint)" />
              <div>
                <span className="font-mono text-[10px] text-(--color-ink-faint)">
                  {message.author}
                </span>
                <p className="text-[12px] leading-relaxed text-(--color-ink-dim)">{message.body}</p>
              </div>
            </div>
          ))}
        </div>
      )}

      {thread.status === 'open' &&
        (replying ? (
          <div className="mt-3">
            <textarea
              rows={2}
              value={answer}
              onChange={(event) => setAnswer(event.target.value)}
              placeholder="Answer in your own words…"
              className="w-full resize-y rounded-md border border-(--color-line) bg-(--color-ground) px-2.5 py-2 text-[12.5px] leading-relaxed text-(--color-ink) placeholder:text-[#a1a1aa] focus:border-(--color-accent-dim) focus:ring-1 focus:ring-(--color-accent-dim) focus:outline-none"
            />
            <div className="mt-2 flex items-center gap-2">
              <Button
                size="sm"
                variant="primary"
                disabled={!answer.trim()}
                busy={respond.isPending && respond.variables?.kind === 'answer'}
                onClick={() => respond.mutate({ kind: 'answer' })}
              >
                Answer
              </Button>
              <Button
                size="sm"
                variant="ghost"
                busy={respond.isPending && respond.variables?.kind === 'reject'}
                onClick={() => respond.mutate({ kind: 'reject' })}
                title="Not a delete — it reaches the spec as something deliberately not done"
              >
                Doesn't apply
              </Button>
            </div>
          </div>
        ) : (
          <Button size="sm" variant="ghost" className="mt-2" onClick={() => setReplying(true)}>
            <MessageSquare size={12} /> Reply
          </Button>
        ))}
    </div>
  );
}
