import Papa from 'papaparse';
import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { modelDisplayName, type AiModelInfo } from '../../api/ai';
import { ApiError, errorMessage } from '../../api/client';
import { jobProblem, jobProgress, waitForJob, type AiJob } from '../../api/jobs';
import type { ProjectSummary } from '../../api/projects';
import { toPaper, type ApiRecord, type CriterionInfo, type Paper, type PrismaCounts, type ProtocolSettings, type SimilarPairs, type StrategyInfo, type WorkflowStageInfo } from '../../api/review';
import { batchLimit } from '../../app/plan';
import { useAuth } from '../../auth/authContext';
import type { ProjectTab } from './tabs';
import type { ProtocolItem, SearchItem } from './types';

const splitCriteria = (criteria: CriterionInfo[]) => {
  const toItem = (criterion: CriterionInfo): ProtocolItem => ({ id: String(criterion.id), text: criterion.text, status: criterion.status });
  return {
    inclusion: criteria.filter(c => c.kind === 'inclusion').map(toItem),
    exclusion: criteria.filter(c => c.kind === 'exclusion').map(toItem),
  };
};

const toSearchItems = (strategies: StrategyInfo[]): SearchItem[] =>
  strategies.map(s => ({ id: String(s.id), database: s.database, string: s.query, status: 'pending' }));

