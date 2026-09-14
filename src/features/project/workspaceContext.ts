import { createContext, useContext } from 'react';
import type { useProjectWorkspaceState } from './useProjectWorkspaceState';

export type Workspace = ReturnType<typeof useProjectWorkspaceState>;

export const WorkspaceContext = createContext<Workspace | null>(null);

export function useWorkspace(): Workspace {
  const workspace = useContext(WorkspaceContext);
  if (!workspace) throw new Error('useWorkspace must be used inside a project workspace');
  return workspace;
}
