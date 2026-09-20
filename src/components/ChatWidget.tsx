import { useState } from 'react';
import { errorMessage } from '../api/client';
import { useAuth } from '../auth/authContext';

import type { HelpSectionInfo } from '../api/collaboration';

type ChatMessage = { role: 'user' | 'ai'; text: string; citations?: HelpSectionInfo[] };

export function ChatWidget() {
  const { apiRequest } = useAuth();
  const [chatOpen, setChatOpen] = useState(false);
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [chatInput, setChatInput] = useState('');
  const [chatLoading, setChatLoading] = useState(false);

  const handleChatSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!chatInput.trim()) return;

    const query = chatInput;
    setChatInput('');
    setChatMessages(prev => [...prev, { role: 'user', text: query }]);
    setChatLoading(true);

    try {
      const data = await apiRequest('POST', '/api/chat', { query });
      setChatMessages(prev => [...prev, { role: 'ai', text: data.answer, citations: data.citations ?? [] }]);
    } catch (err) {
      setChatMessages(prev => [...prev, { role: 'ai', text: errorMessage(err, 'Error connecting to FAQ service.') }]);
    }
    setChatLoading(false);
  };

  return (
    <div style={{ position: 'fixed', bottom: '24px', right: '24px', zIndex: 9999 }}>
      {chatOpen ? (
        <div className="glass-panel" style={{ width: '350px', height: '450px', display: 'flex', flexDirection: 'column', padding: 0, overflow: 'hidden', border: '1px solid var(--border-strong)', boxShadow: '0 12px 32px rgba(5, 28, 96, 0.18)' }}>
          <div style={{ padding: '14px 16px', background: 'var(--navy)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <h4 style={{ margin: 0, color: '#ffffff' }}>OmniReview help</h4>
            <button onClick={() => setChatOpen(false)} aria-label="Close chat" style={{ background: 'transparent', border: 'none', color: '#fff', cursor: 'pointer', fontSize: '1.2rem' }}>×</button>
          </div>
          <div style={{ flex: 1, padding: '16px', overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '12px' }}>
            <div style={{ alignSelf: 'flex-start', background: 'var(--surface-muted)', padding: '10px 14px', borderRadius: '12px' }}>Hi! Ask me anything about using OmniReview. I answer from the documentation and show you where each answer came from.</div>
            {chatMessages.map((msg, idx) => (
              <div key={idx} style={{ alignSelf: msg.role === 'user' ? 'flex-end' : 'flex-start', background: msg.role === 'user' ? 'var(--navy)' : 'var(--surface-muted)', color: msg.role === 'user' ? '#ffffff' : 'var(--ink)', border: '1px solid var(--border)', padding: '10px 14px', borderRadius: '12px', maxWidth: '85%', fontSize: '0.9rem' }}>
                {msg.text}
                {msg.citations && msg.citations.length > 0 && (
                  <div style={{ marginTop: '8px', paddingTop: '6px', borderTop: '1px solid var(--border-strong)', fontSize: '0.75rem', color: 'var(--text-secondary)' }}>
                    From the documentation:{' '}
                    {msg.citations.map((citation, position) => (
                      <span key={citation.id}>
                        {position > 0 && ', '}
                        {citation.title} → {citation.heading}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            ))}
            {chatLoading && <div style={{ alignSelf: 'flex-start', color: 'var(--text-secondary)', fontSize: '0.8rem' }}>AI is typing...</div>}
          </div>
          <form onSubmit={handleChatSubmit} style={{ padding: '12px', borderTop: '1px solid var(--border)', display: 'flex', gap: '8px' }}>
            <input type="text" value={chatInput} onChange={e => setChatInput(e.target.value)} placeholder="Ask a question..." style={{ flex: 1, padding: '10px', borderRadius: '6px', background: 'var(--surface-muted)', border: '1px solid var(--border-strong)', color: 'var(--ink)' }} />
            <button type="submit" className="btn-primary" style={{ padding: '0 16px' }}>Send</button>
          </form>
        </div>
      ) : (
        <button onClick={() => setChatOpen(true)} aria-label="Open help chat" style={{ width: '60px', height: '60px', borderRadius: '50%', background: 'var(--navy)', border: 'none', color: '#fff', fontSize: '1.5rem', cursor: 'pointer', boxShadow: '0 6px 18px rgba(5, 28, 96, 0.3)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>?</button>
      )}
    </div>
  );
}
