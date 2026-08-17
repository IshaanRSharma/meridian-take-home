/** Supabase, used for exactly two things and honest about which.
 *
 * **Auth** gates the UI. **Realtime** on `events` is the browser's only direct
 * database use — one subscription, so a running cycle streams instead of being
 * polled. It never queries a table: FastAPI holds the service role key and does
 * every read and write (`Claude.md` §4), and `events` is the single table with
 * a read policy for `anon`.
 *
 * Optional on purpose. Without the two variables the client is null and the
 * login screen says which ones are missing, rather than the app failing at the
 * first call with a stack trace nobody can act on.
 */
import { createClient, type SupabaseClient } from '@supabase/supabase-js';

const url = import.meta.env.VITE_SUPABASE_URL as string | undefined;
const anonKey = import.meta.env.VITE_SUPABASE_ANON_KEY as string | undefined;

export const isConfigured = Boolean(url && anonKey);

export const supabase: SupabaseClient | null = isConfigured
  ? createClient(url!, anonKey!, {
      auth: { persistSession: true, autoRefreshToken: true },
    })
  : null;

/** Which variable is missing, named, so the fix is one line in `ui/.env`. */
export function missingConfig(): string[] {
  return [
    !url ? 'VITE_SUPABASE_URL' : null,
    !anonKey ? 'VITE_SUPABASE_ANON_KEY' : null,
  ].filter((name): name is string => name !== null);
}
