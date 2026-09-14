import { StageGate } from '../workflow/StageGate';
import { useWorkspace } from './workspaceContext';

export function WorkspaceStageGate({ stage }: { stage: string }) {
  const { stageInfo, workflowBusy, handleCompleteStage, handleReopenStage } = useWorkspace();
  return <StageGate stage={stageInfo(stage)} busy={workflowBusy} onComplete={handleCompleteStage} onReopen={handleReopenStage} />;
}
