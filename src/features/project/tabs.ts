// The screens of a project workspace, in workflow order. Each path is a route under /projects/:projectId/.
export const PROJECT_TABS = [
  { path: 'setup', label: '1. Project Setup' },
  { path: 'protocol', label: '2. AI Protocol Builder' },
  { path: 'search', label: '3. Database Search & Import' },
  { path: 'deduplication', label: '4. Deduplication' },
  { path: 'screening', label: '5. Abstract Screening' },
  { path: 'extraction-fields', label: '6. Extraction Rules' },
  { path: 'extraction', label: '7. Full-Text Screening & Extract' },
  { path: 'prisma', label: '8. PRISMA & Export' },
  { path: 'risk-of-bias', label: '9. Risk of Bias & Quality' },
  { path: 'synthesis', label: '10. Narrative Synthesis' },
] as const;

export type ProjectTab = (typeof PROJECT_TABS)[number]['path'];
