import {test} from 'node:test'
import assert from 'node:assert/strict'
import {complete,nextTask,nextStage,mbomApproved} from '../src/workflow.ts'
const part=(id,approved=true)=>({id,name:id,specifications:{reference_approval:{status:approved?'approved':'pending'}},geometry:{approval_status:approved?'approved':'pending'},process:{approval_status:approved?'approved':'pending'}})
test('复用下级图纸失效时，两条上级路径都必须等待，定位下级而非上级',()=>{
  const child=part('shared');child.geometry.approval_status='pending'
  const project={stage:'MBOM已确认',parts:[part('A',false),part('B',false),child],mbom_links:[{parent_id:null,child_id:'A'},{parent_id:null,child_id:'B'},{parent_id:'A',child_id:'shared'},{parent_id:'B',child_id:'shared'}]}
  assert.equal(complete(child),false)
  assert.deepEqual(nextTask(project),{id:'shared',stage:'drawing',label:'shared'})
  child.geometry.approval_status='approved'
  assert.equal(nextTask(project).id,'A')
  project.parts[0]=part('A');project.parts[1]=part('B')
  assert.equal(nextTask(project).id,null)
})
test('未审核 MBOM 不进入下游；已审核图纸重新进入时直接定位工艺',()=>{
  const p=part('shaft');p.process.approval_status='pending'
  assert.equal(nextStage(p),'process')
  const project={stage:'MBOM待确认',parts:[p],mbom_links:[]}
  assert.equal(nextTask(project).stage,'mbom')
  assert.equal(mbomApproved(null),false)
})
