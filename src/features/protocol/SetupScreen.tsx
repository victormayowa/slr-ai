import { Link } from 'react-router-dom';
import { modelDisplayName } from '../../api/ai';
import { canEditProject } from '../../api/projects';
import { WorkspaceStageGate } from '../project/WorkspaceStageGate';
import { useWorkspace } from '../project/workspaceContext';

function AiModelPicker() {
  const { currentProject, aiModels, handleAiModelChange } = useWorkspace();
  const pinned = currentProject?.ai_model ?? null;
  const offered = pinned === null || aiModels.some(model => model.id === pinned.id);
  const options = offered || pinned === null ? aiModels : [pinned, ...aiModels];
  const selected = aiModels.find(model => model.id === pinned?.id);
  const providerLabels = [...new Set(options.map(model => model.provider_label))];
  const canChange = currentProject !== null && canEditProject(currentProject.role);

  return (
    <div>
      <label htmlFor="project-ai-model" style={{ display: 'block', marginBottom: '8px', color: 'var(--text-secondary)' }}>AI Model</label>
      <select id="project-ai-model" className="search-input" value={pinned?.id ?? ''} disabled={!canChange} onChange={e => handleAiModelChange(Number(e.target.value))}>
        {pinned === null && <option value="">Choose a model</option>}
        {providerLabels.map(providerLabel => (
          <optgroup key={providerLabel} label={providerLabel}>
            {options.filter(model => model.provider_label === providerLabel).map(model => (
              <option key={model.id} value={model.id}>
                {model.label}{model.available === false ? ' (needs an API key)' : ''}
              </option>
            ))}
          </optgroup>
        ))}
      </select>
      <p style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginTop: '8px' }}>
        Every AI step in this project uses this model, and each AI result records the model that produced it.
        {!canChange && ' Only the project owner or lead reviewer can change it.'}
        {selected?.data_location && ` Review content is sent to ${selected.data_location}.`}
      </p>
      {!offered && (
        <p role="alert" style={{ fontSize: '0.85rem', color: '#ef4444' }}>
          {modelDisplayName(pinned)} is no longer offered. Choose another model before running AI steps.
        </p>
      )}
      {selected?.available === false && (
        <p role="alert" style={{ fontSize: '0.85rem', color: '#f59e0b' }}>
          No {selected.provider_label} API key is available to you. <Link to="/settings">Add your own key in Settings</Link> or choose another model.
        </p>
      )}
    </div>
  );
}

function EmbeddingModelPicker() {
  const { currentProject, embeddingModels, handleEmbeddingModelChange } = useWorkspace();
  const pinned = currentProject?.embedding_model ?? null;
  const selected = embeddingModels.find(model => model.id === pinned?.id);
  const canChange = currentProject !== null && canEditProject(currentProject.role);

  return (
    <div>
      <label htmlFor="project-embedding-model" style={{ display: 'block', marginBottom: '8px', color: 'var(--text-secondary)' }}>Similarity Model</label>
      <select id="project-embedding-model" className="search-input" value={pinned?.id ?? ''} disabled={!canChange} onChange={e => handleEmbeddingModelChange(Number(e.target.value))}>
        {pinned === null && <option value="">Choose a model</option>}
        {pinned !== null && selected === undefined && <option value={pinned.id}>{modelDisplayName(pinned)} (no longer offered)</option>}
        {embeddingModels.map(model => (
          <option key={model.id} value={model.id}>
            {modelDisplayName(model)}{model.available === false ? ' (needs an API key)' : ''}
          </option>
        ))}
      </select>
      <p style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginTop: '8px' }}>
        Used to find records that say nearly the same thing, such as possible duplicates. After a change, records are compared again with the new model.
      </p>
    </div>
  );
}

export function SetupScreen() {
  const {
    projectName, setProjectName, reviewType, setReviewType, studyDescription, setStudyDescription,
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
        <AiModelPicker />
        <EmbeddingModelPicker />
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
