import { authorized, backend } from '../../../../lib/service.mjs';
export const maxDuration=300;
export async function GET(request){
  if(!authorized(request,'CRON_SECRET'))return Response.json({error:'unauthorized'},{status:401});
  const response=await backend('/api/internal/outbox',undefined,undefined,20000);
  if(!response.ok)return Response.json({error:'outbox unavailable'},{status:503});
  const tasks=await response.json();
  // Bounded parallelism so a slow provider never consumes an unbounded cron invocation.
  let submitted=0,failed=0;
  for(let index=0;index<tasks.length;index+=10){
    await Promise.all(tasks.slice(index,index+10).map(async task=>{
      try{
        const result=await backend('/api/internal/redispatch',{taskId:task.id,ownerId:task.owner_id},undefined,22000);
        if(result.ok)submitted++;else failed++;
      }catch{failed++}
    }));
  }
  return Response.json({submitted,failed},{status:failed?503:200});
}
