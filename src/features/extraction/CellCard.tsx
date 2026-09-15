import { useState } from 'react';
import { CELL_STATES, type CellInfo, type FieldDef, type Structured, type SuggestionInfo, type ValueInfo } from '../../api/extraction';
import { AMBER, GREEN, RED, chip, muted, percent, row, smallButton } from '../../components/ui';
import { ValueEditor } from './ValueEditor';

export type CellDraft = { value: Structured; not_reported: boolean; unit: string; note: string };

type Props = {
  cell: CellInfo;
  field: FieldDef;
  components: string[];
  canExtract: boolean;
  canReconcile: boolean;
  onSave: (draft: CellDraft) => void;
  onAccept: (suggestion: SuggestionInfo) => void;
  onReconcile: (value: ValueInfo) => void;
  onApprove: (value: ValueInfo) => void;
  onEvidence: (spanIds: number[]) => void;
  onCalculate: () => void;
  onImpute: () => void;
};

const initialDraft = (cell: CellInfo, field: FieldDef): CellDraft =>
  cell.my_value
    ? { value: cell.my_value.value ?? {}, not_reported: cell.my_value.not_reported, unit: cell.my_value.unit, note: cell.my_value.note }
    : { value: {}, not_reported: false, unit: field.unit, note: '' };

function ValueLine({ value, onEvidence, actions }: { value: ValueInfo; onEvidence: (spanIds: number[]) => void; actions?: React.ReactNode }) {
  return (
    <div style={{ fontSize: '0.85rem', marginTop: '4px' }}>
      <strong>{value.extractor ?? 'Former member'}:</strong> {value.display || '–'} {value.unit && <span style={muted}>{value.unit}</span>}
      {value.flags.map(flag => <span key={flag} style={{ ...chip(AMBER), marginLeft: '6px' }}>{flag.replace('_', ' ')}</span>)}
      {value.needs_approval && <span style={{ ...chip(RED), marginLeft: '6px' }}>needs approval</span>}
      {value.span_ids.length > 0 && <button className="btn-glass" style={{ ...smallButton, marginLeft: '6px' }} onClick={() => onEvidence(value.span_ids)}>Evidence</button>}
      {actions}
    </div>
  );
}

