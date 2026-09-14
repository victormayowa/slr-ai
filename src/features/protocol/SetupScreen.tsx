import { WorkspaceStageGate } from '../project/WorkspaceStageGate';
import { useWorkspace } from '../project/workspaceContext';

export function SetupScreen() {
  const {
    projectName, setProjectName, reviewType, setReviewType, framework, setFramework, studyDescription, setStudyDescription,
    suggestedCriteria, setSuggestedCriteria, extractionOutline, setExtractionOutline, protocolLoading, handleSaveSetup, handleGenerateProtocol,
  } = useWorkspace();

  return (
    <section className="glass-panel animate-fade-in" style={{ padding: '40px', maxWidth: '800px', margin: '0 auto' }}>
      <h2 style={{ marginBottom: '24px', color: 'var(--text-primary)' }}>Initialize Review Project</h2>
      <WorkspaceStageGate stage="protocol" />
      <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
        <div>
          <label style={{ display: 'block', marginBottom: '8px', color: 'var(--text-secondary)' }}>Project Title</label>
          <input type="text" className="search-input" value={projectName} onChange={e => setProjectName(e.target.value)} />
        </div>
        <div>
          <label style={{ display: 'block', marginBottom: '8px', color: 'var(--text-secondary)' }}>Type of Review</label>
          <select className="search-input" value={reviewType} onChange={e => setReviewType(e.target.value)}>
            <option>Systematic Review</option>
            <option>Scoping Review</option>
            <option>Rapid Review</option>
            <option>Umbrella Review</option>
            <option>Meta-Analysis</option>
          </select>
        </div>
        <div>
          <label style={{ display: 'block', marginBottom: '8px', color: 'var(--text-secondary)' }}>Eligibility Framework</label>
          <select className="search-input" value={framework} onChange={e => setFramework(e.target.value)}>
            <option>PICO</option>
            <option>PECO</option>
            <option>SPIDER</option>
          </select>
        </div>
        <div>
          <label style={{ display: 'block', marginBottom: '8px', color: 'var(--text-secondary)' }}>Description of Study</label>
          <textarea className="search-input" style={{ height: '80px', resize: 'vertical' }} placeholder="Provide a brief overview of your research objectives..." value={studyDescription} onChange={e => setStudyDescription(e.target.value)} />
        </div>
        <div>
          <label style={{ display: 'block', marginBottom: '8px', color: 'var(--text-secondary)' }}>Suggested Eligibility Criteria</label>
          <textarea className="search-input" style={{ height: '80px', resize: 'vertical' }} placeholder="E.g. Include adults > 18..." value={suggestedCriteria} onChange={e => setSuggestedCriteria(e.target.value)} />
        </div>
        <div>
          <label style={{ display: 'block', marginBottom: '8px', color: 'var(--text-secondary)' }}>Detailed Extraction Outline (One per line)</label>
          <textarea className="search-input" style={{ height: '80px', resize: 'vertical' }} placeholder="E.g. Sample Size..." value={extractionOutline} onChange={e => setExtractionOutline(e.target.value)} />
        </div>
        <button className="btn-glass" onClick={handleSaveSetup} disabled={protocolLoading} style={{ padding: '12px', marginTop: '16px' }}>Save Setup Without AI</button>
        <button className="btn-primary" onClick={handleGenerateProtocol} disabled={protocolLoading} style={{ padding: '16px', fontSize: '1.1rem', marginTop: '16px' }}>
          {protocolLoading ? `Compiling parameters...` : 'Compile & Send to AI Protocol Builder →'}
        </button>
      </div>
    </section>
  );
}
