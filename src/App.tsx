import { useState } from 'react';
import Papa from 'papaparse';
import { API_BASE, ApiError, errorDetail, errorMessage } from './api/client';
import { dedupKey } from './lib/dedup';
import './index.css';

type ProtocolItem = { id: string; text: string; status: 'pending' | 'accepted' | 'rejected' };
type SearchItem = { id: string; database: string; string: string; status: 'pending' | 'accepted' | 'rejected' };
type Paper = { id: string; title: string; authors: string; year: string | number; source: string; doi: string; venue?: string; abstract?: string; ai_decision?: string; ai_reasoning?: string; ai_error?: string; user_decision?: 'Include' | 'Exclude' | 'Undecided' | null; extracted_data?: Record<string, string>; extraction_error?: string; rob_data?: Record<string, string>; rob_error?: string; };

const ProgressBar = ({ progress, label }: { progress: number; label: string }) => (
  <div style={{ marginTop: '16px', width: '100%', maxWidth: '400px' }}>
    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '8px', fontSize: '0.9rem', color: 'var(--accent-primary)' }}>
      <span>{label}</span>
      <span>{progress}%</span>
    </div>
    <div style={{ width: '100%', height: '8px', background: 'rgba(255,255,255,0.1)', borderRadius: '4px', overflow: 'hidden' }}>
      <div style={{ width: `${progress}%`, height: '100%', background: 'linear-gradient(90deg, var(--accent-primary), var(--accent-secondary))', transition: 'width 0.3s ease' }} />
    </div>
  </div>
);

