/** Who is signed in, and whether anybody has to be.
 *
 * The honest framing, because it would be easy to imply more security than
 * exists: this gates the **UI**. The API is single-tenant and unauthenticated
 * by design (`Claude.md` §4 — no auth for the MVP, FastAPI holds the service
 * role key, the browser never queries a table). So a session here is a front
 * door, not row-level security, and nothing downstream reads the user.
 *
 * When Supabase is not configured the app is left open and says so on every
 * screen. The alternative — a login nobody can pass — makes the product
 * unrunnable for anyone who clones it, and a fake local session that *looks*
 * like a login would be the dishonest option.
 */
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import type { Session } from '@supabase/supabase-js';
import { supabase, isConfigured } from '@/lib/supabase';

interface Auth {
  session: Session | null;
  loading: boolean;
  /** True when there is no Supabase to sign in against, so the gate is open. */
  unguarded: boolean;
  email: string | null;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<Auth | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(isConfigured);

  useEffect(() => {
    if (!supabase) return;
    // Read the persisted session first so a refresh does not flash the login
    // screen at somebody who is already signed in.
    supabase.auth.getSession().then(({ data }) => {
      setSession(data.session);
      setLoading(false);
    });
    const { data } = supabase.auth.onAuthStateChange((_event, next) => setSession(next));
    return () => data.subscription.unsubscribe();
  }, []);

  const value = useMemo<Auth>(
    () => ({
      session,
      loading,
      unguarded: !isConfigured,
      email: session?.user.email ?? null,
      signOut: async () => {
        await supabase?.auth.signOut();
      },
    }),
    [session, loading],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): Auth {
  const found = useContext(AuthContext);
  if (!found) throw new Error('useAuth outside AuthProvider');
  return found;
}
