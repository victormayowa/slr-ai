import { useState } from 'react';
import { errorMessage } from '../api/client';
import { usePreferences } from '../app/preferences';
import { useAuth } from '../auth/authContext';

type ChatMessage = { role: 'user' | 'ai'; text: string };

export function ChatWidget() {
  const { apiRequest } = useAuth();
  const { aiProvider } = usePreferences();
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
      const data = await apiRequest('POST', '/api/chat', { query, provider: aiProvider });
      setChatMessages(prev => [...prev, { role: 'ai', text: data.answer }]);
    } catch (err) {
      setChatMessages(prev => [...prev, { role: 'ai', text: errorMessage(err, 'Error connecting to FAQ service.') }]);
    }
    setChatLoading(false);
  };

  return (
    <div style={{ position: 'fixed', bottom: '24px', right: '24px', zIndex: 9999 }}>
      {chatOpen ? (
        <div className="glass-panel" style={{ width: '350px', height: '450px', display: 'flex', flexDirection: 'column', padding: 0, overflow: 'hidden', border: '1px solid var(--accent-primary)', boxShadow: '0 10px 25px rgba(0,0,0,0.5)' }}>
          <div style={{ padding: '16px', background: 'var(--accent-primary)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <h4 style={{ margin: 0 }}>OmniReview FAQ Bot</h4>
            <button onClick={() => setChatOpen(false)} aria-label="Close chat" style={{ background: 'transparent', border: 'none', color: '#fff', cursor: 'pointer', fontSize: '1.2rem' }}>×</button>
          </div>
          <div style={{ flex: 1, padding: '16px', overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '12px' }}>
            <div style={{ alignSelf: 'flex-start', background: 'rgba(255,255,255,0.1)', padding: '10px 14px', borderRadius: '12px' }}>Hi! How can I help you use OmniReview AI today?</div>
            {chatMessages.map((msg, idx) => (
              <div key={idx} style={{ alignSelf: msg.role === 'user' ? 'flex-end' : 'flex-start', background: msg.role === 'user' ? 'var(--accent-primary)' : 'rgba(255,255,255,0.1)', padding: '10px 14px', borderRadius: '12px', maxWidth: '85%', fontSize: '0.9rem' }}>
                {msg.text}
              </div>
            ))}
            {chatLoading && <div style={{ alignSelf: 'flex-start', color: 'var(--text-secondary)', fontSize: '0.8rem' }}>AI is typing...</div>}
          </div>
          <form onSubmit={handleChatSubmit} style={{ padding: '12px', borderTop: '1px solid rgba(255,255,255,0.1)', display: 'flex', gap: '8px' }}>
            <input type="text" value={chatInput} onChange={e => setChatInput(e.target.value)} placeholder="Ask a question..." style={{ flex: 1, padding: '10px', borderRadius: '6px', background: 'rgba(0,0,0,0.3)', border: '1px solid rgba(255,255,255,0.1)', color: '#fff' }} />
            <button type="submit" className="btn-primary" style={{ padding: '0 16px' }}>Send</button>
          </form>
        </div>
      ) : (
        <button onClick={() => setChatOpen(true)} aria-label="Open help chat" style={{ width: '60px', height: '60px', borderRadius: '50%', background: 'linear-gradient(135deg, var(--accent-primary), #3b82f6)', border: 'none', color: '#fff', fontSize: '1.5rem', cursor: 'pointer', boxShadow: '0 4px 15px rgba(59,130,246,0.4)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>?</button>
      )}
    </div>
  );
}
