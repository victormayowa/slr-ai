// Shared inline styles and small formatting helpers for the workspace screens.

export const panel = { background: 'var(--surface-muted)', border: '1px solid var(--border)', borderRadius: '10px', padding: '16px 20px' } as const;
export const muted = { color: 'var(--text-secondary)', fontSize: '0.85rem' } as const;
export const smallButton = { padding: '4px 10px', fontSize: '0.8rem' } as const;
export const fieldLabel = { display: 'flex', flexDirection: 'column', gap: '4px', fontSize: '0.85rem', color: 'var(--text-secondary)' } as const;
export const row = { display: 'flex', gap: '12px', flexWrap: 'wrap', alignItems: 'center' } as const;

export const chip = (color: string) =>
  ({ display: 'inline-block', padding: '1px 8px', borderRadius: '999px', fontSize: '0.75rem', fontWeight: 600, border: `1px solid ${color}`, background: `color-mix(in srgb, ${color} 9%, white)`, color, whiteSpace: 'nowrap' }) as const;

// Status colours, dark enough to read as text on white (WCAG AA; checked in src/theme.test.ts).
export const GREEN = '#137A47';
export const RED = '#C62828';
export const AMBER = '#9A5B00';
export const BLUE = '#1E5FCC';
export const GREY = '#5A6478';

export const fmt = (value: number | null | undefined, digits = 2) =>
  value == null || Number.isNaN(value) ? '–' : String(Number(value.toFixed(digits)));

export const percent = (value: number | null | undefined, digits = 0) =>
  value == null || Number.isNaN(value) ? '–' : `${(value * 100).toFixed(digits)}%`;
