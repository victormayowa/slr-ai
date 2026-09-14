import { describe, expect, it } from 'vitest';
import { dedupKey } from './dedup';

describe('dedupKey', () => {
  it('matches DOIs regardless of case and surrounding whitespace', () => {
    expect(dedupKey({ doi: ' 10.1000/ABC ', title: 'One title' })).toBe(dedupKey({ doi: '10.1000/abc', title: 'Another' }));
  });

  it('falls back to a normalized title when there is no DOI', () => {
    expect(dedupKey({ doi: '', title: 'Aspirin: A Trial!' })).toBe(dedupKey({ doi: '', title: 'aspirin   a trial' }));
  });

  it('keeps records with different titles apart', () => {
    expect(dedupKey({ doi: '', title: 'Aspirin trial' })).not.toBe(dedupKey({ doi: '', title: 'Statin trial' }));
  });

  it('never matches a DOI against a title', () => {
    expect(dedupKey({ doi: 'aspirin trial', title: 'x' })).not.toBe(dedupKey({ doi: '', title: 'aspirin trial' }));
  });
});
