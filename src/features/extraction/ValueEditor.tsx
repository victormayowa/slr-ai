import type { FieldDef, Structured } from '../../api/extraction';
import { fieldLabel, row } from '../../components/ui';

const MEASURES = ['risk_ratio', 'odds_ratio', 'hazard_ratio', 'risk_difference', 'mean_difference', 'standardized_mean_difference', 'other'];

type Props = { field: FieldDef; components: string[]; value: Structured; onChange: (value: Structured) => void; label: string; disabled?: boolean };

// An input for a value in the field type's shape: text, a number, a choice, or the numbers of a structured outcome.
export function ValueEditor({ field, components, value, onChange, label, disabled = false }: Props) {
  const set = (key: string, item: string | number | boolean | string[] | undefined) => {
    const next = { ...value };
    if (item === undefined || item === '') delete next[key];
    else next[key] = item;
    onChange(next);
  };
  const number = (key: string) => (value[key] === undefined ? '' : String(value[key]));
  const toNumber = (text: string) => (text === '' ? undefined : Number(text));

  switch (field.field_type) {
    case 'text':
      return <input aria-label={label} disabled={disabled} className="search-input" value={String(value.text ?? '')} onChange={e => set('text', e.target.value)} />;
    case 'long_text':
      return <textarea aria-label={label} disabled={disabled} className="search-input" style={{ width: '100%', minHeight: '70px' }} value={String(value.text ?? '')} onChange={e => set('text', e.target.value)} />;
    case 'number':
    case 'integer':
      return <input aria-label={label} disabled={disabled} type="number" step={field.field_type === 'integer' ? 1 : 'any'} className="search-input" style={{ width: '160px' }} value={number('number')} onChange={e => set('number', toNumber(e.target.value))} />;
    case 'categorical':
      return (
        <select aria-label={label} disabled={disabled} className="search-input" value={String(value.choice ?? '')} onChange={e => set('choice', e.target.value)}>
          <option value="">Choose…</option>
          {field.options.map(option => <option key={option} value={option}>{option}</option>)}
        </select>
      );
    case 'multi_select': {
      const chosen = Array.isArray(value.choices) ? value.choices : [];
      return (
        <div style={row} aria-label={label} role="group">
          {field.options.map(option => (
            <label key={option} style={{ ...fieldLabel, flexDirection: 'row', alignItems: 'center' }}>
              <input type="checkbox" disabled={disabled} checked={chosen.includes(option)} onChange={e => set('choices', e.target.checked ? [...chosen, option] : chosen.filter(item => item !== option))} />
              {option}
            </label>
          ))}
        </div>
      );
    }
    case 'boolean':
      return (
        <select aria-label={label} disabled={disabled} className="search-input" style={{ width: '120px' }} value={value.bool === undefined ? '' : value.bool ? 'yes' : 'no'} onChange={e => set('bool', e.target.value === '' ? undefined : e.target.value === 'yes')}>
          <option value="">Choose…</option>
          <option value="yes">Yes</option>
          <option value="no">No</option>
        </select>
      );
    case 'date':
      return <input aria-label={label} disabled={disabled} type="date" className="search-input" style={{ width: '180px' }} value={String(value.date ?? '')} onChange={e => set('date', e.target.value)} />;
    default:
      return (
        <div style={row} role="group" aria-label={label}>
          {components.map(component => (
            <label key={component} style={fieldLabel}>
              {component}
              <input aria-label={`${label} ${component}`} disabled={disabled} type="number" step="any" className="search-input" style={{ width: '100px' }} value={number(component)} onChange={e => set(component, toNumber(e.target.value))} />
            </label>
          ))}
          {field.field_type === 'effect_estimate' && (
            <label style={fieldLabel}>
              measure
              <select disabled={disabled} className="search-input" value={String(value.measure ?? 'other')} onChange={e => set('measure', e.target.value)}>
                {MEASURES.map(measure => <option key={measure} value={measure}>{measure.replace(/_/g, ' ')}</option>)}
              </select>
            </label>
          )}
        </div>
      );
  }
}