function App() {
  const [currentView, setCurrentView] = useState<'dashboard' | 'project' | 'settings'>('dashboard');
  const [activeTab, setActiveTab] = useState<'setup' | 'protocol' | 'search' | 'dedup' | 'abstract' | 'extraction_rules' | 'fulltext' | 'prisma' | 'rob' | 'meta'>('setup');
  const [projectName, setProjectName] = useState('Untitled Review');
  const [userTier] = useState<'Free' | 'PLUS' | 'PRO'>('PRO');
  
  const [reviewType, setReviewType] = useState('Systematic Review');
  const [studyDescription, setStudyDescription] = useState('');
  const [framework, setFramework] = useState('PICO');
  const [suggestedCriteria, setSuggestedCriteria] = useState('');
  const [extractionOutline, setExtractionOutline] = useState('');
  
  const [prisma, setPrisma] = useState({
    sources: {} as Record<string, number>,
    searched: 0,
    uploaded: 0,
    duplicates_removed: 0,
    abstract_screened: 0,
    abstract_excluded: 0,
    fulltext_sought: 0,
    fulltext_not_retrieved: 0,
    fulltext_assessed: 0,
    fulltext_excluded: 0,
    final_included: 0
  });

  const [aiProvider, setAiProvider] = useState('gemini');
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
  
  const [extractionColumns, setExtractionColumns] = useState<string[]>(['Sample Size', 'Mean Age', 'Primary Outcome Result', 'Adverse Events']);
  const [newColumn, setNewColumn] = useState('');
  const [fullTextLoading, setFullTextLoading] = useState(false);
  const [fullTextProgress, setFullTextProgress] = useState<number | null>(null);

  const [robTool, setRobTool] = useState('ROB-2');
  const [robLoading, setRobLoading] = useState(false);
  const [robComplete, setRobComplete] = useState(false);
  const [robProgress, setRobProgress] = useState<number | null>(null);

  const [metaLoading, setMetaLoading] = useState(false);
  const [metaReport, setMetaReport] = useState<string | null>(null);
  
  const [chatOpen, setChatOpen] = useState(false);
  const [chatMessages, setChatMessages] = useState<{role: 'user'|'ai', text: string}[]>([]);
  const [chatInput, setChatInput] = useState('');
  const [chatLoading, setChatLoading] = useState(false);
  
  const [currentUser, setCurrentUser] = useState<string | null>(null);
  const [authToken, setAuthToken] = useState<string | null>(null);
  const [authMode, setAuthMode] = useState<'login' | 'register' | 'forgot'>('login');
  const [showShareModal, setShowShareModal] = useState(false);
  const [inviteEmail, setInviteEmail] = useState('');
  
  const [loginIdentifier, setLoginIdentifier] = useState('');
  const [loginPassword, setLoginPassword] = useState('');
  
  const [regFirstName, setRegFirstName] = useState('');
  const [regLastName, setRegLastName] = useState('');
  const [regEmail, setRegEmail] = useState('');
  const [regInstEmail, setRegInstEmail] = useState('');
  const [regOrcid, setRegOrcid] = useState('');
  const [regPassword, setRegPassword] = useState('');
  const [regConfirmPassword, setRegConfirmPassword] = useState('');
  const [regRole, setRegRole] = useState('');
  const [regInstitution, setRegInstitution] = useState('');
  const [regReason, setRegReason] = useState('');
  const [authError, setAuthError] = useState('');

  const signOut = () => {
    setAuthToken(null);
    setCurrentUser(null);
  };

  const apiPost = async (path: string, body: unknown) => {
    const res = await fetch(`${API_BASE}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...(authToken ? { Authorization: `Bearer ${authToken}` } : {}) },
      body: JSON.stringify(body)
    });
    const data = await res.json().catch(() => ({}));
    if (res.status === 401 && authToken) signOut();
    if (!res.ok) throw new ApiError(errorDetail(data, res.status));
    return data;
  };

  // Only a reviewer's decision moves a paper forward; AI suggestions never do.
  const humanIncludedPapers = () => literatureResults.filter(p => p.user_decision === 'Include');

  const getBatchLimit = (stage: 'abstract' | 'fulltext') => {
    if (stage === 'abstract') return userTier === 'PRO' ? 200 : userTier === 'PLUS' ? 100 : 20;
    return userTier === 'PRO' ? 200 : userTier === 'PLUS' ? 100 : 50;
  };

  const handleGenerateProtocol = async () => {
    if (!studyDescription) {
      alert("Please provide a Description of Study.");
      return;
    }
    setProtocolLoading(true);
    
    if (extractionOutline) {
      const outlineLines = extractionOutline.split('\n').filter(l => l.trim().length > 0);
      if (outlineLines.length > 0) {
         setExtractionColumns([...new Set([...extractionColumns, ...outlineLines])]);
      }
    }

    const masterPrompt = `Title: ${projectName}\nReview Type: ${reviewType}\nFramework: ${framework}\nDescription: ${studyDescription}\nSuggested Criteria: ${suggestedCriteria}`;
    try {
      const data = await apiPost('/api/protocol/generate', { research_question: masterPrompt, provider: aiProvider });
      
      setInclusionItems(data.inclusion_criteria.map((c: string, i: number) => ({ id: `inc-${i}-${Date.now()}`, text: c, status: 'pending' })));
      setExclusionItems(data.exclusion_criteria.map((c: string, i: number) => ({ id: `exc-${i}-${Date.now()}`, text: c, status: 'pending' })));
      setSearchItems(data.boolean_searches.map((s: any, i: number) => ({ id: `search-${i}-${Date.now()}`, database: s.database, string: s.string, status: 'pending' })));
      setActiveTab('protocol');
    } catch (err) {
      console.error(err);
      alert(errorMessage(err, "Error connecting to backend API. Is the FastAPI server running?"));
    }
    setProtocolLoading(false);
  };

  const handleItemStatus = (setter: any, items: any[], id: string, status: 'accepted' | 'rejected') => {
    setter(items.map(item => item.id === id ? { ...item, status } : item));
  };

  const handleAcceptAll = (setter: any, items: any[]) => {
    setter(items.map(item => ({ ...item, status: 'accepted' })));
  };

  const handleRunDatabaseSearch = async (database: string) => {
    setSearchDBLoading(database);
    const searchStr = searchItems.find(s => s.database === database)?.string || '';
    
    try {
      const data = await apiPost('/api/search', { database, query: searchStr, limit: 50 });
      
      const realPapers: Paper[] = data.results || [];
      const sourceLabel: string = data.source || database;
      
      setLiteratureResults(prev => [...prev, ...realPapers]);
      setPrisma(prev => ({ 
        ...prev, 
        searched: prev.searched + realPapers.length,
        sources: { ...prev.sources, [sourceLabel]: (prev.sources[sourceLabel] || 0) + realPapers.length }
      }));
    } catch(err) {
      alert(errorMessage(err, "Failed to reach the search API. Ensure the backend is running."));
    }
    setSearchDBLoading(null);
  };

  const handleDownloadTemplate = () => {
    const headers = "Source,Title,Authors,Year,DOI,Venue,Abstract,PMID\n";
    const blob = new Blob([headers], { type: 'text/csv' });
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'OmniReview_Upload_Template.csv';
    a.click();
    window.URL.revokeObjectURL(url);
  };

  const handleFileUpload = () => {
    document.getElementById('csv-upload-input')?.click();
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    
    setUploadLoading(true);
    Papa.parse(file, {
      header: true,
      skipEmptyLines: true,
      complete: (results) => {
        const parsedPapers: Paper[] = [];
        
        results.data.forEach((row: any, i: number) => {
          const title = row['Title'] || row['title'] || `Uploaded Paper ${i}`;
          const author = row['Authors'] || row['Author'] || row['authors'] || row['Author(s)'] || 'Unknown';
          const year = row['Year'] || row['year'] || '';
          const doi = row['DOI'] || row['doi'] || '';
          const abstract = row['Abstract'] || row['abstract'] || 'No abstract provided in CSV.';
          const source = row['Source'] || row['source'] || 'Manual Upload (CSV)';
          
          parsedPapers.push({
            id: `u-${Date.now()}-${i}`,
            source,
            title,
            authors: author,
            year,
            doi,
            abstract
          });
        });
        
        setLiteratureResults(prev => [...prev, ...parsedPapers]);
        setPrisma(prev => ({ 
          ...prev, 
          uploaded: prev.uploaded + parsedPapers.length,
          sources: { ...prev.sources, ['Manual Upload']: (prev.sources['Manual Upload'] || 0) + parsedPapers.length }
        }));
        setUploadLoading(false);
        
        e.target.value = '';
      },
      error: (err) => {
        console.error(err);
        alert("Error parsing CSV.");
        setUploadLoading(false);
      }
    });
  };

  const handleRunDedup = () => {
    setDedupLoading(true);
    // Simple basic deduplication by Title or DOI logic can go here. For now we just filter out explicitly identical DOIs or Titles in the array.
    setTimeout(() => {
      const uniqueIds = new Set();
      const deduped: Paper[] = [];
      let removedCount = 0;
      
      for(const p of literatureResults) {
        const key = dedupKey(p);
        if(uniqueIds.has(key)) {
          removedCount++;
        } else {
          uniqueIds.add(key);
          deduped.push(p);
        }
      }
      
      setLiteratureResults(deduped);
      setPrisma(prev => ({ ...prev, duplicates_removed: prev.duplicates_removed + removedCount, abstract_screened: deduped.length }));
      setDedupLoading(false);
      setActiveTab('abstract');
    }, 1000);
  };

  const handleRunAbstractScreening = async () => {
    const acceptedText = (items: ProtocolItem[]) => items.filter(i => i.status === 'accepted').map(i => i.text);
    const inclusion = acceptedText(inclusionItems);
    const exclusion = acceptedText(exclusionItems);
    if (inclusion.length === 0) {
      alert("Accept at least one inclusion criterion in the AI Protocol Builder before screening.");
      return;
    }
    setAbstractLoading(true);
    setAbstractProgress(0);
    const limit = getBatchLimit('abstract');
    const papersToScreen = literatureResults.slice(0, limit);
    const criteriaStr = `Include only if all of these apply:\n- ${inclusion.join('\n- ')}\n\nExclude if any of these apply:\n- ${exclusion.length > 0 ? exclusion.join('\n- ') : '(none specified)'}`;
    
    const batchSize = 5;
    const allScreened: Paper[] = [];
    let failedBatches = 0;
    let screenedCount = 0;
    
    for (let i = 0; i < papersToScreen.length; i += batchSize) {
      const chunk = papersToScreen.slice(i, i + batchSize);
      try {
        const data = await apiPost('/api/screen/abstract', { papers: chunk, criteria: criteriaStr, provider: aiProvider });
        allScreened.push(...data.results);
      } catch(err) {
        console.error(err);
        failedBatches++;
      }
      screenedCount += chunk.length;
      setAbstractProgress(Math.round((screenedCount / papersToScreen.length) * 100));
    }
    
    const screenedDict = Object.fromEntries(allScreened.map(r => [r.id, r]));
    setLiteratureResults(prev => prev.map(p => {
      const r = screenedDict[p.id];
      return r ? { ...p, ai_decision: r.ai_decision, ai_reasoning: r.ai_reasoning, ai_error: r.ai_error } : p;
    }));
    const failedPapers = allScreened.filter(r => r.ai_error).length;
    if (failedBatches > 0 || failedPapers > 0) {
      alert(`AI screening incomplete: ${failedPapers} paper(s) failed and ${failedBatches} batch(es) could not be processed. Those papers are marked "Error" and need manual screening.`);
    }
    setAbstractLoading(false);
    setTimeout(() => setAbstractProgress(null), 2000);
  };

  const handleUserDecision = (id: string, decision: 'Include' | 'Exclude' | 'Undecided') => {
    setLiteratureResults(prev => prev.map(p => p.id === id ? { ...p, user_decision: decision } : p));
    
    // Update PRISMA dynamically based on user manual choices
    const currentExcluded = literatureResults.filter(p => (p.id === id ? decision : p.user_decision) === 'Exclude').length;
    setPrisma(prev => ({
      ...prev,
      abstract_excluded: currentExcluded,
      fulltext_sought: prev.abstract_screened - currentExcluded
    }));
  };

  const handleResetSearch = () => {
    if (window.confirm("Are you sure you want to clear all imported and searched literature? This cannot be undone.")) {
      setLiteratureResults([]);
      setPrisma(prev => ({
        ...prev,
        sources: {},
        searched: 0,
        uploaded: 0,
        duplicates_removed: 0,
        abstract_screened: 0,
        abstract_excluded: 0,
        fulltext_sought: 0,
        fulltext_not_retrieved: 0,
        fulltext_assessed: 0,
        fulltext_excluded: 0,
        final_included: 0
      }));
    }
  };

  const handleRemoveColumn = (colToRemove: string) => {
    setExtractionColumns(extractionColumns.filter(c => c !== colToRemove));
  };
  const handleAddColumn = () => {
    if (newColumn && !extractionColumns.includes(newColumn)) {
      setExtractionColumns([...extractionColumns, newColumn]);
      setNewColumn('');
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
    
    const papersToScreen = includedPapers.slice(0, getBatchLimit('fulltext'));
    
    const batchSize = 3; // Smaller batch size because extraction prompts are larger
    const allExtracted: Paper[] = [];
    let failedBatches = 0;
    let processedCount = 0;
    
    for (let i = 0; i < papersToScreen.length; i += batchSize) {
      const chunk = papersToScreen.slice(i, i + batchSize);
      try {
        const data = await apiPost('/api/screen/fulltext', { papers: chunk, columns: extractionColumns, provider: aiProvider });
        allExtracted.push(...data.results);
      } catch (err) {
        console.error("FullText AI failed:", err);
        failedBatches++;
      }
      processedCount += chunk.length;
      setFullTextProgress(Math.round((processedCount / papersToScreen.length) * 100));
    }
    
    const dict = Object.fromEntries(allExtracted.map(r => [r.id, r]));
    setLiteratureResults(prev => prev.map(p => dict[p.id] ? { ...p, extracted_data: dict[p.id].extracted_data, extraction_error: dict[p.id].extraction_error } : p));
    
    const failedPapers = allExtracted.filter(r => r.extraction_error).length;
    if (failedBatches > 0 || failedPapers > 0) {
      alert(`AI extraction incomplete: ${failedPapers} paper(s) failed and ${failedBatches} batch(es) could not be processed.`);
    }
    
    setFullTextLoading(false);
    setTimeout(() => setFullTextProgress(null), 2000);
  };

  const handleRunRob = async () => {
    const includedPapers = humanIncludedPapers();
    if (includedPapers.length === 0) {
      alert("Accept at least one paper in Abstract Screening before running a risk of bias assessment.");
      return;
    }
    setRobLoading(true);
    setRobProgress(0);
    
    const papersToScreen = includedPapers.slice(0, 10);
    
    const batchSize = 3;
    const allRob: Paper[] = [];
    let failedBatches = 0;
    let processedCount = 0;
    
    for (let i = 0; i < papersToScreen.length; i += batchSize) {
      const chunk = papersToScreen.slice(i, i + batchSize);
      try {
        const data = await apiPost('/api/screen/rob', { papers: chunk, tool: robTool, provider: aiProvider });
        allRob.push(...data.results);
      } catch (err) {
        console.error("RoB AI failed:", err);
        failedBatches++;
      }
      processedCount += chunk.length;
      setRobProgress(Math.round((processedCount / papersToScreen.length) * 100));
    }
    
    const dict = Object.fromEntries(allRob.map(r => [r.id, r]));
    setLiteratureResults(prev => prev.map(p => dict[p.id] ? { ...p, rob_data: dict[p.id].rob_data, rob_error: dict[p.id].rob_error } : p));
    const failedPapers = allRob.filter(r => r.rob_error).length;
    if (failedBatches > 0 || failedPapers > 0) {
      alert(`Risk of bias assessment incomplete: ${failedPapers} paper(s) failed and ${failedBatches} batch(es) could not be processed.`);
    }
    
    setRobComplete(true);
    setRobLoading(false);
    setTimeout(() => setRobProgress(null), 2000);
  };

  const handleRunMetaAnalysis = async () => {
    const includedPapers = humanIncludedPapers();
    if (includedPapers.length === 0) {
      alert("Accept at least one paper in Abstract Screening before generating a synthesis.");
      return;
    }
    setMetaLoading(true);
    setMetaReport(null);
    
    try {
      const papers = includedPapers.map(({ title, authors, year, doi, extracted_data, rob_data }) => ({ title, authors, year, doi, extracted_data, rob_data }));
      const data = await apiPost('/api/screen/meta', { papers, provider: aiProvider });
      setMetaReport(data.report);
    } catch (err) {
      console.error(err);
      setMetaReport(`**Error**: ${errorMessage(err, 'Failed to generate the synthesis.')}`);
    }
    setMetaLoading(false);
  };

  const handleChatSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!chatInput.trim()) return;
    
    const query = chatInput;
    setChatInput('');
    setChatMessages(prev => [...prev, { role: 'user', text: query }]);
    setChatLoading(true);
    
    try {
      const data = await apiPost('/api/chat', { query, provider: aiProvider });
      setChatMessages(prev => [...prev, { role: 'ai', text: data.answer }]);
    } catch (err) {
      setChatMessages(prev => [...prev, { role: 'ai', text: errorMessage(err, 'Error connecting to FAQ service.') }]);
    }
    setChatLoading(false);
  };

  const handleInvite = (e: React.FormEvent) => {
    e.preventDefault();
    if (inviteEmail) {
      alert(`Invitation sent to ${inviteEmail}! They will receive an email shortly to join this project.`);
      setInviteEmail('');
      setShowShareModal(false);
    }
  };

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setAuthError('');
    try {
      const data = await apiPost('/api/auth/login', { identifier: loginIdentifier, password: loginPassword });
      setAuthToken(data.access_token);
      setCurrentUser(data.user.name);
      setCurrentView('dashboard');
    } catch (err) {
      setAuthError(errorMessage(err, 'Network error'));
    }
  };

  const handleRegister = async (e: React.FormEvent) => {
    e.preventDefault();
    setAuthError('');
    if (regPassword !== regConfirmPassword) {
      setAuthError('Passwords do not match');
      return;
    }
    if (!regRole) {
      setAuthError('Please select a position/role');
      return;
    }
    
    try {
      await apiPost('/api/auth/register', {
        first_name: regFirstName,
        last_name: regLastName,
        email: regEmail,
        institutional_email: regInstEmail || null,
        orcid_id: regOrcid || null,
        password: regPassword,
        position_role: regRole,
        institution: regInstitution,
        reason_for_joining: regReason
      });
      setAuthMode('login');
      alert('Registration successful! Please login.');
    } catch (err) {
      setAuthError(errorMessage(err, 'Network error'));
    }
  };

  const tabs = [
    { id: 'setup', label: '1. Project Setup' },
    { id: 'protocol', label: '2. AI Protocol Builder' },
    { id: 'search', label: '3. Database Search & Import' },
    { id: 'dedup', label: '4. Deduplication' },
    { id: 'abstract', label: '5. Abstract Screening' },
    { id: 'extraction_rules', label: '6. Extraction Rules' },
    { id: 'fulltext', label: '7. Full-Text Screening & Extract' },
    { id: 'prisma', label: '8. PRISMA & Export' },
    { id: 'rob', label: '9. Risk of Bias & Quality' },
    { id: 'meta', label: '10. Narrative Synthesis' }
  ];

  if (!currentUser) {
    return (
      <div className="app-container" style={{ justifyContent: 'center', alignItems: 'center', flexDirection: 'column', height: '100vh', background: 'radial-gradient(circle at 50% 50%, #1e1e2f 0%, #0f0f17 100%)' }}>
        <div style={{ textAlign: 'center', marginBottom: '32px' }}>
          <div style={{ width: '64px', height: '64px', borderRadius: '16px', background: 'linear-gradient(135deg, var(--accent-primary), var(--accent-secondary))', display: 'flex', alignItems: 'center', justifyContent: 'center', fontWeight: 'bold', fontSize: '2rem', margin: '0 auto 16px', boxShadow: '0 8px 24px rgba(59,130,246,0.3)' }}>O</div>
          <h1 style={{ fontSize: '2rem', margin: 0, fontWeight: 700, letterSpacing: '-0.5px' }}>OmniReview AI</h1>
        </div>
        <div className="glass-panel animate-fade-in" style={{ width: '100%', maxWidth: '400px', padding: '40px', border: '1px solid rgba(255,255,255,0.1)' }}>
          {authError && (
            <div style={{ background: 'rgba(239, 68, 68, 0.1)', color: '#ef4444', padding: '12px', borderRadius: '8px', marginBottom: '24px', fontSize: '0.9rem', textAlign: 'center', border: '1px solid rgba(239, 68, 68, 0.2)' }}>
              {authError}
            </div>
          )}
          {authMode === 'login' && (
            <form onSubmit={handleLogin}>
              <h2 style={{ marginBottom: '24px', textAlign: 'center', fontSize: '1.5rem', fontWeight: 600 }}>Welcome Back</h2>
              <input type="text" placeholder="Email, Inst. Email, or ORCID" required value={loginIdentifier} onChange={e => setLoginIdentifier(e.target.value)} className="search-input" style={{ width: '100%', marginBottom: '16px' }} />
              <input type="password" placeholder="Password" required value={loginPassword} onChange={e => setLoginPassword(e.target.value)} className="search-input" style={{ width: '100%', marginBottom: '24px' }} />
              <button type="submit" className="btn-primary" style={{ width: '100%', marginBottom: '16px', height: '44px', fontWeight: 600 }}>Sign In</button>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.9rem' }}>
                <a href="#" style={{ color: 'var(--text-secondary)', textDecoration: 'none', transition: 'color 0.2s' }} onClick={(e) => { e.preventDefault(); setAuthMode('forgot'); }}>Forgot Password?</a>
                <a href="#" style={{ color: 'var(--accent-primary)', textDecoration: 'none', fontWeight: 500 }} onClick={(e) => { e.preventDefault(); setAuthMode('register'); }}>Create Account</a>
              </div>
            </form>
          )}
          {authMode === 'register' && (
            <form onSubmit={handleRegister} style={{ maxHeight: '60vh', overflowY: 'auto', paddingRight: '8px' }}>
              <h2 style={{ marginBottom: '24px', textAlign: 'center', fontSize: '1.5rem', fontWeight: 600 }}>Create Account</h2>
              <div style={{ display: 'flex', gap: '12px', marginBottom: '16px' }}>
                <input type="text" placeholder="First Name" required value={regFirstName} onChange={e => setRegFirstName(e.target.value)} className="search-input" style={{ width: '50%' }} />
                <input type="text" placeholder="Last Name" required value={regLastName} onChange={e => setRegLastName(e.target.value)} className="search-input" style={{ width: '50%' }} />
              </div>
              <input type="email" placeholder="Personal Email Address" required value={regEmail} onChange={e => setRegEmail(e.target.value)} className="search-input" style={{ width: '100%', marginBottom: '16px' }} />
              <input type="email" placeholder="Institutional Email (Optional)" value={regInstEmail} onChange={e => setRegInstEmail(e.target.value)} className="search-input" style={{ width: '100%', marginBottom: '16px' }} />
              <input type="text" placeholder="ORCID iD (Optional)" value={regOrcid} onChange={e => setRegOrcid(e.target.value)} className="search-input" style={{ width: '100%', marginBottom: '16px' }} />
              
              <div style={{ display: 'flex', gap: '12px', marginBottom: '16px' }}>
                <input type="password" placeholder="Create Password" required value={regPassword} onChange={e => setRegPassword(e.target.value)} className="search-input" style={{ width: '50%' }} />
                <input type="password" placeholder="Confirm Password" required value={regConfirmPassword} onChange={e => setRegConfirmPassword(e.target.value)} className="search-input" style={{ width: '50%' }} />
              </div>
              
              <select required value={regRole} onChange={e => setRegRole(e.target.value)} className="search-input" style={{ width: '100%', marginBottom: '16px' }}>
                <option value="" disabled>Select Position / Role</option>
                <option value="Undergrad Student">Undergrad Student</option>
                <option value="Graduate Student">Graduate Student</option>
                <option value="PhD Student">PhD Student</option>
                <option value="Professor">Professor</option>
                <option value="Librarian">Librarian</option>
                <option value="Biomedical Researcher">Biomedical Researcher</option>
                <option value="Engineer Researcher">Engineer Researcher</option>
                <option value="Researcher">Researcher</option>
                <option value="Others">Others</option>
              </select>
              
              <input type="text" placeholder="Institution / Organization" required value={regInstitution} onChange={e => setRegInstitution(e.target.value)} className="search-input" style={{ width: '100%', marginBottom: '16px' }} />
              <input type="text" placeholder="Reason for Joining" required value={regReason} onChange={e => setRegReason(e.target.value)} className="search-input" style={{ width: '100%', marginBottom: '24px' }} />

              <button type="submit" className="btn-primary" style={{ width: '100%', marginBottom: '16px', height: '44px', fontWeight: 600 }}>Sign Up</button>
              <div style={{ textAlign: 'center', fontSize: '0.9rem' }}>
                <span style={{ color: 'var(--text-secondary)' }}>Already have an account? </span>
                <a href="#" style={{ color: 'var(--accent-primary)', textDecoration: 'none', fontWeight: 500 }} onClick={(e) => { e.preventDefault(); setAuthMode('login'); }}>Sign In</a>
              </div>
            </form>
          )}
          {authMode === 'forgot' && (
            <form>
              <h2 style={{ marginBottom: '16px', textAlign: 'center', fontSize: '1.5rem', fontWeight: 600 }}>Reset Password</h2>
              <p style={{ color: 'var(--text-secondary)', fontSize: '0.9rem', marginBottom: '24px', textAlign: 'center', lineHeight: '1.5' }}>Enter your email address and we'll send you a link to reset your password.</p>
              <input type="email" placeholder="Email Address" required className="search-input" style={{ width: '100%', marginBottom: '24px' }} />
              <button type="button" className="btn-primary" style={{ width: '100%', marginBottom: '16px', height: '44px', fontWeight: 600 }} onClick={() => { setAuthMode('login'); alert('Password reset link sent to your email.'); }}>Send Reset Link</button>
              <div style={{ textAlign: 'center', fontSize: '0.9rem' }}>
                <a href="#" style={{ color: 'var(--text-secondary)', textDecoration: 'none', transition: 'color 0.2s' }} onClick={(e) => { e.preventDefault(); setAuthMode('login'); }}>← Back to Login</a>
              </div>
            </form>
          )}
        </div>
      </div>
    );
  }

  if (currentView === 'dashboard') {
    return (
      <div className="app-container" style={{ justifyContent: 'center', alignItems: 'center', flexDirection: 'column', height: '100vh', position: 'relative' }}>
        <div style={{ position: 'absolute', top: '24px', right: '40px', display: 'flex', alignItems: 'center', gap: '20px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            <div style={{ width: '40px', height: '40px', borderRadius: '50%', background: 'linear-gradient(135deg, var(--accent-primary), var(--accent-secondary))', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '1rem', fontWeight: 'bold', boxShadow: '0 4px 12px rgba(59,130,246,0.2)' }}>
              {currentUser.charAt(0).toUpperCase()}
            </div>
            <div>
              <div style={{ fontSize: '0.95rem', fontWeight: 600 }}>{currentUser}</div>
              <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>Pro Researcher</div>
            </div>
          </div>
          <button style={{ background: 'transparent', border: '1px solid rgba(255,255,255,0.1)', color: 'var(--text-secondary)', padding: '8px 16px', borderRadius: '8px', cursor: 'pointer', fontSize: '0.85rem', transition: 'all 0.2s', display: 'flex', alignItems: 'center', gap: '8px' }} onClick={() => setCurrentView('settings')} onMouseOver={e => e.currentTarget.style.color = '#fff'} onMouseOut={e => e.currentTarget.style.color = 'var(--text-secondary)'}>
            ⚙️ Settings
          </button>
          <button style={{ background: 'transparent', border: '1px solid rgba(239, 68, 68, 0.3)', color: '#ef4444', padding: '8px 16px', borderRadius: '8px', cursor: 'pointer', fontSize: '0.85rem', transition: 'all 0.2s' }} onClick={signOut} onMouseOver={e => e.currentTarget.style.background = 'rgba(239, 68, 68, 0.1)'} onMouseOut={e => e.currentTarget.style.background = 'transparent'}>
            Logout
          </button>
        </div>
        <div style={{ textAlign: 'center', marginBottom: '40px' }}>
          <div style={{ width: '80px', height: '80px', borderRadius: '24px', background: 'linear-gradient(135deg, var(--accent-primary), var(--accent-secondary))', display: 'flex', alignItems: 'center', justifyContent: 'center', fontWeight: 'bold', fontSize: '2.5rem', margin: '0 auto 24px', boxShadow: '0 8px 32px rgba(59,130,246,0.4)' }}>O</div>
          <h1 style={{ fontSize: '3rem', marginBottom: '16px', letterSpacing: '-1px' }}>Welcome back!</h1>
        </div>
        <div className="glass-panel" style={{ width: '100%', maxWidth: '800px', padding: '40px', border: '1px solid rgba(255,255,255,0.05)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '32px' }}>
            <h2 style={{ margin: 0 }}>Your Active Projects</h2>
            <button className="btn-primary" style={{ padding: '10px 20px', borderRadius: '8px' }} onClick={() => { setCurrentView('project'); setActiveTab('setup'); }}>+ New Project</button>
          </div>
          <div style={{ background: 'linear-gradient(145deg, rgba(255,255,255,0.03), rgba(0,0,0,0.2))', padding: '24px', borderRadius: '12px', border: '1px solid rgba(255,255,255,0.1)', display: 'flex', justifyContent: 'space-between', alignItems: 'center', transition: 'transform 0.2s', cursor: 'pointer' }} onMouseOver={e => e.currentTarget.style.transform = 'translateY(-2px)'} onMouseOut={e => e.currentTarget.style.transform = 'translateY(0)'}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '20px' }}>
              <div style={{ width: '48px', height: '48px', background: 'rgba(59,130,246,0.1)', borderRadius: '12px', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--accent-primary)', fontSize: '1.2rem' }}>📄</div>
              <div>
                <h3 style={{ margin: '0 0 6px 0', fontSize: '1.1rem' }}>AI in Healthcare Systematics</h3>
                <div style={{ display: 'flex', gap: '12px', color: 'var(--text-secondary)', fontSize: '0.85rem' }}>
                  <span>Last edited 2 hours ago</span>
                  <span>•</span>
                  <span style={{ color: 'var(--accent-primary)' }}>Role: Owner</span>
                </div>
              </div>
            </div>
            <div style={{ display: 'flex', gap: '12px' }}>
              <button style={{ background: 'transparent', border: '1px solid rgba(255,255,255,0.1)', color: 'var(--text-primary)', padding: '8px 16px', borderRadius: '8px', cursor: 'pointer', transition: 'all 0.2s' }} onClick={(e) => { e.stopPropagation(); setShowShareModal(true); }} onMouseOver={e => e.currentTarget.style.background = 'rgba(255,255,255,0.05)'} onMouseOut={e => e.currentTarget.style.background = 'transparent'}>Share</button>
              <button className="btn-primary" style={{ padding: '8px 24px', borderRadius: '8px' }} onClick={() => { setCurrentView('project'); setActiveTab('setup'); }}>Open</button>
            </div>
          </div>
        </div>
      </div>
    );
  }

  if (currentView === 'settings') {
    return (
      <div className="app-container" style={{ justifyContent: 'center', alignItems: 'center', flexDirection: 'column', height: '100vh', position: 'relative' }}>
        <button style={{ position: 'absolute', top: '32px', left: '40px', background: 'transparent', border: 'none', color: 'var(--text-secondary)', cursor: 'pointer', fontSize: '1rem', display: 'flex', alignItems: 'center', gap: '8px' }} onClick={() => setCurrentView('dashboard')}>
          ← Back to Dashboard
        </button>
        <div style={{ textAlign: 'center', marginBottom: '32px' }}>
          <h1 style={{ fontSize: '2.5rem', margin: 0 }}>Account Settings</h1>
        </div>
        <div className="glass-panel" style={{ width: '100%', maxWidth: '600px', padding: '40px', border: '1px solid rgba(255,255,255,0.05)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '20px', marginBottom: '40px' }}>
            <div style={{ width: '80px', height: '80px', borderRadius: '50%', background: 'linear-gradient(135deg, var(--accent-primary), var(--accent-secondary))', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '2.5rem', fontWeight: 'bold', boxShadow: '0 4px 12px rgba(59,130,246,0.3)' }}>
              {currentUser?.charAt(0).toUpperCase()}
            </div>
            <div>
              <h2 style={{ margin: '0 0 8px 0', fontSize: '1.5rem' }}>{currentUser}</h2>
              <div style={{ color: 'var(--text-secondary)' }}>Plan: <span style={{ color: 'var(--accent-primary)', fontWeight: 'bold' }}>{userTier}</span></div>
            </div>
          </div>
          
          <h3 style={{ borderBottom: '1px solid rgba(255,255,255,0.1)', paddingBottom: '12px', marginBottom: '24px' }}>AI Configuration</h3>
          <div style={{ marginBottom: '24px' }}>
            <label style={{ display: 'block', fontSize: '0.9rem', marginBottom: '8px', color: 'var(--text-secondary)' }}>Default AI Model</label>
            <select className="search-input" style={{ width: '100%', padding: '12px', fontSize: '1rem' }} value={aiProvider} onChange={e => setAiProvider(e.target.value)}>
              <option value="gemini">Google Gemini (Flash) - Free Tier</option>
              <option value="openai">OpenAI (GPT-4o) - Pro Plan</option>
              <option value="anthropic">Anthropic (Claude) - Pro Plan</option>
            </select>
            <p style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginTop: '8px' }}>Select the model engine used for Protocol Generation, Screening, and Meta-Analysis.</p>
          </div>
          
          <h3 style={{ borderBottom: '1px solid rgba(255,255,255,0.1)', paddingBottom: '12px', marginBottom: '24px', marginTop: '40px' }}>Subscription & API Keys</h3>
          <div style={{ marginBottom: '24px' }}>
            <label style={{ display: 'block', fontSize: '0.9rem', marginBottom: '8px', color: 'var(--text-secondary)' }}>Bring Your Own Key (Optional)</label>
            <input type="password" placeholder="sk-..." className="search-input" style={{ width: '100%', padding: '12px' }} />
            <p style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginTop: '8px' }}>Bypass plan rate limits by using your own API key.</p>
          </div>
          
          <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: '40px' }}>
            <button className="btn-primary" style={{ padding: '12px 32px', borderRadius: '8px' }} onClick={() => { alert('Settings saved successfully!'); setCurrentView('dashboard'); }}>Save Changes</button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="app-container">
      <aside className="sidebar" style={{ width: '320px', overflowY: 'auto' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '32px', cursor: 'pointer' }} onClick={() => setCurrentView('dashboard')}>
          <div style={{ width: '32px', height: '32px', borderRadius: '8px', background: 'linear-gradient(135deg, var(--accent-primary), var(--accent-secondary))', display: 'flex', alignItems: 'center', justifyContent: 'center', fontWeight: 'bold' }}>O</div>
          <h2 style={{ fontSize: '1.2rem', margin: 0 }}>Back to Home</h2>
        </div>

        <div style={{ marginBottom: '24px', paddingBottom: '24px', borderBottom: '1px solid rgba(255,255,255,0.1)' }}>
          {currentUser ? (
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '16px' }}>
                <div style={{ width: '36px', height: '36px', borderRadius: '50%', background: 'linear-gradient(135deg, var(--accent-primary), var(--accent-secondary))', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '1rem', fontWeight: 'bold', boxShadow: '0 4px 12px rgba(59,130,246,0.3)' }}>
                  {currentUser.charAt(0).toUpperCase()}
                </div>
                <div>
                  <div style={{ fontSize: '0.85rem', fontWeight: 600, color: 'var(--text-primary)' }}>{currentUser}</div>
                  <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>{userTier} Plan</div>
                </div>
              </div>
              <button style={{ width: '100%', background: 'rgba(59,130,246,0.1)', border: '1px solid rgba(59,130,246,0.3)', color: 'var(--accent-primary)', padding: '10px', borderRadius: '8px', cursor: 'pointer', fontSize: '0.85rem', fontWeight: 600, transition: 'all 0.2s', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px' }} onClick={() => setShowShareModal(true)} onMouseOver={e => e.currentTarget.style.background = 'rgba(59,130,246,0.2)'} onMouseOut={e => e.currentTarget.style.background = 'rgba(59,130,246,0.1)'}>
                👥 Share / Collaborate
              </button>
            </div>
          ) : null}
        </div>

        <nav style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
          {tabs.map(tab => (
            <button key={tab.id} className="btn-glass" onClick={() => setActiveTab(tab.id as any)} style={{ 
                textAlign: 'left', fontSize: '0.9rem', padding: '10px 12px',
                border: activeTab === tab.id ? '1px solid var(--accent-primary)' : '1px solid transparent',
                background: activeTab === tab.id ? 'rgba(59, 130, 246, 0.1)' : 'transparent',
                color: activeTab === tab.id ? 'var(--text-primary)' : 'var(--text-secondary)'
              }}>
              {tab.label}
            </button>
          ))}
        </nav>
      </aside>

      <main className="main-content" style={{ overflowY: 'auto' }}>
        
        {/* Phase 1: Setup */}
        {activeTab === 'setup' && (
          <section className="glass-panel animate-fade-in" style={{ padding: '40px', maxWidth: '800px', margin: '0 auto' }}>
            <h2 style={{ marginBottom: '24px', color: 'var(--text-primary)' }}>Initialize Review Project</h2>
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
              <button className="btn-primary" onClick={handleGenerateProtocol} disabled={protocolLoading} style={{ padding: '16px', fontSize: '1.1rem', marginTop: '16px' }}>
                {protocolLoading ? `Compiling parameters...` : 'Compile & Send to AI Protocol Builder →'}
              </button>
            </div>
          </section>
        )}

        {/* Phase 2: Protocol Builder */}
        {activeTab === 'protocol' && (
          <section className="glass-panel animate-fade-in" style={{ padding: '32px' }}>
            <h3 style={{ marginBottom: '16px', color: 'var(--text-primary)' }}>2. AI Protocol Builder</h3>
            <div style={{ display: 'flex', gap: '24px', marginBottom: '24px' }}>
              <div style={{ flex: 1 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
                  <h4 style={{ color: '#34d399', margin: 0 }}>Inclusion Criteria</h4>
                  <button className="btn-glass" onClick={() => handleAcceptAll(setInclusionItems, inclusionItems)} style={{ padding: '4px 8px', fontSize: '0.8rem' }}>Accept All ✓</button>
                </div>
                {inclusionItems.map(item => (
                  <div key={item.id} style={{ display: 'flex', padding: '8px', background: 'rgba(0,0,0,0.2)', marginBottom: '4px', alignItems: 'center', opacity: item.status === 'rejected' ? 0.5 : 1 }}>
                    <div style={{ flex: 1, textDecoration: item.status === 'rejected' ? 'line-through' : 'none' }}>{item.text}</div>
                    <div style={{ display: 'flex', gap: '4px' }}>
                      <button onClick={() => handleItemStatus(setInclusionItems, inclusionItems, item.id, 'accepted')} className="btn-primary" style={{ padding: '4px', background: item.status === 'accepted' ? '#10b981' : undefined }}>✓</button>
                      <button onClick={() => handleItemStatus(setInclusionItems, inclusionItems, item.id, 'rejected')} className="btn-glass" style={{ padding: '4px', color: '#ef4444', borderColor: item.status === 'rejected' ? '#ef4444' : undefined }}>✕</button>
                    </div>
                  </div>
                ))}
              </div>
              <div style={{ flex: 1 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
                  <h4 style={{ color: '#f87171', margin: 0 }}>Exclusion Criteria</h4>
                  <button className="btn-glass" onClick={() => handleAcceptAll(setExclusionItems, exclusionItems)} style={{ padding: '4px 8px', fontSize: '0.8rem' }}>Accept All ✓</button>
                </div>
                {exclusionItems.map(item => (
                  <div key={item.id} style={{ display: 'flex', padding: '8px', background: 'rgba(0,0,0,0.2)', marginBottom: '4px', alignItems: 'center', opacity: item.status === 'rejected' ? 0.5 : 1 }}>
                    <div style={{ flex: 1, textDecoration: item.status === 'rejected' ? 'line-through' : 'none' }}>{item.text}</div>
                    <div style={{ display: 'flex', gap: '4px' }}>
                      <button onClick={() => handleItemStatus(setExclusionItems, exclusionItems, item.id, 'accepted')} className="btn-primary" style={{ padding: '4px', background: item.status === 'accepted' ? '#10b981' : undefined }}>✓</button>
                      <button onClick={() => handleItemStatus(setExclusionItems, exclusionItems, item.id, 'rejected')} className="btn-glass" style={{ padding: '4px', color: '#ef4444', borderColor: item.status === 'rejected' ? '#ef4444' : undefined }}>✕</button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
            <button className="btn-primary" onClick={() => setActiveTab('search')}>Proceed to Literature Search →</button>
          </section>
        )}

        {/* Phase 3: Search */}
        {activeTab === 'search' && (
          <section className="glass-panel animate-fade-in" style={{ padding: '32px' }}>
            <h3 style={{ marginBottom: '16px', color: 'var(--text-primary)' }}>3. Database Search & Manual Import</h3>
            
            <div style={{ marginBottom: '32px' }}>
              <h4 style={{ color: 'var(--accent-primary)', marginBottom: '16px' }}>AI-Generated Database Search Strings</h4>
              {searchItems.length === 0 && <p style={{ color: 'var(--text-secondary)' }}>No automated searches generated.</p>}
              
              {searchItems.map(item => (
                <div key={item.id} style={{ background: 'rgba(0,0,0,0.2)', padding: '16px', borderRadius: '8px', marginBottom: '12px' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
                    <strong style={{ fontSize: '1.1rem' }}>{item.database}</strong>
                    <button className="btn-primary" onClick={() => handleRunDatabaseSearch(item.database)} disabled={searchDBLoading === item.database}>
                      {searchDBLoading === item.database ? 'Querying API...' : `Search ${item.database}`}
                    </button>
                  </div>
                  <textarea className="search-input" style={{ width: '100%', height: '60px', resize: 'vertical' }} value={item.string} onChange={e => setSearchItems(prev => prev.map(s => s.id === item.id ? { ...s, string: e.target.value } : s))} />
                </div>
              ))}
            </div>

            <div style={{ background: 'rgba(16, 185, 129, 0.05)', border: '1px dashed rgba(16,185,129,0.4)', padding: '24px', borderRadius: '12px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div>
                <h4 style={{ color: '#10b981', margin: 0 }}>Manual CSV Upload</h4>
              </div>
              <div style={{ display: 'flex', gap: '12px' }}>
                <button className="btn-glass" onClick={handleDownloadTemplate} style={{ fontSize: '0.9rem' }}>↓ Download Template</button>
                <input type="file" id="csv-upload-input" accept=".csv" style={{ display: 'none' }} onChange={handleFileChange} />
                <button id="manualUploadBtn" className="btn-primary" onClick={handleFileUpload} disabled={uploadLoading}>
                  {uploadLoading ? 'Parsing CSV...' : '↑ Upload CSV'}
                </button>
              </div>
            </div>

            {literatureResults.length > 0 && (
              <div style={{ marginTop: '32px' }}>
                <h4 style={{ color: 'var(--accent-primary)', marginBottom: '16px' }}>Search Results Preview ({literatureResults.length} Papers)</h4>
                <div style={{ overflowX: 'auto', background: 'rgba(0,0,0,0.3)', borderRadius: '12px', maxHeight: '300px' }}>
                  <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left' }}>
                    <thead style={{ position: 'sticky', top: 0, background: '#1e293b' }}>
                      <tr>
                        <th style={{ padding: '12px' }}>Source</th>
                        <th style={{ padding: '12px' }}>Title</th>
                        <th style={{ padding: '12px' }}>Authors</th>
                        <th style={{ padding: '12px' }}>Year</th>
                        <th style={{ padding: '12px' }}>DOI</th>
                      </tr>
                    </thead>
                    <tbody>
                      {literatureResults.map(p => (
                        <tr key={p.id} style={{ borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
                          <td style={{ padding: '12px' }}>{p.source}</td>
                          <td style={{ padding: '12px', maxWidth: '300px', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{p.title}</td>
                          <td style={{ padding: '12px', maxWidth: '200px', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{p.authors}</td>
                          <td style={{ padding: '12px' }}>{p.year}</td>
                          <td style={{ padding: '12px' }}>{p.doi}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
            
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '24px' }}>
              <button className="btn-glass" onClick={handleResetSearch} style={{ color: '#ef4444', borderColor: '#ef4444' }}>
                ✕ Clear All Searches
              </button>
              <button className="btn-primary" onClick={() => setActiveTab('dedup')}>
                Proceed to Deduplication →
              </button>
            </div>
          </section>
        )}

        {/* Phase 4: Dedup */}
        {activeTab === 'dedup' && (
          <section className="glass-panel animate-fade-in" style={{ padding: '32px' }}>
            <h3 style={{ marginBottom: '16px', color: 'var(--text-primary)' }}>4. Deduplication</h3>
            <button className="btn-primary" onClick={handleRunDedup} disabled={dedupLoading} style={{ marginTop: '24px', padding: '16px 32px' }}>
              {dedupLoading ? 'Analyzing IDs...' : 'Run Automated Deduplication'}
            </button>
            {prisma.duplicates_removed > 0 && (
              <div style={{ marginTop: '24px', color: '#10b981', fontSize: '1.2rem' }}>
                ✓ Removed {prisma.duplicates_removed} duplicates. Ready for Abstract Screening.
              </div>
            )}
          </section>
        )}

        {/* Phase 5: Abstract Screening (Table View) */}
        {activeTab === 'abstract' && (
          <section className="glass-panel animate-fade-in" style={{ padding: '32px' }}>
            <h3 style={{ marginBottom: '16px', color: 'var(--text-primary)' }}>5. Abstract Screening</h3>
            
            <button className="btn-primary" onClick={handleRunAbstractScreening} disabled={abstractLoading} style={{ marginBottom: '24px', background: 'linear-gradient(135deg, #10b981, #059669)', padding: '12px 24px' }}>
              {abstractLoading ? 'AI Screening Abstracts...' : `Run Batch AI Abstract Screening (${getBatchLimit('abstract')} / page)`}
            </button>
            
            {abstractProgress !== null && <ProgressBar progress={abstractProgress} label="AI Processing Batch..." />}

            {literatureResults.length > 0 && (
              <div style={{ overflowX: 'auto', background: 'rgba(0,0,0,0.3)', borderRadius: '12px' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left', fontSize: '0.9rem' }}>
                  <thead style={{ background: '#1e293b' }}>
                    <tr>
                      <th style={{ padding: '12px', width: '30%' }}>Title / Abstract</th>
                      <th style={{ padding: '12px', width: '30%' }}>AI Reasoning</th>
                      <th style={{ padding: '12px', width: '15%' }}>AI Decision</th>
                      <th style={{ padding: '12px', width: '25%' }}>Your Decision</th>
                    </tr>
                  </thead>
                  <tbody>
                    {literatureResults.slice(0, getBatchLimit('abstract')).map(p => (
                      <tr key={p.id} style={{ borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
                        <td style={{ padding: '12px' }}>
                          <strong>{p.title}</strong>
                          <div style={{ color: 'var(--text-secondary)', marginTop: '8px', maxHeight: '80px', overflowY: 'auto' }}>
                            {p.abstract || "Abstract not provided in standard search feed."}
                          </div>
                        </td>
                        <td style={{ padding: '12px', color: 'var(--text-secondary)' }}>{p.ai_error ? `AI error: ${p.ai_error}` : p.ai_reasoning || "-"}</td>
                        <td style={{ padding: '12px', fontWeight: 'bold', color: p.ai_decision === 'Include' ? '#10b981' : p.ai_decision === 'Exclude' ? '#ef4444' : '#f59e0b' }}>
                          {p.ai_error ? "Error" : p.ai_decision || "Pending"}
                        </td>
                        <td style={{ padding: '12px' }}>
                          <div style={{ display: 'flex', gap: '8px' }}>
                            <button onClick={() => handleUserDecision(p.id, 'Include')} className="btn-glass" style={{ padding: '6px', fontSize: '0.8rem', background: p.user_decision === 'Include' ? 'rgba(16,185,129,0.2)' : undefined, borderColor: p.user_decision === 'Include' ? '#10b981' : undefined }}>
                              ✓ Accept
                            </button>
                            <button onClick={() => handleUserDecision(p.id, 'Undecided')} className="btn-glass" style={{ padding: '6px', fontSize: '0.8rem', background: p.user_decision === 'Undecided' ? 'rgba(245,158,11,0.2)' : undefined, borderColor: p.user_decision === 'Undecided' ? '#f59e0b' : undefined }}>
                              ? Undecided
                            </button>
                            <button onClick={() => handleUserDecision(p.id, 'Exclude')} className="btn-glass" style={{ padding: '6px', fontSize: '0.8rem', background: p.user_decision === 'Exclude' ? 'rgba(239,68,68,0.2)' : undefined, borderColor: p.user_decision === 'Exclude' ? '#ef4444' : undefined }}>
                              ✕ Reject
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <div style={{ marginTop: '24px' }}>
              <button className="btn-primary" onClick={() => setActiveTab('extraction_rules')}>Proceed to Extraction Rules →</button>
            </div>
          </section>
        )}

        {/* Phase 6: Extraction Rules Setup */}
        {activeTab === 'extraction_rules' && (
          <section className="glass-panel animate-fade-in" style={{ padding: '32px' }}>
            <h3 style={{ marginBottom: '16px', color: 'var(--text-primary)' }}>6. Define Full-Text Extraction Rules</h3>
            <p style={{ color: 'var(--text-secondary)', marginBottom: '24px' }}>Remove any variables you do not need, and add standard or custom ones.</p>
            
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '12px', marginBottom: '24px' }}>
              {extractionColumns.map(col => (
                <div key={col} style={{ display: 'flex', alignItems: 'center', gap: '8px', background: 'rgba(59, 130, 246, 0.2)', border: '1px solid rgba(59, 130, 246, 0.4)', padding: '6px 12px', borderRadius: '20px' }}>
                  <span>{col}</span>
                  <button onClick={() => handleRemoveColumn(col)} style={{ background: 'transparent', border: 'none', color: '#ef4444', cursor: 'pointer', fontWeight: 'bold' }}>✕</button>
                </div>
              ))}
              <div style={{ display: 'flex', gap: '8px' }}>
                <input type="text" className="search-input" style={{ borderRadius: '20px', padding: '6px 16px', width: '200px' }} placeholder="+ Custom Variable..." value={newColumn} onChange={e => setNewColumn(e.target.value)} onKeyDown={e => e.key === 'Enter' && handleAddColumn()} />
              </div>
            </div>

            <div style={{ marginBottom: '32px' }}>
              <h4 style={{ color: 'var(--text-secondary)', fontSize: '0.9rem', marginBottom: '8px' }}>Common AI Suggestions:</h4>
              <div style={{ display: 'flex', gap: '8px' }}>
                {['Study Design', 'Intervention Details', 'Country', 'Funding Source'].map(sugg => (
                  <button key={sugg} onClick={() => { if(!extractionColumns.includes(sugg)) setExtractionColumns([...extractionColumns, sugg]); }} className="btn-glass" style={{ padding: '4px 12px', fontSize: '0.8rem' }}>
                    + {sugg}
                  </button>
                ))}
              </div>
            </div>

            <button className="btn-primary" onClick={() => setActiveTab('fulltext')}>Lock Schema & Proceed to Full Text →</button>
          </section>
        )}

        {/* Phase 7: Full Text Screening & Extraction */}
        {activeTab === 'fulltext' && (
          <section className="glass-panel animate-fade-in" style={{ padding: '32px', border: '1px solid var(--accent-primary)' }}>
            <h3 style={{ marginBottom: '16px', color: 'var(--accent-primary)' }}>7. Full-Text Screening & Batch Extraction</h3>
            <p style={{ color: 'var(--text-secondary)' }}>Full-text PDF retrieval is not available yet. Extraction currently reads only the title and abstract of each paper you accepted, so verify every value against the full article.</p>
            <button className="btn-primary" onClick={handleRunFullTextPipeline} disabled={fullTextLoading} style={{ marginTop: '24px', background: 'linear-gradient(135deg, #8b5cf6, #6d28d9)', padding: '16px 32px', marginBottom: '24px' }}>
              {fullTextLoading ? 'Extracting from abstracts...' : `Run AI Extraction on Accepted Papers (up to ${getBatchLimit('fulltext')})`}
            </button>
            
            {fullTextProgress !== null && <ProgressBar progress={fullTextProgress} label="Extracting Data & Screening..." />}

            {literatureResults.some(p => p.extracted_data) && (
               <div style={{ overflowX: 'auto', background: 'rgba(0,0,0,0.3)', borderRadius: '12px', marginTop: '24px' }}>
                 <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left', fontSize: '0.85rem' }}>
                   <thead style={{ background: '#1e293b' }}>
                     <tr>
                       <th style={{ padding: '12px', whiteSpace: 'nowrap' }}>Study</th>
                       {extractionColumns.map(col => <th key={col} style={{ padding: '12px' }}>{col}</th>)}
                     </tr>
                   </thead>
                   <tbody>
                     {literatureResults.filter((p: any) => p.extracted_data).slice(0, 10).map((p: any) => (
                       <tr key={p.id} style={{ borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
                         <td style={{ padding: '12px', fontWeight: 'bold' }}>{p.title}</td>
                         {extractionColumns.map((col) => (
                           <td key={col} style={{ padding: '12px', color: 'var(--text-secondary)' }}>
                             {p.extracted_data[col] || 'Not Reported'}
                           </td>
                         ))}
                       </tr>
                     ))}
                   </tbody>
                 </table>
               </div>
            )}
            
            {literatureResults.some(p => p.extracted_data) && (
              <button className="btn-primary" onClick={() => setActiveTab('prisma')} style={{ marginTop: '24px' }}>Proceed to PRISMA →</button>
            )}
          </section>
        )}

        {/* Phase 8: PRISMA Diagram */}
        {activeTab === 'prisma' && (
          <section className="glass-panel animate-fade-in" style={{ padding: '32px' }}>
            <h3 style={{ marginBottom: '24px', color: 'var(--text-primary)' }}>8. PRISMA Flow Diagram</h3>
            <div style={{ padding: '32px', background: 'rgba(0,0,0,0.3)', borderRadius: '12px' }}>
              <p style={{ color: 'var(--text-secondary)', marginTop: 0 }}>Counts recorded in this session. The PRISMA 2020 flow diagram itself is not generated yet.</p>
              <ul style={{ lineHeight: 1.8, margin: 0 }}>
                <li>Records from database searches: {prisma.searched}</li>
                <li>Records from manual upload: {prisma.uploaded}</li>
                <li>Duplicates removed: {prisma.duplicates_removed}</li>
                <li>Records remaining for screening: {literatureResults.length}</li>
                <li>Excluded by a reviewer at abstract screening: {literatureResults.filter(p => p.user_decision === 'Exclude').length}</li>
                <li>Included by a reviewer: {literatureResults.filter(p => p.user_decision === 'Include').length}</li>
              </ul>
            </div>
            <div style={{ textAlign: 'center', marginTop: '32px' }}>
              <button className="btn-primary" onClick={() => setActiveTab('rob')}>Proceed to Quality Assessment →</button>
            </div>
          </section>
        )}

        {/* Phase 9: Risk of Bias */}
        {activeTab === 'rob' && (
          <section className="glass-panel animate-fade-in" style={{ padding: '32px' }}>
            <h3 style={{ marginBottom: '16px', color: 'var(--text-primary)' }}>9. Quality Assessment & Risk of Bias</h3>
            <div style={{ display: 'flex', gap: '16px', marginBottom: '32px' }}>
              <div style={{ flex: 1 }}>
                <label style={{ display: 'block', marginBottom: '8px', color: 'var(--text-secondary)' }}>Select Assessment Rubric</label>
                <select className="search-input" value={robTool} onChange={e => setRobTool(e.target.value)}>
                  <option value="ROB-2">Cochrane RoB 2 (Randomized Trials)</option>
                  <option value="ROBINS-I">ROBINS-I (Non-randomized Interventions)</option>
                  <option value="Newcastle-Ottawa">Newcastle-Ottawa Scale (Observational)</option>
                  <option value="QUADAS-2">QUADAS-2 (Diagnostic Accuracy)</option>
                  <option value="PROBAST">PROBAST (Prediction Model Risk of Bias)</option>
                  <option value="PROBAST+AI">PROBAST+AI (Prediction Model Risk of Bias with AI extension)</option>
                </select>
              </div>
              <div style={{ display: 'flex', alignItems: 'flex-end' }}>
                <button className="btn-primary" onClick={handleRunRob} disabled={robLoading} style={{ height: '42px', padding: '0 24px' }}>
                  {robLoading ? `Running ${aiProvider}...` : `Run AI Assessment`}
                </button>
              </div>
            </div>
            
            {robProgress !== null && <ProgressBar progress={robProgress} label={`Running ${robTool} Assessment...`} />}

            {robComplete && (
              <div className="animate-fade-in">
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', marginBottom: '16px', marginTop: '32px' }}>
                  <h4 style={{ margin: 0, color: 'var(--text-primary)' }}>Risk of Bias Assessment Results</h4>
                </div>
                <div style={{ overflowX: 'auto', background: 'rgba(0,0,0,0.3)', borderRadius: '12px' }}>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.9rem' }}>
                    <thead style={{ background: 'rgba(255,255,255,0.05)', textAlign: 'left' }}>
                      <tr>
                        <th style={{ padding: '12px', borderBottom: '1px solid rgba(255,255,255,0.1)' }}>Study</th>
                        {literatureResults.find(p => p.rob_data)?.rob_data && Object.keys(literatureResults.find(p => p.rob_data)!.rob_data!).filter(k => k !== 'Overall').map(domain => (
                          <th key={domain} style={{ padding: '12px', borderBottom: '1px solid rgba(255,255,255,0.1)', textAlign: 'center' }}>{domain}</th>
                        ))}
                        <th style={{ padding: '12px', borderBottom: '1px solid rgba(255,255,255,0.1)', textAlign: 'center' }}>Overall Risk</th>
                      </tr>
                    </thead>
                    <tbody>
                      {literatureResults.filter(p => p.rob_data).slice(0, 10).map((p) => {
                        const domains = Object.keys(p.rob_data!).filter(k => k !== 'Overall');
                        const overall = p.rob_data!['Overall'];
                        
                        const getColor = (val: string) => {
                          const v = val.toLowerCase();
                          if (v.includes('high')) return '#ef4444';
                          if (v.includes('low')) return '#10b981';
                          return '#f59e0b';
                        };
                        
                        return (
                          <tr key={p.id} style={{ borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
                            <td style={{ padding: '12px' }}>{p.title}</td>
                            {domains.map(dom => (
                              <td key={dom} style={{ padding: '12px', textAlign: 'center' }}>
                                <span style={{ color: getColor(p.rob_data![dom]) }}>{p.rob_data![dom]}</span>
                              </td>
                            ))}
                            <td style={{ padding: '12px', textAlign: 'center', fontWeight: 'bold' }}>
                              <span style={{ color: getColor(overall), background: `${getColor(overall)}15`, padding: '4px 8px', borderRadius: '4px' }}>
                                {overall}
                              </span>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
                <div style={{ textAlign: 'right', marginTop: '24px' }}>
                  <button className="btn-primary" onClick={() => setActiveTab('meta')}>Proceed to Meta-Analysis →</button>
                </div>
              </div>
            )}
          </section>
        )}

        {/* Phase 10: Meta-Analysis */}
        {activeTab === 'meta' && (
          <section className="glass-panel animate-fade-in" style={{ padding: '32px', border: '1px solid var(--accent-primary)' }}>
            <h3 style={{ marginBottom: '16px', color: 'var(--accent-primary)' }}>10. AI Narrative Synthesis</h3>
            <p style={{ color: 'var(--text-secondary)', marginBottom: '16px' }}>An AI-written summary of the extracted data and risk of bias for the papers you accepted. No statistical meta-analysis is run: there are no pooled estimates or heterogeneity statistics.</p>
            <button className="btn-primary" onClick={handleRunMetaAnalysis} disabled={metaLoading} style={{ background: 'linear-gradient(135deg, #10b981, #059669)', padding: '16px 32px' }}>
              {metaLoading ? `Synthesizing with ${aiProvider}...` : 'Synthesize Outcomes & Generate Report'}
            </button>
            {metaReport && (
              <div className="animate-fade-in" style={{ padding: '24px', background: 'rgba(0,0,0,0.3)', marginTop: '24px', borderRadius: '12px' }}>
                <h4 style={{ color: 'var(--accent-secondary)' }}>AI Narrative Synthesis</h4>
                <pre style={{ whiteSpace: 'pre-wrap', fontFamily: 'system-ui, -apple-system, sans-serif', fontSize: '0.95rem', lineHeight: '1.6', color: 'var(--text-secondary)' }}>
                  {metaReport}
                </pre>
              </div>
            )}
          </section>
        )}
        {/* Footer */}
        <footer style={{ textAlign: 'center', padding: '40px 24px', color: 'var(--text-secondary)', fontSize: '0.85rem', marginTop: '40px', borderTop: '1px solid rgba(255,255,255,0.1)' }}>
          <div style={{ marginBottom: '16px' }}>
            <span style={{ fontWeight: 'bold', color: 'var(--text-primary)' }}>OmniReview AI Research Platform</span>
          </div>
          <div style={{ display: 'flex', justifyContent: 'center', gap: '24px', marginBottom: '16px' }}>
            <a href="#" style={{ color: 'var(--text-secondary)', textDecoration: 'none' }}>Terms of Service</a>
            <a href="#" style={{ color: 'var(--text-secondary)', textDecoration: 'none' }}>Privacy Policy</a>
            <a href="#" style={{ color: 'var(--text-secondary)', textDecoration: 'none' }}>Documentation</a>
            <a href="mailto:support@omnireview.ai" style={{ color: 'var(--accent-primary)', textDecoration: 'none' }}>Contact Support</a>
          </div>
          <p style={{ margin: 0 }}>&copy; {new Date().getFullYear()} OmniReview AI. All rights reserved.</p>
        </footer>
      </main>

      {/* Floating Chatbot */}
      <div style={{ position: 'fixed', bottom: '24px', right: '24px', zIndex: 9999 }}>
        {chatOpen ? (
          <div className="glass-panel" style={{ width: '350px', height: '450px', display: 'flex', flexDirection: 'column', padding: 0, overflow: 'hidden', border: '1px solid var(--accent-primary)', boxShadow: '0 10px 25px rgba(0,0,0,0.5)' }}>
            <div style={{ padding: '16px', background: 'var(--accent-primary)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <h4 style={{ margin: 0 }}>OmniReview FAQ Bot</h4>
              <button onClick={() => setChatOpen(false)} style={{ background: 'transparent', border: 'none', color: '#fff', cursor: 'pointer', fontSize: '1.2rem' }}>×</button>
            </div>
            <div style={{ flex: 1, padding: '16px', overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '12px' }}>
              <div style={{ alignSelf: 'flex-start', background: 'rgba(255,255,255,0.1)', padding: '10px 14px', borderRadius: '12px' }}>Hi! How can I help you use OmniReview AI today?</div>
              {chatMessages.map((msg, idx) => (
                <div key={idx} style={{ alignSelf: msg.role === 'user' ? 'flex-end' : 'flex-start', background: msg.role === 'user' ? 'var(--accent-primary)' : 'rgba(255,255,255,0.1)', padding: '10px 14px', borderRadius: '12px', maxWidth: '85%', fontSize: '0.9rem' }}>
                  {msg.text}
                </div>
              ))}
              {chatLoading && <div style={{ alignSelf: 'flex-start', color: 'var(--text-secondary)', fontSize: '0.8rem' }}>AI is typing...</div>}
            </div>
            <form onSubmit={handleChatSubmit} style={{ padding: '12px', borderTop: '1px solid rgba(255,255,255,0.1)', display: 'flex', gap: '8px' }}>
              <input type="text" value={chatInput} onChange={e => setChatInput(e.target.value)} placeholder="Ask a question..." style={{ flex: 1, padding: '10px', borderRadius: '6px', background: 'rgba(0,0,0,0.3)', border: '1px solid rgba(255,255,255,0.1)', color: '#fff' }} />
              <button type="submit" className="btn-primary" style={{ padding: '0 16px' }}>Send</button>
            </form>
          </div>
        ) : (
          <button onClick={() => setChatOpen(true)} style={{ width: '60px', height: '60px', borderRadius: '50%', background: 'linear-gradient(135deg, var(--accent-primary), #3b82f6)', border: 'none', color: '#fff', fontSize: '1.5rem', cursor: 'pointer', boxShadow: '0 4px 15px rgba(59,130,246,0.4)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>?</button>
        )}
      </div>

      {/* Share / Collaborate Modal */}
      {showShareModal && (
        <div style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, background: 'rgba(0,0,0,0.7)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 10000 }}>
          <div className="glass-panel" style={{ width: '400px', padding: '32px' }}>
            <h3 style={{ marginBottom: '8px' }}>Invite Collaborator</h3>
            <p style={{ color: 'var(--text-secondary)', fontSize: '0.9rem', marginBottom: '24px' }}>Invite a team member to collaborate on this review. They will receive an email invitation to join.</p>
            <form onSubmit={handleInvite}>
              <div style={{ marginBottom: '24px' }}>
                <label style={{ display: 'block', fontSize: '0.9rem', marginBottom: '8px' }}>Collaborator Email Address</label>
                <input type="email" required value={inviteEmail} onChange={e => setInviteEmail(e.target.value)} placeholder="colleague@university.edu" className="search-input" style={{ width: '100%' }} />
              </div>
              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '12px' }}>
                <button type="button" style={{ background: 'transparent', border: '1px solid rgba(255,255,255,0.1)', color: 'var(--text-secondary)', padding: '10px 20px', borderRadius: '8px', cursor: 'pointer', transition: 'all 0.2s', fontWeight: 500 }} onClick={() => setShowShareModal(false)} onMouseOver={e => { e.currentTarget.style.color = '#fff'; e.currentTarget.style.border = '1px solid rgba(255,255,255,0.3)'; }} onMouseOut={e => { e.currentTarget.style.color = 'var(--text-secondary)'; e.currentTarget.style.border = '1px solid rgba(255,255,255,0.1)'; }}>Cancel</button>
                <button type="submit" className="btn-primary" style={{ padding: '10px 24px', borderRadius: '8px', fontWeight: 600 }}>Send Invite</button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}

export default App;
