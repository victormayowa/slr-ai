import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import type { BenchmarkDataset, BenchmarkRunInfo } from '../api/collaboration';
import { ApiError, errorMessage } from '../api/client';
import { useAuth } from '../auth/authContext';
import { AMBER, BLUE, GREEN, GREY, RED, chip, fmt, muted, panel, row, smallButton } from '../components/ui';

const STATUS_COLORS: Record<string, string> = { passed: GREEN, failed: RED, unvalidated: GREY, exempt: BLUE };

type AdminModel = {
  id: number;
  provider_label: string;
  label: string;
  purpose: string;
  enabled: boolean;
  is_default: boolean;
  benchmark_status: string;
  status_note: string;
  validated_prompts: string[];
  benchmarks: Record<string, { run_id: number; dataset: string; passed: boolean | null; metrics: Record<string, number | null> } | null>;
  current_prompt_versions: Record<string, string>;
};

type Catalog = { models: AdminModel[]; validation_required: boolean; thresholds: Record<string, Record<string, number>> };

// Platform administration: the model catalog and the benchmark suite that decides whether a model may be used.
// Administrator rights are granted with backend/scripts/make_admin.py.
export function AdminPage() {
  const { apiRequest } = useAuth();
  const navigate = useNavigate();
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [datasets, setDatasets] = useState<BenchmarkDataset[]>([]);
  const [runs, setRuns] = useState<BenchmarkRunInfo[]>([]);
  const [choice, setChoice] = useState({ dataset_key: '', ai_model_id: 0 });
  const [forbidden, setForbidden] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const fetchCatalog = useCallback(async () => {
    const [models, datasetList, runList] = await Promise.all([
      apiRequest('GET', '/api/admin/models'),
      apiRequest('GET', '/api/admin/benchmarks/datasets'),
      apiRequest('GET', '/api/admin/benchmarks/runs'),
    ]);
    return { models, datasets: datasetList.datasets as BenchmarkDataset[], runs: runList as BenchmarkRunInfo[] };
  }, [apiRequest]);

  const apply = (result: { models: Catalog; datasets: BenchmarkDataset[]; runs: BenchmarkRunInfo[] }) => {
    setCatalog(result.models);
    setDatasets(result.datasets);
    setRuns(result.runs);
  };

  useEffect(() => {
    let cancelled = false;
    fetchCatalog()
      .then(result => {
        if (!cancelled) apply(result);
      })
      .catch(err => {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 403) setForbidden(true);
        else setNotice(errorMessage(err, 'Could not load the catalog.'));
      });
    return () => {
      cancelled = true;
    };
  }, [fetchCatalog]);

  const load = async () => apply(await fetchCatalog());

  const act = async (action: () => Promise<string | void>, failure: string) => {
    setBusy(true);
    setNotice(null);
    try {
      const message = await action();
      await load();
      if (message) setNotice(message);
    } catch (err) {
      setNotice(errorMessage(err, failure));
    }
    setBusy(false);
  };

  if (forbidden) {
    return (
      <div className="app-container" style={{ justifyContent: 'center', alignItems: 'center', height: '100vh' }}>
        <div className="glass-panel" style={{ padding: '40px', maxWidth: '520px', textAlign: 'center' }}>
          <h2 style={{ marginTop: 0 }}>Administrators only</h2>
          <p style={muted}>This page manages the shared model catalog. Rights are granted on the server.</p>
          <button className="btn-primary" style={{ padding: '10px 24px', borderRadius: '8px' }} onClick={() => navigate('/')}>Back</button>
        </div>
      </div>
    );
  }

  if (!catalog) return <div className="app-container" style={{ padding: '48px' }}>{notice ?? 'Loading…'}</div>;

  return (
    <div className="app-container" style={{ flexDirection: 'column', padding: '48px 24px', minHeight: '100vh' }}>
      <div className="glass-panel" style={{ width: '100%', maxWidth: '960px', margin: '0 auto', padding: '32px' }}>
        <div style={{ ...row, justifyContent: 'space-between' }}>
          <h2 style={{ margin: 0 }}>Model catalog and benchmarks</h2>
          <button className="btn-glass" style={smallButton} onClick={() => navigate('/')}>Back to projects</button>
        </div>
        <p style={muted}>
          {catalog.validation_required
            ? 'This server only lets projects pin models that passed the benchmark suite.'
            : 'Validation is not enforced on this server; set REQUIRE_VALIDATED_MODELS=true to require it.'}
        </p>
        {notice && <p role="status" style={{ ...panel, margin: '12px 0' }}>{notice}</p>}

        <h4>Models</h4>
        {catalog.models.map(model => (
          <div key={model.id} style={{ ...panel, marginBottom: '8px', fontSize: '0.85rem' }}>
            <div style={row}>
              <span style={{ flex: '1 1 220px' }}>{model.provider_label} {model.label}</span>
              <span style={chip(STATUS_COLORS[model.benchmark_status] ?? GREY)}>{model.benchmark_status}</span>
              {model.is_default && <span style={chip(BLUE)}>default</span>}
              {!model.enabled && <span style={chip(GREY)}>disabled</span>}
              <button className="btn-glass" style={smallButton} disabled={busy} onClick={() => act(async () => {
                await apiRequest('PATCH', `/api/admin/models/${model.id}`, { enabled: !model.enabled });
              }, 'Could not update the model.')}>
                {model.enabled ? 'Disable' : 'Enable'}
              </button>
              <button className="btn-glass" style={smallButton} disabled={busy} onClick={() => act(async () => {
                const note = window.prompt('Why is this model exempt from benchmarking?') ?? '';
                if (!note.trim()) return 'Exemption needs a reason.';
                await apiRequest('PATCH', `/api/admin/models/${model.id}`, { benchmark_status: 'exempt', status_note: note.trim() });
              }, 'Could not mark the model exempt.')}>
                Mark exempt
              </button>
            </div>
            {model.status_note && <p style={muted}>{model.status_note}</p>}
            {model.purpose === 'chat' && (
              <div style={{ ...row, marginTop: '4px' }}>
                {Object.entries(model.benchmarks).map(([task, result]) => (
                  <span key={task} style={chip(result?.passed ? GREEN : result ? RED : GREY)}>
                    {task}: {result ? (result.passed ? 'passed' : 'failed') : 'not run'}
                    {result?.metrics?.recall != null && ` (recall ${fmt(result.metrics.recall)})`}
                  </span>
                ))}
              </div>
            )}
          </div>
        ))}

        <h4 style={{ marginTop: '24px' }}>Run a benchmark</h4>
        <div style={{ ...panel, ...row }}>
          <select aria-label="Benchmark data set" className="search-input" style={{ width: 'auto' }} value={choice.dataset_key} onChange={e => setChoice({ ...choice, dataset_key: e.target.value })}>
            <option value="">Data set…</option>
            {datasets.map(dataset => <option key={dataset.key} value={dataset.key}>{dataset.name} ({dataset.task}, {dataset.items})</option>)}
          </select>
          <select aria-label="Model to benchmark" className="search-input" style={{ width: 'auto' }} value={choice.ai_model_id} onChange={e => setChoice({ ...choice, ai_model_id: Number(e.target.value) })}>
            <option value={0}>No model (statistics engine)</option>
            {catalog.models.filter(model => model.purpose === 'chat').map(model => (
              <option key={model.id} value={model.id}>{model.provider_label} {model.label}</option>
            ))}
          </select>
          <button className="btn-primary" disabled={busy || !choice.dataset_key} onClick={() => act(async () => {
            await apiRequest('POST', '/api/admin/benchmarks/runs', {
              dataset_key: choice.dataset_key,
              ai_model_id: choice.ai_model_id || null,
            });
            return 'Benchmark queued.';
          }, 'Could not start the benchmark.')}>
            Run benchmark
          </button>
        </div>
        <p style={muted}>Screening data sets can be built from SYNERGY with backend/scripts/fetch_synergy_dataset.py.</p>

        <h4 style={{ marginTop: '24px' }}>Recent runs</h4>
        {runs.length === 0 && <p style={muted}>No benchmark has been run yet.</p>}
        {runs.map(run => (
          <div key={run.id} style={{ ...row, fontSize: '0.82rem', marginTop: '4px' }}>
            <span style={chip(run.status === 'completed' ? (run.passed ? GREEN : RED) : run.status === 'failed' ? RED : AMBER)}>
              {run.status === 'completed' ? (run.passed ? 'passed' : 'failed thresholds') : run.status}
            </span>
            <span style={{ flex: '1 1 200px' }}>{run.dataset_name}</span>
            <span style={muted}>{run.model} · {run.prompt_version || 'n/a'}</span>
            <span style={muted}>{run.processed}/{run.items}</span>
            {run.error && <span style={{ ...muted, color: RED }}>{run.error}</span>}
          </div>
        ))}
      </div>
    </div>
  );
}
