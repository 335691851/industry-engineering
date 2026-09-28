export type Stage = 'mbom' | 'source' | 'drawing' | 'process'
type WorkPart = {id:string; name:string; specifications:{reference_approval?:{status?:string}}; geometry:{approval_status?:string}; process:{approval_status?:string}}
type WorkProject = {stage:string; parts:WorkPart[]; mbom_links:{parent_id:string|null;child_id:string}[]}
export function complete(part:WorkPart) {
  return part.specifications.reference_approval?.status === 'approved' && part.geometry.approval_status === 'approved' && part.process.approval_status === 'approved'
}
export function nextStage(part:WorkPart):Stage {
  if (part.specifications.reference_approval?.status !== 'approved') return 'source'
  if (part.geometry.approval_status !== 'approved') return 'drawing'
  return 'process'
}
export function mbomApproved(project:WorkProject|null) {
  return !!project && ['MBOM已确认','草案待审核','已归档'].includes(project.stage)
}
export function nextTask(project:WorkProject):{id:string|null;stage:Stage;label:string} {
  if (!mbomApproved(project)) return {id:null,stage:'mbom',label:'审核 MBOM'}
  const candidate=project.parts.find(part=>!complete(part) && project.mbom_links.filter(link=>link.parent_id===part.id).every(link=>{
    const child=project.parts.find(item=>item.id===link.child_id)
    return !!child && complete(child)
  }))
  return candidate ? {id:candidate.id,stage:nextStage(candidate),label:candidate.name} : {id:null,stage:'process',label:'总装工艺'}
}
