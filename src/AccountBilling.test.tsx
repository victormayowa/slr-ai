// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import { AuthProvider } from './auth/AuthProvider';

const LOGIN = { access_token: 'token-123', token_type: 'bearer', user: { id: 1, email: 'lead@omnireview.test', name: 'Liam Lead' } };
const ME = { id: 1, email: 'lead@omnireview.test', name: 'Liam Lead', is_platform_admin: false, email_verified: true, mfa_enabled: false, terms_version: 'v1', current_terms_version: 'v1', organizations: [] };
const LIMIT_LABELS = {
  projects: 'projects', members_per_project: 'members in a project', records_per_month: 'records added this month',
  ai_credits_per_month: 'AI credits this month', storage_mb: 'MB of stored documents', living_schedules: 'active surveillance schedules',
  compute_minutes_per_month: 'minutes of analysis compute this month',
};
const plan = (code: string, name: string, monthly: number | null, projects: number | null) => ({
  code, name, description: `${name} plan`, monthly_price_cents: monthly, yearly_price_cents: monthly === null ? null : monthly * 10, currency: 'USD',
  limits: { projects, members_per_project: 5, records_per_month: 2000, ai_credits_per_month: 200, storage_mb: 500, living_schedules: 0, compute_minutes_per_month: 30, api_access: code !== 'free', webhooks: false },
  online_intervals: [],
});
const CONFIG = { enabled: true, provider: 'dev', provider_label: 'Simulated payments (development)', checkout_available: true, limit_labels: LIMIT_LABELS, feature_labels: { api_access: 'API access with personal tokens', webhooks: 'webhooks' }, platform_ai_keys: true };
const PLANS = { ...CONFIG, plans: [plan('free', 'Free', 0, 1), plan('researcher', 'Researcher', 2900, 5), plan('institution', 'Institution', null, null)] };

function mockApi(routes: Record<string, unknown>) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const key = `${init?.method ?? 'GET'} ${new URL(String(input)).pathname}`;
    if (!(key in routes)) return new Response(JSON.stringify({ detail: `Unexpected request: ${key}` }), { status: 500 });
    const value = routes[key];
    if (value instanceof Response) return value;
    return new Response(JSON.stringify(value), { status: 200 });
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function renderApp(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AuthProvider>
        <App />
      </AuthProvider>
    </MemoryRouter>,
  );
}

function fillSignIn() {
  fireEvent.change(screen.getByPlaceholderText('Email, Inst. Email, or ORCID'), { target: { value: 'lead@omnireview.test' } });
  fireEvent.change(screen.getByPlaceholderText('Password'), { target: { value: 'Review-Dev-2026!' } });
  fireEvent.click(screen.getByRole('button', { name: 'Sign In' }));
}

const body = (fetchMock: ReturnType<typeof mockApi>, method: string, path: string) => {
  const call = fetchMock.mock.calls.find(([url, init]) => init?.method === method && new URL(String(url)).pathname === path);
  return call ? JSON.parse(String(call[1]?.body)) : undefined;
};

