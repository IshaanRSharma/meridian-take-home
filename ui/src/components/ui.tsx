/** Shared dumb UI. Nothing here knows what a primitive or a thread is.
 *
 * One file rather than a directory: these are ten small components that differ
 * in name and not in content, which is the repo's own test for whether a
 * directory has earned itself.
 */
import type { ReactNode } from 'react';
import { Loader2 } from 'lucide-react';

export type Severity = 'blocking' | 'important' | 'minor';

export const cx = (...parts: (string | false | null | undefined)[]) =>
  parts.filter(Boolean).join(' ');

// ── button ───────────────────────────────────────────────────────────────────

type ButtonProps = {
  children: ReactNode;
  onClick?: () => void;
  variant?: 'primary' | 'ghost' | 'outline' | 'danger';
  size?: 'sm' | 'md';
  disabled?: boolean;
  busy?: boolean;
  title?: string;
  type?: 'button' | 'submit';
  className?: string;
};

const VARIANT: Record<string, string> = {
  primary: 'bg-(--color-accent) text-white hover:bg-[#5c88ff] disabled:bg-(--color-accent-dim)',
  outline:
    'border border-(--color-line-strong) text-(--color-ink) hover:bg-(--color-raised) hover:border-[#45454d]',
  ghost: 'text-(--color-ink-dim) hover:text-(--color-ink) hover:bg-(--color-raised)',
  danger: 'border border-[#5a2326] text-(--color-blocking) hover:bg-[#e5484d14]',
};

export function Button({
  children,
  onClick,
  variant = 'outline',
  size = 'md',
  disabled,
  busy,
  title,
  type = 'button',
  className,
}: ButtonProps) {
  return (
    <button
      type={type}
      title={title}
      onClick={onClick}
      disabled={disabled || busy}
      className={cx(
        'inline-flex items-center justify-center gap-2 rounded-md font-medium',
        'transition-colors duration-100 disabled:cursor-not-allowed disabled:opacity-55',
        'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-(--color-accent)',
        size === 'sm' ? 'h-7 px-2.5 text-[12.5px]' : 'h-9 px-3.5 text-[13px]',
        VARIANT[variant],
        className,
      )}
    >
      {busy && <Loader2 size={13} className="animate-spin" />}
      {children}
    </button>
  );
}

// ── surfaces ─────────────────────────────────────────────────────────────────

export function Panel({
  children,
  className,
  as: Tag = 'div',
}: {
  children: ReactNode;
  className?: string;
  as?: 'div' | 'section' | 'aside';
}) {
  return (
    <Tag
      className={cx(
        'rounded-lg border border-(--color-line) bg-(--color-surface)',
        className,
      )}
    >
      {children}
    </Tag>
  );
}

export function PanelHeader({
  title,
  hint,
  right,
}: {
  title: string;
  hint?: ReactNode;
  right?: ReactNode;
}) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-(--color-line) px-4 py-2.5">
      <div className="flex min-w-0 items-baseline gap-2.5">
        <h2 className="text-[13px] font-semibold text-(--color-ink)">{title}</h2>
        {hint && <span className="truncate text-[12px] text-(--color-ink-faint)">{hint}</span>}
      </div>
      {right}
    </div>
  );
}

// ── labels ───────────────────────────────────────────────────────────────────

const SEVERITY_TONE: Record<Severity, string> = {
  blocking: 'border-[#5a2326] bg-[#e5484d14] text-(--color-blocking)',
  important: 'border-[#54431c] bg-[#d9a44114] text-(--color-important)',
  minor: 'border-(--color-line-strong) bg-transparent text-(--color-ink-faint)',
};

export function SeverityBadge({ severity }: { severity: Severity }) {
  return (
    <span
      className={cx(
        'inline-flex shrink-0 items-center rounded border px-1.5 py-0.5',
        'font-mono text-[10.5px] tracking-tight uppercase',
        SEVERITY_TONE[severity],
      )}
    >
      {severity}
    </span>
  );
}

export function Badge({
  children,
  tone = 'neutral',
  mono,
}: {
  children: ReactNode;
  tone?: 'neutral' | 'accent' | 'ok' | 'warn';
  mono?: boolean;
}) {
  const tones = {
    neutral: 'border-(--color-line-strong) text-(--color-ink-dim)',
    accent: 'border-(--color-accent-dim) bg-(--color-accent-wash) text-[#8fa9ff]',
    ok: 'border-[#1f5138] bg-[#3dd68c14] text-(--color-ok)',
    warn: 'border-[#54431c] bg-[#d9a44114] text-(--color-important)',
  };
  return (
    <span
      className={cx(
        'inline-flex shrink-0 items-center rounded border px-1.5 py-0.5 text-[11px]',
        mono && 'font-mono',
        tones[tone],
      )}
    >
      {children}
    </span>
  );
}

export function Dot({ tone }: { tone: 'ok' | 'blocking' | 'important' | 'idle' | 'running' }) {
  const tones = {
    ok: 'bg-(--color-ok)',
    blocking: 'bg-(--color-blocking)',
    important: 'bg-(--color-important)',
    idle: 'bg-(--color-ink-faint)',
    running: 'bg-(--color-accent) animate-pulse',
  };
  return <span className={cx('inline-block size-1.5 shrink-0 rounded-full', tones[tone])} />;
}

// ── states ───────────────────────────────────────────────────────────────────

export function Empty({ title, body, action }: { title: string; body?: string; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-14 text-center">
      <p className="text-[13px] font-medium text-(--color-ink-dim)">{title}</p>
      {body && <p className="max-w-sm text-[12.5px] leading-relaxed text-(--color-ink-faint)">{body}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 py-10 text-[12.5px] text-(--color-ink-faint)">
      <Loader2 size={14} className="animate-spin" />
      {label}
    </div>
  );
}

/** A failure, shown as what it is rather than as a toast that disappears. */
export function Problem({ title, body }: { title: string; body?: ReactNode }) {
  return (
    <div className="rounded-md border border-[#5a2326] bg-[#e5484d0d] px-3 py-2.5">
      <p className="text-[12.5px] font-medium text-(--color-blocking)">{title}</p>
      {body && <div className="mt-1 text-[12px] leading-relaxed text-(--color-ink-dim)">{body}</div>}
    </div>
  );
}

// ── input ────────────────────────────────────────────────────────────────────

export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-[12px] font-medium text-(--color-ink-dim)">{label}</span>
      {children}
      {hint && <span className="mt-1.5 block text-[11.5px] text-(--color-ink-faint)">{hint}</span>}
    </label>
  );
}

export const inputStyles = cx(
  'w-full rounded-md border border-(--color-line) bg-(--color-ground) px-3 py-2',
  'text-[13px] text-(--color-ink) placeholder:text-(--color-ink-faint)',
  'focus:border-(--color-accent-dim) focus:outline-none focus:ring-1 focus:ring-(--color-accent-dim)',
  'transition-colors',
);
