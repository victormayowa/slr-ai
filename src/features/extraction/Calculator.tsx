import { useState } from 'react';
import { errorMessage } from '../../api/client';
import type { Conversion, ConversionInfo } from '../../api/extraction';
import { useAuth } from '../../auth/authContext';
import { fieldLabel, fmt, muted, panel, row, smallButton } from '../../components/ui';
import { useWorkspace } from '../project/workspaceContext';

type Props = { conversions: ConversionInfo[]; target: string; onUse: (conversion: Conversion) => void; onClose: () => void };

// Statistical conversions (median to mean and SD, CI to SD or SE, combining groups...) with formula and reference.
export function Calculator({ conversions, target, onUse, onClose }: Props) {
  const { projectId } = useWorkspace();
  const { apiRequest } = useAuth();
  const [method, setMethod] = useState(conversions[0]?.key ?? '');
  const [inputs, setInputs] = useState<Record<string, string>>({});
  const [result, setResult] = useState<Conversion | null>(null);
  const [error, setError] = useState<string | null>(null);
  const info = conversions.find(conversion => conversion.key === method);

  const compute = async () => {
    setError(null);
    setResult(null);
    const values: Record<string, number | boolean> = {};
    for (const input of info?.inputs ?? []) {
      const text = inputs[input.name]?.trim();
      if (!text) continue;
      values[input.name] = input.name === 'ratio' ? text === 'yes' : Number(text);
    }
    try {
      setResult(await apiRequest('POST', `/api/projects/${projectId}/extraction/convert`, { method, inputs: values }));
    } catch (err) {
      setError(errorMessage(err, 'The calculation failed.'));
    }
  };

  return (
    <div style={{ ...panel, marginTop: '12px', border: '1px solid var(--accent-primary)' }} role="dialog" aria-label="Calculator">
      <div style={{ ...row, justifyContent: 'space-between' }}>
        <h4 style={{ margin: 0 }}>Calculate for {target}</h4>
        <button className="btn-glass" style={smallButton} onClick={onClose}>Close</button>
      </div>
      <div style={{ ...row, marginTop: '8px' }}>
        <label style={fieldLabel}>
          Conversion
          <select className="search-input" value={method} onChange={e => { setMethod(e.target.value); setResult(null); }}>
            {conversions.map(conversion => <option key={conversion.key} value={conversion.key}>{conversion.label}</option>)}
          </select>
        </label>
        {info?.inputs.map(input => (
          <label key={input.name} style={fieldLabel}>
            {input.name}{input.optional ? ' (optional)' : ''}
            {input.name === 'ratio' ? (
              <select className="search-input" value={inputs.ratio ?? 'yes'} onChange={e => setInputs({ ...inputs, ratio: e.target.value })}>
                <option value="yes">Ratio measure</option>
                <option value="no">Difference measure</option>
              </select>
            ) : (
              <input type="number" step="any" className="search-input" style={{ width: '100px' }} value={inputs[input.name] ?? ''} onChange={e => setInputs({ ...inputs, [input.name]: e.target.value })} />
            )}
          </label>
        ))}
        <button className="btn-primary" style={{ ...smallButton, alignSelf: 'flex-end' }} onClick={compute}>Calculate</button>
      </div>
      {error && <p role="status" style={{ color: '#C62828' }}>{error}</p>}
      {result && (
        <div style={{ marginTop: '8px' }}>
          <p style={{ margin: '4px 0' }}>{Object.entries(result.values).map(([key, value]) => `${key} = ${fmt(value, 4)}`).join(' · ')}</p>
          <p style={muted}>{result.formula} · {result.reference}</p>
          {result.assumptions.map(assumption => <p key={assumption} style={muted}>Assumes: {assumption}</p>)}
          <button className="btn-primary" style={smallButton} onClick={() => onUse(result)}>Save as a calculated value</button>
        </div>
      )}
    </div>
  );
}
