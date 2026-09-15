// The screens of a project workspace, in workflow order. Each path is a route under /projects/:projectId/.
export const PROJECT_TABS = [
  { path: 'setup', label: '1. Project Setup' },
  { path: 'question', label: '2. Review Question' },
  { path: 'protocol', label: '3. Eligibility Criteria' },
  { path: 'analysis-plan', label: '4. Analysis Plan' },
  { path: 'protocol-document', label: '5. Protocol Document' },
  { path: 'search', label: '6. Database Search & Import' },
  { path: 'deduplication', label: '7. Deduplication' },
  { path: 'screening', label: '8. Abstract Screening' },
  { path: 'extraction-fields', label: '9. Extraction Rules' },
  { path: 'extraction', label: '10. Full-Text Screening & Extract' },
  { path: 'prisma', label: '11. PRISMA & Export' },
  { path: 'risk-of-bias', label: '12. Risk of Bias & Quality' },
  { path: 'synthesis', label: '13. Narrative Synthesis' },
] as const;

export type ProjectTab = (typeof PROJECT_TABS)[number]['path'];
