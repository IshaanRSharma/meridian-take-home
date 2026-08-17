/** The frozen spec — the bridge between the two halves of the product.
 *
 * Before the freeze, ground truth lives in a person's head, so the loop asks a
 * human. After it, ground truth lives in an eval suite. This screen is that
 * transfer made visible, and the download is not a convenience: `spec.lock.json`
 * is the input to the `spec-to-agent` skill and the contract the self-healing
 * loop checks conformance against, both of which run in a terminal.
 *
 * **The spec's shape is not the board's shape**, and rendering it as though it
 * were is what broke this screen the first time. Three differences matter:
 *
 *   primitives      a map keyed by board key, not a list — every consumer looks
 *                   a card up by name rather than walking in order
 *   entities        a bare config. No key, no context, never a `SpecPrimitive`,
 *                   because a thing is not a step
 *   entity_context  where a statement about a thing actually lives, beside
 *                   `edge_context` for the same reason: what is settled about a
 *                   certificate is true wherever it is read, so it belongs to
 *                   the thing rather than to whichever step read it first
 *
 * Showing a step with its inherited, local and negative statements is the
 * clearest evidence the scope chain works — a board-level statement physically
 * appears on every card that inherits it, inlined rather than referenced, so a
 * generator reads one entry and joins nothing.
 */
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, Download, FileLock2, Lock, ShieldCheck } from 'lucide-react';
import {
  ApiError,
  api,
  type Config,
  type ScopedContext,
  type SpecPrimitive,
} from '@/lib/api';
import { Badge, Button, Empty, Panel, PanelHeader, Problem, Spinner, cx } from '@/components/ui';

