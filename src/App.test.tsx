// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import { AuthProvider } from './auth/AuthProvider';

const LOGIN = { access_token: 'token-123', token_type: 'bearer', user: { id: 1, email: 'lead@omnireview.test', name: 'Liam Lead' } };
const model = (id: number, provider: string, providerLabel: string, modelId: string, label: string, available: boolean) => ({
  id, provider, provider_label: providerLabel, model_id: modelId, label, is_default: id === 4, enabled: true,
  data_location: `${providerLabel}, somewhere`, available,
});
const GEMINI = model(4, 'gemini', 'Google Gemini', 'gemini-3.8-flash', 'Gemini 3.8 Flash', true);
const MISTRAL = model(17, 'mistral', 'Mistral AI', 'mistral-large-latest', 'Mistral Large (latest)', false);
const PROJECT = { id: 7, title: 'Aspirin review', description: null, role: 'lead_reviewer', member_count: 3, organization: null, ai_model: GEMINI };

const stage = (name: string, label: string, status: string) => ({
  stage: name, label, status, requirements: [], completed_at: null, completed_by: null, completion_note: null,
  reopen_rationale: null, latest_version: null, latest_snapshot_id: null, can_manage: true,
});

const WORKSPACE_API = {
  'GET /api/projects/7': PROJECT,
  'GET /api/projects/7/protocol': { review_type: 'Systematic Review', framework: 'PICO', description: 'Does aspirin help?', suggested_criteria: '', extraction_outline: '', rob_tool: 'ROB-2' },
  'GET /api/projects/7/criteria': [{ id: 1, kind: 'inclusion', text: 'Adults', status: 'accepted' }],
  'GET /api/projects/7/search-strategies': [{ id: 1, database: 'PubMed', query: 'aspirin[tiab]' }],
  'GET /api/projects/7/extraction-fields': ['Sample Size'],
  'GET /api/projects/7/synthesis': null,
  'GET /api/projects/7/records': [{
    id: 11, title: 'Low-dose aspirin trial', authors: 'Smith J', year: '2019', venue: '', doi: '10.1/a', abstract: 'Adults randomized.',
    source: 'PubMed', duplicate_of_id: null, ai_screening: null, my_decision: null, final_decision: null, extraction: null, appraisal: null,
  }],
  'GET /api/projects/7/prisma': { identified_from_databases: 1, identified_from_uploads: 0, by_source: { PubMed: 1 }, duplicates_removed: 0, screened: 1, excluded: 0, included: 0, awaiting_decision: 1 },
  'GET /api/projects/7/workflow': [
    stage('protocol', 'Protocol', 'completed'),
    stage('search', 'Search and deduplication', 'completed'),
    stage('screening', 'Title and abstract screening', 'open'),
  ],
  'GET /api/ai/models': [GEMINI, MISTRAL],
};

// Answers fetch calls from a table keyed by "METHOD /path"; unexpected requests get a 500.
function mockApi(routes: Record<string, unknown>) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const key = `${init?.method ?? 'GET'} ${new URL(String(input)).pathname}`;
    if (!(key in routes)) return new Response(JSON.stringify({ detail: `Unexpected request: ${key}` }), { status: 500 });
    return new Response(JSON.stringify(routes[key]), { status: 200 });
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

function signIn() {
  fireEvent.change(screen.getByPlaceholderText('Email, Inst. Email, or ORCID'), { target: { value: 'lead@omnireview.test' } });
  fireEvent.change(screen.getByPlaceholderText('Password'), { target: { value: 'Review-Dev-2026!' } });
  fireEvent.click(screen.getByRole('button', { name: 'Sign In' }));
}

beforeEach(() => {
  vi.stubGlobal('alert', vi.fn());
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('App routing', () => {
  it('sends signed-out visitors to the login page', () => {
    mockApi({});
    renderApp('/projects/7/screening');

    expect(screen.getByRole('heading', { name: 'Welcome Back' })).toBeTruthy();
  });

  it('signs in and lists the projects the user belongs to', async () => {
    const fetchMock = mockApi({ 'POST /api/auth/login': LOGIN, 'GET /api/projects': [PROJECT] });
    renderApp('/');

    signIn();

    expect(await screen.findByRole('heading', { name: 'Aspirin review' })).toBeTruthy();
    const projectsCall = fetchMock.mock.calls.find(([url]) => String(url).endsWith('/api/projects'));
    expect(projectsCall?.[1]).toMatchObject({ headers: { Authorization: 'Bearer token-123' } });
  });

  it('returns to the requested project screen after signing in and loads the saved workspace', async () => {
    mockApi({ 'POST /api/auth/login': LOGIN, ...WORKSPACE_API });
    renderApp('/projects/7/screening');

    signIn();

    expect(await screen.findByRole('heading', { name: '5. Abstract Screening' })).toBeTruthy();
    expect(screen.getByText('Low-dose aspirin trial')).toBeTruthy();
    expect(screen.getByRole('region', { name: 'Title and abstract screening sign-off' })).toBeTruthy();
  });

  it('pins the project to another AI model and says when it needs an API key', async () => {
    const fetchMock = mockApi({ 'POST /api/auth/login': LOGIN, ...WORKSPACE_API, 'PUT /api/projects/7/ai-model': { ...PROJECT, ai_model: MISTRAL } });
    renderApp('/projects/7/setup');

    signIn();
    const picker = await screen.findByLabelText('AI Model');
    expect((picker as HTMLSelectElement).value).toBe('4');
    fireEvent.change(picker, { target: { value: '17' } });

    expect(await screen.findByText(/No Mistral AI API key is available to you/)).toBeTruthy();
    const pinCall = fetchMock.mock.calls.find(([, init]) => init?.method === 'PUT');
    expect(pinCall?.[1]?.body).toBe(JSON.stringify({ ai_model_id: 17 }));
  });

  it('explains when a project cannot be opened', async () => {
    mockApi({ 'POST /api/auth/login': LOGIN });
    renderApp('/projects/7/setup');

    signIn();

    expect(await screen.findByRole('heading', { name: "This project couldn't be opened" })).toBeTruthy();
  });
});
