type Tab<T extends string> = { key: T; label: string };

// A row of buttons switching between the views of one screen.
export function Tabs<T extends string>({ tabs, active, onChange }: { tabs: Tab<T>[]; active: T; onChange: (key: T) => void }) {
  return (
    <div role="tablist" style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', margin: '16px 0 20px' }}>
      {tabs.map(tab => (
        <button
          key={tab.key}
          role="tab"
          aria-selected={tab.key === active}
          className="btn-glass"
          onClick={() => onChange(tab.key)}
          style={{
            padding: '6px 14px',
            fontSize: '0.85rem',
            border: tab.key === active ? '1px solid var(--accent-primary)' : '1px solid transparent',
            background: tab.key === active ? 'rgba(59, 130, 246, 0.12)' : undefined,
          }}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}