export default function Spec({ boardId }: { boardId: string }) {
  const queryClient = useQueryClient();
  const [copied, setCopied] = useState(false);

  const spec = useQuery({
    queryKey: ['spec', boardId],
    queryFn: () => api.spec.get(boardId).catch(() => null),
  });

  const freeze = useMutation({
    mutationFn: () => api.spec.freeze(boardId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['spec', boardId] });
      queryClient.invalidateQueries({ queryKey: ['board', boardId] });
      queryClient.invalidateQueries({ queryKey: ['boards'] });
    },
  });

  if (spec.isLoading) return <Spinner label="Loading spec…" />;

  const sealed = spec.data;

  if (!sealed) {
    return (
      <div className="mx-auto max-w-2xl px-6 py-12">
        <Panel>
          <Empty
            title="Nothing frozen yet"
            body="Freezing seals the board into what a code generator reads. It needs every question settled — the freeze refuses and tells you which ones are not."
            action={
              <Button variant="primary" busy={freeze.isPending} onClick={() => freeze.mutate()}>
                <Lock size={14} /> Freeze the spec
              </Button>
            }
          />
        </Panel>
        {freeze.error && (
          <div className="mt-4">
            <FreezeRefusal error={freeze.error} />
          </div>
        )}
      </div>
    );
  }

  const steps = Object.entries(sealed.primitives);
  const things = Object.entries(sealed.entities);

  const download = () => {
    const blob = new Blob([JSON.stringify(sealed, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = 'spec.lock.json';
    link.click();
    URL.revokeObjectURL(url);
  };

  const copy = async () => {
    await navigator.clipboard.writeText(JSON.stringify(sealed, null, 2));
    setCopied(true);
    setTimeout(() => setCopied(false), 1600);
  };

  return (
    <div className="mx-auto max-w-5xl px-6 py-8">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2.5">
            <FileLock2 size={17} className="text-(--color-accent)" />
            <h1 className="text-[19px] font-semibold tracking-[-0.015em]">{sealed.name}</h1>
            <Badge tone="accent" mono>
              v{sealed.version}
            </Badge>
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 font-mono text-[11.5px] text-(--color-ink-faint)">
            <span className="flex items-center gap-1.5">
              <ShieldCheck size={12} /> {sealed.checksum.slice(0, 16)}…
            </span>
            <span>frozen {new Date(sealed.frozen_at).toLocaleString()}</span>
            <span>agents/{sealed.slug}/</span>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <Button onClick={copy}>
            {copied && <Check size={14} className="text-(--color-ok)" />}
            {copied ? 'Copied' : 'Copy JSON'}
          </Button>
          <Button variant="primary" onClick={download}>
            <Download size={14} /> spec.lock.json
          </Button>
        </div>
      </header>

      <Panel className="mt-5 px-4 py-3">
        <p className="text-[12.5px] leading-relaxed text-(--color-ink-dim)">
          This is the hand-off. Authority has moved from the process owner to a test suite —
          the file is immutable and checksummed, and every statement on every card below came
          from a question somebody answered. Download it into{' '}
          <code className="font-mono text-[11.5px] text-(--color-ink)">agents/{sealed.slug}/</code>{' '}
          and the <code className="font-mono text-[11.5px] text-(--color-ink)">spec-to-agent</code>{' '}
          skill generates from it; the healing loop checks conformance against the same
          checksum.
        </p>
      </Panel>

      <div className="mt-6 grid gap-5 lg:grid-cols-[1fr_260px]">
        <div className="space-y-5">
          <Panel className="overflow-hidden">
            <PanelHeader title="Steps" hint={`${steps.length}`} />
            {steps.length === 0 ? (
              <Empty title="No steps in this spec" />
            ) : (
              <div className="divide-y divide-(--color-line-soft)">
                {steps.map(([key, step]) => (
                  <StepCard key={key} cardKey={key} step={step} />
                ))}
              </div>
            )}
          </Panel>

          {things.length > 0 && (
            <Panel className="overflow-hidden">
              <PanelHeader title="Things" hint={`${things.length} the steps read`} />
              <div className="divide-y divide-(--color-line-soft)">
                {things.map(([key, config]) => (
                  <ThingCard
                    key={key}
                    cardKey={key}
                    config={config}
                    context={sealed.entity_context[key]}
                  />
                ))}
              </div>
            </Panel>
          )}
        </div>

        <div className="space-y-5">
          <Panel className="overflow-hidden">
            <PanelHeader title="Transitions" hint={`${sealed.edges.length}`} />
            <ul className="divide-y divide-(--color-line-soft)">
              {sealed.edges.map((edge) => (
                <li key={edge.key} className="px-4 py-2 font-mono text-[11px]">
                  <div className="flex items-center gap-1.5 text-(--color-ink-dim)">
                    <span className="truncate">{edge.from_key}</span>
                    <span className={cx(edge.relation === 'exception' && 'text-(--color-ink)')}>
                      →
                    </span>
                    <span className="truncate">{edge.to_key}</span>
                  </div>
                  {(edge.on_outcomes.length > 0 || edge.relation !== 'normal') && (
                    <p className="mt-0.5 text-[10px] text-(--color-ink-faint)">
                      {edge.on_outcomes.join(' / ')}
                      {edge.relation !== 'normal' && ` · ${edge.relation}`}
                    </p>
                  )}
                  <Statements context={sealed.edge_context[edge.key]} compact />
                </li>
              ))}
            </ul>
          </Panel>

          {sealed.capabilities.length > 0 && (
            <Panel className="overflow-hidden">
              <PanelHeader title="Capabilities" hint="bound outside the spec" />
              <ul className="space-y-1 px-4 py-3">
                {sealed.capabilities.map((capability) => (
                  <li key={capability} className="font-mono text-[11px] text-(--color-ink-dim)">
                    {capability}
                  </li>
                ))}
              </ul>
            </Panel>
          )}

          <Panel className="px-4 py-3">
            <p className="text-[11.5px] leading-relaxed text-(--color-ink-faint)">
              Editing the board after a freeze is the only path to v2 — the spec itself can
              never change. A trigger on the table enforces it.
            </p>
          </Panel>
        </div>
      </div>
    </div>
  );
}

function StepCard({ cardKey, step }: { cardKey: string; step: SpecPrimitive }) {
  const name = typeof step.config.name === 'string' ? step.config.name : cardKey;
  return (
    <div className="px-4 py-3.5">
      <div className="flex items-center gap-2">
        <Badge mono>{step.primitive_type}</Badge>
        <span className="truncate text-[13px] font-medium">{name}</span>
        <span className="ml-auto shrink-0 font-mono text-[10.5px] text-(--color-ink-faint)">
          {cardKey}
        </span>
      </div>

      <Facts config={step.config} />

      {step.capabilities && step.capabilities.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {step.capabilities.map((capability) => (
            <span
              key={capability}
              className="rounded border border-(--color-line-soft) px-1.5 py-0.5 font-mono text-[10px] text-(--color-ink-faint)"
            >
              {capability}
            </span>
          ))}
        </div>
      )}

      <Statements context={step.context} />
    </div>
  );
}

function ThingCard({
  cardKey,
  config,
  context,
}: {
  cardKey: string;
  config: Config;
  context?: ScopedContext;
}) {
  const name = typeof config.name === 'string' ? config.name : cardKey;
  const fields = config.fields;
  const count = Array.isArray(fields) ? fields.length : 0;

  return (
    <div className="px-4 py-3.5">
      <div className="flex items-center gap-2">
        <Badge mono>entity</Badge>
        <span className="truncate text-[13px] font-medium">{name}</span>
        <span className="ml-auto shrink-0 font-mono text-[10.5px] text-(--color-ink-faint)">
          {cardKey}
        </span>
      </div>

      <Facts config={config} />

      {count > 0 && (
        <p className="mt-1.5 font-mono text-[10.5px] text-(--color-ink-faint)">
          {count} field{count === 1 ? '' : 's'} extracted
        </p>
      )}

      <Statements context={context} />
    </div>
  );
}

/** The handful of config values worth reading at a glance.
 *
 * Not every key. A spec entry carries whatever the owner filled in, and dumping
 * all of it turns the page into the JSON the download already gives you. */
const SHOWN = [
  'effect',
  'channel',
  'system',
  'scope',
  'quantifier',
  'on_missing_input',
  'cardinality',
  'identified_by',
  'is_terminal',
] as const;

function Facts({ config }: { config: Config }) {
  const shown = SHOWN.map((key) => [key, config[key]] as const).filter(
    ([, value]) => value !== undefined && value !== null && value !== false,
  );
  if (shown.length === 0) return null;
  return (
    <dl className="mt-1.5 flex flex-wrap gap-x-4 gap-y-0.5">
      {shown.map(([key, value]) => (
        <div key={key} className="flex gap-1.5 font-mono text-[10.5px]">
          <dt className="text-(--color-ink-faint)">{key}</dt>
          <dd className="text-(--color-ink-dim)">{plain(value)}</dd>
        </div>
      ))}
    </dl>
  );
}

function Statements({ context, compact }: { context?: ScopedContext; compact?: boolean }) {
  const inherited = context?.inherited ?? [];
  const local = context?.local ?? [];
  const negative = context?.negative ?? [];
  const provenance = context?.provenance ?? [];

  if (inherited.length + local.length + negative.length === 0) {
    return compact ? null : (
      <p className="mt-2 text-[11.5px] text-(--color-ink-faint) italic">
        Nothing was settled about this.
      </p>
    );
  }

  return (
    <div className={cx('space-y-2', compact ? 'mt-1.5' : 'mt-2.5')}>
      <Group label="inherited" hint="anchored more broadly, inlined here" items={inherited} />
      <Group label="local" hint="anchored on this" items={local} />
      <Group label="negative" hint="deliberately not done" items={negative} faint />
      {provenance.length > 0 && !compact && (
        <p className="font-mono text-[10px] text-(--color-ink-faint)">
          from {provenance.length} conversation{provenance.length === 1 ? '' : 's'}
        </p>
      )}
    </div>
  );
}

function Group({
  label,
  hint,
  items,
  faint,
}: {
  label: string;
  hint: string;
  items: string[];
  faint?: boolean;
}) {
  if (items.length === 0) return null;
  return (
    <div>
      <p className="font-mono text-[9.5px] tracking-wide text-(--color-ink-faint) uppercase">
        {label} <span className="normal-case opacity-70">· {hint}</span>
      </p>
      <ul className="mt-1 space-y-1">
        {items.map((statement, index) => (
          <li
            key={index}
            className={cx(
              'border-l border-(--color-line-soft) pl-2.5 text-[12px] leading-snug',
              faint ? 'text-(--color-ink-faint)' : 'text-(--color-ink-dim)',
            )}
          >
            {statement}
          </li>
        ))}
      </ul>
    </div>
  );
}

function plain(value: unknown): string {
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  if (value && typeof value === 'object' && 'kind' in value) {
    return String((value as { kind: unknown }).kind);
  }
  return JSON.stringify(value);
}

/** A refusal is the interesting behaviour here, so it renders as a list of
 *  things to go and do rather than as a message. */
function FreezeRefusal({ error }: { error: unknown }) {
  if (error instanceof ApiError && (error.unsettled.length > 0 || error.findings.length > 0)) {
    return (
      <Problem
        title="Not ready to freeze"
        body={
          <ul className="mt-1.5 space-y-1">
            {error.unsettled.map((one, index) => (
              <li key={index} className="text-[12px]">
                {one.question}
              </li>
            ))}
            {error.findings.map((finding) => (
              <li key={`${finding.anchor}.${finding.field}`} className="text-[12px]">
                <span className="font-mono text-[11px] text-(--color-ink-faint)">
                  {finding.anchor}
                </span>{' '}
                {finding.reason}
              </li>
            ))}
          </ul>
        }
      />
    );
  }
  return (
    <Problem
      title="Could not freeze"
      body={error instanceof Error ? error.message : String(error)}
    />
  );
}
