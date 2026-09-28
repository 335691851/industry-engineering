import { sleep, FatalError } from 'workflow';
import { backend } from '../lib/service.mjs';

async function execute(task) {
  'use step';
  const response=await backend(`/api/internal/tasks/${task.taskId}`,undefined,task.ownerId);
  if(response.status===409)return {busy:true};
  if(response.status===401||response.status===403||response.status===404)
    throw new FatalError(`Engineering task rejected: ${response.status}`);
  if(!response.ok)throw new Error(`Engineering backend unavailable: ${response.status}`);
  return response.json();
}

export async function engineeringTask(task) {
  'use workflow';
  // Large drawings and model responses remain in Supabase, only IDs/status enter the event log.
  for(let attempt=0;attempt<120;attempt++){
    const result=await execute(task);
    if(result.state==='queued') { await sleep('1s'); continue; }
    if(!result.busy)return result;
    await sleep('30s');
  }
  // Failure leaves queued work recoverable by the outbox reconciler.
  throw new FatalError('Project stayed busy; outbox reconciliation will retry scheduling');
}