beforeEach(() => {
  vi.stubGlobal('alert', vi.fn());
  window.localStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('Accounts and billing', () => {
  it('only creates an account once the terms are accepted', async () => {
    const fetchMock = mockApi({ 'POST /api/auth/register': { message: 'User created successfully', verification_required: true }, 'GET /api/legal/terms': { slug: 'terms', title: 'Terms', version: 'v1', content: '# Terms' } });
    renderApp('/login');

    fireEvent.click(screen.getByText('Create Account'));
    fireEvent.change(screen.getByPlaceholderText('First Name'), { target: { value: 'Ada' } });
    fireEvent.change(screen.getByPlaceholderText('Last Name'), { target: { value: 'Lovelace' } });
    fireEvent.change(screen.getByPlaceholderText('Personal Email Address'), { target: { value: 'ada@example.org' } });
    fireEvent.change(screen.getByPlaceholderText('Create Password'), { target: { value: 'a long password' } });
    fireEvent.change(screen.getByPlaceholderText('Confirm Password'), { target: { value: 'a long password' } });
    fireEvent.change(screen.getByDisplayValue('Select Position / Role'), { target: { value: 'Researcher' } });
    fireEvent.change(screen.getByPlaceholderText('Institution / Organization'), { target: { value: 'Example University' } });
    fireEvent.change(screen.getByPlaceholderText('Reason for Joining'), { target: { value: 'Reviews' } });

    fireEvent.click(screen.getByRole('button', { name: 'Sign Up' }));
    expect(await screen.findByText('Accept the Terms of Service and Privacy Policy to create an account')).toBeTruthy();
    expect(body(fetchMock, 'POST', '/api/auth/register')).toBeUndefined();

    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: 'Sign Up' }));
    expect(await screen.findByText(/Open the link we sent to ada@example.org/)).toBeTruthy();
    expect(body(fetchMock, 'POST', '/api/auth/register')).toMatchObject({ email: 'ada@example.org', accept_terms: true });
  });

  it('asks for a two-factor code before signing in', async () => {
    const fetchMock = mockApi({
      'POST /api/auth/login': { mfa_required: true, mfa_token: 'mfa-token-1' },
      'POST /api/auth/mfa/verify': LOGIN,
      'GET /api/projects': [],
      'GET /api/auth/me': ME,
      'GET /api/billing/accounts': [{ kind: 'user', id: 1, label: 'Liam Lead', plan: 'Free' }],
      'GET /api/notifications': { unread: 0, notifications: [] },
    });
    renderApp('/');

    fillSignIn();
    fireEvent.change(await screen.findByLabelText('Authentication code'), { target: { value: '123456' } });
    fireEvent.click(screen.getByRole('button', { name: 'Verify' }));

    expect(await screen.findByText('Your Active Projects')).toBeTruthy();
    expect(body(fetchMock, 'POST', '/api/auth/mfa/verify')).toEqual({ mfa_token: 'mfa-token-1', code: '123456' });
    expect(await screen.findByText('Free plan')).toBeTruthy();
  });

  it('requests a password reset link', async () => {
    const message = "If an account uses that address, we've sent it a link to reset the password.";
    const fetchMock = mockApi({ 'POST /api/auth/password-reset/request': { message } });
    renderApp('/login');

    fireEvent.click(screen.getByText('Forgot Password?'));
    fireEvent.change(screen.getByLabelText('Email address for password reset'), { target: { value: 'ada@example.org' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send Reset Link' }));

    expect(await screen.findByText(message)).toBeTruthy();
    expect(body(fetchMock, 'POST', '/api/auth/password-reset/request')).toEqual({ email: 'ada@example.org' });
  });

  it('sets a new password from a reset link', async () => {
    const fetchMock = mockApi({ 'POST /api/auth/password-reset/confirm': { message: 'Changed' } });
    renderApp('/reset-password?token=reset-token-123');

    fireEvent.change(screen.getByLabelText('New password'), { target: { value: 'a brand new password' } });
    fireEvent.change(screen.getByLabelText('Confirm new password'), { target: { value: 'a brand new password' } });
    fireEvent.click(screen.getByRole('button', { name: 'Change password' }));

    expect(await screen.findByText(/Your password has been changed/)).toBeTruthy();
    expect(body(fetchMock, 'POST', '/api/auth/password-reset/confirm')).toEqual({ token: 'reset-token-123', password: 'a brand new password' });
  });

  it('shows the plans to visitors who are not signed in', async () => {
    mockApi({ 'GET /api/billing/plans': PLANS });
    renderApp('/pricing');

    expect(await screen.findByRole('region', { name: 'Researcher plan' })).toBeTruthy();
    expect(screen.getByText('$29 / month')).toBeTruthy();
    expect(screen.getByText('Contact us')).toBeTruthy();
    expect(screen.getAllByRole('button', { name: 'Create an account' }).length).toBeGreaterThan(0);
  });

  it('leaves AI credits off the plans when the server uses only users\' own AI keys', async () => {
    mockApi({ 'GET /api/billing/plans': { ...PLANS, platform_ai_keys: false } });
    renderApp('/pricing');

    const researcher = await screen.findByRole('region', { name: 'Researcher plan' });
    expect(within(researcher).queryByText(/AI credits/)).toBeNull();
    expect(screen.getByText(/AI features use your own provider API keys/)).toBeTruthy();
  });

  it('shows usage against the plan and starts a checkout', async () => {
    const account = {
      ...CONFIG, kind: 'user', id: 1, label: 'Liam Lead', plan: plan('free', 'Free', 0, 1), subscription: null,
      usage: { projects: 1, members_per_project: 2, records_per_month: 1900, ai_credits_per_month: 12.5, storage_mb: 3, living_schedules: 0, compute_minutes_per_month: 0 },
      usage_resets_on: '2026-10-01T00:00:00Z',
    };
    const fetchMock = mockApi({
      'POST /api/auth/login': LOGIN,
      'GET /api/billing/accounts': [{ kind: 'user', id: 1, label: 'Liam Lead', plan: 'Free' }],
      'GET /api/billing/plans': PLANS,
      'GET /api/billing/accounts/user/1': account,
      'POST /api/billing/accounts/user/1/checkout': { url: 'http://localhost:3000/billing/dev-checkout?session=abc.def', kind: 'checkout' },
    });
    renderApp('/billing');

    fillSignIn();

    expect(await screen.findByRole('meter', { name: 'records added this month' })).toBeTruthy();
    expect(screen.getByText('1,900 / 2,000')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Choose Researcher' }));

    expect(await screen.findByText('Simulated checkout for development. No real payment is taken.')).toBeTruthy();
    expect(body(fetchMock, 'POST', '/api/billing/accounts/user/1/checkout')).toEqual({ plan_code: 'researcher', interval: 'month' });
  });

  it('renders a legal document safely', async () => {
    mockApi({
      'GET /api/legal/privacy': { slug: 'privacy', title: 'Privacy Policy', version: 'v1', content: '# Privacy Policy\n\n- **Account details:** your name\n\n<script>alert(1)</script>' },
    });
    renderApp('/legal/privacy');

    expect(await screen.findByRole('heading', { name: 'Privacy Policy' })).toBeTruthy();
    expect(screen.getByText('Account details:')).toBeTruthy();
    // Text that looks like HTML is shown as text, never inserted as markup.
    expect(document.querySelector('script')).toBeNull();
    expect(screen.getByText('<script>alert(1)</script>')).toBeTruthy();
  });

  it('remembers that the cookie notice was dismissed', async () => {
    mockApi({ 'GET /api/billing/plans': PLANS });
    const { unmount } = renderApp('/pricing');

    fireEvent.click(screen.getByRole('button', { name: 'Got it' }));
    expect(screen.queryByRole('region', { name: 'Cookie notice' })).toBeNull();
    unmount();

    renderApp('/pricing');
    expect(screen.queryByRole('region', { name: 'Cookie notice' })).toBeNull();
  });

  it('keeps administration for platform administrators', async () => {
    mockApi({ 'POST /api/auth/login': LOGIN, 'GET /api/auth/me': ME });
    renderApp('/admin');

    fillSignIn();

    expect(await screen.findByRole('heading', { name: 'Administrators only' })).toBeTruthy();
  });
});
