import { useCallback, useMemo, useState, type ReactNode } from 'react';
import { ApiError, requestJson } from '../api/client';
import { AuthContext } from './authContext';

// The login token is kept only in memory until sign-in moves to server-side sessions, so reloading signs the user out.
export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(null);
  const [userName, setUserName] = useState<string | null>(null);

  const signIn = useCallback((newToken: string, name: string) => {
    setToken(newToken);
    setUserName(name);
  }, []);

  const signOut = useCallback(() => {
    setToken(null);
    setUserName(null);
  }, []);

  const apiRequest = useCallback(
    async (method: string, path: string, body?: unknown) => {
      try {
        return await requestJson(method, path, body, token);
      } catch (err) {
        if (err instanceof ApiError && err.status === 401 && token) signOut();
        throw err;
      }
    },
    [token, signOut],
  );

  const value = useMemo(() => ({ token, userName, signIn, signOut, apiRequest }), [token, userName, signIn, signOut, apiRequest]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
