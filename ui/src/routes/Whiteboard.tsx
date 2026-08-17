/** Draw the process, see what is missing, ask for a review.
 *
 * The gate in the top right is the screen's argument. **Ready for review** is
 * enabled only when nothing is blocking — meaning the drawing works as a
 * process: every reference resolves, every declared outcome has a line, every
 * step that stops says so. Blanks that are merely unfilled do not block,
 * because who receives a report is knowledge only the process owner has, and
 * that is a question to ask rather than a refusal to hand somebody who came to
 * draw a diagram.
 *
 * Two gates, not one: *enter review* needs the drawing to work; *freeze* needs
 * that plus every question settled. The second lives on the spec screen.
 */
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  ChevronLeft,
  ChevronRight,
  MessagesSquare,
  TriangleAlert,
} from "lucide-react";
import { api, ApiError, type Finding } from "@/lib/api";
import Canvas from "@/features/canvas/Canvas";
import LintPanel from "@/features/canvas/LintPanel";
import ThreadPanel from "@/features/threads/ThreadPanel";
import CardModal from "@/features/inspector/CardModal";
import { Button, Problem, Spinner, cx } from "@/components/ui";

type Tab = "missing" | "questions";

export default function Whiteboard({ boardId }: { boardId: string }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<string | null>(null);
  const [opened, setOpened] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("missing");
  const [panelOpen, setPanelOpen] = useState(true);

  const board = useQuery({
    queryKey: ["board", boardId],
    queryFn: () => api.boards.get(boardId),
  });
  const lint = useQuery({
    queryKey: ["lint", boardId],
    queryFn: () => api.boards.lint(boardId),
  });
  const threads = useQuery({
    queryKey: ["threads", boardId],
    queryFn: () => api.review.threads(boardId),
  });

  const findings = useMemo(() => lint.data ?? [], [lint.data]);
  const blocking = findings.filter(
    (finding) => finding.severity === "blocking",
  );
  const openQuestions = (threads.data ?? []).filter(
    (thread) => thread.status === "open",
  );

  const review = useMutation({
    mutationFn: () => api.review.run(boardId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["threads", boardId] });
      queryClient.invalidateQueries({ queryKey: ["board", boardId] });
      setTab("questions");
    },
  });

  if (board.isLoading) return <Spinner label="Loading board…" />;
  if (board.error || !board.data) {
    return (
      <div className="mx-auto max-w-lg px-6 py-16">
        <Problem
          title="Could not load this board"
          body={
            board.error instanceof Error ? board.error.message : "Unknown error"
          }
        />
      </div>
    );
  }

  const byCard = (key: string) =>
    findings.filter(
      (finding) =>
        finding.anchor === `primitive:${key}` || finding.anchor === key,
    );

  const openedCard = opened
    ? (board.data.primitives.find((card) => card.key === opened) ?? null)
    : null;

  return (
    <div className="flex h-full">
      <div className="relative min-w-0 flex-1">
        <Canvas
          board={board.data}
          findings={findings}
          threads={threads.data ?? []}
          selected={selected}
          onSelect={setSelected}
          onOpen={setOpened}
          highlight={null}
        />

        {/* The handle, on the canvas rather than on the panel, so it stays
            reachable when the panel is shut. It keeps the two counts, because
            a collapsed panel that hides how many things are wrong would be a
            way to stop looking at them. */}
        <button
          onClick={() => setPanelOpen((was) => !was)}
          title={panelOpen ? "Hide the panel" : "Show what is missing"}
          className={cx(
            "absolute top-1/2 right-0 z-10 -translate-y-1/2 rounded-l-md border border-r-0",
            "border-(--color-line) bg-(--color-surface) py-3 pr-1 pl-1.5 transition-colors",
            "hover:bg-(--color-raised)",
          )}
        >
          <div className="flex flex-col items-center gap-2">
            {panelOpen ? <ChevronRight size={14} /> : <ChevronLeft size={14} />}
            {!panelOpen &&
              (findings.length > 0 || openQuestions.length > 0) && (
                <span className="flex flex-col items-center gap-1 font-mono text-[10px]">
                  {blocking.length > 0 && (
                    <span className="text-(--color-blocking)">
                      {blocking.length}
                    </span>
                  )}
                  {openQuestions.length > 0 && (
                    <span className="text-(--color-accent)">
                      {openQuestions.length}
                    </span>
                  )}
                </span>
              )}
          </div>
        </button>
      </div>

      <aside
        className={cx(
          "flex shrink-0 flex-col overflow-hidden border-l border-(--color-line) bg-(--color-surface)",
          "transition-[width] duration-200 ease-out",
          panelOpen ? "w-[368px]" : "w-0 border-l-0",
        )}
      >
        {/* Fixed inner width so the contents do not reflow while the panel is
            animating shut — a list re-wrapping mid-slide reads as a glitch. */}
        <div className="flex h-full w-[368px] flex-col">
          {/* The gate. */}
          <div className="shrink-0 border-b border-(--color-line) p-3">
            <ReviewGate
              blocking={blocking}
              openQuestions={openQuestions.length}
              round={board.data.review_round}
              busy={review.isPending}
              onRun={() => review.mutate()}
              onSpec={() => navigate(`/boards/${boardId}/spec`)}
            />
            {review.error && (
              <div className="mt-2.5">
                <Problem
                  title="The review would not run"
                  body={
                    review.error instanceof ApiError &&
                    review.error.findings.length > 0
                      ? `${review.error.findings.length} thing${review.error.findings.length === 1 ? "" : "s"} must be fixed on the canvas first.`
                      : review.error instanceof Error
                        ? review.error.message
                        : String(review.error)
                  }
                />
              </div>
            )}
          </div>

          <div className="flex shrink-0 border-b border-(--color-line)">
            {(
              [
                ["missing", "Missing", findings.length],
                ["questions", "Questions", openQuestions.length],
              ] as [Tab, string, number][]
            ).map(([key, label, count]) => (
              <button
                key={key}
                onClick={() => setTab(key)}
                className={cx(
                  "flex flex-1 items-center justify-center gap-2 py-2.5 text-[12.5px] transition-colors",
                  tab === key
                    ? "border-b-2 border-(--color-accent) text-(--color-ink)"
                    : "border-b-2 border-transparent text-(--color-ink-faint) hover:text-(--color-ink-dim)",
                )}
              >
                {label}
                {count > 0 && (
                  <span className="font-mono text-[10.5px] text-(--color-ink-faint)">
                    {count}
                  </span>
                )}
              </button>
            ))}
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto">
            {tab === "missing" ? (
              lint.isLoading ? (
                <Spinner />
              ) : (
                <LintPanel
                  findings={findings}
                  selected={selected}
                  onGoTo={(key) => {
                    setSelected(key);
                    setOpened(key);
                  }}
                />
              )
            ) : threads.isLoading ? (
              <Spinner />
            ) : (
              <ThreadPanel
                boardId={boardId}
                threads={threads.data ?? []}
                selected={selected}
                onGoTo={(key) => setSelected(key)}
              />
            )}
          </div>
        </div>
      </aside>

      {openedCard && (
        <CardModal
          board={board.data}
          card={openedCard}
          findings={byCard(openedCard.key)}
          onClose={() => setOpened(null)}
        />
      )}
    </div>
  );
}

