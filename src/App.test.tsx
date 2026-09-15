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
const CATALOG = {
  frameworks: [
    { key: 'PICO', label: 'PICO', use_for: 'Intervention reviews', elements: [
      { key: 'population', label: 'Population', hint: 'Who is studied' },
      { key: 'intervention', label: 'Intervention', hint: 'The treatment' },
    ] },
    { key: 'PCC', label: 'PCC', use_for: 'Scoping reviews', elements: [
      { key: 'population', label: 'Population', hint: 'Who is studied' },
      { key: 'concept', label: 'Concept', hint: 'The main idea' },
    ] },
  ],
  general_criterion_elements: [{ key: 'other', label: 'Other', hint: '' }],
  finer_criteria: [{ key: 'novel', label: 'Novel', hint: 'Not already answered' }],
  finer_ratings: ['yes', 'partly', 'no'],
  sections: [],
  synthesis_approaches: [],
  outcome_priorities: [],
};
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
  'GET /api/projects/7/criteria': [{ id: 1, kind: 'inclusion', text: 'Adults', status: 'accepted', element: 'population', source: 'ai' }],
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
  'GET /api/projects/7/review-settings': {
    screening: { title_abstract_reviewers: 1, full_text_reviewers: 1, blind_dual_screening: true, recall_target: 0.95, stopping_alpha: 0.05, retrain_every: 10, custom_exclusion_reasons: [] },
    extraction: { mode: 'single', numeric_absolute_tolerance: 0, numeric_relative_tolerance: 0 },
    exclusion_reasons: [{ code: 'wrong_population', label: 'Wrong population' }],
  },
  'GET /api/projects/7/screening/queue': {
    stage: 'title_abstract', remaining: 1, model: null,
    records: [{
      id: 11, title: 'Low-dose aspirin trial', authors: 'Smith J', year: '2019', venue: '', doi: '10.1/a', abstract: 'Adults randomized.', source: 'PubMed',
      screening: { title_abstract: { state: 'unscreened', final_decision: null, reason_code: null, my_decision: null, my_reason_code: null, my_note: null, reviewers_decided: 0, reviewers_required: 1 }, full_text: { state: 'unscreened', final_decision: null, reason_code: null, my_decision: null, my_reason_code: null, my_note: null, reviewers_decided: 0, reviewers_required: 1 } },
      ai_screening: null, ai_screening_hidden: false, ai_full_text_screening: null, priority: null,
    }],
  },
  'GET /api/protocol-frameworks': CATALOG,
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

    expect(await screen.findByRole('heading', { name: 'Abstract Screening' })).toBeTruthy();
    expect(await screen.findByText('Low-dose aspirin trial')).toBeTruthy();
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

  it('applies an AI question suggestion only when the reviewer saves it', async () => {
    const question = { framework: 'PICO', question: '', elements: { population: '', intervention: '' }, finer: {} };
    const suggestion = {
      id: 5, kind: 'question', section_key: null, provider: 'gemini', model: 'gemini-3.8-flash', created_at: '2026-09-15T00:00:00Z',
      content: { framework: 'PCC', question: 'What is known about aspirin use?', elements: { population: 'Adults', concept: '' }, finer_notes: { novel: 'Look for recent reviews.' } },
    };
    const fetchMock = mockApi({
      'POST /api/auth/login': LOGIN, ...WORKSPACE_API,
      'GET /api/projects/7/question': question,
      'POST /api/projects/7/question/ai': suggestion,
      'PUT /api/projects/7/question': { ...question, framework: 'PCC', question: 'What is known about aspirin use?', elements: { population: 'Adults', concept: '' } },
    });
    renderApp('/projects/7/question');

    signIn();
    fireEvent.click(await screen.findByRole('button', { name: /Suggest with/ }));
    fireEvent.click(await screen.findByRole('button', { name: 'Apply suggestion' }));

    expect((screen.getByLabelText('Population') as HTMLInputElement).value).toBe('Adults');
    expect(screen.getByLabelText('Concept')).toBeTruthy();
    expect(screen.getByText('AI prompt to consider: Look for recent reviews.')).toBeTruthy();
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'PUT')).toBe(false);

    fireEvent.click(screen.getByRole('button', { name: 'Save review question' }));
    expect(await screen.findByText('Review question saved.')).toBeTruthy();
    const saved = fetchMock.mock.calls.find(([, init]) => init?.method === 'PUT');
    expect(JSON.parse(String(saved?.[1]?.body))).toMatchObject({ framework: 'PCC', elements: { population: 'Adults', concept: '' } });
  });

  it('explores a topic and flags reviews that may be outdated', async () => {
    const exploration = {
      id: 3, query: 'aspirin', assumptions: {}, created_by: 'Liam Lead', created_at: '2026-09-15T00:00:00Z', ai_questions: null,
      results: {
        query: 'aspirin', run_at: '2026-09-15T00:00:00Z',
        sources: { pubmed: { label: 'PubMed records', count: 1200, error: null }, clinicaltrials_gov: { label: 'ClinicalTrials.gov registered studies', count: null, error: 'ClinicalTrials.gov search failed.' } },
        publications_by_year: { 2025: 10, 2026: 4 },
        existing_reviews: [{ ref: 'PubMed:1', id: '1', source: 'PubMed', title: 'Aspirin: a systematic review', year: '2016', venue: 'BMJ', doi: '', url: 'https://pubmed.ncbi.nlm.nih.gov/1/', possibly_outdated: true, newer_randomized_trials: 4 }],
        review_errors: [], registrations: [], registration_error: null, prospero_search_url: 'https://www.crd.york.ac.uk/prospero/',
        meta_analysis_feasibility: { level: 'likely', randomized_trials: 9, explanation: '9 randomized trials match.' },
        workload: null, notes: [],
      },
    };
    const fetchMock = mockApi({ 'POST /api/auth/login': LOGIN, ...WORKSPACE_API, 'GET /api/projects/7/topic-explorations': [], 'POST /api/projects/7/topic-explorations': exploration });
    renderApp('/projects/7/topic');

    signIn();
    fireEvent.click(await screen.findByRole('button', { name: 'Explore topic' }));

    expect(await screen.findByText('Possibly outdated: 4 newer randomized trials in PubMed')).toBeTruthy();
    expect(screen.getByText('ClinicalTrials.gov search failed.')).toBeTruthy();
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST' && String(init?.body).includes('reviewers'));
    expect(JSON.parse(String(post?.[1]?.body))).toMatchObject({ query: 'Aspirin review', reviewers: 2 });
  });

  it('lists full texts, explains missing ones, and shows parsed passages', async () => {
    const doc = {
      id: 5, record_id: 11, role: 'full_text', origin: 'europepmc', source_url: '', file_name: 'PMC1.xml', media_type: 'application/xml',
      size_bytes: 2048, sha256: 'abc', license: 'cc-by', oa_status: '', version: '', parse_status: 'parsed', parse_error: null,
      parser: 'jats/v1', page_count: null, span_count: 2, uploaded_by: null, created_at: '2026-09-15T00:00:00Z', parsed_at: '2026-09-15T00:00:00Z',
    };
    const brief = (id: number, title: string, doi: string) => ({ id, title, authors: '', year: '2019', doi, source: 'PubMed' });
    mockApi({
      'POST /api/auth/login': LOGIN,
      ...WORKSPACE_API,
      'GET /api/projects/7/full-texts': {
        records: [
          { record: brief(11, 'Low-dose aspirin trial', '10.1/a'), final_decision: 'include', documents: [doc], latest_retrieval: null },
          {
            record: brief(12, 'Statin trial', ''), final_decision: 'include', documents: [],
            latest_retrieval: {
              id: 2, record_id: 12, status: 'not_found', document_id: null, requested_by: 'Liam Lead', created_at: '2026-09-15T00:00:00Z',
              attempts: [{ source: 'europepmc', outcome: 'skipped', detail: 'The record has no DOI, PMID, or PMCID' }],
            },
          },
        ],
        counts: { sought: 2, retrieved: 1, not_retrieved: 1 },
        max_document_bytes: 52428800,
        unpaywall_configured: true,
      },
      'GET /api/projects/7/documents/5': {
        ...doc,
        spans: [
          { id: 1, position: 0, kind: 'heading', section: 'Methods', page: null, label: '', text: 'Methods', start: 0, end: 7 },
          { id: 2, position: 1, kind: 'paragraph', section: 'Methods', page: null, label: '', text: 'We randomized 120 adults.', start: 9, end: 34 },
        ],
      },
    });
    renderApp('/projects/7/full-texts');

    signIn();

    expect(await screen.findByText(/Europe PMC: The record has no DOI, PMID, or PMCID/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'View passages' }));
    expect(await screen.findByText('We randomized 120 adults.')).toBeTruthy();
    expect(screen.getByRole('region', { name: 'Passages of PMC1.xml' })).toBeTruthy();
  });

  it('needs a reason to exclude a report at full-text screening', async () => {
    const fetchMock = mockApi({
      'POST /api/auth/login': LOGIN,
      ...WORKSPACE_API,
      'GET /api/projects/7/full-texts': { records: [], counts: { sought: 1, retrieved: 0, not_retrieved: 1 }, max_document_bytes: 1000, unpaywall_configured: true },
      'PUT /api/projects/7/records/11/decision': {},
    });
    renderApp('/projects/7/full-text-screening');

    signIn();

    expect(await screen.findByRole('heading', { name: 'Full-Text Screening' })).toBeTruthy();
    const exclude = await screen.findByRole('button', { name: '✕ Exclude' });
    expect((exclude as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText('Exclusion reason for Low-dose aspirin trial'), { target: { value: 'wrong_population' } });
    fireEvent.click(exclude);

    await vi.waitFor(() => expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'PUT')).toBe(true));
    const put = fetchMock.mock.calls.find(([, init]) => init?.method === 'PUT');
    expect(JSON.parse(String(put?.[1]?.body))).toMatchObject({ decision: 'exclude', stage: 'full_text', reason_code: 'wrong_population' });
  });

  it('accepts a grounded AI extraction suggestion into the extractor\'s own value', async () => {
    const field = {
      id: 5, name: 'Pain score', position: 0, section: 'Outcomes', field_type: 'continuous', options: [], unit: '', required: true,
      help_text: '', per_arm: false, outcome: '', timepoint: '', settings: {},
    };
    const settings = { mode: 'single', numeric_absolute_tolerance: 0, numeric_relative_tolerance: 0 };
    const cell = {
      field_id: 5, arm_id: null, arm_label: null, state: 'empty', my_value: null, other_values: [], values_hidden: false, final: null, ai_hidden: false, missing_components: [],
      ai_suggestion: {
        id: 9, value: '', structured: { mean: 5, sd: 1, n: 60 }, display: 'mean=5, sd=1, n=60', unit: '', not_reported: false,
        quote: 'Mean pain 5 (SD 1)', span_ids: [], confidence: 0.8, ambiguous: false, grounding: 'grounded', arm_label: '',
      },
    };
    const fetchMock = mockApi({
      'POST /api/auth/login': LOGIN,
      ...WORKSPACE_API,
      'GET /api/projects/7/extraction/form': {
        fields: [field], field_types: [{ key: 'continuous', label: 'Continuous outcome', components: ['mean', 'sd', 'n'] }], templates: [], conversions: [],
        units: { groups: {}, analytes: [] }, settings, analysis_outcomes: [],
      },
      'GET /api/projects/7/extraction/progress': {
        totals: { studies: 1, unlinked_reports: 0, cells: 1, settled: 0, missing_required: 1, discrepancies: 0, awaiting: 0, unapproved_imputations: 0 },
        studies: [{ study_id: 3, label: 'Smith 2019', cells: 1, settled: 0, discrepancies: 0, awaiting: 0, missing_required: 1 }],
        discrepancies: [], mode: 'single',
      },
      'GET /api/projects/7/studies/3/extraction': {
        study: { id: 3, label: 'Smith 2019', registry_ids: [], arms: [], reports: [{ record_id: 11, title: 'Low-dose aspirin trial', doi: '', is_primary: true, document_id: null, file_name: null }] },
        fields: [field], cells: [cell], settings, can_reconcile: true, can_extract: true,
      },
      'GET /api/projects/7/author-contacts': [],
      'PUT /api/projects/7/studies/3/extraction/values': { ...cell, state: 'final' },
    });
    renderApp('/projects/7/extraction');

    signIn();
    fireEvent.click(await screen.findByRole('button', { name: 'Accept as my value' }));

    await vi.waitFor(() => expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'PUT')).toBe(true));
    const put = fetchMock.mock.calls.find(([, init]) => init?.method === 'PUT');
    expect(JSON.parse(String(put?.[1]?.body))).toMatchObject({ field_id: 5, arm_id: null, source: 'ai_accepted', ai_suggestion_id: 9 });
  });

  it('explains when a project cannot be opened', async () => {
    mockApi({ 'POST /api/auth/login': LOGIN });
    renderApp('/projects/7/setup');

    signIn();

    expect(await screen.findByRole('heading', { name: "This project couldn't be opened" })).toBeTruthy();
  });
});