// All state and actions for one project's workspace. The workspace remounts per project, so state never leaks between projects.
export function useProjectWorkspaceState(projectId: number) {
  const { apiRequest } = useAuth();
  const navigate = useNavigate();

  const [currentProject, setCurrentProject] = useState<ProjectSummary | null>(null);
  const [aiModels, setAiModels] = useState<AiModelInfo[]>([]);
  const [embeddingModels, setEmbeddingModels] = useState<AiModelInfo[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [projectName, setProjectName] = useState('');

  const [reviewType, setReviewType] = useState('Systematic Review');
  const [studyDescription, setStudyDescription] = useState('');
  const [framework, setFramework] = useState('PICO');
  const [suggestedCriteria, setSuggestedCriteria] = useState('');
  const [extractionOutline, setExtractionOutline] = useState('');

  const [prisma, setPrisma] = useState<PrismaCounts | null>(null);
  const [protocolLoading, setProtocolLoading] = useState(false);
  const [inclusionItems, setInclusionItems] = useState<ProtocolItem[]>([]);
  const [exclusionItems, setExclusionItems] = useState<ProtocolItem[]>([]);
  const [searchItems, setSearchItems] = useState<SearchItem[]>([]);

  const [searchDBLoading, setSearchDBLoading] = useState<string | null>(null);
  const [literatureResults, setLiteratureResults] = useState<Paper[]>([]);
  const [dedupLoading, setDedupLoading] = useState(false);
  const [uploadLoading, setUploadLoading] = useState(false);
  const [abstractLoading, setAbstractLoading] = useState(false);
  const [abstractProgress, setAbstractProgress] = useState<number | null>(null);

  const [extractionColumns, setExtractionColumns] = useState<string[]>([]);
  const [fullTextLoading, setFullTextLoading] = useState(false);
  const [fullTextProgress, setFullTextProgress] = useState<number | null>(null);

  const [robTool, setRobTool] = useState('ROB-2');
  const [robLoading, setRobLoading] = useState(false);
  const [robComplete, setRobComplete] = useState(false);
  const [robProgress, setRobProgress] = useState<number | null>(null);

  const [metaLoading, setMetaLoading] = useState(false);
  const [metaReport, setMetaReport] = useState<string | null>(null);

  const [workflow, setWorkflow] = useState<WorkflowStageInfo[]>([]);
  const [workflowBusy, setWorkflowBusy] = useState(false);

  const [similar, setSimilar] = useState<SimilarPairs | null>(null);
  const [similarLoading, setSimilarLoading] = useState(false);
  const [similarProgress, setSimilarProgress] = useState<number | null>(null);

  // Stops following background jobs once the workspace closes. Reset on mount, because development mode mounts twice.
  const unmounted = useRef(false);
  useEffect(() => {
    unmounted.current = false;
    return () => {
      unmounted.current = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    const base = `/api/projects/${projectId}`;
    Promise.all([
      apiRequest('GET', base),
      apiRequest('GET', `${base}/protocol`),
      apiRequest('GET', `${base}/criteria`),
      apiRequest('GET', `${base}/search-strategies`),
      apiRequest('GET', `${base}/extraction-fields`),
      apiRequest('GET', `${base}/synthesis`),
      apiRequest('GET', `${base}/records`),
      apiRequest('GET', `${base}/prisma`),
      apiRequest('GET', `${base}/workflow`),
      apiRequest('GET', '/api/ai/models'),
      apiRequest('GET', '/api/ai/models?purpose=embedding'),
    ])
      .then(([project, protocol, criteria, strategies, fields, synthesis, records, counts, stages, models, embeddingModelList]) => {
        if (cancelled) return;
        const papers = (records as ApiRecord[]).map(toPaper);
        const { inclusion, exclusion } = splitCriteria(criteria);
        setCurrentProject(project);
        setProjectName(project.title);
        setReviewType(protocol.review_type);
        setFramework(protocol.framework);
        setStudyDescription(protocol.description);
        setSuggestedCriteria(protocol.suggested_criteria);
        setExtractionOutline(protocol.extraction_outline);
        setRobTool(protocol.rob_tool);
        setInclusionItems(inclusion);
        setExclusionItems(exclusion);
        setSearchItems(toSearchItems(strategies));
        setExtractionColumns(fields);
        setMetaReport(synthesis?.content ?? null);
        setLiteratureResults(papers);
        setPrisma(counts);
        setRobComplete(papers.some(p => p.rob_data));
        setWorkflow(stages);
        setAiModels(models);
        setEmbeddingModels(embeddingModelList);
      })
      .catch(err => {
        if (!cancelled) setLoadError(errorMessage(err, 'Could not load this project.'));
      });
    return () => {
      cancelled = true;
    };
  }, [apiRequest, projectId]);

  const apiPost = (path: string, body: unknown) => apiRequest('POST', path, body);
  const projectPath = (suffix: string) => `/api/projects/${projectId}/${suffix}`;
  const goTo = (tab: ProjectTab) => navigate(`/projects/${projectId}/${tab}`);
  const aiModelName = currentProject?.ai_model ? modelDisplayName(currentProject.ai_model) : 'AI';

  const applyProtocol = (protocol: ProtocolSettings) => {
    setReviewType(protocol.review_type);
    setFramework(protocol.framework);
    setStudyDescription(protocol.description);
    setSuggestedCriteria(protocol.suggested_criteria);
    setExtractionOutline(protocol.extraction_outline);
    setRobTool(protocol.rob_tool);
  };

  const applyCriteria = (criteria: CriterionInfo[]) => {
    const { inclusion, exclusion } = splitCriteria(criteria);
    setInclusionItems(inclusion);
    setExclusionItems(exclusion);
  };

  const loadWorkflow = async () => {
    setWorkflow(await apiRequest('GET', projectPath('workflow')));
  };

  const refreshRecords = async () => {
    const [records, counts] = await Promise.all([apiRequest('GET', projectPath('records')), apiRequest('GET', projectPath('prisma'))]);
    const papers = (records as ApiRecord[]).map(toPaper);
    setLiteratureResults(papers);
    setPrisma(counts);
    setRobComplete(papers.some(p => p.rob_data));
    await loadWorkflow();
  };

  const mergeRecords = (updated: ApiRecord[]) => {
    const byId = new Map(updated.map(record => [String(record.id), toPaper(record)]));
    setLiteratureResults(prev => prev.map(p => byId.get(p.id) ?? p));
  };

  const stageInfo = (stage: string) => workflow.find(item => item.stage === stage);

  const handleCompleteStage = async (stage: WorkflowStageInfo) => {
    const note = window.prompt(`Sign off "${stage.label}". Note for the audit trail (what you checked):`);
    if (note === null) return;
    if (note.trim().length < 3) {
      alert('Please write a short sign-off note (at least 3 characters).');
      return;
    }
    setWorkflowBusy(true);
    try {
      setWorkflow(await apiPost(projectPath(`workflow/${stage.stage}/complete`), { note: note.trim() }));
    } catch (err) {
      alert(errorMessage(err, 'Could not sign off this stage.'));
    }
    setWorkflowBusy(false);
  };

  const handleReopenStage = async (stage: WorkflowStageInfo) => {
    const rationale = window.prompt(`Reopen "${stage.label}"? Every later stage reopens too. Explain why (at least 10 characters):`);
    if (rationale === null) return;
    if (rationale.trim().length < 10) {
      alert('Please explain why the stage is being reopened (at least 10 characters).');
      return;
    }
    setWorkflowBusy(true);
    try {
      setWorkflow(await apiPost(projectPath(`workflow/${stage.stage}/reopen`), { rationale: rationale.trim() }));
    } catch (err) {
      alert(errorMessage(err, 'Could not reopen this stage.'));
    }
    setWorkflowBusy(false);
  };

  const saveTitle = async () => {
    if (!currentProject || !projectName.trim() || projectName.trim() === currentProject.title) return;
    const updated: ProjectSummary = await apiRequest('PATCH', `/api/projects/${projectId}`, { title: projectName.trim() });
    setCurrentProject(updated);
  };

  const handleAiModelChange = async (modelId: number) => {
    try {
      setCurrentProject(await apiRequest('PUT', `/api/projects/${projectId}/ai-model`, { ai_model_id: modelId }));
    } catch (err) {
      alert(errorMessage(err, 'Could not change the AI model.'));
    }
  };

  const handleEmbeddingModelChange = async (modelId: number) => {
    try {
      setCurrentProject(await apiRequest('PUT', `/api/projects/${projectId}/embedding-model`, { ai_model_id: modelId }));
      setSimilar(null);
    } catch (err) {
      alert(errorMessage(err, 'Could not change the similarity model.'));
    }
  };

  const saveProtocol = async (overrides: Partial<ProtocolSettings> = {}) => {
    const saved: ProtocolSettings = await apiRequest('PUT', projectPath('protocol'), {
      review_type: reviewType,
      framework,
      description: studyDescription,
      suggested_criteria: suggestedCriteria,
      extraction_outline: extractionOutline,
      rob_tool: robTool,
      ...overrides,
    });
    applyProtocol(saved);
    await loadWorkflow();
  };

  const followJob = (job: AiJob, onProgress: (percent: number) => void) =>
    waitForJob(job, id => apiRequest('GET', projectPath(`jobs/${id}`)), update => onProgress(jobProgress(update)), {
      stopped: () => unmounted.current,
    });

  // Starts a background AI job for the records, follows its progress, then reloads the stored results.
  const runAiJob = async (endpoint: string, ids: number[], label: string, onProgress: (percent: number) => void) => {
    try {
      const job = await followJob(await apiPost(projectPath(endpoint), { record_ids: ids }), onProgress);
      if (unmounted.current) return;
      await refreshRecords();
      const problem = jobProblem(job, label);
      if (problem) alert(problem);
    } catch (err) {
      alert(errorMessage(err, `${label} could not be started.`));
    }
  };

  // Only a reviewer's decision moves a paper forward; AI suggestions never do.
  const humanIncludedPapers = () => literatureResults.filter(p => p.user_decision === 'Include');

  const handleSaveSetup = async () => {
    try {
      await saveTitle();
      await saveProtocol();
      alert('Project setup saved.');
    } catch (err) {
      alert(errorMessage(err, 'Could not save the project setup.'));
    }
  };

  const handleGenerateProtocol = async () => {
    if (!studyDescription.trim()) {
      alert("Please provide a Description of Study.");
      return;
    }
    const hasProtocol = inclusionItems.length > 0 || exclusionItems.length > 0 || searchItems.length > 0;
    if (hasProtocol && !window.confirm('Regenerating replaces pending criteria and all search strings. Criteria you accepted or rejected are kept. Continue?')) return;

    setProtocolLoading(true);
    try {
      await saveTitle();
      await saveProtocol();
      const data = await apiPost(projectPath('protocol/generate'), {});
      applyCriteria(data.criteria);
      setSearchItems(toSearchItems(data.search_strategies));
      setExtractionColumns(data.extraction_fields);
      await loadWorkflow();
      goTo('protocol');
    } catch (err) {
      console.error(err);
      alert(errorMessage(err, "Error connecting to backend API. Is the FastAPI server running?"));
    }
    setProtocolLoading(false);
  };

  const handleCriterionStatus = async (id: string, status: 'accepted' | 'rejected') => {
    try {
      const updated: CriterionInfo = await apiRequest('PATCH', projectPath(`criteria/${id}`), { status });
      const update = (items: ProtocolItem[]) => items.map(item => item.id === id ? { ...item, status: updated.status } : item);
      setInclusionItems(update);
      setExclusionItems(update);
      await loadWorkflow();
    } catch (err) {
      alert(errorMessage(err, 'Could not update the criterion.'));
    }
  };

  const handleAcceptAll = async (kind: 'inclusion' | 'exclusion') => {
    try {
      applyCriteria(await apiPost(projectPath('criteria/accept-all'), { kind }));
      await loadWorkflow();
    } catch (err) {
      alert(errorMessage(err, 'Could not accept the criteria.'));
    }
  };

  const saveSearchString = async (id: string, query: string) => {
    if (!query.trim()) return;
    try {
      await apiRequest('PATCH', projectPath(`search-strategies/${id}`), { query });
    } catch (err) {
      alert(errorMessage(err, 'Could not save the search string.'));
    }
  };

  const handleRunDatabaseSearch = async (item: SearchItem) => {
    setSearchDBLoading(item.database);
    try {
      await apiRequest('PATCH', projectPath(`search-strategies/${item.id}`), { query: item.string });
      await apiPost(projectPath('searches'), { strategy_id: Number(item.id), limit: 50 });
      await refreshRecords();
    } catch (err) {
      alert(errorMessage(err, "Failed to reach the search API. Ensure the backend is running."));
    }
    setSearchDBLoading(null);
  };

  const importCsvFile = (file: File) => {
    setUploadLoading(true);
    Papa.parse(file, {
      header: true,
      skipEmptyLines: true,
      complete: async (results) => {
        const records = (results.data as any[]).map((row, i) => ({
          title: String(row['Title'] || row['title'] || `Uploaded record ${i + 1}`).slice(0, 2000),
          authors: String(row['Authors'] || row['Author'] || row['authors'] || row['Author(s)'] || '').slice(0, 5000),
          year: String(row['Year'] || row['year'] || '').slice(0, 20),
          venue: String(row['Venue'] || row['venue'] || '').slice(0, 1000),
          doi: String(row['DOI'] || row['doi'] || '').slice(0, 255),
          abstract: String(row['Abstract'] || row['abstract'] || '').slice(0, 50000),
        }));
        try {
          if (records.length === 0) throw new ApiError('The file has no rows to import.');
          await apiPost(projectPath('imports'), { file_name: file.name.slice(0, 200), records });
          await refreshRecords();
        } catch (err) {
          alert(errorMessage(err, 'Could not import the file.'));
        }
        setUploadLoading(false);
      },
      error: (err) => {
        console.error(err);
        alert("Error parsing CSV.");
        setUploadLoading(false);
      }
    });
  };

  const handleRunDedup = async () => {
    setDedupLoading(true);
    try {
      const result = await apiPost(projectPath('deduplicate'), {});
      await refreshRecords();
      alert(`Set aside ${result.duplicates_marked} duplicate record(s).`);
      goTo('screening');
    } catch (err) {
      alert(errorMessage(err, 'Deduplication failed.'));
    }
    setDedupLoading(false);
  };

  const handleRunAbstractScreening = async () => {
    if (!inclusionItems.some(item => item.status === 'accepted')) {
      alert("Accept at least one inclusion criterion in the AI Protocol Builder before screening.");
      return;
    }
    const ids = literatureResults.slice(0, batchLimit('abstract')).map(p => Number(p.id));
    if (ids.length === 0) {
      alert("Search for or import records first.");
      return;
    }
    setAbstractLoading(true);
    setAbstractProgress(0);
    await runAiJob('screening/ai', ids, 'AI screening', setAbstractProgress);
    setAbstractLoading(false);
    setTimeout(() => setAbstractProgress(null), 2000);
  };

  const handleUserDecision = async (id: string, decision: 'Include' | 'Exclude' | 'Undecided') => {
    try {
      const updated: ApiRecord = await apiRequest('PUT', projectPath(`records/${id}/decision`), { decision: decision.toLowerCase() });
      mergeRecords([updated]);
      setPrisma(await apiRequest('GET', projectPath('prisma')));
      await loadWorkflow();
    } catch (err) {
      alert(errorMessage(err, 'Could not save your decision.'));
    }
  };

  const loadSimilar = async () => {
    setSimilar(await apiRequest('GET', projectPath('similar-pairs?min_similarity=0.9')));
  };

  const handleFindSimilar = async () => {
    setSimilarLoading(true);
    setSimilarProgress(0);
    try {
      const job = await followJob(await apiPost(projectPath('embeddings'), {}), setSimilarProgress);
      if (!unmounted.current) {
        const problem = jobProblem(job, 'Comparing records');
        if (problem) alert(problem);
        if (job.status === 'completed') await loadSimilar();
      }
    } catch (err) {
      alert(errorMessage(err, 'Could not compare the records.'));
    }
    setSimilarLoading(false);
    setTimeout(() => setSimilarProgress(null), 2000);
  };

  const handleMarkDuplicate = async (recordId: number, originalId: number) => {
    try {
      await apiRequest('PUT', projectPath(`records/${recordId}/duplicate-of`), { duplicate_of_id: originalId });
      await refreshRecords();
      await loadSimilar();
    } catch (err) {
      alert(errorMessage(err, 'Could not mark the duplicate.'));
    }
  };

  const handleResetSearch = async () => {
    if (!window.confirm("Are you sure you want to delete all imported and searched records, with their screening decisions and AI results? This cannot be undone.")) return;
    try {
      await apiRequest('DELETE', projectPath('records'));
      await refreshRecords();
    } catch (err) {
      alert(errorMessage(err, 'Could not clear the records.'));
    }
  };

  const saveExtractionColumns = async (names: string[]) => {
    try {
      setExtractionColumns(await apiRequest('PUT', projectPath('extraction-fields'), { names }));
      await loadWorkflow();
    } catch (err) {
      alert(errorMessage(err, 'Could not save the extraction fields.'));
    }
  };

  const handleRunFullTextPipeline = async () => {
    const includedPapers = humanIncludedPapers();
    if (includedPapers.length === 0) {
      alert("Accept at least one paper in Abstract Screening before running extraction.");
      return;
    }
    setFullTextLoading(true);
    setFullTextProgress(0);
    const ids = includedPapers.slice(0, batchLimit('fulltext')).map(p => Number(p.id));
    await runAiJob('extraction/ai', ids, 'AI extraction', setFullTextProgress);
    setFullTextLoading(false);
    setTimeout(() => setFullTextProgress(null), 2000);
  };

  const handleRobToolChange = async (tool: string) => {
    setRobTool(tool);
    try {
      await saveProtocol({ rob_tool: tool });
    } catch (err) {
      alert(errorMessage(err, 'Could not save the assessment tool.'));
    }
  };

  const handleRunRob = async () => {
    const includedPapers = humanIncludedPapers();
    if (includedPapers.length === 0) {
      alert("Accept at least one paper in Abstract Screening before running a risk of bias assessment.");
      return;
    }
    setRobLoading(true);
    setRobProgress(0);
    const ids = includedPapers.slice(0, 10).map(p => Number(p.id));
    await runAiJob('appraisal/ai', ids, 'Risk of bias assessment', setRobProgress);
    setRobComplete(true);
    setRobLoading(false);
    setTimeout(() => setRobProgress(null), 2000);
  };

  const handleRunMetaAnalysis = async () => {
    if (humanIncludedPapers().length === 0) {
      alert("Accept at least one paper in Abstract Screening before generating a synthesis.");
      return;
    }
    setMetaLoading(true);
    setMetaReport(null);
    try {
      const report = await apiPost(projectPath('synthesis'), {});
      setMetaReport(report.content);
      await loadWorkflow();
    } catch (err) {
      console.error(err);
      setMetaReport(`**Error**: ${errorMessage(err, 'Failed to generate the synthesis.')}`);
    }
    setMetaLoading(false);
  };

  return {
    projectId,
    currentProject,
    loadError,
    aiModels,
    aiModelName,
    handleAiModelChange,
    embeddingModels,
    handleEmbeddingModelChange,
    similar,
    similarLoading,
    similarProgress,
    handleFindSimilar,
    handleMarkDuplicate,
    goTo,
    projectName,
    setProjectName,
    reviewType,
    setReviewType,
    framework,
    setFramework,
    studyDescription,
    setStudyDescription,
    suggestedCriteria,
    setSuggestedCriteria,
    extractionOutline,
    setExtractionOutline,
    protocolLoading,
    inclusionItems,
    exclusionItems,
    searchItems,
    setSearchItems,
    searchDBLoading,
    literatureResults,
    dedupLoading,
    uploadLoading,
    abstractLoading,
    abstractProgress,
    extractionColumns,
    fullTextLoading,
    fullTextProgress,
    robTool,
    robLoading,
    robComplete,
    robProgress,
    metaLoading,
    metaReport,
    prisma,
    workflowBusy,
    stageInfo,
    handleCompleteStage,
    handleReopenStage,
    handleSaveSetup,
    handleGenerateProtocol,
    handleCriterionStatus,
    handleAcceptAll,
    saveSearchString,
    handleRunDatabaseSearch,
    importCsvFile,
    handleRunDedup,
    handleRunAbstractScreening,
    handleUserDecision,
    handleResetSearch,
    saveExtractionColumns,
    handleRunFullTextPipeline,
    handleRobToolChange,
    handleRunRob,
    handleRunMetaAnalysis,
  };
}