function ReviewGate({
  blocking,
  openQuestions,
  round,
  busy,
  onRun,
  onSpec,
}: {
  blocking: Finding[];
  openQuestions: number;
  round: number;
  busy: boolean;
  onRun: () => void;
  onSpec: () => void;
}) {
  if (blocking.length > 0) {
    return (
      <div className="rounded-md border border-[#000000] bg-[#0000000d] px-3 py-2.5">
        <div className="flex items-center gap-2">
          <TriangleAlert size={14} className="text-(--color-blocking)" />
          <p className="text-[12.5px] font-medium text-(--color-blocking)">
            {blocking.length} thing{blocking.length === 1 ? "" : "s"} to fix
            first
          </p>
        </div>
        <p className="mt-1 text-[11.5px] leading-relaxed text-(--color-ink-dim)">
          These stop the drawing working as a process — an outcome with nowhere
          to go, a step it ends at without saying so. All of them are visible on
          the canvas.
        </p>
      </div>
    );
  }

  if (openQuestions > 0) {
    return (
      <div className="space-y-2">
        <div className="rounded-md border border-(--color-accent-dim) bg-(--color-accent-wash) px-3 py-2.5">
          <div className="flex items-center gap-2">
            <MessagesSquare size={14} className="text-[#0f766e]" />
            <p className="text-[12.5px] font-medium text-[#0f766e]">
              {openQuestions} question{openQuestions === 1 ? "" : "s"} waiting
              on you
            </p>
          </div>
          <p className="mt-1 text-[11.5px] leading-relaxed text-(--color-ink-dim)">
            Answer or dismiss each one. Nothing can be frozen while a question
            is open.
          </p>
        </div>
        <Button
          variant="outline"
          busy={busy}
          onClick={onRun}
          className="w-full"
        >
          Run another round
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <Button variant="primary" busy={busy} onClick={onRun} className="w-full">
        Ready for review <ArrowRight size={14} />
      </Button>
      {round > 0 && (
        <div className="flex items-center justify-between px-1">
          <span className="text-[11.5px] text-(--color-ink-faint)">
            {round} round{round === 1 ? "" : "s"} done, nothing open
          </span>
          <button
            onClick={onSpec}
            className="text-[11.5px] text-[#0f766e] transition-colors hover:text-(--color-ink)"
          >
            Freeze the spec →
          </button>
        </div>
      )}
      {round === 0 && (
        <p className="px-1 text-[11.5px] leading-relaxed text-(--color-ink-faint)">
          The drawing works as a process. A review round will ask what it does
          not say.
        </p>
      )}
    </div>
  );
}
