/** Supabase, used for exactly one thing and honest about which.
 *
 * **Auth only.** It never queries a table and never reaches the Supabase REST
 * API — FastAPI talks to Postgres directly over asyncpg with `DATABASE_URL`
 * (`Claude.md` §4: the browser never queries tables). So the only credential
 * this app needs anywhere is the **publishable** key, and there is no place in
 * the codebase a secret key would go.
 *
 * The realtime subscription to `events` that §19 describes is not wired yet;
 * `Runs` polls instead. When it lands it will use this same client and this
 * same key, because `events` is the one table with a read policy for `anon`.
 *
 * **Key naming.** Supabase replaced the `anon` / `service_role` JWTs with
 * `sb_publishable_…` / `sb_secret_…`. The publishable key goes exactly where
 * the anon key went — same argument position, same client — so only the name
 * changed. The legacy variable is still read as a fallback, because a project
 * created before the change still hands out a JWT and both work today.
 *
 * Optional on purpose. Without the two variables the client is null and the
 * login screen names what is missing, rather than the app failing at the first
 * call with a stack trace nobody can act on.
 */
import { createClient, type SupabaseClient } from '@supabase/supabase-js';

const url = import.meta.env.VITE_SUPABASE_URL as string | undefined;

/** The browser-safe key: `sb_publishable_…`, or a legacy `anon` JWT.
 *
 * Safe to inline into the bundle by design — it identifies the project and
 * carries the `anon` role, and every table is guarded by row-level security.
 * A `sb_secret_…` key here would bypass RLS in a file anyone can view-source,
 * which is why nothing in this app ever reads one. */
const publishableKey = (import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY ??
  import.meta.env.VITE_SUPABASE_ANON_KEY) as string | undefined;

export const isConfigured = Boolean(url && publishableKey);

export const supabase: SupabaseClient | null = isConfigured
  ? createClient(url!, publishableKey!, {
      auth: { persistSession: true, autoRefreshToken: true },
    })
  : null;

/** Which variable is missing, named, so the fix is one line in `ui/.env`. */
export function missingConfig(): string[] {
  return [
    !url ? 'VITE_SUPABASE_URL' : null,
    !publishableKey ? 'VITE_SUPABASE_PUBLISHABLE_KEY' : null,
  ].filter((name): name is string => name !== null);
}

/** A secret key pasted into the browser env, caught at startup.
 *
 * Worth a hard failure rather than a warning: it is an easy mistake — the two
 * keys sit next to each other in the dashboard — and the consequence is a
 * credential that bypasses row-level security published in a static asset.
 * Better to refuse to start than to ship it. */
if (publishableKey?.startsWith('sb_secret_')) {
  throw new Error(
    'VITE_SUPABASE_PUBLISHABLE_KEY holds a SECRET key (sb_secret_…). It bypasses ' +
      'row-level security and Vite inlines it into the bundle. Use the publishable ' +
      'key (sb_publishable_…) instead, and rotate the secret you just exposed.',
  );
}
