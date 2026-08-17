import { QueryClient } from '@tanstack/react-query';

/** Server state is almost all of the state, so the defaults matter.
 *
 * `retry: false` because every failure here is a refusal worth showing — a 422
 * carrying lint findings retried three times is three seconds of silence and
 * then the same answer. `refetchOnWindowFocus: false` because a canvas somebody
 * is dragging on must not reorder itself when they alt-tab back. */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: false, refetchOnWindowFocus: false, staleTime: 5_000 },
    mutations: { retry: false },
  },
});
