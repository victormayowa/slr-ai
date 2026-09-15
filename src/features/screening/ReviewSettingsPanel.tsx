import { useEffect, useState } from 'react';
import { errorMessage } from '../../api/client';
import type { ReviewSettings } from '../../api/screening';
import { useAuth } from '../../auth/authContext';
import { fieldLabel, muted, panel, row, smallButton } from '../../components/ui';
import { useWorkspace } from '../project/workspaceContext';

// How screening and extraction run: reviewers per record, blinding, recall target, exclusion reasons, extraction mode.
export function ReviewSettingsPanel({ onSaved }: { onSaved?: (settings: ReviewSettings) => void }) {
  const { projectId, refreshWorkflow } = useWorkspace();
  const { apiRequest } = useAuth();
  const base = `/api/projects/${projectId}`;
  const [settings, setSettings] = useState<ReviewSettings | null>(null);
  const [newReason, setNewReason] = useState('');
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    apiRequest('GET', `${base}/review-settings`)
      .then(data => {
        if (!cancelled) setSettings(data);
      })
      .catch(err => {
        if (!cancelled) setNotice(errorMessage(err, 'Could not load the settings.'));
      });
    return () => {
      cancelled = true;
    };
  }, [apiRequest, base]);

  if (!settings) return <p style={muted}>{notice ?? 'Loading settings…'}</p>;
  const { screening, extraction } = settings;
  const setScreening = (changes: Partial<typeof screening>) => setSettings({ ...settings, screening: { ...screening, ...changes } });
  const setExtraction = (changes: Partial<typeof extraction>) => setSettings({ ...settings, extraction: { ...extraction, ...changes } });

  const addReason = () => {
    const label = newReason.trim();
    const code = label.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '').slice(0, 60);
    if (label.length < 2 || code.length < 2 || settings.exclusion_reasons.some(reason => reason.code === code)) return;
    setScreening({ custom_exclusion_reasons: [...screening.custom_exclusion_reasons, { code, label }] });
    setNewReason('');
  };

  const save = async () => {
    setBusy(true);
    setNotice(null);
    try {
      const saved: ReviewSettings = await apiRequest('PUT', `${base}/review-settings`, { screening, extraction });
      setSettings(saved);
      onSaved?.(saved);
      await refreshWorkflow();
      setNotice('Settings saved. The change is recorded in the audit trail.');
    } catch (err) {
      setNotice(errorMessage(err, 'Could not save the settings.'));
    }
    setBusy(false);
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
      <section style={panel}>
        <h4 style={{ marginTop: 0 }}>Screening</h4>
        <div style={row}>
          <label style={fieldLabel}>
            Reviewers per record at title and abstract
            <select className="search-input" value={screening.title_abstract_reviewers} onChange={e => setScreening({ title_abstract_reviewers: Number(e.target.value) as 1 | 2 })}>
              <option value={1}>One reviewer</option>
              <option value={2}>Two independent reviewers</option>
            </select>
          </label>
          <label style={fieldLabel}>
            Reviewers per report at full text
            <select className="search-input" value={screening.full_text_reviewers} onChange={e => setScreening({ full_text_reviewers: Number(e.target.value) as 1 | 2 })}>
              <option value={1}>One reviewer</option>
              <option value={2}>Two independent reviewers</option>
            </select>
          </label>
          <label style={{ ...fieldLabel, flexDirection: 'row', alignItems: 'center' }}>
            <input type="checkbox" checked={screening.blind_dual_screening} onChange={e => setScreening({ blind_dual_screening: e.target.checked })} />
            Blind reviewers to each other's decisions and AI suggestions until they decide
          </label>
        </div>
        <div style={{ ...row, marginTop: '12px' }}>
          <label style={fieldLabel}>
            Recall target
            <input type="number" step={0.01} min={0.5} max={1} className="search-input" style={{ width: '110px' }} value={screening.recall_target} onChange={e => setScreening({ recall_target: Number(e.target.value) })} />
          </label>
          <label style={fieldLabel}>
            Stopping test α
            <input type="number" step={0.01} min={0.001} max={0.49} className="search-input" style={{ width: '110px' }} value={screening.stopping_alpha} onChange={e => setScreening({ stopping_alpha: Number(e.target.value) })} />
          </label>
          <label style={fieldLabel}>
            Retrain prioritization every
            <input type="number" min={1} max={1000} className="search-input" style={{ width: '110px' }} value={screening.retrain_every} onChange={e => setScreening({ retrain_every: Number(e.target.value) })} />
          </label>
        </div>
        <h5 style={{ margin: '16px 0 6px' }}>Full-text exclusion reasons</h5>
        <p style={muted}>{settings.exclusion_reasons.filter(reason => !screening.custom_exclusion_reasons.some(custom => custom.code === reason.code)).map(reason => reason.label).join(' · ')}</p>
        <div style={row}>
          {screening.custom_exclusion_reasons.map(reason => (
            <span key={reason.code} style={{ ...muted, border: '1px solid rgba(255,255,255,0.2)', borderRadius: '999px', padding: '2px 10px' }}>
              {reason.label}{' '}
              <button aria-label={`Remove ${reason.label}`} style={{ background: 'none', border: 'none', color: '#ef4444', cursor: 'pointer' }} onClick={() => setScreening({ custom_exclusion_reasons: screening.custom_exclusion_reasons.filter(r => r.code !== reason.code) })}>✕</button>
            </span>
          ))}
          <input aria-label="New exclusion reason" className="search-input" style={{ maxWidth: '260px' }} placeholder="Add a reason, such as Wrong dose" value={newReason} onChange={e => setNewReason(e.target.value)} />
          <button className="btn-glass" style={smallButton} onClick={addReason}>Add</button>
        </div>
      </section>

      <section style={panel}>
        <h4 style={{ marginTop: 0 }}>Extraction</h4>
        <div style={row}>
          <label style={fieldLabel}>
            How values become final
            <select className="search-input" value={extraction.mode} onChange={e => setExtraction({ mode: e.target.value as typeof extraction.mode })}>
              <option value="single">One extractor</option>
              <option value="dual">Two independent extractors, discrepancies reconciled</option>
              <option value="human_and_ai">One extractor checked against AI suggestions</option>
            </select>
          </label>
          <label style={fieldLabel}>
            Numbers agree within (relative)
            <input type="number" step={0.001} min={0} max={0.5} className="search-input" style={{ width: '110px' }} value={extraction.numeric_relative_tolerance} onChange={e => setExtraction({ numeric_relative_tolerance: Number(e.target.value) })} />
          </label>
          <label style={fieldLabel}>
            or within (absolute)
            <input type="number" step={0.001} min={0} className="search-input" style={{ width: '110px' }} value={extraction.numeric_absolute_tolerance} onChange={e => setExtraction({ numeric_absolute_tolerance: Number(e.target.value) })} />
          </label>
        </div>
      </section>

      <div style={row}>
        <button className="btn-primary" style={{ padding: '8px 16px' }} disabled={busy} onClick={save}>{busy ? 'Saving…' : 'Save settings'}</button>
        {notice && <span role="status" style={muted}>{notice}</span>}
      </div>
    </div>
  );
}
