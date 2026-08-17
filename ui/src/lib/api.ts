/** One fetch wrapper, and the shapes the backend actually returns.
 *
 * Hand-written rather than generated, deliberately and narrowly: `make types`
 * regenerates `schema.d.ts` from the live OpenAPI document, and that stays the
 * drift check. What is here is the subset a screen renders, named the way a
 * screen thinks about it — a generated union of eleven config variants is the
 * right thing for a compiler to consume and the wrong thing to write a form
 * against.
 *
 * Errors arrive as objects, never as strings. A 422 from the freeze carries
 * every blank and every open question so a canvas can pin each one to the card
 * it names; flattening that to a message would throw away the only part a
 * person can act on.
 */

const BASE = import.meta.env.VITE_API_URL ?? '/api';

// ── what the backend sends ───────────────────────────────────────────────────

export type PrimitiveType = 'event' | 'action' | 'check' | 'entity';
export type Relation = 'normal' | 'exception' | 'repeat';
export type Severity = 'blocking' | 'important' | 'minor';

/** Config is genuinely open: eleven shapes discriminated by card type, and a
 *  form reads named fields off it. Typed as a record because narrowing it here
 *  would duplicate Pydantic in TypeScript and go stale the first time a field
 *  is added. */
export type Config = Record<string, unknown>;

export interface Primitive {
  key: string;
  primitive_type: PrimitiveType;
  group_key: string | null;
  config: Config;
}

export interface Edge {
  key: string;
  from_key: string;
  to_key: string;
  relation: Relation;
  on_outcomes: string[];
  condition: string | null;
}

export interface Board {
  id: string;
  name: string;
  status: 'draft' | 'in_review' | 'submitted';
  review_round: number;
  primitives: Primitive[];
  edges: Edge[];
  layout: Record<string, { x: number; y: number }>;
}

export interface BoardSummary {
  id: string;
  name: string;
  status: Board['status'];
  review_round: number;
  created_at: string;
  cards: number;
  edges: number;
  open_threads: number;
  spec_version: number | null;
}

/** A blank, with where it is and how much it matters.
 *  `blocking` means the drawing does not work as a process. Everything else is
 *  a question for the review, never a refusal handed to somebody drawing. */
export interface Finding {
  anchor: string;
  field: string;
  reason: string;
  severity: Severity;
  kind: string;
}

export interface Anchor {
  kind: 'primitive' | 'edge' | 'group' | 'entity_field' | 'board';
  key: string | null;
}

export interface Message {
  seq: number;
  author: 'ai' | 'human';
  body: string;
}

export interface Thread {
  id: string;
  category: string;
  severity: Severity;
  status: 'open' | 'answered' | 'rejected' | 'resolved';
  origin: string;
  round: number;
  question: string;
  reason: string | null;
  anchors: Anchor[];
  messages: Message[];
  scenario_key: string | null;
  resolved_at: string | null;
}

export interface Round {
  number: number;
  asked: Thread[];
  resolved: string[];
  reopened: string[];
  walked: unknown[];
  dropped: Record<string, number>;
  consulted: Record<string, number>;
}

/** Everything review settled that bears on one element.
 *
 * `inherited` was anchored more broadly and reaches here through the scope
 * chain; `local` was anchored on this element; narrow beats broad on conflict.
 * `negative` is collected at every level and never overridden — it states
 * something about the world rather than about a step. */
export interface ScopedContext {
  inherited?: string[];
  local?: string[];
  negative?: string[];
  provenance?: string[];
}

/** One step as a code generator receives it. Never an entity — the spec's
 *  `primitive_type` here is narrowed to the three kinds that are traversed. */
export interface SpecPrimitive {
  key: string;
  primitive_type: 'event' | 'action' | 'check';
  config: Config;
  capabilities?: string[];
  context?: ScopedContext;
}

/** The frozen contract.
 *
 * **Keyed maps, not arrays.** `primitives` and `entities` arrive as objects
 * keyed by board key, because every consumer looks a card up by name rather
 * than iterating in order.
 *
 * **An entity is its config, with its statements held separately.** `entities`
 * maps to a bare `EntityConfig` — no key, no context — and anything settled
 * about a thing lives in `entity_context` under the same key. That split is
 * deliberate: a statement about how a batch number is written is true wherever
 * it is read, so it belongs to the thing rather than to whichever step happened
 * to read it first. `edge_context` is the same idea for transitions. */
export interface FrozenSpec {
  version: number;
  board_id: string;
  name: string;
  slug: string;
  checksum: string;
  frozen_at: string;
  entities: Record<string, Config>;
  primitives: Record<string, SpecPrimitive>;
  edges: Edge[];
  edge_context: Record<string, ScopedContext>;
  entity_context: Record<string, ScopedContext>;
  capabilities: string[];
}

export interface CycleEvent {
  id: number;
  at: string;
  cycle_id: string;
  phase: 'review' | 'compile' | 'codegen' | 'eval' | 'repair' | 'deploy' | 'prod';
  kind: string;
  status: 'started' | 'ok' | 'failed' | 'rejected';
  duration_ms: number | null;
  cost_usd: number | null;
  thread_id: string | null;
  primitive_key: string | null;
  case_key: string | null;
  detail: Record<string, unknown>;
}

