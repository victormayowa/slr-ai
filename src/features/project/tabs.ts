// The screens of a project workspace, in workflow order. Each path is a route under /projects/:projectId/.
export const PROJECT_TABS = [
  { path: 'setup', label: '1. Project Setup' },
  { path: 'topic', label: '2. Topic & Feasibility' },
  { path: 'question', label: '3. Review Question' },
  { path: 'protocol', label: '4. Eligibility Criteria' },
  { path: 'analysis-plan', label: '5. Analysis Plan' },
  { path: 'protocol-document', label: '6. Protocol Document' },
  { path: 'registration', label: '7. Registration & Export' },
  { path: 'search', label: '8. Database Search & Import' },
  { path: 'deduplication', label: '9. Deduplication' },
  { path: 'screening', label: '10. Abstract Screening' },
  { path: 'extraction-fields', label: '11. Extraction Rules' },
  { path: 'extraction', label: '12. Full-Text Screening & Extract' },
  { path: 'prisma', label: '13. PRISMA & Export' },
  { path: 'risk-of-bias', label: '14. Risk of Bias & Quality' },
  { path: 'synthesis', label: '15. Narrative Synthesis' },
] as const;

export type ProjectTab = (typeof PROJECT_TABS)[number]['path'];
