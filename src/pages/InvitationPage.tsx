import { useEffect, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { errorMessage } from '../api/client';
import { useAuth } from '../auth/authContext';

// Accepts a project invitation for the signed-in account. RequireAuth sends people to sign in first and returns them
// here, so the invited address can be checked against the account that is actually signed in.
export function InvitationPage() {
  const { token } = useParams();
  const { apiRequest } = useAuth();
  const navigate = useNavigate();
  const [problem, setProblem] = useState<string | null>(null);
  const [joined, setJoined] = useState<{ project_id: number; title: string; role: string } | null>(null);
  // Development mode mounts twice; the second attempt would fail because the invitation was already used.
  const attempted = useRef(false);

  useEffect(() => {
    if (!token || attempted.current) return;
    attempted.current = true;
    apiRequest('POST', '/api/invitations/accept', { token })
      .then(setJoined)
      .catch(err => setProblem(errorMessage(err, 'That invitation could not be accepted.')));
  }, [apiRequest, token]);

  return (
    <div className="app-container" style={{ justifyContent: 'center', alignItems: 'center', flexDirection: 'column', height: '100vh' }}>
      <div className="glass-panel" style={{ maxWidth: '520px', padding: '40px', textAlign: 'center' }}>
        {joined ? (
          <>
            <h2 style={{ marginTop: 0 }}>You've joined {joined.title}</h2>
            <p style={{ color: 'var(--text-secondary)' }}>Your role is {joined.role.replace(/_/g, ' ')}.</p>
            <button className="btn-primary" style={{ padding: '10px 24px', borderRadius: '8px' }} onClick={() => navigate(`/projects/${joined.project_id}/setup`)}>
              Open the review
            </button>
          </>
        ) : problem ? (
          <>
            <h2 style={{ marginTop: 0 }}>This invitation couldn't be used</h2>
            <p role="alert" style={{ color: 'var(--text-secondary)' }}>{problem}</p>
            <button className="btn-primary" style={{ padding: '10px 24px', borderRadius: '8px' }} onClick={() => navigate('/')}>
              Back to your projects
            </button>
          </>
        ) : (
          <p style={{ color: 'var(--text-secondary)' }}>Accepting your invitation…</p>
        )}
      </div>
    </div>
  );
}
