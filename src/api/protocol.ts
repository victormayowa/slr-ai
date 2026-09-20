// Mirrors backend/protocol_frameworks.py and backend/protocol_design_routes.py.

export type ElementInfo = { key: string; label: string; hint: string };

export type FrameworkInfo = { key: string; label: string; use_for: string; elements: ElementInfo[] };

export type SectionSpec = { key: string; label: string; prisma_p_item: string; guidance: string; required: boolean };

export type Option = { key: string; label: string };

export type ProtocolCatalog = {
  frameworks: FrameworkInfo[];
  general_criterion_elements: ElementInfo[];
  finer_criteria: ElementInfo[];
  finer_ratings: string[];
  sections: SectionSpec[];
  synthesis_approaches: Option[];
  outcome_priorities: Option[];
};

export type FinerRating = 'yes' | 'partly' | 'no';

export type FinerAssessment = { rating: FinerRating; note: string };

export type ReviewQuestion = {
  framework: string;
  question: string;
  elements: Record<string, string>;
  finer: Record<string, FinerAssessment>;
};

export type ProtocolSuggestion<T> = {
  id: number;
  kind: string;
  section_key: string | null;
  content: T;
  provider: string;
  model: string;
  created_at: string;
};

export type QuestionSuggestion = {
  framework: string;
  question: string;
  elements: Record<string, string>;
  finer_notes: Record<string, string>;
};

export type SectionDraft = { content: string; missing_information: string[] };

export type ProtocolSectionInfo = SectionSpec & {
  content: string;
  ai_assisted: boolean;
  updated_at: string | null;
  updated_by: string | null;
  draft: ProtocolSuggestion<SectionDraft> | null;
};

export type PlannedOutcome = { name: string; priority: 'primary' | 'secondary' | 'adverse'; timepoint: string; measure: string };

export type PlannedAnalysis = { name: string; rationale: string };

// The AI's proposed plan: the same shape as the plan reviewers edit, so it can fill the form.
export type AnalysisPlanSuggestion = {
  synthesis_approach: AnalysisPlan['synthesis_approach'];
  outcomes: PlannedOutcome[];
  subgroups: PlannedAnalysis[];
  sensitivity_analyses: PlannedAnalysis[];
  heterogeneity: string;
};

export type AnalysisPlan = {
  synthesis_approach: 'meta_analysis' | 'swim' | 'narrative' | 'undecided';
  outcomes: PlannedOutcome[];
  subgroups: PlannedAnalysis[];
  sensitivity_analyses: PlannedAnalysis[];
  heterogeneity: string;
};

export type ProtocolIssue = {
  code: string;
  severity: 'error' | 'warning';
  message: string;
  criterion_ids: number[];
  elements: string[];
};

export type ConsistencyReview = { issues: Omit<ProtocolIssue, 'code'>[] };

export type ProtocolChecks = { issues: ProtocolIssue[]; ai_review: ProtocolSuggestion<ConsistencyReview> | null };

// What a criterion can restrict: the framework's elements, then the general ones the framework doesn't already have.
export function criterionElements(catalog: ProtocolCatalog | null, frameworkKey: string): ElementInfo[] {
  if (!catalog) return [];
  const framework = catalog.frameworks.find(item => item.key === frameworkKey);
  const own = framework?.elements ?? [];
  return [...own, ...catalog.general_criterion_elements.filter(element => !own.some(item => item.key === element.key))];
}
