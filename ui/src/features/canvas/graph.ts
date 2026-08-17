/** Board → what React Flow draws.
 *
 * Three things happen here and each has a reason worth knowing.
 *
 * **Entities get a computed lane, not a stored position.** `boards.layout`
 * holds steps only, by design — an entity is referenced, never traversed. So
 * the canvas lays them out in a column of their own rather than persisting
 * coordinates the backend would refuse. Drag a step and it saves; drag a thing
 * and it springs back, because there is nowhere to put it.
 *
 * **Data links are rendered, never stored.** A Check reading an entity is
 * `inputs` on the Check — a property of the step. `edges` is the transition
 * relation, which is what makes "state machine, not DAG" a fact about the data
 * model rather than a claim; a data link written as an edge row would be
 * permanently null in half its columns and force a guard into every shape
 * matcher. So these are drawn dashed, with `selectable: false`, and no
 * interaction produces one.
 *
 * **Outcomes come from config and wiring from edges**, joined here, so a card
 * can show which of its declared outcomes has no line out.
 */
import type { Edge as FlowEdge, Node as FlowNode } from '@xyflow/react';
import type { Board, Finding, Primitive, Severity, Thread } from '@/lib/api';
import type { CardData } from './nodes/CardNode';

const RANK: Record<Severity, number> = { blocking: 0, important: 1, minor: 2 };

/** Where the entity lane sits, and how the steps are laid out when nobody has
 *  dragged them yet. A board created through the API always has positions, so
 *  the fallback only shows up for a board seeded from JSON. */
const LANE_X = -340;
const LANE_TOP = 40;
const LANE_GAP = 96;

function outcomeNames(card: Primitive): string[] {
  const declared = card.config.outcomes;
  if (!Array.isArray(declared)) return [];
  return declared
    .map((outcome) =>
      typeof outcome === 'string'
        ? outcome
        : outcome && typeof outcome === 'object' && 'name' in outcome
          ? String((outcome as { name: unknown }).name)
          : null,
    )
    .filter((name): name is string => Boolean(name));
}

/** Entity keys this card reads. `inputs` is the declared list; that is what
 *  `references_are_declared` checks and therefore what is honest to draw. */
function readsFrom(card: Primitive): string[] {
  const inputs = card.config.inputs;
  return Array.isArray(inputs) ? inputs.filter((key): key is string => typeof key === 'string') : [];
}

export interface Built {
  nodes: FlowNode[];
  edges: FlowEdge[];
}

