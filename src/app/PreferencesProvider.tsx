import { useCallback, useMemo, useState, type ReactNode } from 'react';
import { AI_PROVIDERS, PreferencesContext, type AiProvider } from './preferences';

const STORAGE_KEY = 'omnireview.aiProvider';

function storedProvider(): AiProvider {
  try {
    const value = window.localStorage.getItem(STORAGE_KEY);
    return AI_PROVIDERS.find(provider => provider === value) ?? 'gemini';
  } catch {
    return 'gemini';
  }
}

// Per-browser interface preferences, remembered across page reloads.
export function PreferencesProvider({ children }: { children: ReactNode }) {
  const [aiProvider, setProvider] = useState<AiProvider>(storedProvider);

  const setAiProvider = useCallback((provider: AiProvider) => {
    setProvider(provider);
    try {
      window.localStorage.setItem(STORAGE_KEY, provider);
    } catch {
      // Storage can be unavailable (private browsing); the choice still applies until the page reloads.
    }
  }, []);

  const value = useMemo(() => ({ aiProvider, setAiProvider }), [aiProvider, setAiProvider]);
  return <PreferencesContext.Provider value={value}>{children}</PreferencesContext.Provider>;
}
