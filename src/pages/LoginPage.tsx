import { useState } from 'react';
import { Link } from 'react-router-dom';
import { errorMessage, requestJson } from '../api/client';
import { useAuth } from '../auth/authContext';
import { LegalLinks } from '../components/LegalLinks';
import { DEMO_ACCOUNTS, DEMO_PASSWORD } from '../dev/demoAccounts';

type Mode = 'login' | 'register' | 'forgot' | 'mfa';

export function LoginPage() {
  const { signIn } = useAuth();
  const [authMode, setAuthMode] = useState<Mode>('login');
  const [authError, setAuthError] = useState('');
  const [authNotice, setAuthNotice] = useState('');

  const [loginIdentifier, setLoginIdentifier] = useState('');
  const [loginPassword, setLoginPassword] = useState('');
  const [mfaToken, setMfaToken] = useState('');
  const [mfaCode, setMfaCode] = useState('');
  const [resetEmail, setResetEmail] = useState('');

  const [regFirstName, setRegFirstName] = useState('');
  const [regLastName, setRegLastName] = useState('');
  const [regEmail, setRegEmail] = useState('');
  const [regInstEmail, setRegInstEmail] = useState('');
  const [regOrcid, setRegOrcid] = useState('');
  const [regPassword, setRegPassword] = useState('');
  const [regConfirmPassword, setRegConfirmPassword] = useState('');
  const [regRole, setRegRole] = useState('');
  const [regInstitution, setRegInstitution] = useState('');
  const [regReason, setRegReason] = useState('');
  const [regTerms, setRegTerms] = useState(false);

  const switchMode = (mode: Mode) => {
    setAuthMode(mode);
    setAuthError('');
    setAuthNotice('');
  };

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setAuthError('');
    setAuthNotice('');
    try {
      const data = await requestJson('POST', '/api/auth/login', { identifier: loginIdentifier, password: loginPassword });
      if (data.mfa_required) {
        setMfaToken(data.mfa_token);
        setMfaCode('');
        setAuthMode('mfa');
        return;
      }
      // Signing in re-renders the login route, which sends the user to the page they originally requested.
      signIn(data.access_token, data.user.name);
    } catch (err) {
      setAuthError(errorMessage(err, 'Network error'));
    }
  };

  const handleMfa = async (e: React.FormEvent) => {
    e.preventDefault();
    setAuthError('');
    try {
      const data = await requestJson('POST', '/api/auth/mfa/verify', { mfa_token: mfaToken, code: mfaCode.trim() });
      signIn(data.access_token, data.user.name);
    } catch (err) {
      // The sign-in step is used up by a wrong code, so the person signs in again.
      setAuthMode('login');
      setAuthError(errorMessage(err, 'That code was not accepted. Sign in again.'));
    }
  };

  const handleForgot = async (e: React.FormEvent) => {
    e.preventDefault();
    setAuthError('');
    try {
      const data = await requestJson('POST', '/api/auth/password-reset/request', { email: resetEmail.trim() });
      setAuthMode('login');
      setAuthNotice(data.message);
    } catch (err) {
      setAuthError(errorMessage(err, 'The reset link could not be requested.'));
    }
  };

  const handleRegister = async (e: React.FormEvent) => {
    e.preventDefault();
    setAuthError('');
    if (regPassword !== regConfirmPassword) {
      setAuthError('Passwords do not match');
      return;
    }
    if (!regRole) {
      setAuthError('Please select a position/role');
      return;
    }
    if (!regTerms) {
      setAuthError('Accept the Terms of Service and Privacy Policy to create an account');
      return;
    }

    try {
      const data = await requestJson('POST', '/api/auth/register', {
        first_name: regFirstName,
        last_name: regLastName,
        email: regEmail,
        institutional_email: regInstEmail || null,
        orcid_id: regOrcid || null,
        password: regPassword,
        position_role: regRole,
        institution: regInstitution,
        reason_for_joining: regReason,
        accept_terms: regTerms,
      });
      setAuthMode('login');
      setLoginIdentifier(regEmail);
      setAuthNotice(
        data.verification_required
          ? `Account created. Open the link we sent to ${regEmail} to confirm your address, then sign in.`
          : `Account created. We sent a confirmation link to ${regEmail}; you can sign in now.`,
      );
    } catch (err) {
      setAuthError(errorMessage(err, 'Network error'));
    }
  };

  return (
    <div className="app-container" style={{ justifyContent: 'center', alignItems: 'center', flexDirection: 'column', minHeight: '100vh', background: 'radial-gradient(circle at 50% 50%, #1e1e2f 0%, #0f0f17 100%)' }}>
      <div style={{ textAlign: 'center', marginBottom: '32px' }}>
        <div style={{ width: '64px', height: '64px', borderRadius: '16px', background: 'linear-gradient(135deg, var(--accent-primary), var(--accent-secondary))', display: 'flex', alignItems: 'center', justifyContent: 'center', fontWeight: 'bold', fontSize: '2rem', margin: '0 auto 16px', boxShadow: '0 8px 24px rgba(59,130,246,0.3)' }}>O</div>
        <h1 style={{ fontSize: '2rem', margin: 0, fontWeight: 700, letterSpacing: '-0.5px' }}>OmniReview AI</h1>
      </div>
      <div className="glass-panel animate-fade-in" style={{ width: '100%', maxWidth: '400px', padding: '40px', border: '1px solid rgba(255,255,255,0.1)' }}>
        {authError && (
          <div role="alert" style={{ background: 'rgba(239, 68, 68, 0.1)', color: '#ef4444', padding: '12px', borderRadius: '8px', marginBottom: '24px', fontSize: '0.9rem', textAlign: 'center', border: '1px solid rgba(239, 68, 68, 0.2)' }}>
            {authError}
          </div>
        )}
        {authNotice && (
          <div role="status" style={{ background: 'rgba(16, 185, 129, 0.1)', color: '#10b981', padding: '12px', borderRadius: '8px', marginBottom: '24px', fontSize: '0.9rem', textAlign: 'center', border: '1px solid rgba(16, 185, 129, 0.2)' }}>
            {authNotice}
          </div>
        )}
        {authMode === 'login' && (
          <form onSubmit={handleLogin}>
            <h2 style={{ marginBottom: '24px', textAlign: 'center', fontSize: '1.5rem', fontWeight: 600 }}>Welcome Back</h2>
            {import.meta.env.DEV && (
              <select className="search-input" style={{ width: '100%', marginBottom: '16px' }} value="" onChange={e => { if (e.target.value) { setLoginIdentifier(e.target.value); setLoginPassword(DEMO_PASSWORD); } }}>
                <option value="">Development only: fill in a demo account…</option>
                {DEMO_ACCOUNTS.map(account => <option key={account.email} value={account.email}>{account.label}</option>)}
              </select>
            )}
            <input type="text" placeholder="Email, Inst. Email, or ORCID" required value={loginIdentifier} onChange={e => setLoginIdentifier(e.target.value)} className="search-input" style={{ width: '100%', marginBottom: '16px' }} />
            <input type="password" placeholder="Password" required value={loginPassword} onChange={e => setLoginPassword(e.target.value)} className="search-input" style={{ width: '100%', marginBottom: '24px' }} />
            <button type="submit" className="btn-primary" style={{ width: '100%', marginBottom: '16px', height: '44px', fontWeight: 600 }}>Sign In</button>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.9rem' }}>
              <a href="#" style={{ color: 'var(--text-secondary)', textDecoration: 'none', transition: 'color 0.2s' }} onClick={(e) => { e.preventDefault(); setResetEmail(loginIdentifier.includes('@') ? loginIdentifier : ''); switchMode('forgot'); }}>Forgot Password?</a>
              <a href="#" style={{ color: 'var(--accent-primary)', textDecoration: 'none', fontWeight: 500 }} onClick={(e) => { e.preventDefault(); switchMode('register'); }}>Create Account</a>
            </div>
          </form>
        )}
        {authMode === 'mfa' && (
          <form onSubmit={handleMfa}>
            <h2 style={{ marginBottom: '16px', textAlign: 'center', fontSize: '1.5rem', fontWeight: 600 }}>Two-factor sign-in</h2>
            <p style={{ color: 'var(--text-secondary)', fontSize: '0.9rem', marginBottom: '20px', textAlign: 'center' }}>Enter the 6-digit code from your authenticator app, or one of your recovery codes.</p>
            <input aria-label="Authentication code" type="text" inputMode="text" autoComplete="one-time-code" autoFocus required value={mfaCode} onChange={e => setMfaCode(e.target.value)} className="search-input" style={{ width: '100%', marginBottom: '20px', letterSpacing: '0.2em', textAlign: 'center' }} />
            <button type="submit" className="btn-primary" style={{ width: '100%', marginBottom: '16px', height: '44px', fontWeight: 600 }}>Verify</button>
            <div style={{ textAlign: 'center', fontSize: '0.9rem' }}>
              <a href="#" style={{ color: 'var(--text-secondary)', textDecoration: 'none' }} onClick={(e) => { e.preventDefault(); switchMode('login'); }}>← Back to Login</a>
            </div>
          </form>
        )}
        {authMode === 'register' && (
          <form onSubmit={handleRegister} style={{ maxHeight: '60vh', overflowY: 'auto', paddingRight: '8px' }}>
            <h2 style={{ marginBottom: '24px', textAlign: 'center', fontSize: '1.5rem', fontWeight: 600 }}>Create Account</h2>
            <div style={{ display: 'flex', gap: '12px', marginBottom: '16px' }}>
              <input type="text" placeholder="First Name" required value={regFirstName} onChange={e => setRegFirstName(e.target.value)} className="search-input" style={{ width: '50%' }} />
              <input type="text" placeholder="Last Name" required value={regLastName} onChange={e => setRegLastName(e.target.value)} className="search-input" style={{ width: '50%' }} />
            </div>
            <input type="email" placeholder="Personal Email Address" required value={regEmail} onChange={e => setRegEmail(e.target.value)} className="search-input" style={{ width: '100%', marginBottom: '16px' }} />
            <input type="email" placeholder="Institutional Email (Optional)" value={regInstEmail} onChange={e => setRegInstEmail(e.target.value)} className="search-input" style={{ width: '100%', marginBottom: '16px' }} />
            <input type="text" placeholder="ORCID iD (Optional)" value={regOrcid} onChange={e => setRegOrcid(e.target.value)} className="search-input" style={{ width: '100%', marginBottom: '16px' }} />

            <div style={{ display: 'flex', gap: '12px', marginBottom: '16px' }}>
              <input type="password" placeholder="Create Password" required minLength={8} value={regPassword} onChange={e => setRegPassword(e.target.value)} className="search-input" style={{ width: '50%' }} />
              <input type="password" placeholder="Confirm Password" required minLength={8} value={regConfirmPassword} onChange={e => setRegConfirmPassword(e.target.value)} className="search-input" style={{ width: '50%' }} />
            </div>

            <select required value={regRole} onChange={e => setRegRole(e.target.value)} className="search-input" style={{ width: '100%', marginBottom: '16px' }}>
              <option value="" disabled>Select Position / Role</option>
              <option value="Undergrad Student">Undergrad Student</option>
              <option value="Graduate Student">Graduate Student</option>
              <option value="PhD Student">PhD Student</option>
              <option value="Professor">Professor</option>
              <option value="Librarian">Librarian</option>
              <option value="Biomedical Researcher">Biomedical Researcher</option>
              <option value="Engineer Researcher">Engineer Researcher</option>
              <option value="Researcher">Researcher</option>
              <option value="Others">Others</option>
            </select>

            <input type="text" placeholder="Institution / Organization" required value={regInstitution} onChange={e => setRegInstitution(e.target.value)} className="search-input" style={{ width: '100%', marginBottom: '16px' }} />
            <input type="text" placeholder="Reason for Joining" required value={regReason} onChange={e => setRegReason(e.target.value)} className="search-input" style={{ width: '100%', marginBottom: '16px' }} />

            <label style={{ display: 'flex', gap: '8px', alignItems: 'flex-start', fontSize: '0.85rem', color: 'var(--text-secondary)', marginBottom: '24px' }}>
              <input type="checkbox" checked={regTerms} onChange={e => setRegTerms(e.target.checked)} style={{ marginTop: '3px' }} />
              <span>
                I agree to the <Link to="/legal/terms" target="_blank" style={{ color: 'var(--accent-primary)' }}>Terms of Service</Link> and have read the{' '}
                <Link to="/legal/privacy" target="_blank" style={{ color: 'var(--accent-primary)' }}>Privacy Policy</Link>.
              </span>
            </label>

            <button type="submit" className="btn-primary" style={{ width: '100%', marginBottom: '16px', height: '44px', fontWeight: 600 }}>Sign Up</button>
            <div style={{ textAlign: 'center', fontSize: '0.9rem' }}>
              <span style={{ color: 'var(--text-secondary)' }}>Already have an account? </span>
              <a href="#" style={{ color: 'var(--accent-primary)', textDecoration: 'none', fontWeight: 500 }} onClick={(e) => { e.preventDefault(); switchMode('login'); }}>Sign In</a>
            </div>
          </form>
        )}
        {authMode === 'forgot' && (
          <form onSubmit={handleForgot}>
            <h2 style={{ marginBottom: '16px', textAlign: 'center', fontSize: '1.5rem', fontWeight: 600 }}>Reset Password</h2>
            <p style={{ color: 'var(--text-secondary)', fontSize: '0.9rem', marginBottom: '24px', textAlign: 'center', lineHeight: '1.5' }}>Enter your account's email address and we'll send you a link to reset your password.</p>
            <input aria-label="Email address for password reset" type="email" placeholder="Email Address" required value={resetEmail} onChange={e => setResetEmail(e.target.value)} className="search-input" style={{ width: '100%', marginBottom: '24px' }} />
            <button type="submit" className="btn-primary" style={{ width: '100%', marginBottom: '16px', height: '44px', fontWeight: 600 }}>Send Reset Link</button>
            <div style={{ textAlign: 'center', fontSize: '0.9rem' }}>
              <a href="#" style={{ color: 'var(--text-secondary)', textDecoration: 'none', transition: 'color 0.2s' }} onClick={(e) => { e.preventDefault(); switchMode('login'); }}>← Back to Login</a>
            </div>
          </form>
        )}
      </div>
      <div style={{ marginTop: '24px' }}>
        <LegalLinks />
      </div>
    </div>
  );
}