export function build(
  board: Board,
  findings: Finding[],
  threads: Thread[],
  options: { showDataLinks: boolean; highlight: string | null },
): Built {
  const byAnchor = new Map<string, Finding[]>();
  for (const finding of findings) {
    // `anchor` arrives as `primitive:coas_valid` or `edge:e3`.
    const key = finding.anchor.includes(':') ? finding.anchor.split(':')[1]! : finding.anchor;
    byAnchor.set(key, [...(byAnchor.get(key) ?? []), finding]);
  }

  const openAnchors = new Set(
    threads
      .filter((thread) => thread.status === 'open')
      .flatMap((thread) => thread.anchors.map((anchor) => anchor.key))
      .filter((key): key is string => Boolean(key)),
  );

  const wired = new Set(
    board.edges.flatMap((edge) =>
      edge.on_outcomes.length > 0
        ? edge.on_outcomes.map((outcome) => `${edge.from_key}::${outcome}`)
        : [`${edge.from_key}::*`],
    ),
  );
  const hasAnyOut = new Set(board.edges.map((edge) => edge.from_key));
  const hasAnyIn = new Set(board.edges.map((edge) => edge.to_key));

  const entities = board.primitives.filter((card) => card.primitive_type === 'entity');
  const steps = board.primitives.filter((card) => card.primitive_type !== 'entity');

  const nodes: FlowNode[] = [];

  steps.forEach((card, index) => {
    const stored = board.layout[card.key];
    const found = byAnchor.get(card.key) ?? [];
    const names = outcomeNames(card);

    nodes.push({
      id: card.key,
      type: 'card',
      position: stored ?? { x: 60 + (index % 3) * 300, y: 60 + Math.floor(index / 3) * 200 },
      data: {
        primitiveKey: card.key,
        primitiveType: card.primitive_type,
        config: card.config,
        outcomes: names.map((name) => ({
          name,
          wired: wired.has(`${card.key}::${name}`) || wired.has(`${card.key}::*`),
        })),
        worst: found.length
          ? found.reduce<Severity>(
              (worst, one) => (RANK[one.severity] < RANK[worst] ? one.severity : worst),
              'minor',
            )
          : null,
        findingCount: found.length,
        hasOpenThread: openAnchors.has(card.key),
        // Derived, never configured: an Event nothing leads to is what starts
        // the process; one with an incoming edge is what resumes it.
        isTrigger: card.primitive_type === 'event' && !hasAnyIn.has(card.key),
        isTerminal: card.config.is_terminal === true || (!hasAnyOut.has(card.key) && names.length === 0 && card.primitive_type === 'action'),
      } satisfies CardData,
      className: options.highlight && options.highlight !== card.key ? 'opacity-35' : undefined,
    });
  });

  entities.forEach((card, index) => {
    const found = byAnchor.get(card.key) ?? [];
    nodes.push({
      id: card.key,
      type: 'card',
      position: { x: LANE_X, y: LANE_TOP + index * LANE_GAP },
      // No stored position exists for a thing, so dragging one would write
      // nowhere. Saying so with the cursor beats letting somebody move it and
      // watching it snap back on the next load.
      draggable: false,
      data: {
        primitiveKey: card.key,
        primitiveType: card.primitive_type,
        config: card.config,
        outcomes: [],
        worst: found.length
          ? found.reduce<Severity>(
              (worst, one) => (RANK[one.severity] < RANK[worst] ? one.severity : worst),
              'minor',
            )
          : null,
        findingCount: found.length,
        hasOpenThread: openAnchors.has(card.key),
        isTrigger: false,
        isTerminal: false,
      } satisfies CardData,
      className: options.highlight && options.highlight !== card.key ? 'opacity-35' : undefined,
    });
  });

  const edges: FlowEdge[] = board.edges.map((edge) => ({
    id: edge.key,
    source: edge.from_key,
    target: edge.to_key,
    sourceHandle: edge.on_outcomes[0] ?? null,
    type: 'smoothstep',
    animated: false,
    label: edge.on_outcomes.join(' / ') || undefined,
    labelBgStyle: { fill: '#ffffff' },
    labelStyle: { fill: '#52525b', fontSize: 10, fontFamily: 'ui-monospace, monospace' },
    labelBgPadding: [4, 2] as [number, number],
    style: {
      stroke: edge.relation === 'exception' ? '#000000' : '#71717a',
      strokeWidth: 1.4,
      strokeDasharray: edge.relation === 'exception' ? '5 3' : undefined,
    },
    // A repeat routes around the graph rather than through it, which is the
    // one relation that visually proves this is not a DAG.
    pathOptions: edge.relation === 'repeat' ? { offset: 60, borderRadius: 12 } : undefined,
    data: { relation: edge.relation },
  }));

  if (options.showDataLinks) {
    for (const step of steps) {
      for (const entityKey of readsFrom(step)) {
        if (!entities.some((entity) => entity.key === entityKey)) continue;
        edges.push({
          id: `reads:${step.key}:${entityKey}`,
          source: entityKey,
          target: step.key,
          type: 'straight',
          selectable: false,
          focusable: false,
          style: { stroke: '#000000', strokeWidth: 1, strokeDasharray: '2 4' },
          data: { relation: 'reads' },
        });
      }
    }
  }

  return { nodes, edges };
}
