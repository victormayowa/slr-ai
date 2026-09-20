type Tab<T extends string> = { key: T; label: string };

// A row of buttons switching between the views of one screen.
export function Tabs<T extends string>({ tabs, active, onChange }: { tabs: Tab<T>[]; active: T; onChange: (key: T) => void }) {
  return (
    <div role="tablist" style={{ display: 'flex', gap: '4px', flexWrap: 'wrap', margin: '16px 0 20px', borderBottom: '1px solid var(--border)' }}>
      {tabs.map(tab => {
        const selected = tab.key === active;
        return (
          <button
            key={tab.key}
            role="tab"
            aria-selected={selected}
            onClick={() => onChange(tab.key)}
            style={{
              position: 'relative',
              padding: '10px 14px',
              marginBottom: '-1px',
              fontFamily: 'var(--nav-font)',
              fontSize: '0.92rem',
              fontWeight: selected ? 600 : 500,
              color: selected ? 'var(--navy)' : 'var(--grey)',
              background: 'transparent',
              border: 'none',
              borderBottom: '3px solid transparent',
              borderImage: selected ? 'var(--orbit) 1' : undefined,
              cursor: 'pointer',
            }}
          >
            {tab.label}
          </button>
        );
      })}
    </div>
  );
}
