import { useNavigate } from 'react-router-dom';
import { USER_TIER } from '../app/plan';
import { useAuth } from '../auth/authContext';
import { ApiKeysPanel } from '../features/settings/ApiKeysPanel';
import { TokensPanel } from '../features/settings/TokensPanel';

export function SettingsPage() {
  const { userName } = useAuth();
  const navigate = useNavigate();
  const name = userName ?? '';

  return (
    <div className="app-container" style={{ alignItems: 'center', flexDirection: 'column', minHeight: '100vh', position: 'relative', padding: '96px 16px 48px' }}>
      <button style={{ position: 'absolute', top: '32px', left: '40px', background: 'transparent', border: 'none', color: 'var(--text-secondary)', cursor: 'pointer', fontSize: '1rem', display: 'flex', alignItems: 'center', gap: '8px' }} onClick={() => navigate('/')}>
        ← Back to Dashboard
      </button>
      <div style={{ textAlign: 'center', marginBottom: '32px' }}>
        <h1 style={{ fontSize: '2.5rem', margin: 0 }}>Account Settings</h1>
      </div>
      <div className="glass-panel" style={{ width: '100%', maxWidth: '720px', padding: '40px', border: '1px solid rgba(255,255,255,0.05)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '20px', marginBottom: '40px' }}>
          <div style={{ width: '80px', height: '80px', borderRadius: '50%', background: 'linear-gradient(135deg, var(--accent-primary), var(--accent-secondary))', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '2.5rem', fontWeight: 'bold', boxShadow: '0 4px 12px rgba(59,130,246,0.3)' }}>
            {name.charAt(0).toUpperCase()}
          </div>
          <div>
            <h2 style={{ margin: '0 0 8px 0', fontSize: '1.5rem' }}>{name}</h2>
            <div style={{ color: 'var(--text-secondary)' }}>Plan: <span style={{ color: 'var(--accent-primary)', fontWeight: 'bold' }}>{USER_TIER}</span></div>
          </div>
        </div>

        <p style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', marginBottom: '32px' }}>
          Each project chooses its AI model in Project Setup, so everyone on a review uses the same model.
        </p>

        <ApiKeysPanel />

        <TokensPanel />

        <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: '40px' }}>
          <button className="btn-primary" style={{ padding: '12px 32px', borderRadius: '8px' }} onClick={() => navigate('/')}>Done</button>
        </div>
      </div>
    </div>
  );
}
