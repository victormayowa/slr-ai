// The screens of a project workspace, in workflow order. Each path is a route under /projects/:projectId/.
export const PROJECT_TABS = [
  { path: 'setup', label: '1. Project Setup' },
  { path: 'topic', label: '2. Topic & Feasibility' },
  { path: 'question', label: '3. Review Question' },
  { path: 'protocol', label: '4. Eligibility Criteria' },
  { path: 'analysis-plan', label: '5. Analysis Plan' },
  { path: 'protocol-document', label: '6. Protocol Document' },
  { path: 'search', label: '7. Database Search & Import' },
  { path: 'deduplication', label: '8. Deduplication' },
  { path: 'screening', label: '9. Abstract Screening' },
  { path: 'extraction-fields', label: '10. Extraction Rules' },
  { path: 'extraction', label: '11. Full-Text Screening & Extract' },
  { path: 'prisma', label: '12. PRISMA & Export' },
  { path: 'risk-of-bias', label: '13. Risk of Bias & Quality' },
  { path: 'synthesis', label: '14. Narrative Synthesis' },
] as const;

export type ProjectTab = (typeof PROJECT_TABS)[number]['path'];
