import { useNavigate } from 'react-router-dom';
import { USER_TIER } from '../app/plan';
import { usePreferences, type AiProvider } from '../app/preferences';
import { useAuth } from '../auth/authContext';

export function SettingsPage() {
  const { userName } = useAuth();
  const { aiProvider, setAiProvider } = usePreferences();
  const navigate = useNavigate();
  const name = userName ?? '';

  return (
    <div className="app-container" style={{ justifyContent: 'center', alignItems: 'center', flexDirection: 'column', height: '100vh', position: 'relative' }}>
      <button style={{ position: 'absolute', top: '32px', left: '40px', background: 'transparent', border: 'none', color: 'var(--text-secondary)', cursor: 'pointer', fontSize: '1rem', display: 'flex', alignItems: 'center', gap: '8px' }} onClick={() => navigate('/')}>
        ← Back to Dashboard
      </button>
      <div style={{ textAlign: 'center', marginBottom: '32px' }}>
        <h1 style={{ fontSize: '2.5rem', margin: 0 }}>Account Settings</h1>
      </div>
      <div className="glass-panel" style={{ width: '100%', maxWidth: '600px', padding: '40px', border: '1px solid rgba(255,255,255,0.05)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '20px', marginBottom: '40px' }}>
          <div style={{ width: '80px', height: '80px', borderRadius: '50%', background: 'linear-gradient(135deg, var(--accent-primary), var(--accent-secondary))', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '2.5rem', fontWeight: 'bold', boxShadow: '0 4px 12px rgba(59,130,246,0.3)' }}>
            {name.charAt(0).toUpperCase()}
          </div>
          <div>
            <h2 style={{ margin: '0 0 8px 0', fontSize: '1.5rem' }}>{name}</h2>
            <div style={{ color: 'var(--text-secondary)' }}>Plan: <span style={{ color: 'var(--accent-primary)', fontWeight: 'bold' }}>{USER_TIER}</span></div>
          </div>
        </div>

        <h3 style={{ borderBottom: '1px solid rgba(255,255,255,0.1)', paddingBottom: '12px', marginBottom: '24px' }}>AI Configuration</h3>
        <div style={{ marginBottom: '24px' }}>
          <label style={{ display: 'block', fontSize: '0.9rem', marginBottom: '8px', color: 'var(--text-secondary)' }}>Default AI Model</label>
          <select className="search-input" style={{ width: '100%', padding: '12px', fontSize: '1rem' }} value={aiProvider} onChange={e => setAiProvider(e.target.value as AiProvider)}>
            <option value="gemini">Google Gemini (Flash) - Free Tier</option>
            <option value="openai">OpenAI (GPT-4o) - Pro Plan</option>
            <option value="anthropic">Anthropic (Claude) - Pro Plan</option>
          </select>
          <p style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginTop: '8px' }}>Select the model engine used for Protocol Generation, Screening, and Meta-Analysis. Your choice is remembered in this browser.</p>
        </div>

        <h3 style={{ borderBottom: '1px solid rgba(255,255,255,0.1)', paddingBottom: '12px', marginBottom: '24px', marginTop: '40px' }}>Subscription & API Keys</h3>
        <div style={{ marginBottom: '24px' }}>
          <label style={{ display: 'block', fontSize: '0.9rem', marginBottom: '8px', color: 'var(--text-secondary)' }}>Bring Your Own Key (Optional)</label>
          <input type="password" placeholder="sk-..." className="search-input" style={{ width: '100%', padding: '12px' }} />
          <p style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginTop: '8px' }}>Bypass plan rate limits by using your own API key.</p>
        </div>

        <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: '40px' }}>
          <button className="btn-primary" style={{ padding: '12px 32px', borderRadius: '8px' }} onClick={() => navigate('/')}>Done</button>
        </div>
      </div>
    </div>
  );
}
