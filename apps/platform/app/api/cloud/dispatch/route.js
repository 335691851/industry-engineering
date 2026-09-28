import { start, getRun } from 'workflow/api';
import { engineeringTask } from '../../../../workflows/engineering.js';
import { authorized, validTask } from '../../../../lib/service.mjs';
export const maxDuration=30;

export async function POST(request){
  if(!authorized(request))return Response.json({error:'unauthorized'},{status:401});
  const text=await request.text();
  if(text.length>2048)return Response.json({error:'too large'},{status:413});
  let task;try{task=JSON.parse(text)}catch{return Response.json({error:'invalid json'},{status:400})}
  if(!validTask(task))return Response.json({error:'invalid task'},{status:400});
  if(task.previousRunId){
    // Fail closed on a transient status lookup error; don't start duplicate runs unnecessarily.
    const prior=getRun(task.previousRunId);
    const status=await prior.status;
    if(!['failed','cancelled','completed'].includes(status))return Response.json({runId:task.previousRunId});
  }
  const run=await start(engineeringTask,[{taskId:task.taskId,ownerId:task.ownerId}]);
  return Response.json({runId:run.runId},{status:202});
}
