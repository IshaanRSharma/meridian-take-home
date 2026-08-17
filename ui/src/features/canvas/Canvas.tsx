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
  type EdgeChange,
  type NodeChange,
  type OnConnect,
} from '@xyflow/react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import type { Board, Finding, PrimitiveType, Relation, Thread } from '@/lib/api';
import { api } from '@/lib/api';
import { CardNode } from './nodes/CardNode';
import { build } from './graph';
import Palette from './Palette';
import Legend from './Legend';

const nodeTypes = { card: CardNode };

/** The line that follows the cursor mid-drag, drawn as the thing it will
 *  become — so the relation is visible before the edge exists rather than only
 *  after it is committed. */
const CONNECTION_LINE: Record<Relation, React.CSSProperties> = {
  normal: { stroke: '#0f766e', strokeWidth: 1.6 },
  exception: { stroke: '#000000', strokeWidth: 1.6, strokeDasharray: '5 3' },
  repeat: { stroke: '#0f766e', strokeWidth: 1.6, strokeDasharray: '1 4' },
};

interface Props {
  board: Board;
  findings: Finding[];
  threads: Thread[];
  selected: string | null;
  /** Highlight a card on the canvas. Does not open anything. */
  onSelect: (key: string | null) => void;
  /** Open a card for editing. Deliberately separate from selecting: a card
   *  arrives on the board first and is opened second, so dropping one does not
   *  bury the thing you just placed under a dialogue. */
  onOpen: (key: string) => void;
  highlight: string | null;
}

export default function Canvas(props: Props) {
  return (
    <ReactFlowProvider>
      <Inner {...props} />
    </ReactFlowProvider>
  );
}

function Inner({ board, findings, threads, selected, onSelect, onOpen, highlight }: Props) {
  const queryClient = useQueryClient();
  const flow = useReactFlow();
  const [showDataLinks, setShowDataLinks] = useState(true);
  // Cleared on a timer so the drop animation plays once rather than on every
  // re-render for as long as the card stays selected.
  const [landed, setLanded] = useState<string | null>(null);
  // What kind of line the next drag draws. A mode rather than a per-edge edit,
  // because there is no endpoint to change an edge's relation in place — the
  // choice has to be made before the line exists.
  const [drawing, setDrawing] = useState<Relation>('normal');
  const pending = useRef<Record<string, [number, number]>>({});
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const built = useMemo(
    () =>
      build(board, findings, threads, {
        showDataLinks,
        highlight,
        landed,
        onRemove: (key) => removeCard.mutate(key),
      }),
    // `removeCard.mutate` is stable across renders, so the nodes are not
    // rebuilt every time the mutation's status changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [board, findings, threads, showDataLinks, highlight, landed],
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
      // Selected, not opened. The card lands where it was dropped and stays
      // visible; entering values is a second, deliberate act.
      onSelect(made.key);
      setLanded(made.key);
      setTimeout(() => setLanded(null), 700);
    },
  });

  const removeCard = useMutation({
    mutationFn: (key: string) => api.boards.removeCard(board.id, key),
    onSuccess: (removed) => {
      refresh();
      if (selected === removed.key) onSelect(null);
    },
  });

  const connect = useMutation({
    mutationFn: (edge: {
      from_key: string;
      to_key: string;
      on_outcomes: string[];
      relation: Relation;
    }) => api.boards.connect(board.id, edge),
    onSuccess: refresh,
  });

  const disconnect = useMutation({
    mutationFn: (key: string) => api.boards.disconnect(board.id, key),
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
        // A line onto its own card can only mean `repeat`, and the database
        // says so — `check (from_key <> to_key or relation = 'repeat')`. Sending
        // the picked relation there would be a constraint violation shown to
        // somebody who drew a perfectly sensible loop.
        relation: connection.source === connection.target ? 'repeat' : drawing,
      });
    },
    [connect, drawing],
  );

  /** Select a line and press Delete. Removing is never a click, because a line
   *  is a one-pixel target and an accidental delete is silent — the process
   *  simply stops routing somewhere and nothing says it used to. */
  const onEdgesChange = useCallback(
    (changes: EdgeChange[]) => {
      for (const change of changes) {
        if (change.type === 'remove') disconnect.mutate(change.id);
      }
    },
    [disconnect],
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
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        deleteKeyCode={['Backspace', 'Delete']}
        connectionLineStyle={CONNECTION_LINE[drawing]}
        isValidConnection={isValidConnection}
        onDrop={onDrop}
        onDragOver={(event) => {
          event.preventDefault();
          event.dataTransfer.dropEffect = 'move';
        }}
        onNodeClick={(_, node) => {
          onSelect(node.id);
          onOpen(node.id);
        }}
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
        <Background variant={BackgroundVariant.Dots} gap={22} size={1} color="#d4d4d8" />
        <Controls
          showInteractive={false}
          className="!bottom-4 !left-4 overflow-hidden !rounded-md !border !border-(--color-line) !shadow-none"
        />
      </ReactFlow>

      <Palette pending={addCard.isPending} drawing={drawing} onDrawing={setDrawing} />

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
