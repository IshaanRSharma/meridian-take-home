/** The whiteboard.
 *
 * Two persistence paths, and keeping them apart is the whole design:
 *
 *   layout     debounced, writes `boards.layout` only. Cosmetic. A drag must
 *              never touch a card row — `primitives` changes when the *process*
 *              changes, not when somebody tidies the drawing.
 *   structure  immediate, and re-lints. Adding a card or a line changes what
 *              the process is.
 *
 * Invalid connections are refused at the point of drawing rather than reported
 * afterwards, because a line that cannot mean anything is easier to not-draw
 * than to explain.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Background,
  BackgroundVariant,
  Controls,
  ReactFlow,
  ReactFlowProvider,
  applyNodeChanges,
  useReactFlow,
  type Connection,
  type Node as FlowNode,
  type NodeChange,
  type OnConnect,
} from '@xyflow/react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import type { Board, Finding, PrimitiveType, Thread } from '@/lib/api';
import { api } from '@/lib/api';
import { CardNode } from './nodes/CardNode';
import { build } from './graph';
import Palette from './Palette';
import Legend from './Legend';

const nodeTypes = { card: CardNode };

interface Props {
  board: Board;
  findings: Finding[];
  threads: Thread[];
  selected: string | null;
  onSelect: (key: string | null) => void;
  highlight: string | null;
}

export default function Canvas(props: Props) {
  return (
    <ReactFlowProvider>
      <Inner {...props} />
    </ReactFlowProvider>
  );
}

function Inner({ board, findings, threads, selected, onSelect, highlight }: Props) {
  const queryClient = useQueryClient();
  const flow = useReactFlow();
  const [showDataLinks, setShowDataLinks] = useState(true);
  const pending = useRef<Record<string, [number, number]>>({});
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const built = useMemo(
    () => build(board, findings, threads, { showDataLinks, highlight }),
    [board, findings, threads, showDataLinks, highlight],
  );

  // React Flow owns node positions while a drag is in flight; the query owns
  // them the rest of the time. Local state is the drag buffer, resynced
  // whenever the board actually changes.
  const [nodes, setNodes] = useState<FlowNode[]>(built.nodes);
  useEffect(() => setNodes(built.nodes), [built.nodes]);

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['board', board.id] });
    queryClient.invalidateQueries({ queryKey: ['lint', board.id] });
  };

  const saveLayout = useMutation({
    mutationFn: (positions: Record<string, [number, number]>) =>
      api.boards.moveCards(board.id, positions),
  });

  const addCard = useMutation({
    mutationFn: (card: { primitive_type: PrimitiveType; x: number; y: number }) =>
      api.boards.addCard(board.id, card),
    onSuccess: (made) => {
      refresh();
      onSelect(made.key);
    },
  });

  const connect = useMutation({
    mutationFn: (edge: { from_key: string; to_key: string; on_outcomes: string[] }) =>
      api.boards.connect(board.id, edge),
    onSuccess: refresh,
  });

  const onNodesChange = useCallback(
    (changes: NodeChange[]) => {
      setNodes((current) => applyNodeChanges(changes, current));

      // Only the end of a drag is worth writing. `dragging: false` is the
      // release; everything before it is a frame.
      const settled = changes.filter(
        (change): change is NodeChange & { type: 'position'; id: string } =>
          change.type === 'position' && change.dragging === false,
      );
      if (settled.length === 0) return;

      for (const change of settled) {
        const node = flow.getNode(change.id);
        if (node) pending.current[change.id] = [node.position.x, node.position.y];
      }
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => {
        const positions = pending.current;
        pending.current = {};
        if (Object.keys(positions).length) saveLayout.mutate(positions);
      }, 400);
    },
    [flow, saveLayout],
  );

  const onConnect: OnConnect = useCallback(
    (connection) => {
      if (!connection.source || !connection.target) return;
      connect.mutate({
        from_key: connection.source,
        to_key: connection.target,
        // The handle *is* the outcome. One drag, one edge, carrying the one
        // outcome that handle represents — two outcomes to the same target are
        // two lines, because the owner drew two.
        on_outcomes: connection.sourceHandle ? [connection.sourceHandle] : [],
      });
    },
    [connect],
  );

  /** Refused rather than reported. */
  const isValidConnection = useCallback(
    (connection: Connection | { source: string | null; target: string | null }) => {
      const from = board.primitives.find((card) => card.key === connection.source);
      const to = board.primitives.find((card) => card.key === connection.target);
      if (!from || !to) return false;
      // A thing is referenced, never traversed. No transition may start or end
      // on one — that is `inputs` on the step, and it is a different concept.
      if (from.primitive_type === 'entity' || to.primitive_type === 'entity') return false;
      // An Event is something arriving; after it arrives something must happen,
      // so it is never the end of a line.
      if (to.primitive_type === 'event' && from.primitive_type === 'event') return false;
      if (from.config.is_terminal === true) return false;
      return true;
    },
    [board.primitives],
  );

  const onDrop = useCallback(
    (event: React.DragEvent) => {
      event.preventDefault();
      const kind = event.dataTransfer.getData('primitive_type') as PrimitiveType;
      if (!kind) return;
      const at = flow.screenToFlowPosition({ x: event.clientX, y: event.clientY });
      addCard.mutate({ primitive_type: kind, x: Math.round(at.x), y: Math.round(at.y) });
    },
    [flow, addCard],
  );

  return (
    <div className="relative h-full w-full">
      <ReactFlow
        nodes={nodes}
        edges={built.edges}
        nodeTypes={nodeTypes}
        onNodesChange={onNodesChange}
        onConnect={onConnect}
        isValidConnection={isValidConnection}
        onDrop={onDrop}
        onDragOver={(event) => {
          event.preventDefault();
          event.dataTransfer.dropEffect = 'move';
        }}
        onNodeClick={(_, node) => onSelect(node.id)}
        onPaneClick={() => onSelect(null)}
        nodesConnectable
        proOptions={{ hideAttribution: true }}
        defaultEdgeOptions={{ type: 'smoothstep' }}
        fitView
        fitViewOptions={{ padding: 0.25, maxZoom: 1 }}
        minZoom={0.25}
        maxZoom={1.6}
        selectNodesOnDrag={false}
      >
        <Background variant={BackgroundVariant.Dots} gap={22} size={1} color="#1e1e21" />
        <Controls
          showInteractive={false}
          className="!bottom-4 !left-4 overflow-hidden !rounded-md !border !border-(--color-line) !shadow-none"
        />
      </ReactFlow>

      <Palette pending={addCard.isPending} />

      <Legend
        showDataLinks={showDataLinks}
        onToggleDataLinks={() => setShowDataLinks((on) => !on)}
      />

      {selected && (
        <div className="pointer-events-none absolute inset-x-0 bottom-4 flex justify-center">
          <span className="pointer-events-auto rounded-md border border-(--color-line) bg-(--color-surface) px-2.5 py-1 font-mono text-[11px] text-(--color-ink-dim)">
            {selected}
          </span>
        </div>
      )}
    </div>
  );
}
