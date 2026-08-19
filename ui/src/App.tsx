/** Routes, and the frame every screen sits in.
 *
 * The frame is deliberately thin — a rail, a board name, and where you are in
 * the pipeline. The pipeline order is the information: Whiteboard → Review →
 * Spec → Runs is the product's own sequence, and showing it as a path rather
 * than as a menu is what tells somebody what comes next.
 */
import { Navigate, Route, Routes, useParams } from 'react-router-dom';
import { AuthProvider, useAuth } from '@/features/auth/session';
import { Spinner } from '@/components/ui';
import Shell from '@/routes/Shell';
import Login from '@/routes/Login';
import Boards from '@/routes/Boards';
import Whiteboard from '@/routes/Whiteboard';
import Spec from '@/routes/Spec';
import Runs from '@/routes/Runs';
import GymPage from '@/routes/Gym';

/** Signed in, or the gate is open because there is no gate. */
function Guarded({ children }: { children: React.ReactNode }) {
  const { session, loading, unguarded } = useAuth();
  if (loading) return <Spinner label="Restoring session…" />;
  if (!session && !unguarded) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

/** Reads `:boardId` once so the screens below it never parse the URL. */
function BoardScoped({ page }: { page: 'board' | 'spec' }) {
  const { boardId } = useParams<{ boardId: string }>();
  if (!boardId) return <Navigate to="/boards" replace />;
  return (
    <Shell boardId={boardId}>
      {page === 'board' ? <Whiteboard boardId={boardId} /> : <Spec boardId={boardId} />}
    </Shell>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route
          path="/boards"
          element={
            <Guarded>
              <Shell>
                <Boards />
              </Shell>
            </Guarded>
          }
        />
        <Route
          path="/boards/:boardId"
          element={
            <Guarded>
              <BoardScoped page="board" />
            </Guarded>
          }
        />
        <Route
          path="/boards/:boardId/spec"
          element={
            <Guarded>
              <BoardScoped page="spec" />
            </Guarded>
          }
        />
        <Route
          path="/runs"
          element={
            <Guarded>
              <Shell>
                <Runs />
              </Shell>
            </Guarded>
          }
        />
        <Route
          path="/gym"
          element={
            <Guarded>
              <Shell>
                <GymPage />
              </Shell>
            </Guarded>
          }
        />
        <Route path="*" element={<Navigate to="/boards" replace />} />
      </Routes>
    </AuthProvider>
  );
}
