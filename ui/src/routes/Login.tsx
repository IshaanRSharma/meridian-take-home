/** The front door.
 *
 * Two states, and the second one matters more than it looks. With Supabase
 * configured this is an ordinary email/password sign-in. Without it, the screen
 * says which variables are missing and lets somebody through — because a
 * take-home that cannot be opened without provisioning an auth project is a
 * take-home nobody runs, and a gate that silently lets everybody through would
 * be worse than no gate at all.
 */
import { useState, type FormEvent } from 'react';
import { Navigate, useNavigate } from 'react-router-dom';
import { ArrowRight, KeyRound } from 'lucide-react';
import { supabase, missingConfig } from '@/lib/supabase';
import { useAuth } from '@/features/auth/session';
import { Button, Field, Problem, inputStyles } from '@/components/ui';

export default function Login() {
  const { session, unguarded } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (session) return <Navigate to="/boards" replace />;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!supabase) return;
    setBusy(true);
    setProblem(null);
    const { error } = await supabase.auth.signInWithPassword({ email, password });
    setBusy(false);
    if (error) setProblem(error.message);
    else navigate('/boards', { replace: true });
  }

  return (
    <div className="grid min-h-full lg:grid-cols-[1fr_1.15fr]">
      {/* Left: what this is. A login screen for an internal tool should say
          what the tool does, because the person opening it may have been sent
          a link and nothing else. */}
      <div className="hidden flex-col justify-between border-r border-(--color-line) bg-(--color-surface) p-12 lg:flex">
        <Wordmark />
        <div className="max-w-md">
          <h1 className="text-[28px] leading-[1.15] font-semibold tracking-[-0.02em]">
            Turn how the work is actually done into an agent that does it.
          </h1>
          <p className="mt-4 text-[13.5px] leading-relaxed text-(--color-ink-dim)">
            Draw the process. The reviewer asks what the drawing does not say. Answer, freeze
            the spec, and generate an agent that repairs itself against real cases.
          </p>
          <ol className="mt-8 space-y-2.5">
            {[
              ['Whiteboard', 'cards, connections, and what is still blank'],
              ['Review', 'questions only the process owner can answer'],
              ['Spec', 'frozen, checksummed, the hand-off to codegen'],
              ['Runs', 'what the agent produced against ground truth'],
            ].map(([name, what], index) => (
              <li key={name} className="flex gap-3 text-[12.5px]">
                <span className="font-mono text-(--color-ink-faint)">
                  {String(index + 1).padStart(2, '0')}
                </span>
                <span className="text-(--color-ink)">{name}</span>
                <span className="text-(--color-ink-faint)">{what}</span>
              </li>
            ))}
          </ol>
        </div>
        <p className="font-mono text-[11px] text-(--color-ink-faint)">
          Inbound pre-alert validation · pharma distribution
        </p>
      </div>

      {/* Right: the actual door. */}
      <div className="flex items-center justify-center p-8">
        <div className="w-full max-w-[340px]">
          <div className="lg:hidden">
            <Wordmark />
          </div>

          {unguarded ? (
            <UnguardedNotice missing={missingConfig()} onContinue={() => navigate('/boards')} />
          ) : (
            <form onSubmit={submit} className="mt-8 space-y-4 lg:mt-0">
              <div>
                <h2 className="text-[17px] font-semibold">Sign in</h2>
                <p className="mt-1 text-[12.5px] text-(--color-ink-faint)">
                  Continue to your workspace.
                </p>
              </div>

              {problem && <Problem title="Could not sign in" body={problem} />}

              <Field label="Email">
                <input
                  type="email"
                  required
                  autoComplete="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@company.com"
                  className={inputStyles}
                />
              </Field>
              <Field label="Password">
                <input
                  type="password"
                  required
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="••••••••"
                  className={inputStyles}
                />
              </Field>

              <Button type="submit" variant="primary" busy={busy} className="w-full">
                Continue <ArrowRight size={14} />
              </Button>
            </form>
          )}
        </div>
      </div>
    </div>
  );
}

function Wordmark() {
  return (
    <div className="flex items-center gap-2.5">
      <div className="grid size-7 place-items-center rounded-md border border-(--color-line-strong) bg-(--color-raised)">
        <div className="size-2 rounded-[2px] bg-(--color-accent)" />
      </div>
      <span className="text-[15px] font-semibold tracking-[-0.01em]">Meridian</span>
    </div>
  );
}

function UnguardedNotice({ missing, onContinue }: { missing: string[]; onContinue: () => void }) {
  return (
    <div className="mt-8 space-y-4 lg:mt-0">
      <div>
        <h2 className="text-[17px] font-semibold">Sign-in is not configured</h2>
        <p className="mt-1 text-[12.5px] leading-relaxed text-(--color-ink-faint)">
          Supabase Auth has not been set up, so this build has no front door. Everything below
          still works — the API is single-tenant and unauthenticated by design.
        </p>
      </div>

      <div className="rounded-md border border-(--color-line) bg-(--color-surface) p-3">
        <div className="flex items-center gap-2 text-[12px] font-medium text-(--color-ink-dim)">
          <KeyRound size={13} /> Set in <code className="font-mono">ui/.env</code>
        </div>
        <ul className="mt-2 space-y-1">
          {missing.map((name) => (
            <li key={name} className="font-mono text-[11.5px] text-(--color-important)">
              {name}
            </li>
          ))}
        </ul>
      </div>

      <Button variant="primary" onClick={onContinue} className="w-full">
        Continue without signing in <ArrowRight size={14} />
      </Button>
    </div>
  );
}
