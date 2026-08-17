/** The frozen spec — the bridge between the two halves of the product.
 *
 * Before the freeze, ground truth lives in a person's head, so the loop asks a
 * human. After it, ground truth lives in an eval suite. This screen is that
 * transfer made visible, and the download is not a convenience: `spec.lock.json`
 * is the input to the `spec-to-agent` skill and the contract the self-healing
 * loop checks conformance against, both of which run in a terminal.
 *
 * The reason to show a card with its **inherited**, **local** and **negative**
 * statements is that it is the clearest evidence the scoped-context mechanism
 * works. A board-level statement physically appears on every card that inherits
 * it — inlined, not referenced — so a generator reads one entry and has
 * everything, joining nothing.
 */
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, Download, FileLock2, Lock, ShieldCheck } from 'lucide-react';
import { api, ApiError, type SpecPrimitive } from '@/lib/api';
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
            <FileLock2 size={17} className="text-[#5ee3d6]" />
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
            <span>{sealed.slug}</span>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <Button onClick={copy}>
            {copied ? <Check size={14} className="text-(--color-ok)" /> : null}
            {copied ? 'Copied' : 'Copy JSON'}
          </Button>
          <Button variant="primary" onClick={download}>
            <Download size={14} /> spec.lock.json
          </Button>
        </div>
      </header>

      {/* What this file is for, stated once. Somebody arriving here needs to
          know it is the input to two terminal workflows, not an export. */}
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
          <Panel>
            <PanelHeader
              title="Steps"
              hint={`${sealed.primitives.length} cards, in the order they compile`}
            />
            <div className="divide-y divide-(--color-line)">
              {sealed.primitives.map((card) => (
                <SpecCard key={card.key} card={card} />
              ))}
            </div>
          </Panel>

          {sealed.entities.length > 0 && (
            <Panel>
              <PanelHeader title="Things" hint={`${sealed.entities.length} the steps read`} />
              <div className="divide-y divide-(--color-line)">
                {sealed.entities.map((card) => (
                  <SpecCard key={card.key} card={card} />
                ))}
              </div>
            </Panel>
          )}
        </div>

        <div className="space-y-5">
          <Panel>
            <PanelHeader title="Transitions" hint={`${sealed.edges.length}`} />
            <ul className="divide-y divide-(--color-line)">
              {sealed.edges.map((edge) => (
                <li key={edge.key} className="px-4 py-2 font-mono text-[11px]">
                  <div className="flex items-center gap-1.5 text-(--color-ink-dim)">
                    <span className="truncate">{edge.from_key}</span>
                    <span
                      className={cx(
                        edge.relation === 'exception'
                          ? 'text-(--color-blocking)'
                          : 'text-(--color-ink-faint)',
                      )}
                    >
                      →
                    </span>
                    <span className="truncate">{edge.to_key}</span>
                  </div>
                  {edge.on_outcomes.length > 0 && (
                    <p className="mt-0.5 text-[10px] text-(--color-ink-faint)">
                      on {edge.on_outcomes.join(' / ')}
                    </p>
                  )}
                </li>
              ))}
            </ul>
          </Panel>

          {sealed.capabilities && sealed.capabilities.length > 0 && (
            <Panel>
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

function SpecCard({ card }: { card: SpecPrimitive }) {
  const context = card.context ?? {};
  const name = typeof card.config.name === 'string' ? card.config.name : card.key;
  const has =
    (context.inherited?.length ?? 0) +
    (context.local?.length ?? 0) +
    (context.negative?.length ?? 0);

  return (
    <div className="px-4 py-3.5">
      <div className="flex items-center gap-2">
        <Badge mono>{card.primitive_type}</Badge>
        <span className="truncate text-[13px] font-medium">{name}</span>
        <span className="ml-auto shrink-0 font-mono text-[10.5px] text-(--color-ink-faint)">
          {card.key}
        </span>
      </div>

      {has > 0 ? (
        <div className="mt-2.5 space-y-2">
          <Statements
            label="inherited"
            hint="anchored more broadly, inlined here"
            items={context.inherited}
          />
          <Statements label="local" hint="about this card" items={context.local} />
          <Statements
            label="negative"
            hint="deliberately not done"
            items={context.negative}
            tone="text-(--color-ink-faint)"
          />
          {context.provenance && context.provenance.length > 0 && (
            <p className="font-mono text-[10px] text-(--color-ink-faint)">
              from {context.provenance.length} conversation
              {context.provenance.length === 1 ? '' : 's'}
            </p>
          )}
        </div>
      ) : (
        <p className="mt-2 text-[11.5px] text-(--color-ink-faint) italic">
          Nothing was settled about this card.
        </p>
      )}
    </div>
  );
}

function Statements({
  label,
  hint,
  items,
  tone = 'text-(--color-ink-dim)',
}: {
  label: string;
  hint: string;
  items?: string[];
  tone?: string;
}) {
  if (!items || items.length === 0) return null;
  return (
    <div>
      <p className="font-mono text-[9.5px] tracking-wide text-(--color-ink-faint) uppercase">
        {label} <span className="normal-case opacity-70">· {hint}</span>
      </p>
      <ul className="mt-1 space-y-1">
        {items.map((statement, index) => (
          <li
            key={index}
            className={cx('border-l border-(--color-line) pl-2.5 text-[12px] leading-snug', tone)}
          >
            {statement}
          </li>
        ))}
      </ul>
    </div>
  );
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
