import { useCallback, useEffect, useState } from 'react';
import type { BenchmarkDataset, BenchmarkRunInfo } from '../../api/collaboration';
import { errorMessage } from '../../api/client';
import { useAuth } from '../../auth/authContext';
import { AMBER, BLUE, GREEN, GREY, RED, chip, fmt, muted, panel, row, smallButton } from '../../components/ui';

const STATUS_COLORS: Record<string, string> = { passed: GREEN, failed: RED, unvalidated: GREY, exempt: BLUE };

type AdminModel = {
  id: number;
  provider_label: string;
  label: string;
  purpose: string;
  enabled: boolean;
  is_default: boolean;
  benchmark_status: string;
  input_price_per_mtok: number | null;
  output_price_per_mtok: number | null;
  charged_per_mtok: { input_per_mtok: number | null; output_per_mtok: number | null };
  status_note: string;
  validated_prompts: string[];
  benchmarks: Record<string, { run_id: number; dataset: string; passed: boolean | null; metrics: Record<string, number | null> } | null>;
  current_prompt_versions: Record<string, string>;
};

type Catalog = { models: AdminModel[]; validation_required: boolean; thresholds: Record<string, Record<string, number>> };

// The model catalog and the benchmark suite that decides whether a model may be offered.
export function ModelsTab() {
  const { apiRequest } = useAuth();
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [datasets, setDatasets] = useState<BenchmarkDataset[]>([]);
  const [runs, setRuns] = useState<BenchmarkRunInfo[]>([]);
  const [choice, setChoice] = useState({ dataset_key: '', ai_model_id: 0 });
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
        if (!cancelled) setNotice(errorMessage(err, 'Could not load the catalog.'));
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

  if (!catalog) return <p>{notice ?? 'Loading…'}</p>;

  return (
    <div>
      <div>
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
            <div style={{ ...row, marginTop: '4px', alignItems: 'center' }}>
              <span style={muted}>What the provider charges, per million tokens:</span>
              {(['input', 'output'] as const).map(side => (
                <label key={side} style={{ ...muted, display: 'flex', gap: '4px', alignItems: 'center' }}>
                  {side}
                  <input
                    className="search-input"
                    aria-label={`${model.label} ${side} cost per million tokens`}
                    type="number"
                    min={0}
                    step="0.01"
                    defaultValue={model[`${side}_price_per_mtok`] ?? ''}
                    style={{ width: '90px', padding: '4px 6px' }}
                    onBlur={e => {
                      const raw = e.target.value.trim();
                      const value = raw === '' ? null : Number(raw);
                      if (value === (model[`${side}_price_per_mtok`] ?? null)) return;
                      act(async () => {
                        await apiRequest('PATCH', `/api/admin/models/${model.id}`, { [`${side}_price_per_mtok`]: value });
                      }, 'Could not save the price.');
                    }}
                  />
                </label>
              ))}
              <span style={muted}>
                {model.charged_per_mtok.input_per_mtok !== null
                  ? `Customers pay $${model.charged_per_mtok.input_per_mtok} in / $${model.charged_per_mtok.output_per_mtok} out`
                  : 'No price: this model can only be used with a reviewer\'s own key'}
              </span>
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
