import { createContext, useContext } from 'react';

export type AiProvider = 'gemini' | 'openai' | 'anthropic';

export const AI_PROVIDERS: AiProvider[] = ['gemini', 'openai', 'anthropic'];

export type Preferences = {
  aiProvider: AiProvider;
  setAiProvider: (provider: AiProvider) => void;
};

export const PreferencesContext = createContext<Preferences | null>(null);

export function usePreferences(): Preferences {
  const preferences = useContext(PreferencesContext);
  if (!preferences) throw new Error('usePreferences must be used inside PreferencesProvider');
  return preferences;
}