export interface EvalRow {
  expected: Record<string, string | number>;
  actual: Record<string, unknown> | null;
  outcome: string | null;
}

export interface Evals {
  unit: string;
  source: string;
  runs_recorded: number;
  shipments: EvalRow[];
}

// ── failure, as something a screen can render ────────────────────────────────

/** What the API refused, with the parts a screen needs kept separate.
 *
 * `findings` and `unsettled` are the two 422 bodies the backend produces, and
 * both are lists a canvas pins to cards. Anything else degrades to `detail`. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly findings: Finding[] = [],
    readonly unsettled: { question: string; anchor?: string }[] = [],
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...init?.headers },
    });
  } catch {
    // A dead API is the single most likely thing to be wrong in dev, and
    // "Failed to fetch" tells nobody which process to start.
    throw new ApiError(0, 'Cannot reach the API. Is `make api` running on :8000?');
  }

  if (response.status === 204) return undefined as T;

  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const payload = (body ?? {}) as Record<string, unknown>;
    throw new ApiError(
      response.status,
      typeof payload.detail === 'string' ? payload.detail : describe(response.status),
      Array.isArray(payload.findings) ? (payload.findings as Finding[]) : [],
      Array.isArray(payload.unsettled)
        ? (payload.unsettled as { question: string; anchor?: string }[])
        : [],
    );
  }
  return body as T;
}

function describe(status: number): string {
  if (status === 404) return 'Not found.';
  if (status === 409) return 'That conflicts with the current state.';
  if (status === 422) return 'The board is not ready for this yet.';
  return `The API answered ${status}.`;
}

const send = <T>(method: string, path: string, body?: unknown) =>
  request<T>(path, { method, body: body === undefined ? undefined : JSON.stringify(body) });

/** Every call the UI makes, grouped the way the screens are.
 *
 * Flat and explicit: a generated client would be one import and would hide
 * which of these is a mutation, which is what a caller has to know to decide
 * whether to invalidate. */
export const api = {
  boards: {
    list: () => request<BoardSummary[]>('/boards'),
    get: (id: string) => request<Board>(`/boards/${id}`),
    create: (name: string) => send<Board>('POST', '/boards', { name }),
    lint: (id: string) => request<Finding[]>(`/boards/${id}/lint`),

    addCard: (
      id: string,
      card: { primitive_type: PrimitiveType; name?: string; x?: number; y?: number },
    ) => send<Primitive>('POST', `/boards/${id}/primitives`, card),
    setCard: (id: string, key: string, config: Config) =>
      send<Primitive>('PATCH', `/boards/${id}/primitives/${key}`, { config }),
    /** The one LLM call on this screen: a sentence, and the fields it settles. */
    describeCard: (id: string, key: string, said: string, overwrite = false) =>
      send<Primitive>('POST', `/boards/${id}/primitives/${key}/describe`, { said, overwrite }),
    removeCard: (id: string, key: string) =>
      send<{ key: string; dangling: Edge[]; still_named_by: string[] }>(
        'DELETE',
        `/boards/${id}/primitives/${key}`,
      ),

    connect: (
      id: string,
      edge: {
        from_key: string;
        to_key: string;
        relation?: Relation;
        on_outcomes?: string[];
        condition?: string | null;
      },
    ) => send<Edge>('POST', `/boards/${id}/edges`, edge),
    disconnect: (id: string, key: string) => send<void>('DELETE', `/boards/${id}/edges/${key}`),

    /** Cosmetic, debounced, and deliberately touches no card row. */
    moveCards: (id: string, positions: Record<string, [number, number]>) =>
      send<void>('PATCH', `/boards/${id}/layout`, { positions }),
  },

  review: {
    run: (boardId: string) => send<Round>('POST', `/boards/${boardId}/review`),
    settle: (boardId: string) => send<string[]>('POST', `/boards/${boardId}/settle`),
    threads: (boardId: string, status?: string) =>
      request<Thread[]>(`/boards/${boardId}/threads${status ? `?status=${status}` : ''}`),
    answer: (threadId: string, body: string) =>
      send<void>('PATCH', `/threads/${threadId}`, { status: 'answered', body }),
    reject: (threadId: string, body: string) =>
      send<void>('PATCH', `/threads/${threadId}`, { status: 'rejected', body }),
    reply: (threadId: string, body: string) =>
      send<void>('POST', `/threads/${threadId}/messages`, { body }),
  },

  spec: {
    freeze: (boardId: string) => send<FrozenSpec>('POST', `/boards/${boardId}/freeze`),
    get: (boardId: string) => request<FrozenSpec>(`/boards/${boardId}/spec`),
  },

  observability: {
    events: (limit = 200) => request<CycleEvent[]>(`/events?limit=${limit}`),
    cycle: (cycleId: string) => request<CycleEvent[]>(`/events/${cycleId}`),
    evals: () => request<Evals>('/evals'),
  },
};
