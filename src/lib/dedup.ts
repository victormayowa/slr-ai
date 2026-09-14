// Records with the same DOI, or with no DOI and the same normalized title, are treated as duplicates.
export const dedupKey = (paper: { doi?: string; title: string }): string =>
  paper.doi?.trim().toLowerCase() || `title:${String(paper.title).toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim()}`;
