import { describe, expect, it } from 'vitest';
import { toPaper, type ApiRecord } from './review';

const record: ApiRecord = {
  id: 7,
  title: 'Aspirin trial',
  authors: 'Smith J',
  year: '2020',
  venue: 'The Lancet',
  doi: '10.1/a',
  abstract: 'Adults were randomized.',
  source: 'PubMed',
  duplicate_of_id: null,
  ai_screening: null,
  my_decision: null,
  final_decision: null,
  extraction: null,
  appraisal: null,
};

const run = { provider: 'gemini', model: 'gemini-3.6-flash', created_at: '2026-09-14T12:00:00Z' };

describe('toPaper', () => {
  it('uses string ids and leaves undecided records without a decision', () => {
    const paper = toPaper(record);

    expect(paper.id).toBe('7');
    expect(paper.user_decision).toBeNull();
    expect(paper.ai_decision).toBeUndefined();
  });

  it('keeps the AI suggestion separate from the reviewer decision', () => {
    const paper = toPaper({
      ...record,
      ai_screening: { ...run, error: null, decision: 'Include', reasoning: 'Adults in a trial', supporting_quote: null },
      final_decision: 'exclude',
    });

    expect(paper.ai_decision).toBe('Include');
    expect(paper.user_decision).toBe('Exclude');
  });

  it('reports failed AI runs as errors, never as values', () => {
    const paper = toPaper({
      ...record,
      ai_screening: { ...run, error: 'OPENAI_API_KEY is not configured on the server', decision: null, reasoning: null, supporting_quote: null },
      extraction: { ...run, error: 'The gemini request failed.', values: {} },
    });

    expect(paper.ai_error).toContain('OPENAI_API_KEY');
    expect(paper.ai_decision).toBeUndefined();
    expect(paper.extracted_data).toBeUndefined();
    expect(paper.extraction_error).toBe('The gemini request failed.');
  });

  it('exposes extraction values and risk of bias judgments from successful runs', () => {
    const paper = toPaper({
      ...record,
      extraction: { ...run, error: null, values: { 'Sample Size': '120' } },
      appraisal: { ...run, error: null, tool: 'ROB-2', judgments: { Overall: 'Low Risk' } },
    });

    expect(paper.extracted_data).toEqual({ 'Sample Size': '120' });
    expect(paper.rob_data).toEqual({ Overall: 'Low Risk' });
  });
});