// One cell of the extraction form (a field for a study, or for one of its arms): the reviewer's value, other extractors'
// values when visible, the AI suggestion with its evidence, and the final value.
export function CellCard({ cell, field, components, canExtract, canReconcile, onSave, onAccept, onReconcile, onApprove, onEvidence, onCalculate, onImpute }: Props) {
  const [draft, setDraft] = useState<CellDraft>(() => initialDraft(cell, field));
  const state = CELL_STATES[cell.state];
  const label = `${field.name}${cell.arm_label ? ` (${cell.arm_label})` : ''}`;
  const suggestion = cell.ai_suggestion;
  const acceptable = suggestion && (suggestion.grounding === 'grounded' || suggestion.grounding === 'not_reported') && (suggestion.structured || suggestion.not_reported);

  return (
    <div style={{ borderTop: '1px solid rgba(255,255,255,0.08)', padding: '10px 0' }} aria-label={label} role="group">
      <div style={{ ...row, justifyContent: 'space-between' }}>
        <span>{cell.arm_label ? <strong>{cell.arm_label}</strong> : <strong>{field.name}</strong>}</span>
        <span style={chip(state.color)}>{state.label}</span>
      </div>

      {cell.final && (
        <div style={{ fontSize: '0.9rem', marginTop: '4px', color: GREEN }}>
          Final: {cell.final.display || '–'} {cell.final.unit} <span style={muted}>({cell.final.source}{cell.final.rationale && `: ${cell.final.rationale}`})</span>
          {cell.final.span_ids.length > 0 && <button className="btn-glass" style={{ ...smallButton, marginLeft: '6px' }} onClick={() => onEvidence(cell.final?.span_ids ?? [])}>Evidence</button>}
        </div>
      )}

      {suggestion && (
        <div style={{ fontSize: '0.85rem', marginTop: '6px' }}>
          <span style={chip(suggestion.grounding === 'ungrounded' ? RED : '#3b82f6')}>AI</span>{' '}
          {suggestion.not_reported ? 'Not reported' : suggestion.display || suggestion.value} {suggestion.unit && <span style={muted}>{suggestion.unit}</span>}
          {suggestion.confidence != null && <span style={muted}> · confidence {percent(suggestion.confidence)}</span>}
          {suggestion.ambiguous && <span style={{ ...chip(AMBER), marginLeft: '6px' }}>ambiguous in the report</span>}
          {suggestion.quote && (
            <div style={{ ...muted, fontStyle: 'italic', color: suggestion.grounding === 'grounded' ? 'var(--text-secondary)' : RED }}>
              “{suggestion.quote}” {suggestion.grounding === 'grounded' ? '(found in the report)' : '(not found in the report, so it can\'t be accepted)'}
            </div>
          )}
          <div style={{ ...row, marginTop: '4px' }}>
            {canExtract && acceptable && <button className="btn-glass" style={smallButton} onClick={() => onAccept(suggestion)}>Accept as my value</button>}
            {suggestion.span_ids.length > 0 && <button className="btn-glass" style={smallButton} onClick={() => onEvidence(suggestion.span_ids)}>Show evidence</button>}
          </div>
        </div>
      )}
      {cell.ai_hidden && <p style={muted}>The AI's value is hidden until you enter yours.</p>}

      {canExtract && cell.state !== 'arms_missing' && (
        <div style={{ marginTop: '8px' }}>
          <div style={row}>
            <ValueEditor field={field} components={components} value={draft.value} onChange={value => setDraft({ ...draft, value })} label={label} disabled={draft.not_reported} />
            {(components.length > 0 || field.field_type === 'number') && (
              <input aria-label={`${label} unit`} className="search-input" style={{ width: '90px' }} placeholder="unit" value={draft.unit} onChange={e => setDraft({ ...draft, unit: e.target.value })} />
            )}
            <label style={{ ...muted, display: 'flex', gap: '4px', alignItems: 'center' }}>
              <input type="checkbox" checked={draft.not_reported} onChange={e => setDraft({ ...draft, not_reported: e.target.checked })} /> Not reported
            </label>
          </div>
          <div style={{ ...row, marginTop: '6px' }}>
            <input aria-label={`${label} note`} className="search-input" style={{ flex: '1 1 240px' }} placeholder="Note (optional)" value={draft.note} onChange={e => setDraft({ ...draft, note: e.target.value })} />
            <button className="btn-primary" style={smallButton} onClick={() => onSave(draft)}>Save</button>
            {components.length > 0 && <button className="btn-glass" style={smallButton} onClick={onCalculate}>Calculate…</button>}
            {field.field_type === 'continuous' && cell.missing_components.includes('sd') && <button className="btn-glass" style={smallButton} onClick={onImpute}>Impute SD…</button>}
          </div>
          {cell.missing_components.length > 0 && <p style={{ ...muted, margin: '4px 0 0' }}>Missing: {cell.missing_components.join(', ')}. Calculate it from what the report gives, impute it with approval, or ask the authors.</p>}
        </div>
      )}

      {cell.my_value && (
        <ValueLine value={cell.my_value} onEvidence={onEvidence} />
      )}
      {cell.other_values?.map(value => (
        <ValueLine
          key={value.id}
          value={value}
          onEvidence={onEvidence}
          actions={
            <>
              {canReconcile && <button className="btn-glass" style={{ ...smallButton, marginLeft: '6px' }} onClick={() => onReconcile(value)}>Use as final</button>}
              {value.needs_approval && canExtract && <button className="btn-glass" style={{ ...smallButton, marginLeft: '6px' }} onClick={() => onApprove(value)}>Approve imputation</button>}
            </>
          }
        />
      ))}
      {cell.values_hidden && <p style={muted}>Another extractor's value is hidden until you enter yours.</p>}
      {cell.my_value && canReconcile && cell.state === 'discrepancy' && (
        <button className="btn-glass" style={{ ...smallButton, marginTop: '4px' }} onClick={() => cell.my_value && onReconcile(cell.my_value)}>Use my value as final</button>
      )}
    </div>
  );
}
