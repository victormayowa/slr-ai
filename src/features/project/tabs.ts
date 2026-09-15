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
  { path: 'other-sources', label: '9. Citations & Grey Literature' },
  { path: 'deduplication', label: '10. Deduplication' },
  { path: 'screening', label: '11. Abstract Screening' },
  { path: 'full-texts', label: '12. Full Texts' },
  { path: 'extraction-fields', label: '13. Extraction Rules' },
  { path: 'extraction', label: '14. Full-Text Screening & Extract' },
  { path: 'prisma', label: '15. PRISMA & Export' },
  { path: 'risk-of-bias', label: '16. Risk of Bias & Quality' },
  { path: 'synthesis', label: '17. Narrative Synthesis' },
] as const;

export type ProjectTab = (typeof PROJECT_TABS)[number]['path'];
