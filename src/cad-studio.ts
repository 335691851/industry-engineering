import { createApp, defineComponent, h, ref } from 'vue'
import { authenticatedFetch } from './cloud-client'
import ElementPlus from 'element-plus'
import { MlCadViewer, i18n } from '@mlightcad/cad-viewer'
import { AcApDocManager, AcEdOpenMode, AcApOpenViewMode } from '@mlightcad/cad-simple-viewer'
import 'element-plus/dist/index.css'
import './cad-studio.css'

type Session={conversion_warning?:string;session_id:string;revision:string;filename:string;layouts:string[]}
const partId=new URLSearchParams(location.search).get('part')||''
const api=`/api/parts/${encodeURIComponent(partId)}/cad/studio`
async function request<T>(path:string,body:unknown):Promise<T>{
  const result=await authenticatedFetch(api+path,body instanceof FormData?{method:'POST',body}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
  const value=await result.json()
  if(!result.ok)throw new Error(typeof value.detail==='string'?value.detail:'CAD 操作失败')
  return value
}
function send(type:string){parent.postMessage({type,partId},location.origin)}
createApp(defineComponent({setup(){
  const busy=ref(false),ready=ref(false),message=ref('正在初始化 CAD Studio…'),name=ref(''),layout=ref('Model'),layouts=ref<string[]>([])
  let session:Session|null=null,loadedDatabase:unknown=null,baselineText=''
  const file=ref<HTMLInputElement>()
  function serialized(){
    const db=AcApDocManager.instance.curDocument.database
    if(db!==loadedDatabase)throw new Error('请用顶部“加载 DWG / DXF”关联图纸，当前文件不能直接覆盖本部件')
    const output=db.dxfOut(undefined,8,undefined,{format:'ascii'})
    return typeof output==='string'?output:new TextDecoder().decode(output)
  }
  async function load(next:Session){
    ready.value=false;session=next;name.value=next.filename
    const response=await authenticatedFetch(`${api}/${next.session_id}/source`)
    if(!response.ok)throw new Error('图纸下载失败')
    const opened=await AcApDocManager.instance.openDocument('drawing.dxf',await response.arrayBuffer(),{mode:AcEdOpenMode.Write,openViewMode:AcApOpenViewMode.Extents,progressiveRendering:true})
    if(!opened)throw new Error('编辑器无法解析此图纸，请检查文件格式')
    loadedDatabase=AcApDocManager.instance.curDocument.database
    baselineText=serialized()
    const baseline=await request<{preserved_entities:number}>(`/${next.session_id}/baseline`,{revision:next.revision,dxf:baselineText})
    AcApDocManager.instance.curDocument.database.transactionManager.clearUndoStack()
    layouts.value=next.layouts;layout.value='Model';ready.value=true
    message.value=baseline.preserved_entities?`已打开；${baseline.preserved_entities} 个未解析实体将在保存时保留原件`:'已打开，可编辑实体、标注与图层；保存后重新进入图纸审核'
    if(next.conversion_warning)message.value+=' · '+next.conversion_warning
  }
  async function execute(action:()=>Promise<void>){busy.value=true;try{await action()}catch(error){message.value=(error as Error).message}finally{busy.value=false}}
  async function init(){send('cad-studio-ready');await execute(async()=>{await load(await request<Session>('/open',{}))})}
  function changed(){
    if(!ready.value)return false
    const db=AcApDocManager.instance.curDocument.database
    return db!==loadedDatabase || db.transactionManager.canUndo()
  }
  function chooseFile(event:Event){const input=event.target as HTMLInputElement;const selected=input.files?.[0];if(!selected)return
    if(changed()&&!confirm('加载新文件将放弃当前未保存的编辑，继续？')){input.value='';return}
    void execute(async()=>{const body=new FormData();body.append('file',selected);await load(await request<Session>('/upload',body))});input.value=''
  }
  async function save(){if(!session||!ready.value)return
    await execute(async()=>{
      message.value='正在保存 CAD 新版本并生成预览…'
      await request(`/${session!.session_id}/save`,{revision:session!.revision,dxf:serialized(),layout:layout.value})
      ready.value=false;send('cad-studio-saved')
      const savedLayout=layout.value
      try{await load(await request<Session>('/open',{}));layout.value=savedLayout;message.value='已保存新版本 · 可继续编辑，也可关闭后审核图纸'}
      catch(error){message.value=`新版本已保存；继续编辑需重新打开：${(error as Error).message}`}
    })
  }
  function close(){if(changed()&&!confirm('有尚未保存的 CAD 修改，放弃修改并关闭？'))return;send('cad-studio-close')}
  window.addEventListener('keydown',event=>{if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='s'){event.preventDefault();if(ready.value&&!busy.value)void save()}})
  window.addEventListener('beforeunload',event=>{if(changed()){event.preventDefault();event.returnValue=''}})
  return()=>h('div',{class:'studio-shell'},[
    h('header',{class:'studio-header'},[h('div',[h('strong','工程 CAD Studio'),h('span',`MLightCAD · ${name.value||'加载图纸开始编辑'}`)]),
      h('div',{class:'studio-actions'},[
        h('input',{ref:file,type:'file',accept:'.dwg,.dxf',hidden:true,onChange:chooseFile}),
        h('button',{disabled:busy.value,onClick:()=>file.value?.click()},'加载 DWG / DXF'),
        h('label',{class:'layout-select'},['导出布局 ',h('select',{value:layout.value,disabled:busy.value,title:'保存预览使用的布局',onChange:(e:Event)=>layout.value=(e.target as HTMLSelectElement).value},layouts.value.map(item=>h('option',{value:item},item)))]),
        h('button',{class:'primary',disabled:busy.value||!ready.value,onClick:save},busy.value?'正在处理…':'保存为图纸新版本'),
        h('button',{onClick:close},'关闭')])]),
    h('div',{class:'studio-status',role:'status'},message.value),
    h('main',{class:'studio-canvas','aria-busy':busy.value},[h(MlCadViewer,{locale:'zh',theme:'dark',mode:AcEdOpenMode.Write,baseUrl:location.origin+'/cad-assets',useMainThreadDraw:true,disableExport:true,progressiveRendering:true,openViewMode:AcApOpenViewMode.Extents,onCreate:init})])])
}})).use(i18n).use(ElementPlus).mount('#studio')
