import { createContext, useContext } from 'react';

export type AuthState = {
  token: string | null;
  userName: string | null;
  signIn: (token: string, userName: string) => void;
  signOut: () => void;
  // Calls the API with the signed-in user's token; a 401 response signs the user out.
  apiRequest: (method: string, path: string, body?: unknown) => Promise<any>;
};

export const AuthContext = createContext<AuthState | null>(null);

export function useAuth(): AuthState {
  const auth = useContext(AuthContext);
  if (!auth) throw new Error('useAuth must be used inside AuthProvider');
  return auth;
}
