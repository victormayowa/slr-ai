export function ProgressBar({ progress, label }: { progress: number; label: string }) {
  return (
    <div style={{ marginTop: '16px', width: '100%', maxWidth: '400px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '8px', fontSize: '0.9rem', color: 'var(--navy)', fontFamily: 'var(--nav-font)' }}>
        <span>{label}</span>
        <span>{progress}%</span>
      </div>
      <div style={{ width: '100%', height: '8px', background: 'var(--surface-muted)', borderRadius: '4px', overflow: 'hidden' }}>
        <div style={{ width: `${progress}%`, height: '100%', background: 'var(--orbit)', transition: 'width 0.3s ease' }} />
      </div>
    </div>
  );
}
