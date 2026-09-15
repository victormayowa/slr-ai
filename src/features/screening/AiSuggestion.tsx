import type { AiScreening, CriterionJudgment } from '../../api/screening';
import { AMBER, GREEN, RED, chip, muted, percent, smallButton } from '../../components/ui';

const JUDGMENT = { met: { label: 'Met', color: GREEN }, not_met: { label: 'Not met', color: RED }, unclear: { label: 'Unclear', color: AMBER } } as const;

function Quote({ quote, verified }: { quote: string; verified: boolean | null }) {
  const color = verified ? GREEN : RED;
  return (
    <blockquote style={{ margin: '4px 0 0', paddingLeft: '8px', borderLeft: `3px solid ${color}`, fontSize: '0.85rem' }}>
      <span style={{ fontStyle: 'italic' }}>“{quote}”</span>
      <span style={{ ...muted, color, marginLeft: '6px' }}>{verified ? 'found in the text' : 'not found in the text: check before relying on it'}</span>
    </blockquote>
  );
}

// The AI's suggestion for a record, with its judgment of each criterion and the evidence it quoted.
export function AiSuggestion({ suggestion, onShowPassage }: { suggestion: AiScreening; onShowPassage?: (spanIds: number[]) => void }) {
  if (suggestion.error) return <p style={{ color: RED, margin: 0 }}>AI error: {suggestion.error}</p>;
  const color = suggestion.decision === 'Include' ? GREEN : suggestion.decision === 'Exclude' ? RED : AMBER;
  const judged = suggestion.criteria_judgments;
  const evidenceSpans = (items: CriterionJudgment[]) => items.map(item => item.span_id).filter((id): id is number => id != null);

  return (
    <div style={{ fontSize: '0.9rem' }}>
      <div style={{ display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap' }}>
        <span style={chip(color)}>AI suggests: {suggestion.decision ?? 'nothing'}</span>
        {suggestion.confidence != null && <span style={muted}>confidence {percent(suggestion.confidence)}</span>}
        <span style={muted}>{suggestion.model}</span>
        {onShowPassage && evidenceSpans(judged).length > 0 && (
          <button className="btn-glass" style={smallButton} onClick={() => onShowPassage(evidenceSpans(judged))}>Show evidence in the full text</button>
        )}
      </div>
      {suggestion.reasoning && <p style={{ margin: '6px 0', color: 'var(--text-secondary)' }}>{suggestion.reasoning}</p>}
      {suggestion.supporting_quote && <Quote quote={suggestion.supporting_quote} verified={suggestion.quote_verified} />}
      {judged.length > 0 && (
        <ul style={{ listStyle: 'none', padding: 0, margin: '8px 0 0', display: 'flex', flexDirection: 'column', gap: '6px' }}>
          {judged.map(item => (
            <li key={item.criterion_id}>
              <span style={chip(JUDGMENT[item.judgment].color)}>{JUDGMENT[item.judgment].label}</span>{' '}
              <span style={muted}>{item.kind === 'inclusion' ? 'Include if' : 'Exclude if'}:</span> {item.text}
              {item.rationale && <div style={muted}>{item.rationale}</div>}
              {item.quote && <Quote quote={item.quote} verified={item.quote_verified} />}
              {onShowPassage && item.span_id != null && (
                <button className="btn-glass" style={{ ...smallButton, marginTop: '4px' }} onClick={() => onShowPassage([item.span_id as number])}>
                  Show passage
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
