export function ProgressBar({ progress, label }: { progress: number; label: string }) {
  return (
    <div style={{ marginTop: '16px', width: '100%', maxWidth: '400px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '8px', fontSize: '0.9rem', color: 'var(--accent-primary)' }}>
        <span>{label}</span>
        <span>{progress}%</span>
      </div>
      <div style={{ width: '100%', height: '8px', background: 'rgba(255,255,255,0.1)', borderRadius: '4px', overflow: 'hidden' }}>
        <div style={{ width: `${progress}%`, height: '100%', background: 'linear-gradient(90deg, var(--accent-primary), var(--accent-secondary))', transition: 'width 0.3s ease' }} />
      </div>
    </div>
  );
}
