import type { ProtocolSuggestion } from './protocol';

// Mirrors backend/topic_exploration.py and backend/topic_routes.py.

export type SourceCount = { label: string; count: number | null; error: string | null };

export type ExistingReview = {
  ref: string;
  id: string;
  source: string;
  title: string;
  year: string;
  venue: string;
  doi: string;
  url: string;
  possibly_outdated: boolean;
  newer_randomized_trials: number | null;
};

export type Registration = { id: string; title: string; registered: string; url: string };

export type WorkloadFigures = {
  records: number;
  full_texts: number;
  included_studies: number;
  screening_hours: number;
  full_text_hours: number;
  extraction_hours: number;
  total_hours: number;
};

export type WorkloadAssumptions = {
  reviewers: number;
  minutes_per_abstract: number;
  full_text_fraction: number;
  minutes_per_full_text: number;
  include_fraction: number;
  hours_per_included_study: number;
};

export const DEFAULT_ASSUMPTIONS: WorkloadAssumptions = {
  reviewers: 2,
  minutes_per_abstract: 0.5,
  full_text_fraction: 0.05,
  minutes_per_full_text: 5,
  include_fraction: 0.3,
  hours_per_included_study: 1.5,
};

export type ExplorationResults = {
  query: string;
  run_at: string;
  sources: Record<string, SourceCount>;
  publications_by_year: Record<string, number>;
  existing_reviews: ExistingReview[];
  review_errors: string[];
  registrations: Registration[];
  registration_error: string | null;
  prospero_search_url: string;
  meta_analysis_feasibility: { level: 'likely' | 'possible' | 'unlikely' | 'unknown'; randomized_trials: number | null; explanation: string };
  workload: { low: WorkloadFigures; high: WorkloadFigures; explanation: string } | null;
  notes: string[];
};

export type TopicQuestion = { question: string; framework: string; gap: string; rationale: string; based_on_review_ids: string[] };

export type TopicQuestions = { exploration_id: number; questions: TopicQuestion[]; evidence_limitations: string };

export type TopicExploration = {
  id: number;
  query: string;
  assumptions: WorkloadAssumptions;
  results: ExplorationResults;
  created_by: string | null;
  created_at: string;
  ai_questions: ProtocolSuggestion<TopicQuestions> | null;
};

export type ExplorationSummary = { id: number; query: string; created_at: string; created_by: string | null };

export const GAP_LABELS: Record<string, string> = {
  no_review_found: 'No review found in these searches',
  outdated_review: 'Existing review may be outdated',
  uncovered_population_or_setting: 'Population or setting not covered',
  conflicting_findings: 'Conflicting findings',
  other: 'Other gap',
};
