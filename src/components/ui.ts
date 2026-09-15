// Shared inline styles and small formatting helpers for the workspace screens.

export const panel = { background: 'rgba(0,0,0,0.25)', borderRadius: '12px', padding: '16px 20px' } as const;
export const muted = { color: 'var(--text-secondary)', fontSize: '0.85rem' } as const;
export const smallButton = { padding: '4px 10px', fontSize: '0.8rem' } as const;
export const fieldLabel = { display: 'flex', flexDirection: 'column', gap: '4px', fontSize: '0.85rem', color: 'var(--text-secondary)' } as const;
export const row = { display: 'flex', gap: '12px', flexWrap: 'wrap', alignItems: 'center' } as const;

export const chip = (color: string) =>
  ({ display: 'inline-block', padding: '1px 8px', borderRadius: '999px', fontSize: '0.75rem', border: `1px solid ${color}`, color, whiteSpace: 'nowrap' }) as const;

export const GREEN = '#10b981';
export const RED = '#ef4444';
export const AMBER = '#f59e0b';
export const BLUE = '#3b82f6';
export const GREY = '#9ca3af';

export const fmt = (value: number | null | undefined, digits = 2) =>
  value == null || Number.isNaN(value) ? '–' : String(Number(value.toFixed(digits)));

export const percent = (value: number | null | undefined, digits = 0) =>
  value == null || Number.isNaN(value) ? '–' : `${(value * 100).toFixed(digits)}%`;
