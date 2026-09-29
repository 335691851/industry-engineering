import React, { useEffect, useMemo, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import {
  Activity, ArrowDownToLine, ArrowRight, Bot, Box, Check, ChevronDown, ChevronRight,
  CircleAlert, ClipboardList, Clock3, Database, FileDown, FilePlus2, FileText, FolderTree,
  Layers3, MessageSquareText, MoreHorizontal, Plus, RefreshCw, Search,
  Send, Settings2, ShieldCheck, Sparkles, UploadCloud, WandSparkles, X
} from 'lucide-react'
import './style.css'
import { CadEditor } from './CadEditor'
import { DrawingPreview } from './DrawingPreview'
import { EngineeringReport } from './EngineeringReport'
import { CloudSession } from './CloudSession'
import { api } from './cloud-client'
import { ManufacturingEditor, type ManufacturingPlan } from './ManufacturingEditor'
import { complete, nextStage, nextTask, mbomApproved } from './workflow'

type Segment = { length_mm: number | null; diameter_mm: number | null; basis?: string; note?: string }
type Step = { seq: number; operation: string; equipment: string; description: string; inspection: string; basis?: string; review_required?: boolean }
type Approval = { status?: 'pending' | 'approved'; approved_at?: string; reference_id?: string; label?: string; note?: string }
type Geometry = { dwg_warning?:string; outer_tolerance?:string; inner_tolerance?:string; length_tolerance?:string; drawing_notes?:string[]; manufacturing?: ManufacturingPlan; summary?: string; material?: string; shape_type?: string; outer_diameter_mm?: number | null; inner_diameter_mm?: number | null; thickness_mm?: number | null; overall_length_mm?: number | null; segments?: Segment[]; features?: string[]; tolerances?: string[]; technical_requirements?: string[]; review_items?: string[]; confidence?: string; approval_status?: 'pending' | 'approved'; approved_at?: string }
type Process = { title?: string; material?: string; summary?: string; steps?: Step[]; review_items?: string[]; pdf_path?: string; xlsx_path?: string; approval_status?: 'pending' | 'approved'; approved_at?: string }
type MemoryItem = { id: string; category: 'standard' | 'drawing_example' | 'process_example'; title: string; source: string; has_file: boolean; step_count: number }
type Part = { workflow?: { can_generate_drawing: boolean; can_generate_process: boolean; drawing_blockers: string[] }; id: string; project_id: string; parent_id: string | null; quantity: number; name: string; drawing_no: string; kind: string; material: string; status: string; specifications: Record<string, unknown> & {reference_approval?:Approval}; geometry: Geometry; process: Process; source_pdf: string; drawing_pdf: string; drawing_dxf: string; drawing_dwg: string; resources: {id:string;name:string;kind:string}[]; usages?: {link_id:string;parent_id:string|null;parent_name:string;quantity:number}[]; updated_at: string }
type MbomLink = { id: string; project_id: string; parent_id: string | null; child_id: string; quantity: number; evidence: string; confidence: string }
type Message = { id: string; role: string; mode: string; content: string; part_id: string; created_at: string }
type Project = { id: string; name: string; drawing_no: string; stage: string; job?: Job | null; source_path: string; analysis: { technical_requirements?: string[]; review_items?: string[]; dimensions?: {label:string;value:string}[]; assembly_process?: Process; source_cad_path?: string }; parts: Part[]; mbom_links: MbomLink[]; messages: Message[]; created_at: string }
type ProjectSummary = { id: string; name: string; drawing_no: string; part_count: number; created_at: string }
type Status = { ai_configured: boolean; dwg_available: boolean; vision_model: string; agent_framework: string }
type Mode = 'drawing' | 'process'
type Preview = 'mbom' | 'source' | 'drawing' | 'process'
type Job = { id: string; status: string; total: number; completed: number; current: string; errors: string[] }
type SimilarDrawing = { id: string; name: string; drawing_no: string; material: string; kind: string; score: number; source: string; url: string }

const json = (method: string, body: unknown): RequestInit => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
const date = (value: string) => new Date(value).toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })

function App() {
  const [projects, setProjects] = useState<ProjectSummary[]>([])
  const [project, setProject] = useState<Project | null>(null)
  const [status, setStatus] = useState<Status | null>(null)
  const [mode, setMode] = useState<Mode>('drawing')
  const [preview, setPreview] = useState<Preview>('source')
  const [partId, setPartId] = useState<string | null>(null)
  const [view, setView] = useState<'workspace' | 'library'>('workspace')
  const [prompt, setPrompt] = useState('')
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [toast, setToast] = useState('')
  const [editor, setEditor] = useState<'details' | 'geometry' | 'process'>('details')
  const [geometry, setGeometry] = useState<Geometry>({})
  const [process, setProcess] = useState<Process>({})
  const [material, setMaterial] = useState('')
  const [drawingNo, setDrawingNo] = useState('')
  const [kindDraft, setKindDraft] = useState('零件')
  const [seriesDraft, setSeriesDraft] = useState('')
  const [parentIdDraft, setParentIdDraft] = useState('')
  const [newPart, setNewPart] = useState(false)
  const [newPartName, setNewPartName] = useState('')
  const [libraryQuery, setLibraryQuery] = useState('')
  const [revision, setRevision] = useState(0)
  const [memory, setMemory] = useState<MemoryItem[]>([])
  const [mbomDraft, setMbomDraft] = useState<Part[]>([])
  const [linkDraft, setLinkDraft] = useState<MbomLink[]>([])
  const [reuseChildId, setReuseChildId] = useState('')
  const [reuseParentId, setReuseParentId] = useState('')
  const [job, setJob] = useState<Job | null>(null)
  const [pendingUpload, setPendingUpload] = useState<File | null>(null)
  const [uploadName, setUploadName] = useState('')
  const [uploadDrawingNo, setUploadDrawingNo] = useState('')
  const [collapsedBranches, setCollapsedBranches] = useState<Set<string>>(new Set())
  const [cadOpen, setCadOpen] = useState(false)
  const [reportOpen, setReportOpen] = useState(false)
  const [inspectorOpen, setInspectorOpen] = useState(false)
  const [similarDrawings, setSimilarDrawings] = useState<SimilarDrawing[]>([])
  const [similarDrawingUrl, setSimilarDrawingUrl] = useState('')
  const [similarSearched, setSimilarSearched] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)
  const resourceInput = useRef<HTMLInputElement>(null)
  const similarDrawingInput = useRef<HTMLInputElement>(null)
  const drawingMemoryInput = useRef<HTMLInputElement>(null)
  const processMemoryInput = useRef<HTMLInputElement>(null)
  const messagesEnd = useRef<HTMLDivElement>(null)
  const projectRequest = useRef(0)

  const selectedPart = project?.parts.find(p => p.id === partId) || null
  const visibleMessages = project?.messages || []
  const activeProcess = selectedPart ? selectedPart.process : project?.analysis.assembly_process || {}
  const hasGenerated = Boolean(selectedPart?.drawing_pdf)
  const canDraw = Boolean(selectedPart)
  const partsByParent = useMemo(() => {
    const map = new Map<string | null, {part:Part;link:MbomLink}[]>()
    const byId = new Map((project?.parts || []).map(p => [p.id, p]))
    for (const link of project?.mbom_links || []) {
      const part = byId.get(link.child_id)
      if (!part) continue
      const key = link.parent_id || null
      map.set(key, [...(map.get(key) || []), {part, link}])
    }
    return map
  }, [project])
  const directChildren = useMemo(() => {
    if (!project) return [] as Part[]
    const childIds = new Set(project.mbom_links.filter(link => (link.parent_id || null) === (selectedPart?.id || null)).map(link => link.child_id))
    return project.parts.filter(part => childIds.has(part.id))
  }, [project, selectedPart?.id])
  const incompleteChildren = directChildren.filter(part => !complete(part))
  const hierarchyUnlocked = incompleteChildren.length === 0
  const drawingReady = Boolean(selectedPart?.drawing_pdf)
  const processReady = Boolean(activeProcess?.pdf_path)
  const referenceApproved = selectedPart?.specifications?.reference_approval?.status === 'approved'
  const referenceSelectionApproved = referenceApproved && (selectedPart?.specifications.reference_approval?.reference_id || '') === (similarDrawings.find(item => item.url === similarDrawingUrl)?.id || '')
  const drawingApproved = selectedPart?.geometry?.approval_status === 'approved'
  const processApproved = selectedPart ? selectedPart.process?.approval_status === 'approved' : activeProcess?.approval_status === 'approved'
  const currentComplete = selectedPart ? complete(selectedPart) : processApproved && hierarchyUnlocked && directChildren.length > 0
  const detailsDirty = !!selectedPart && (material !== selectedPart.material || drawingNo !== selectedPart.drawing_no || kindDraft !== selectedPart.kind || seriesDraft !== String(selectedPart.specifications.series || '') || parentIdDraft !== (selectedPart.parent_id || ''))
  const geometryDirty = !!selectedPart && JSON.stringify(geometry) !== JSON.stringify(selectedPart.geometry)
  const processDirty = JSON.stringify(process) !== JSON.stringify(activeProcess)
  const editorDirty = detailsDirty || geometryDirty || processDirty
  const pendingTask = project ? nextTask(project) : null
  const completeCount = project?.parts.filter(complete).length || 0
  const orderedMbomLinks = useMemo(() => {
    const byParent = new Map<string | null, MbomLink[]>()
    for (const link of linkDraft) byParent.set(link.parent_id || null, [...(byParent.get(link.parent_id || null) || []), link])
    const result: {link:MbomLink;depth:number}[] = []
    const visited = new Set<string>()
    const visit = (parent: string | null, depth: number) => {
      for (const link of byParent.get(parent) || []) {
        if (visited.has(link.id)) continue
        visited.add(link.id); result.push({link, depth})
        if (depth < 12) visit(link.child_id, depth + 1)
      }
    }
    visit(null, 0)
    for (const link of linkDraft) if (!visited.has(link.id)) result.push({link, depth:0})
    return result
  }, [linkDraft])

  useEffect(() => {
    Promise.all([api<ProjectSummary[]>('/api/projects'), api<Status>('/api/status')])
      .then(([items, state]) => {
        setProjects(items); setStatus(state)
        if (items.length) {
          const saved = window.localStorage.getItem('engineering_project_id')
          loadProject(items.find(p => p.id === saved)?.id || items[0].id)
        }
      }).catch(e => setError(e.message))
  }, [])
  useEffect(() => {
    if (view === 'library') api<MemoryItem[]>('/api/memory').then(setMemory).catch(e => setError(e.message))
  }, [view])
  useEffect(() => { messagesEnd.current?.scrollIntoView({ behavior: 'smooth' }) }, [visibleMessages.length, mode, busy])
  useEffect(() => {
    setGeometry(selectedPart?.geometry || {})
    setMaterial(selectedPart?.material || '')
    setDrawingNo(selectedPart?.drawing_no || '')
    setKindDraft(selectedPart?.kind || '零件')
    setSeriesDraft(String(selectedPart?.specifications?.series || ''))
    setParentIdDraft(selectedPart?.parent_id || '')
    setProcess(activeProcess || {})
  }, [partId, project?.id, selectedPart?.updated_at, project?.analysis.assembly_process])
  useEffect(() => { setMbomDraft(project?.parts || []); setLinkDraft(project?.mbom_links || []) }, [project?.id, project?.parts, project?.mbom_links])
  useEffect(() => {
    setSimilarDrawings([]); setSimilarDrawingUrl(''); setSimilarSearched(false)
    if (!partId) return
    let active = true
    api<SimilarDrawing[]>(`/api/parts/${partId}/similar-drawings`).then(items => {
      if (!active) return
      setSimilarDrawings(items)
      const reference = selectedPart?.specifications?.reference_approval?.reference_id
      setSimilarDrawingUrl(items.find(item => item.id === reference)?.url || items[0]?.url || '')
      setSimilarSearched(true)
    }).catch(() => { /* A failed search can be retried explicitly. */ })
    return () => { active = false }
  }, [partId, project?.id])
  useEffect(() => {
    if (preview === 'mbom' || !hierarchyUnlocked) {
      setInspectorOpen(false)
      return
    }
    setEditor(preview === 'source' ? 'details' : preview === 'drawing' ? 'geometry' : 'process')
    setInspectorOpen(false)
  }, [preview, hierarchyUnlocked])
  useEffect(() => {
    const guard = (event: BeforeUnloadEvent) => { if (editorDirty) { event.preventDefault(); event.returnValue = '' } }
    window.addEventListener('beforeunload', guard)
    return () => window.removeEventListener('beforeunload', guard)
  }, [editorDirty])
  useEffect(() => {
    if (!job || !['等待中', '运行中'].includes(job.status)) return
    const timer = window.setInterval(async () => {
      try {
        const next = await api<Job>(`/api/jobs/${job.id}`)
        setJob(next)
        if (!['等待中', '运行中'].includes(next.status) && project) {
          const fresh = await api<Project>(`/api/projects/${project.id}`)
          setProject(fresh); setRevision(v => v + 1)
          flash(next.status === '等待审核' ? next.current : next.errors.length ? `Agent 流程暂停，${next.errors.length} 项需处理` : 'Agent 全流程已完成')
        }
      } catch (e) { setError((e as Error).message) }
    }, 2500)
    return () => window.clearInterval(timer)
  }, [job?.id, job?.status, project?.id])

  async function loadProject(id: string, keepPart = false) {
    if (!keepPart && (busy || (editorDirty && !window.confirm('当前编辑尚未保存，切换项目将放弃这些修改。继续？')))) return
    const requestId = ++projectRequest.current
    const next = await api<Project>(`/api/projects/${id}`)
    if (requestId !== projectRequest.current) return
    window.localStorage.setItem('engineering_project_id', id)
    setProject(next)
    setJob(next.job || null)
    if (!keepPart || (partId !== null && !next.parts.some(p => p.id === partId))) {
      const task = nextTask(next)
      setPartId(task.id); setPreview(task.stage)
    }
    setRevision(v => v + 1)
  }
  function flash(message: string) {
    setToast(message)
    window.setTimeout(() => setToast(''), 3300)
  }
  function prepareUpload(file: File) {
    if (busy) return
    if (!/\.(pdf|dwg|dxf)$/i.test(file.name)) { setError('请选择 PDF、DWG 或 DXF 装配图'); return }
    if (editorDirty && !window.confirm('当前编辑尚未保存，上传新装配图将切换项目。继续？')) return
    setPendingUpload(file)
    setUploadName(file.name.replace(/\.[^.]+$/, ''))
    setUploadDrawingNo('')
  }
  function closeUpload() {
    setPendingUpload(null); setUploadName(''); setUploadDrawingNo('')
  }
  async function upload() {
    if (!pendingUpload || !uploadName.trim() || !uploadDrawingNo.trim()) return
    setBusy('正在上传装配图')
    setError('')
    try {
      const form = new FormData()
      form.append('file', pendingUpload)
      form.append('name', uploadName.trim())
      form.append('drawing_no', uploadDrawingNo.trim())
      const created = await api<Project>('/api/projects/upload', { method: 'POST', body: form })
      closeUpload()
      window.localStorage.setItem('engineering_project_id', created.id)
      setProjects(await api('/api/projects'))
      setProject(created); setJob(null); setPartId(null); setView('workspace'); setPreview('source')
      setBusy('AI 正在解析装配图')
      const analyzed = await api<Project>(`/api/projects/${created.id}/analyze`, { method: 'POST' })
      setProject(analyzed); setPartId(null); setPreview('mbom')
      setProjects(await api('/api/projects'))
      flash(`已识别 ${analyzed.parts.length} 个候选零部件`)
    } catch (e) { setError((e as Error).message) } finally { setBusy('') }
  }
  async function analyzeAgain() {
    if (!project) return
    setBusy('AI 正在重新解析装配图'); setError('')
    try {
      const updated = await api<Project>(`/api/projects/${project.id}/analyze`, { method: 'POST' })
      setProject(updated); setPartId(null); setPreview('mbom'); flash('装配图解析已更新')
    } catch (e) { setError((e as Error).message) } finally { setBusy('') }
  }
  async function send(text = prompt) {
    if (!project || !text.trim() || busy) return
    setBusy('Agent 正在分析并执行任务')
    setError(''); setPrompt('')
    const optimistic: Message = { id: `tmp-${Date.now()}`, role: 'user', mode: 'agent', content: text, part_id: partId || '', created_at: new Date().toISOString() }
    setProject({ ...project, messages: [...project.messages, optimistic] })
    try {
      const result = await api<{ answer: string; project: Project; events: {output:string;object_id:string;object_name:string}[] }>('/api/chat', json('POST', {
        project_id: project.id, part_id: partId, message: text.trim()
      }))
      setProject(result.project)
      setRevision(v => v + 1)
      const generated = result.events.at(-1)
      if (generated) {
        if (generated.object_id !== project.id) setPartId(generated.object_id)
        else setPartId(null)
        setPreview(generated.output === 'mbom' ? 'mbom' : ['drawing', 'parameters'].includes(generated.output) ? 'drawing' : 'process')
        setEditor(['drawing', 'parameters'].includes(generated.output) ? 'geometry' : 'process')
      }
    } catch (e) {
      setError((e as Error).message)
      setPrompt(text)
      try { await loadProject(project.id, true) } catch { /* Keep the original request error visible. */ }
    } finally { setBusy('') }
  }
  async function saveDetails(includeGeometry: boolean) {
    if (!selectedPart) return
    setBusy('正在保存零件信息')
    try {
      await api(`/api/parts/${selectedPart.id}`, json('PATCH', { material, drawing_no: drawingNo, kind: kindDraft, ...((selectedPart.usages?.length || 0) <= 1 ? {parent_id: parentIdDraft || null} : {}), specifications: { ...selectedPart.specifications, series: seriesDraft }, ...(includeGeometry ? { geometry } : {}) }))
      await loadProject(project!.id, true)
      if (includeGeometry) setPreview('drawing')
      flash(includeGeometry ? '二维图与零件信息已更新' : '零件信息已更新')
    } catch (e) { setError((e as Error).message) } finally { setBusy('') }
  }
  async function confirmMbom() {
    if (!project) return
    setBusy('正在保存 MBOM'); setError('')
    try {
      const updated = await api<Project>(`/api/projects/${project.id}/mbom`, json('PATCH', {
        parts: mbomDraft.map(p => ({ id:p.id, name:p.name, parent_id:p.parent_id, quantity:Number(p.quantity), kind:p.kind, material:p.material, drawing_no:p.drawing_no })),
        links: linkDraft.map(l => ({ id:l.id, parent_id:l.parent_id, child_id:l.child_id, quantity:Number(l.quantity) }))
      }))
      setProject(updated)
      const task = nextTask(updated); setPartId(task.id); setPreview(task.stage)
      flash('MBOM 已审核通过，已定位最下级待处理零件')
    } catch (e) { setError((e as Error).message) } finally { setBusy('') }
  }
  async function removeMbomLink(id: string) {
    if (!project) return
    setBusy('正在移除候选零件'); setError('')
    try {
      await api(`/api/mbom-links/${id}`, { method:'DELETE' })
      const updated = await api<Project>(`/api/projects/${project.id}`)
      setProject(updated); if (partId && !updated.parts.some(p => p.id === partId)) setPartId(null)
      flash('已移除装配使用关系')
    } catch (e) { setError((e as Error).message) } finally { setBusy('') }
  }
  async function addReuseLink() {
    if (!project || !reuseChildId || !reuseParentId) return
    setBusy('正在添加复用关系'); setError('')
    try {
      const updated = await api<Project>(`/api/projects/${project.id}/mbom-links`, json('POST', {
        parent_id: reuseParentId, child_id: reuseChildId, quantity: 1
      }))
      setProject(updated); setReuseChildId(''); setReuseParentId(''); flash('已复用零件到另一部件')
    } catch (e) { setError((e as Error).message) } finally { setBusy('') }
  }
  async function generateAll() {
    if (!project) return
    setBusy('正在启动批量生成'); setError('')
    try {
      const next = await api<Job>(`/api/projects/${project.id}/generate-all`, { method:'POST' })
      setJob(next); flash(next.status === '等待审核' ? next.current : '编排 Agent 已启动完整工程流程')
    } catch (e) { setError((e as Error).message) } finally { setBusy('') }
  }
  async function archiveAll() {
    if (!project) return
    setBusy('正在保存全部成果'); setError('')
    try {
      const updated = await api<Project>(`/api/projects/${project.id}/archive`, { method:'POST' })
      setProject(updated); flash('全部图纸与工艺已保存到零件管理')
    } catch (e) { setError((e as Error).message) } finally { setBusy('') }
  }
  async function saveProcess() {
    if (!project) return
    setBusy('正在保存工艺流程')
    try {
      const path = selectedPart ? `/api/parts/${selectedPart.id}/process` : `/api/projects/${project.id}/process`
      await api(path, json('PATCH', process))
      await loadProject(project.id, true)
      setPreview('process'); flash('工艺流程已更新')
    } catch (e) { setError((e as Error).message) } finally { setBusy('') }
  }
  async function searchSimilarDrawings() {
    if (!selectedPart) return
    setBusy('正在检索企业零件库'); setError('')
    try {
      const matches = await api<SimilarDrawing[]>(`/api/parts/${selectedPart.id}/similar-drawings`)
      setSimilarDrawings(matches); setSimilarDrawingUrl(matches[0]?.url || ''); setSimilarSearched(true)
      flash(matches.length ? `找到 ${matches.length} 份相似图纸` : '零件库中暂无相似图纸')
    } catch (e) { setError((e as Error).message) } finally { setBusy('') }
  }
  async function uploadSimilarDrawing(file: File) {
    if (!selectedPart || !project) return
    setBusy('正在上传相似部件图纸'); setError('')
    try {
      const form = new FormData(); form.append('file', file)
      const saved = await api<{id:string;name:string;kind:string}>(`/api/parts/${selectedPart.id}/resources`, { method:'POST', body:form })
      await loadProject(project.id, true)
      const item: SimilarDrawing = { id:`resource:${saved.id}`, name:selectedPart.name, drawing_no:saved.name,
        material:selectedPart.material || '待确认', kind:'用户参考图', score:1, source:'用户上传', url:`/api/resources/${saved.id}` }
      setSimilarDrawings(current => [item, ...current.filter(match => match.id !== item.id)])
      setSimilarDrawingUrl(item.url); setSimilarSearched(true); setPreview('source')
      flash('相似图纸已上传并作为当前生成证据')
    } catch (e) { setError((e as Error).message) } finally { setBusy('') }
  }
  async function generateCurrentDrawing() {
    if (!selectedPart || !project || !selectedPart.workflow?.can_generate_drawing || busy) return
    setBusy('制图 Agent 正在生成'); setError('')
    try {
      if (JSON.stringify(geometry) !== JSON.stringify(selectedPart.geometry) || material !== selectedPart.material || drawingNo !== selectedPart.drawing_no) {
        await api(`/api/parts/${selectedPart.id}`, json('PATCH', { geometry, material, drawing_no: drawingNo }))
      }
      const result = await api<{ project: Project }>(`/api/parts/${selectedPart.id}/generate/drawing`, json('POST', {
        instruction: '按已保存参数、已审核参考输入与下级成果，生成当前对象的成品制造图和独立放量建议。保留用户锁定参数。'
      }))
      setProject(result.project); setRevision(v => v + 1); setPreview('drawing'); flash('图纸草案已生成，请审核')
    } catch (e) { setError((e as Error).message); await loadProject(project.id, true) } finally { setBusy('') }
  }
  async function generateCurrentProcess() {
    if (!project || !hierarchyUnlocked || (selectedPart && !selectedPart.workflow?.can_generate_process) || busy) return
    if (!selectedPart) {
      if (processDirty && process.steps?.length) {
        try { await api(`/api/projects/${project.id}/process`, json('PATCH', process)) }
        catch (e) { setError((e as Error).message); return }
      }
      await send('依据下级已审核成果，重新规划总装配体作为合格成品的核心装配工艺。')
      return
    }
    setBusy('工艺 Agent 正在规划'); setError('')
    try {
      if (JSON.stringify(process) !== JSON.stringify(activeProcess) && process.steps?.length) {
        await api(`/api/parts/${selectedPart.id}/process`, json('PATCH', process))
      }
      const result = await api<{ project: Project }>(`/api/parts/${selectedPart.id}/generate/process`, json('POST', {
        instruction: '基于已审核成品图、制造放量和当前工序编辑，重新规划本部件作为合格成品的核心工艺路线，明确余量消耗和最终检验。'
      }))
      setProject(result.project); setRevision(v => v + 1); setPreview('process'); flash('工艺规划已更新，请审核')
    } catch (e) { setError((e as Error).message); await loadProject(project.id, true) } finally { setBusy('') }
  }
  async function approveStage(stage: 'reference' | 'drawing' | 'process') {
    if (!project) return
    setBusy('正在记录人工审核'); setError('')
    try {
      let updated: Project
      if (!selectedPart) {
        if (stage !== 'process') return
        updated = await api<Project>(`/api/projects/${project.id}/approve-process`, { method:'POST' })
      } else {
        const selectedReference = similarDrawings.find(item => item.url === similarDrawingUrl)
        updated = await api<Project>(`/api/parts/${selectedPart.id}/approve/${stage}`, json('POST', {
          reference_id: stage === 'reference' ? (selectedReference?.id || '') : '',
          note: stage === 'reference' && !selectedReference ? '未找到相似图纸，工程师确认以装配接口和后续输入继续' : ''
        }))
      }
      setProject(updated); setJob(updated.job || null); setRevision(value => value + 1)
      if (stage === 'reference') setPreview('drawing')
      if (stage === 'drawing') setPreview('process')
      flash(stage === 'reference' ? '相似图纸输入已审核通过' : stage === 'drawing' ? '生成图纸已审核通过' : '工艺流程单已审核通过')
    } catch (e) { setError((e as Error).message) } finally { setBusy('') }
  }
  function continueToParent() {
    if (!project || !selectedPart || !currentComplete) return
    const usage = selectedPart.usages?.[0]
    const nextId = usage?.parent_id || null
    pickPart(nextId)
  }
  async function archive() {
    if (!selectedPart || !project) return
    setBusy('正在归档')
    try {
      await api(`/api/parts/${selectedPart.id}/save`, { method: 'POST' })
      await loadProject(project.id, true); flash('已保存到零件管理')
    } catch (e) { setError((e as Error).message) } finally { setBusy('') }
  }
  async function createPart() {
    if (!project || !newPartName.trim()) return
    setBusy('正在创建零件')
    try {
      const created = await api<Part>(`/api/projects/${project.id}/parts`, json('POST', { name: newPartName.trim(), parent_id: partId }))
      const updated = await api<Project>(`/api/projects/${project.id}`)
      setProject(updated); setPartId(created.id); setNewPart(false); setNewPartName(''); flash('零件已创建')
    } catch (e) { setError((e as Error).message) } finally { setBusy('') }
  }
  async function uploadResource(file: File) {
    if (!selectedPart || !project) return
    setBusy('正在上传资源')
    try {
      const form = new FormData(); form.append('file', file)
      await api(`/api/parts/${selectedPart.id}/resources`, { method: 'POST', body: form })
      await loadProject(project.id, true); flash('资源已保存到零件管理')
    } catch (e) { setError((e as Error).message) } finally { setBusy('') }
  }
  async function uploadMemory(file: File, category: 'drawing_example' | 'process_example') {
    setBusy('正在加入工程记忆'); setError('')
    try {
      const form = new FormData(); form.append('file', file); form.append('category', category)
      await api('/api/memory', { method:'POST', body:form })
      setMemory(await api<MemoryItem[]>('/api/memory'))
      flash('历史资料已加入工程记忆，后续生成时可检索')
    } catch (e) { setError((e as Error).message) } finally { setBusy('') }
  }
  function pickPart(id: string | null) {
    if (busy || (id !== partId && editorDirty && !window.confirm('当前编辑尚未保存，切换对象将放弃这些修改。继续？'))) return
    const part = project?.parts.find(item => item.id === id)
    setPartId(id); setPreview(part && mbomApproved(project) ? nextStage(part) : id === null && mbomApproved(project) ? 'process' : 'mbom'); setInspectorOpen(false)
  }
  function toggleBranch(key: string) {
    setCollapsedBranches(current => {
      const next = new Set(current)
      if (next.has(key)) next.delete(key); else next.add(key)
      return next
    })
  }
  function renderNode(part: Part, link: MbomLink, depth = 0): React.ReactNode {
    const children = partsByParent.get(part.id) || []
    const pendingChildren = children.filter(item => !complete(item.part))
    const collapsed = collapsedBranches.has(link.id)
    return <React.Fragment key={link.id}>
      <div className={`tree-row ${partId === part.id ? 'active' : ''}`} style={{ paddingLeft: 8 + depth * 18 }}>
        {children.length ? <button className="tree-toggle" title={collapsed ? `展开${part.name}` : `收起${part.name}`} aria-label={collapsed ? `展开${part.name}` : `收起${part.name}`} onClick={() => toggleBranch(link.id)}>{collapsed ? <ChevronRight size={13} /> : <ChevronDown size={13} />}</button> : <span className="tree-spacer" />}
        <button className={`tree-node ${pendingChildren.length ? 'waiting-children' : ''}`} onClick={() => pickPart(part.id)}><span className={`part-dot ${part.kind === '部件' ? 'assembly' : ''}`} /><span className="tree-label">{part.name}</span>{link.quantity !== 1 && <small className="tree-quantity">×{link.quantity}</small>}{pendingChildren.length > 0 && <small className="tree-stage">待下级审核</small>}{!pendingChildren.length && part.process?.approval_status === 'approved' && <Check size={13} className="tree-check" />}</button>
      </div>
      {!collapsed && children.map(child => renderNode(child.part, child.link, depth + 1))}
    </React.Fragment>
  }
  const filteredParts = (project?.parts || []).filter(p => `${p.name} ${p.drawing_no} ${p.material}`.toLowerCase().includes(libraryQuery.toLowerCase()))

  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand"><div className="brand-mark"><Layers3 size={21} strokeWidth={1.8} /></div><div><strong>工程解析平台</strong><small>ENGINEERING STUDIO</small></div></div>
      <div className="side-label">工作空间</div>
      <button className={`side-link ${view === 'workspace' ? 'selected' : ''}`} onClick={() => setView('workspace')}><MessageSquareText size={18} /> AI 工程工作台 <span className="side-link-tail">01</span></button>
      <button className={`side-link ${view === 'library' ? 'selected' : ''}`} onClick={() => setView('library')}><Database size={18} /> 零件管理 <span className="side-link-tail">{project?.parts.length || 0}</span></button>
      <div className="side-divider" />
      <div className="side-heading"><span>当前项目</span><button title="上传装配图" onClick={() => fileInput.current?.click()}><Plus size={17} /></button></div>
      <input ref={fileInput} type="file" accept=".pdf,.dwg,.dxf,application/pdf" hidden onChange={e => { const f = e.target.files?.[0]; if (f) prepareUpload(f); e.currentTarget.value = '' }} />
      {projects.length ? <div className="project-select-wrap"><select className="project-select" disabled={!!busy} value={project?.id || ''} onChange={e => { void loadProject(e.target.value).catch(error => setError(error.message)) }}>{projects.map(p => <option value={p.id} key={p.id}>{p.name} · {p.drawing_no || '图号待确认'}</option>)}</select><ChevronDown size={14} /></div> : <button className="empty-project" onClick={() => fileInput.current?.click()}><UploadCloud size={18} /> 上传第一张装配图</button>}
      {project && <>
        <div className="project-meta">{project.drawing_no || '图号待确认'} <span>·</span> {project.parts.length} 个零件</div>
        <div className="tree-head"><span>零件谱系</span><button title="新增下级零件" onClick={() => setNewPart(true)}><Plus size={15} /></button></div>
        <div className="tree-scroll">
          <div className={`tree-row root-row ${partId === null ? 'active' : ''}`}><button className="tree-toggle" title={collapsedBranches.has(`root:${project.id}`) ? `展开${project.name}` : `收起${project.name}`} aria-label={collapsedBranches.has(`root:${project.id}`) ? `展开${project.name}` : `收起${project.name}`} onClick={() => toggleBranch(`root:${project.id}`)}>{collapsedBranches.has(`root:${project.id}`) ? <ChevronRight size={13} /> : <ChevronDown size={13} />}</button><button className="tree-node" onClick={() => pickPart(null)}><span className="part-dot assembly" /><span className="tree-label">{project.name}</span><small className="tree-drawing-no">{project.drawing_no || '图号待确认'}</small></button></div>
          {!collapsedBranches.has(`root:${project.id}`) && (partsByParent.get(null) || []).map(item => renderNode(item.part, item.link))}
        </div>
      </>}
      <div className="sidebar-bottom"><div className="api-status"><span className={`status-led ${status?.ai_configured ? 'on' : ''}`} /><div><b>DeepSeek 模型服务</b><small>{status?.ai_configured ? '已配置 · 可生成草案' : '尚未配置 API'}</small></div><Activity size={16} /></div><div className="side-footer"><ShieldCheck size={15} /> 工程草案需人工审核 <span>v0.1</span></div></div>
    </aside>

    <main className="main-area">
      <header className="topbar"><div className="breadcrumb">工作空间 <ChevronRight size={14} /> <strong>{view === 'workspace' ? project?.name || '新项目' : '零件管理'}</strong></div><div className="topbar-right"><span className="model-badge"><Sparkles size={13} /> {status?.agent_framework || 'DeepAgents'} · {status?.vision_model || 'DeepSeek'}</span><button className="icon-button" title="上传装配图" onClick={() => fileInput.current?.click()}><UploadCloud size={18} /></button></div></header>

      {view === 'workspace' ? <div className="workspace">
        <section className="conversation">
          <div className="conversation-head"><div><div className="eyebrow">AI ENGINEERING COPILOT</div><h1>工程会话</h1><p>从装配图到零件图与工艺流程</p></div><button className="icon-button muted" title="重新解析" onClick={analyzeAgain} disabled={!project || !!busy}><RefreshCw size={17} /></button></div>
          <div className="workflow-actions"><span>① 审核 MBOM　→　② Agent 从最下级规划生成　→　③ 每阶段人工审核　→　④ 自动进入上级部件</span>{project?.stage === 'MBOM待确认' && <button className="primary-button" onClick={() => setPreview('mbom')}>审核 MBOM</button>}{project && project.stage !== 'MBOM待确认' && project.stage !== '已归档' && (!job || ['完成','部分完成','中断'].includes(job.status)) && <button className="primary-button" onClick={generateAll} disabled={!!busy}>启动 Agent 全流程</button>}{project?.stage === '草案待审核' && <button className="primary-button" onClick={archiveAll} disabled={!!busy || job?.status === '运行中'}>全部完成后保存</button>}{project?.stage === '已归档' && <span>全部成果已保存</span>}</div>
          {job && ['等待中','运行中','等待审核'].includes(job.status) && <div className={`job-progress ${job.status === '等待审核' ? 'waiting-review' : ''}`}><Activity size={16} /> {job.current || '准备中'} · {job.completed}/{job.total} 个阶段</div>}
          <div className="context-strip"><div className="context-icon"><FolderTree size={17} /></div><div><small>当前处理对象</small><b>{selectedPart?.name || project?.name || '请上传装配图'}</b></div><ChevronDown size={16} /></div>
          <div className="chat-scroll">
            {project ? <>
              <div className="assistant-intro"><div className="bot-avatar"><Bot size={18} /></div><div className="chat-bubble intro-bubble"><b>工程 Agent 已就绪</b><p>请按右侧步骤从左到右操作。系统先开放最下级零件，完成图纸与工艺后再逐级开放上层部件。</p></div></div>
              {visibleMessages.map(m => <div className={`message ${m.role}`} key={m.id}><div className="message-avatar">{m.role === 'assistant' ? <Bot size={16} /> : '工'}</div><div className="message-body"><div className="message-name">{m.role === 'assistant' ? '工程 Agent' : '工程师'} <span>{new Date(m.created_at).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}</span></div><div className="chat-bubble">{m.content}</div></div></div>)}
              {busy && <div className="message assistant"><div className="message-avatar"><Bot size={16} /></div><div className="message-body"><div className="message-name">工程 Agent</div><div className="chat-bubble loading"><span className="pulse-dot" />{busy}…</div></div></div>}
              <div ref={messagesEnd} />
            </> : <div className="chat-empty"><div className="empty-emblem"><FilePlus2 size={28} /></div><h3>从一张装配图开始</h3><p>上传 PDF 装配图，识别零件与技术要求，然后在会话中生成工程草案。</p><button className="primary-button" onClick={() => fileInput.current?.click()}><UploadCloud size={16} /> 上传装配图</button></div>}
          </div>
          {project && <div className="composer-wrap"><div className="composer"><textarea value={prompt} onChange={e => setPrompt(e.target.value)} placeholder="输入修改或问题，例如：把轴头直径改为 60 mm，或增加热装后复测工序" onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send() } }} /><div className="composer-actions"><button className="outline-button" onClick={() => fileInput.current?.click()}><UploadCloud size={15} /> 上传装配图</button><span>Enter 发送</span><button className="send-button" disabled={!prompt.trim() || !!busy} onClick={() => send()}><Send size={17} /></button></div></div><div className="composer-note"><CircleAlert size={13} /> AI 输出仅为工程草案，制造前必须复核。</div></div>}
        </section>

        <section className="result-area">
          <input ref={similarDrawingInput} hidden type="file" accept=".pdf,application/pdf" onChange={e => { const file=e.target.files?.[0]; if(file) uploadSimilarDrawing(file); e.currentTarget.value='' }} />
          <div className="result-head"><div><div className="eyebrow">ENGINEERING OUTPUT</div><h2>{selectedPart?.name || project?.name || '图纸预览'}</h2><div className="result-subtitle">{selectedPart?.drawing_no || project?.drawing_no || '图号待确认'} <span>·</span> {selectedPart?.kind || '装配体'} <span>·</span> <i className={selectedPart?.status === '已归档' ? 'saved' : ''}>{selectedPart?.status || '源图'}</i></div></div><div className="result-actions">{selectedPart && currentComplete && <button className="outline-button" onClick={continueToParent}><ArrowRight size={15} /> 继续上级部件</button>}{selectedPart && <button className="outline-button" onClick={archive} disabled={!!busy || !currentComplete}><Check size={15} /> 另存到零件管理</button>}{preview !== 'mbom' && <button className="icon-button muted" title={inspectorOpen ? '收起编辑抽屉' : '打开编辑抽屉'} onClick={() => setInspectorOpen(value => !value)}><MoreHorizontal size={20} /></button>}</div></div>
          <div className="result-tabs workflow-tabs"><button className={preview === 'mbom' ? 'active' : ''} onClick={() => setPreview('mbom')}><span className="step-number">1</span><FolderTree size={15} /> MBOM{mbomApproved(project) && <Check size={12} />}</button><button className={preview === 'source' ? 'active' : ''} onClick={() => setPreview('source')} disabled={!selectedPart || !hierarchyUnlocked || project?.stage === 'MBOM待确认'}><span className="step-number">2</span><FileText size={15} /> 相似图纸{referenceApproved && <Check size={12} />}</button><button className={preview === 'drawing' ? 'active' : ''} onClick={() => setPreview('drawing')} disabled={!selectedPart || !hierarchyUnlocked || !referenceApproved}><span className="step-number">3</span><Layers3 size={15} /> 生成图纸{drawingApproved && <Check size={12} />}</button><button className={preview === 'process' ? 'active' : ''} onClick={() => setPreview('process')} disabled={!mbomApproved(project) || !hierarchyUnlocked || (!!selectedPart && !drawingApproved)}><span className="step-number">4</span><ClipboardList size={15} /> 工艺流程单{processApproved && <Check size={12} />}</button></div>
          {project && <div className="engineering-progress">
            <div><strong>{completeCount}/{project.parts.length} 个零部件已审核完成</strong><span>{editorDirty ? '编辑内容尚未保存；保存后才能审核和进入下一阶段' : preview === 'mbom' ? '核对结构、每上级用量、材料与复用关系' : preview === 'source' ? '核对参考图与当前零件的边界、图号和适用性' : preview === 'drawing' ? '复核成品尺寸、公差、加工放量与制造图；通过后生成工艺' : '复核当前对象成为合格成品所需的加工、装配和检验路径'}</span></div>
            {pendingTask && <button disabled={!!busy} onClick={() => { if (editorDirty && !window.confirm('放弃尚未保存的修改，定位下一待办？')) return; setPartId(pendingTask.id); setPreview(pendingTask.stage); setInspectorOpen(false) }}><ArrowRight size={14}/>{pendingTask.label === selectedPart?.name ? '当前待办' : `下一待办：${pendingTask.label}`}</button>}
          </div>}
          <div className="result-content"><div className="preview-panel">
            <div className="preview-toolbar"><div><span className="preview-live" /> {preview === 'mbom' ? '多级制造物料清单' : preview === 'source' ? '企业库相似图纸' : preview === 'drawing' ? '二维制造图预览' : '工艺流程单预览'} <span className="preview-scale">{preview === 'source' ? 'LIBRARY' : preview === 'drawing' ? 'SVG / CAD' : preview === 'mbom' ? 'MBOM' : 'FORM'}</span></div><div className="toolbar-actions">{preview === 'mbom' && <button className="toolbar-generate" onClick={analyzeAgain} disabled={!!busy}><RefreshCw size={14} /> 重新识别 MBOM</button>}{preview === 'source' && <button className="toolbar-generate" onClick={searchSimilarDrawings} disabled={!selectedPart || !!busy}><Search size={14} /> {similarSearched ? '重新搜索' : '搜索相似图纸'}</button>}{preview === 'source' && <button className="toolbar-edit" onClick={() => similarDrawingInput.current?.click()} disabled={!selectedPart || !!busy}><UploadCloud size={14} /> 上传参考图</button>}{preview === 'source' && <button className="toolbar-approve" onClick={() => approveStage('reference')} disabled={!selectedPart || !!busy || !hierarchyUnlocked || !mbomApproved(project) || referenceSelectionApproved || detailsDirty || (!similarDrawingUrl && !similarSearched)}><Check size={14} /> {referenceSelectionApproved ? '已审核' : similarDrawingUrl ? '审核通过并进入图纸' : '确认无参考图并继续'}</button>}{preview === 'source' && similarDrawingUrl && <a href={similarDrawingUrl} target="_blank" rel="noreferrer" title="打开相似图纸"><ArrowRight size={15} /></a>}{preview === 'drawing' && <button className="toolbar-edit" disabled={!selectedPart?.workflow?.can_generate_drawing || !!busy || editorDirty} title={editorDirty ? '请先保存编辑页修改' : '加载 DWG / DXF，编辑实体与标注'} onClick={() => setCadOpen(true)}>在线 CAD 编辑 / 加载 DWG</button>}{preview === 'drawing' && <button className={`toolbar-edit ${inspectorOpen && editor === 'geometry' ? 'active' : ''}`} onClick={() => {setEditor('geometry'); setInspectorOpen(true)}} disabled={!selectedPart || !!busy}><Settings2 size={14} /> 尺寸与放量</button>}{preview === 'drawing' && <button className="toolbar-generate" title={selectedPart?.workflow?.drawing_blockers.length ? '请先处理尺寸与放量校核项' : drawingReady ? '按已保存的当前参数重新生成图纸' : '按已保存的当前参数生成图纸'} onClick={generateCurrentDrawing} disabled={!selectedPart?.workflow?.can_generate_drawing || !!selectedPart?.workflow?.drawing_blockers.length || !!busy}><WandSparkles size={14} /> {drawingReady ? '重新生成' : '生成图纸'}</button>}{preview === 'drawing' && drawingReady && <button className="toolbar-approve" onClick={() => approveStage('drawing')} disabled={!!busy || drawingApproved || !!selectedPart?.workflow?.drawing_blockers.length || material !== selectedPart?.material || drawingNo !== selectedPart?.drawing_no || JSON.stringify(geometry) !== JSON.stringify(selectedPart?.geometry)}><Check size={14} /> {drawingApproved ? '图纸已审核' : '审核通过并进入工艺'}</button>}{preview === 'drawing' && selectedPart?.drawing_pdf && <a href={`/api/parts/${selectedPart.id}/files/pdf`} target="_blank" rel="noreferrer" title="打开 PDF"><FileDown size={15} /></a>}{preview === 'process' && <button className="toolbar-generate" onClick={generateCurrentProcess} disabled={!hierarchyUnlocked || (!!selectedPart && !selectedPart.workflow?.can_generate_process) || !!busy}><WandSparkles size={14} /> {processReady ? '按当前参数重新生成' : '生成流程单'}</button>}{preview === 'process' && processReady && <button className="toolbar-approve" onClick={() => approveStage('process')} disabled={!!busy || processApproved || JSON.stringify(process) !== JSON.stringify(activeProcess)}><Check size={14} /> {processApproved ? '工艺已审核' : '审核通过并完成'}</button>}{(preview === 'source' || preview === 'process') && <button className={`toolbar-edit ${inspectorOpen ? 'active' : ''}`} onClick={() => setInspectorOpen(value => !value)}><Settings2 size={14} /> 编辑{preview === 'source' ? '概览' : '工艺'}</button>}</div></div>
            {preview === 'drawing' && selectedPart && <div className="engineering-checks">
              {!!selectedPart.workflow?.drawing_blockers.length && <div role="alert"><strong>生成前需要处理：</strong><ul>{selectedPart.workflow.drawing_blockers.map((reason, i) => <li key={i}>{reason}</li>)}</ul><small>请使用上方“尺寸与放量”完善参数。</small></div>}
              {(selectedPart.workflow as {drawing_warnings?:string[]})?.drawing_warnings?.map((reason, i) => <p key={i}>待复核：{reason}</p>)}
              {(geometryDirty || detailsDirty) && <p>当前有未保存修改，请先保存编辑内容，再审核图纸。</p>}
              <button className="report-link" onClick={()=>setReportOpen(true)}>查看工程数据与校核报告</button>
            </div>}
            {preview === 'mbom' && project && <div className="mbom-panel">
              <div className="mbom-heading"><div><h3>{project.name} · MBOM</h3><p>{mbomDraft.length} 种零部件 · {linkDraft.length} 条装配关系</p></div><button className="primary-button" onClick={confirmMbom} disabled={!!busy}>保存并确认</button></div>
              <div className="mbom-columns" aria-hidden="true"><span /><span>零部件 / 层级</span><span>上级</span><span>用量</span><span>材料</span><span>状态</span></div>
              <div className="mbom-list">{orderedMbomLinks.map(({link, depth}) => {
                const p = mbomDraft.find(x => x.id === link.child_id)
                if (!p) return null
                const reuseCount = linkDraft.filter(x => x.child_id === p.id).length
                return <div className="mbom-row" key={link.id}>
                  <button className="mbom-remove" title="移除此装配使用关系" onClick={() => removeMbomLink(link.id)} disabled={!!busy}><X size={14} /></button>
                  <div className="mbom-name-cell" style={{paddingLeft:depth*19}}><span className="mbom-branch">{depth ? '↳' : '●'}</span><input aria-label="零部件名称" value={p.name} onChange={e => setMbomDraft(mbomDraft.map(x => x.id===p.id ? {...x,name:e.target.value}:x))} />{reuseCount > 1 && <small>复用 ×{reuseCount}</small>}</div>
                  <select aria-label={`${p.name}的上级`} value={link.parent_id || ''} onChange={e => setLinkDraft(linkDraft.map(x => x.id===link.id ? {...x,parent_id:e.target.value || null}:x))}><option value="">{project.name}</option>{mbomDraft.filter(x => x.id!==p.id).map(x => <option value={x.id} key={x.id}>{x.name}</option>)}</select>
                  <input aria-label={`${p.name}每上级用量`} type="number" min="0.001" step="any" value={link.quantity} onChange={e => setLinkDraft(linkDraft.map(x => x.id===link.id ? {...x,quantity:Number(e.target.value)}:x))} />
                  <input aria-label={`${p.name}材料`} value={p.material} onChange={e => setMbomDraft(mbomDraft.map(x => x.id===p.id ? {...x,material:e.target.value}:x))} />
                  <small className={`mbom-evidence ${link.confidence === '待复核' ? 'needs-review' : ''}`} title={link.evidence || String(p.specifications?.evidence || '装配关系待核对')}>{depth === 0 ? '一级' : `${depth+1}级`} · {link.confidence || '待复核'}</small>
                </div>
              })}</div>
              <div className="mbom-reuse"><b>复用已有零件</b><select aria-label="选择复用零件" value={reuseChildId} onChange={e => setReuseChildId(e.target.value)}><option value="">选择零件</option>{mbomDraft.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select><select aria-label="选择复用上级" value={reuseParentId} onChange={e => setReuseParentId(e.target.value)}><option value="">选择上级部件</option>{mbomDraft.filter(p => p.kind === '部件' && p.id !== reuseChildId).map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select><button onClick={addReuseLink} disabled={!!busy || !reuseChildId || !reuseParentId}>添加关系</button></div>
              <button className="add-row" onClick={() => setNewPart(true)}><Plus size={15} /> 添加新零部件</button>
            </div>}
            {preview !== 'mbom' && !hierarchyUnlocked && <div className="workflow-locked"><FolderTree size={38} /><h3>请先审核通过下级部件</h3><p>当前对象必须使用已审核通过的下级工艺作为输入。请依次完成：</p><div>{incompleteChildren.map(part => <button key={part.id} onClick={() => pickPart(part.id)}>{part.name}<span>{part.process?.approval_status !== 'approved' ? '工艺待审核' : '待完成'}</span><ArrowRight size={14} /></button>)}</div></div>}
            {preview === 'source' && hierarchyUnlocked && selectedPart && <div className="similar-workspace">{similarDrawings.length > 0 ? <><aside className="similar-list">{similarDrawings.map(item => <button className={similarDrawingUrl === item.url ? 'active' : ''} key={item.id} onClick={() => setSimilarDrawingUrl(item.url)}><b>{item.name}</b><span>{item.drawing_no}</span><small>{item.source} · 检索匹配度 {Math.round(item.score * 100)}%</small></button>)}</aside><div className="pdf-frame">{similarDrawingUrl ? <iframe title="相似零部件图纸" src={`${similarDrawingUrl}#toolbar=0&navpanes=0`} /> : <p>请在左侧选择参考图纸</p>}</div></> : <div className="preview-empty"><Search size={36} /><h3>{similarSearched ? '零件库中暂无相似图纸' : '尚未检索相似图纸'}</h3><p>系统不会使用装配图替代零部件原始图纸。可检索企业资料，也可上传当前零件参考图，上传后会立即展示并用于后续生成。</p><div className="empty-actions"><button onClick={searchSimilarDrawings} disabled={!!busy}><Search size={15} /> 搜索相似部件图纸</button><button onClick={() => similarDrawingInput.current?.click()} disabled={!!busy}><UploadCloud size={15} /> 上传参考图纸</button></div></div>}</div>}
            {preview === 'source' && hierarchyUnlocked && !selectedPart && <div className="preview-empty"><Box size={34} /><h3>请选择零部件</h3><p>相似图纸检索仅针对具体零部件。</p></div>}
            {preview === 'drawing' && hierarchyUnlocked && selectedPart && (selectedPart.drawing_pdf ? <DrawingPreview partId={selectedPart.id} name={selectedPart.name} revision={revision} approved={drawingApproved} /> : <div className="preview-empty"><Layers3 size={36} /><h3>尚未生成二维图纸</h3><p>{selectedPart.workflow?.drawing_blockers.length ? '请先使用上方“尺寸与放量”处理校核项，完成后即可生成。' : '参数已满足生成条件，请使用上方“生成图纸”。'}</p></div>)}
            {preview === 'process' && hierarchyUnlocked && <div className="process-preview">{(activeProcess?.steps?.length || 0) ? <><div className="process-title"><div><span>工艺流程单 / PROCESS SHEET</span><h3>{activeProcess.title || `${selectedPart?.name || project?.name}工艺流程`}</h3></div><span className="draft-pill">草案</span></div><div className="process-meta"><div><small>零部件</small><b>{selectedPart?.name || project?.name}</b></div><div><small>图号</small><b>{selectedPart?.drawing_no || project?.drawing_no || '待确认'}</b></div><div><small>材料</small><b>{selectedPart?.material || '待确认'}</b></div><div><small>工序数</small><b>{activeProcess.steps?.length}</b></div></div><table className="process-table"><thead><tr><th>序号</th><th>工序</th><th>工序说明 / 设备与检验</th></tr></thead><tbody>{activeProcess.steps?.map((s, i) => <tr key={i}><td>{String(s.seq || i+1).padStart(2, '0')}</td><td><b>{s.operation}</b><small>{s.equipment}</small></td><td>{s.description}<div className="inspection">检验：{s.inspection || '待确定'}</div></td></tr>)}</tbody></table>{activeProcess.review_items?.length ? <div className="review-note"><CircleAlert size={17} /><div><b>待复核项</b><p>{activeProcess.review_items.join('；')}</p></div></div> : null}</> : <div className="preview-empty"><ClipboardList size={34} /><h3>尚未生成工艺流程单</h3><p>{selectedPart ? '完成图纸后，根据当前工艺参数生成流程单。' : '完成全部下级部件后生成总装工艺流程单。'}</p><button onClick={generateCurrentProcess}><WandSparkles size={15} /> 生成流程单</button></div>}</div>}
            {preview === 'drawing' && !selectedPart && <div className="preview-empty"><Box size={34} /><h3>选择一个零部件</h3><p>在左侧零件谱系中选择目标部件。</p></div>}
            <div className="preview-footer"><span><ShieldCheck size={14} /> 来源、推断与待确认数据分层展示</span><span>单位：mm</span></div>
          </div>
          {inspectorOpen && <button className="drawer-scrim" aria-label="关闭编辑抽屉" onClick={() => setInspectorOpen(false)} />}
          <aside className={`inspector output-drawer ${inspectorOpen ? 'open' : ''}`} inert={!inspectorOpen} aria-hidden={!inspectorOpen}><div className="inspector-head"><div><div className="eyebrow">{preview === 'source' ? 'SIMILAR DRAWINGS' : preview === 'drawing' ? 'GENERATED DRAWING' : 'PROCESS SHEET'}</div><h3>{preview === 'source' ? '相似图纸 · 检索参数' : preview === 'drawing' ? '生成图纸 · 尺寸编辑' : '工艺流程单 · 工艺编辑'}</h3></div><button className="inspector-close" title="关闭编辑抽屉" onClick={() => setInspectorOpen(false)}><X size={18} /></button></div><div className="drawer-context"><span>{selectedPart?.name || project?.name || '当前对象'}</span><small>{selectedPart?.drawing_no || project?.drawing_no || '图号待确认'}</small></div>
            <div className="inspector-scroll">
              {editor === 'details' && selectedPart && (selectedPart.usages?.length || 0) > 1 && <div className="inspector-section"><div className="section-title">装配复用 <FolderTree size={15} /></div>{selectedPart.usages?.map(usage => <div className="property" key={usage.link_id}><span>{usage.parent_name}</span><b>每上级 ×{usage.quantity}</b></div>)}<p className="hint">此零件被多个部件引用；请在 MBOM 结构中编辑全部上级关系。</p></div>}
              {editor === 'details' && <><div className="inspector-section"><div className="section-title">对象信息 <Settings2 size={15} /></div><div className="property"><span>名称</span><b>{selectedPart?.name || project?.name || '—'}</b></div><div className="property"><span>类型</span><b>{selectedPart?.kind || '装配体'}</b></div><div className="property"><span>状态</span><b className="property-status">{selectedPart?.status || '待生成'}</b></div>{selectedPart && <><label className="field-label">图号<input value={drawingNo} onChange={e => setDrawingNo(e.target.value)} placeholder="填写图号" /></label><label className="field-label">材料<input value={material} onChange={e => setMaterial(e.target.value)} placeholder="待确认" /></label><label className="field-label">分类<select value={kindDraft} onChange={e => setKindDraft(e.target.value)}><option value="零件">零件</option><option value="部件">部件</option><option value="标准件">标准件</option><option value="外购件">外购件</option></select></label><label className="field-label">型谱 / 系列<input value={seriesDraft} onChange={e => setSeriesDraft(e.target.value)} placeholder="例如：LTJ 收卷轴系列" /></label><label className="field-label">{(selectedPart.usages?.length || 0) > 1 ? '首个上级（请在 MBOM 编辑）' : '上级对象'}<select disabled={(selectedPart.usages?.length || 0) > 1} value={parentIdDraft} onChange={e => setParentIdDraft(e.target.value)}><option value="">装配体：{project?.name}</option>{project?.parts.filter(p => p.id !== selectedPart.id).map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label><button className="soft-button full" onClick={() => saveDetails(false)} disabled={!!busy}>保存属性</button></>}</div>{selectedPart && <div className="inspector-section"><div className="section-title">资源文件 <FileText size={15} /></div><input ref={resourceInput} hidden type="file" accept=".pdf,.dwg,.dxf" onChange={e => { const f=e.target.files?.[0]; if(f) uploadResource(f); e.currentTarget.value='' }} />{selectedPart.resources?.map(r => <a className="resource-link" key={r.id} href={`/api/resources/${r.id}`} target="_blank" rel="noreferrer"><FileText size={14} /><span>{r.name}</span><small>{r.kind}</small></a>)}<button className="add-row" onClick={() => resourceInput.current?.click()}><Plus size={15} /> 上传 PDF / DWG / DXF</button></div>}<div className="inspector-section"><div className="section-title">图纸证据 <FileText size={15} /></div>{(project?.analysis.dimensions || []).slice(0, 6).map((d, i) => <div className="evidence-row" key={i}><span>{d.label}</span><small>{d.value}</small></div>)}{!project?.analysis.dimensions?.length && <p className="muted-copy">解析后显示装配图标注。</p>}</div><div className="inspector-section"><div className="section-title">复核提示 <CircleAlert size={15} /></div><ul className="review-list">{(selectedPart?.geometry.review_items || project?.analysis.review_items || ['尺寸、公差及工艺参数须核对']).map((s, i) => <li key={i}>{s}</li>)}</ul></div></>}
              {editor === 'geometry' && selectedPart && <><div className="inspector-section"><div className="section-title">二维外形尺寸 <Settings2 size={15} /></div>{['plate', 'tube'].includes(geometry.shape_type || '') ? <><p className="hint">当前单件成品尺寸，单位 mm。空心筒体不包含轴头、闷板。</p>{(['outer_diameter_mm','inner_diameter_mm', geometry.shape_type === 'tube' ? 'overall_length_mm' : 'thickness_mm'] as const).map((key,i) => <label className="field-label" key={key}>{['成品外径','成品内孔',geometry.shape_type === 'tube' ? '筒体长度' : '厚度'][i]}<input type="number" min="0" value={geometry[key] ?? ''} onChange={e => setGeometry({...geometry,[key]:e.target.value === '' ? null : Number(e.target.value)})} /></label>)}</> : <><p className="hint">按轴线从左到右编辑各段长度与直径，单位 mm。</p><label className="field-label">总长<input type="number" min="0" value={geometry.overall_length_mm ?? ''} onChange={e => setGeometry({ ...geometry, overall_length_mm: e.target.value === '' ? null : Number(e.target.value) })} /></label>{(geometry.segments || []).map((seg, i) => <div className="segment-card" key={i}><div className="segment-head"><b>段 {String(i+1).padStart(2, '0')}</b><button title="删除此段" onClick={() => setGeometry({ ...geometry, segments: geometry.segments?.filter((_, n) => n !== i) })}><X size={14} /></button></div><div className="segment-inputs"><label>长度<input type="number" min="0" value={seg.length_mm ?? ''} onChange={e => setGeometry({ ...geometry, segments: geometry.segments?.map((x,n) => n === i ? {...x, length_mm: e.target.value === '' ? null : Number(e.target.value)} : x) })} /></label><label>直径<input type="number" min="0" value={seg.diameter_mm ?? ''} onChange={e => setGeometry({ ...geometry, segments: geometry.segments?.map((x,n) => n === i ? {...x, diameter_mm: e.target.value === '' ? null : Number(e.target.value)} : x) })} /></label></div><small>{seg.basis || '人工录入'}</small></div>)}<button className="add-row" onClick={() => setGeometry({ ...geometry, segments: [...(geometry.segments || []), { length_mm: null, diameter_mm: null, basis: '用户指定' }] })}><Plus size={15} /> 添加轮廓段</button></>}{(['outer_tolerance','inner_tolerance','length_tolerance'] as const).map((key,i)=><label className="field-label" key={key}>{['外径公差','内孔公差','长度公差'][i]}<input value={geometry[key]||''} placeholder="例如：+0.04/0 或 ±0.02" onChange={e=>setGeometry({...geometry,[key]:e.target.value})}/></label>)}<label className="field-label">图面技术要求（每行一条，仅制造验收要求）<textarea value={(geometry.drawing_notes||[]).join('\n')} onChange={e=>setGeometry({...geometry,drawing_notes:e.target.value.split('\n').filter(Boolean)})}/></label></div><ManufacturingEditor value={geometry.manufacturing || {}} onChange={value => setGeometry({ ...geometry, manufacturing: value })} dimensions={['tube','plate'].includes(geometry.shape_type || '') ? [
                {value:'outer_diameter_mm',label:'外圆径向'}, {value:'inner_diameter_mm',label:'内孔径向'},
                {value:geometry.shape_type === 'tube' ? 'overall_length_mm' : 'thickness_mm',label:'端面轴向'}
              ] : (geometry.segments || []).flatMap((_,i) => [{value:`segments.${i}.diameter_mm`,label:`段 ${i+1} 外圆`},{value:`segments.${i}.length_mm`,label:`段 ${i+1} 长度`}])} /><div className="inspector-section"><label className="field-label">技术特性（每行一项）<textarea value={(geometry.features || []).join('\n')} onChange={e => setGeometry({ ...geometry, features: e.target.value.split('\n').filter(Boolean) })} /></label><label className="field-label">公差与配合（每行一项）<textarea value={(geometry.tolerances || []).join('\n')} onChange={e => setGeometry({ ...geometry, tolerances: e.target.value.split('\n').filter(Boolean) })} /></label><label className="field-label">待复核项（每行一项）<textarea value={(geometry.review_items || []).join('\n')} onChange={e => setGeometry({ ...geometry, review_items: e.target.value.split('\n').filter(Boolean) })} /></label><button className="primary-button full" disabled={!!busy} onClick={() => saveDetails(true)}><Check size={15} /> 保存尺寸与放量并更新预览</button></div></>}
              {editor === 'process' && <><div className="inspector-section"><div className="section-title">工序编辑 <ClipboardList size={15} /></div><p className="hint">编辑后保存到当前对象，流程单 PDF 会同步更新。</p>{(process.steps || []).map((step, i) => <div className="step-edit" key={i}><div className="segment-head"><b>{String(i+1).padStart(2, '0')} · {step.operation || '新工序'}</b><button onClick={() => setProcess({ ...process, steps: process.steps?.filter((_, n) => n !== i).map((s,n) => ({...s, seq:n+1})) })}><X size={14} /></button></div><label>工序<input value={step.operation} onChange={e => setProcess({ ...process, steps: process.steps?.map((s,n) => n===i ? {...s,operation:e.target.value}:s) })} /></label><label>设备<input value={step.equipment} onChange={e => setProcess({ ...process, steps: process.steps?.map((s,n) => n===i ? {...s,equipment:e.target.value}:s) })} /></label><label>说明<textarea value={step.description} onChange={e => setProcess({ ...process, steps: process.steps?.map((s,n) => n===i ? {...s,description:e.target.value}:s) })} /></label><label>检验<input value={step.inspection} onChange={e => setProcess({ ...process, steps: process.steps?.map((s,n) => n===i ? {...s,inspection:e.target.value}:s) })} /></label></div>)}<button className="add-row" onClick={() => setProcess({ ...process, steps: [...(process.steps || []), {seq:(process.steps?.length || 0)+1,operation:'',equipment:'',description:'',inspection:''}] })}><Plus size={15} /> 添加工序</button><button className="primary-button full" disabled={!!busy || !(process.steps?.length)} onClick={saveProcess}><Check size={15} /> 保存工序编辑并更新流程单</button></div></>}
            </div>
            <div className="download-area"><div className="section-title">导出文件 <ArrowDownToLine size={15} /></div><div className="downloads">{selectedPart?.drawing_pdf && <a href={`/api/parts/${selectedPart.id}/files/pdf`} download><FileDown size={15} /> 图纸 PDF</a>}{selectedPart?.drawing_dxf && <a href={`/api/parts/${selectedPart.id}/files/dxf`} download><FileDown size={15} /> 图纸 DXF</a>}{selectedPart?.drawing_dwg && <a href={`/api/parts/${selectedPart.id}/files/dwg`} download><FileDown size={15} /> 图纸 DWG</a>}{selectedPart?.process.pdf_path && <a href={`/api/parts/${selectedPart.id}/files/process`} download><FileDown size={15} /> 工艺 PDF</a>}{selectedPart?.process.xlsx_path && <a href={`/api/parts/${selectedPart.id}/files/process-xlsx`} download><FileDown size={15} /> 工艺 Excel</a>}{!selectedPart && project?.analysis.assembly_process?.pdf_path && <a href={`/api/projects/${project.id}/process.pdf`} download><FileDown size={15} /> 装配工艺 PDF</a>}{!selectedPart && project?.analysis.assembly_process?.xlsx_path && <a href={`/api/projects/${project.id}/process.xlsx`} download><FileDown size={15} /> 装配工艺 Excel</a>}{!selectedPart?.drawing_pdf && !activeProcess?.pdf_path && <small>生成后可在此导出</small>}</div>{selectedPart?.geometry.dwg_warning && <p className="converter-note">{String(selectedPart.geometry.dwg_warning)}</p>}{!status?.dwg_available && selectedPart?.drawing_dxf && <p className="converter-note">DWG 使用 LibreDWG 转换，请先安装开源转换工具。</p>}</div>
          </aside></div>
        </section>
      </div> : <div className="library-view"><div className="library-top"><div><div className="eyebrow">PARTS & ASSEMBLIES</div><h1>零件管理</h1><p>维护型谱、上下级关系、图纸资源与工艺流程。</p></div><button className="primary-button" onClick={() => setNewPart(true)} disabled={!project}><Plus size={16} /> 新增零件</button></div><div className="library-cards"><div><FolderTree size={20} /><span>当前项目零件</span><b>{project?.parts.length || 0}</b></div><div><Check size={20} /><span>已归档</span><b>{project?.parts.filter(p => p.status === '已归档').length || 0}</b></div><div><ClipboardList size={20} /><span>工艺流程</span><b>{project?.parts.filter(p => p.process.steps?.length).length || 0}</b></div></div>{project && <div className="assembly-library-card"><div><b>{project.name} · 装配体</b><small>{project.drawing_no || '图号待确认'} · {project.stage}</small></div><div>{project.analysis.assembly_process?.pdf_path && <a href={`/api/projects/${project.id}/process.pdf`} download>下载装配工艺 PDF</a>}{project.analysis.assembly_process?.xlsx_path && <a href={`/api/projects/${project.id}/process.xlsx`} download>下载 Excel</a>}<button className="outline-button" onClick={() => { setPartId(null); setView('workspace'); setPreview('process') }}>查看装配工艺</button></div></div>}<section className="memory-panel"><div className="memory-head"><div><h2>工程记忆</h2><p>标准引用、历史零件图和工艺卡会在生成时按名称检索。</p></div><div className="memory-actions"><input ref={drawingMemoryInput} hidden type="file" accept=".pdf,application/pdf" onChange={e => { const file=e.target.files?.[0]; if(file) uploadMemory(file,'drawing_example'); e.currentTarget.value='' }} /><input ref={processMemoryInput} hidden type="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" onChange={e => { const file=e.target.files?.[0]; if(file) uploadMemory(file,'process_example'); e.currentTarget.value='' }} /><button className="outline-button" onClick={() => drawingMemoryInput.current?.click()} disabled={!!busy}><Plus size={14} /> 历史图纸 PDF</button><button className="outline-button" onClick={() => processMemoryInput.current?.click()} disabled={!!busy}><Plus size={14} /> 历史工艺 Excel</button></div></div><details><summary>查看已载入的 {memory.length} 条知识</summary><div className="memory-items">{memory.map(item => <div key={item.id}><span>{item.category === 'standard' ? '标准' : item.category === 'drawing_example' ? '图纸' : '工艺'}</span><b>{item.title}</b><small>{item.category === 'standard' ? '制图与公差标准引用' : item.source}{item.step_count ? ` · ${item.step_count} 道工序` : ''}</small>{item.category === 'standard' && <a href={item.source} target="_blank" rel="noreferrer">标准页面</a>}{item.has_file && <a href={`/api/memory/${item.id}/file`} target="_blank" rel="noreferrer">查看</a>}</div>)}</div></details></section><div className="library-table-wrap"><div className="library-table-head"><h2>零部件清单</h2><div className="search-box"><Search size={16} /><input placeholder="搜索名称、图号或材料" value={libraryQuery} onChange={e => setLibraryQuery(e.target.value)} /></div></div><table className="library-table"><thead><tr><th>名称 / 图号</th><th>层级</th><th>材料</th><th>资源</th><th>状态</th><th>更新时间</th><th></th></tr></thead><tbody>{filteredParts.map(p => <tr key={p.id} onClick={() => { setPartId(p.id); setView('workspace'); setPreview('source') }}><td><div className="item-name"><span className="table-icon"><Box size={17} /></span><div><b>{p.name}</b><small>{p.drawing_no || '图号待确认'}</small></div></div></td><td>{p.kind}<small className="series-label">上级：{p.usages?.map(u => `${u.parent_name} ×${u.quantity}`).join('、') || project?.name}{p.specifications?.series ? ` · ${String(p.specifications.series)}` : ''}</small></td><td>{p.material || '待确认'}</td><td><span className="resource-pill">{[p.source_pdf,p.drawing_pdf,p.drawing_dxf,p.drawing_dwg,p.process.pdf_path,p.process.xlsx_path].filter(Boolean).length} 份文件</span></td><td><span className={`state-pill ${p.status === '已归档' ? 'archived' : ''}`}>{p.status}</span></td><td>{date(p.updated_at)}</td><td><ChevronRight size={17} /></td></tr>)}</tbody></table>{!filteredParts.length && <div className="library-empty">暂无匹配零件。可上传装配图并解析，或手动新增。</div>}</div></div>}
      </main>
      {error && <div className="error-toast" role="alert"><CircleAlert size={17} /><span>{error}</span><button onClick={() => setError('')}><X size={15} /></button></div>}
      {toast && <div className="success-toast" role="status"><Check size={17} />{toast}</div>}
      {pendingUpload && <div className="modal-backdrop" onMouseDown={closeUpload}><div className="modal" onMouseDown={e => e.stopPropagation()}><div className="modal-head"><div><div className="eyebrow">ASSEMBLY DRAWING</div><h2>上传装配图</h2></div><button onClick={closeUpload}><X size={19} /></button></div><p className="upload-file-name"><FileText size={15} /> {pendingUpload.name}</p><label className="field-label">装配体名称<input autoFocus value={uploadName} onChange={e => setUploadName(e.target.value)} placeholder="例如：收卷轴" /></label><label className="field-label">装配图号 <span className="required-mark">必填</span><input value={uploadDrawingNo} onChange={e => setUploadDrawingNo(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') upload() }} placeholder="例如：JX.LTJ-04-05-01" /></label><p>图号将作为项目、MBOM、零部件图纸和工艺流程单的来源标识。</p><div className="modal-actions"><button className="outline-button" onClick={closeUpload}>取消</button><button className="primary-button" disabled={!uploadName.trim() || !uploadDrawingNo.trim() || !!busy} onClick={upload}><UploadCloud size={15} /> 上传并解析</button></div></div></div>}
      {newPart && <div className="modal-backdrop" onMouseDown={() => setNewPart(false)}><div className="modal" onMouseDown={e => e.stopPropagation()}><div className="modal-head"><div><div className="eyebrow">PART HIERARCHY</div><h2>新增零部件</h2></div><button onClick={() => setNewPart(false)}><X size={19} /></button></div><p>上级对象：{selectedPart?.name || project?.name}</p><label className="field-label">零部件名称<input autoFocus value={newPartName} onChange={e => setNewPartName(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') createPart() }} placeholder="例如：左端轴头" /></label><div className="modal-actions"><button className="outline-button" onClick={() => setNewPart(false)}>取消</button><button className="primary-button" disabled={!newPartName.trim() || !!busy} onClick={createPart}>创建零部件</button></div></div></div>}
      {reportOpen && selectedPart && <EngineeringReport partId={selectedPart.id} name={selectedPart.name} onClose={()=>setReportOpen(false)} />}
      {cadOpen && selectedPart && <CadEditor partId={selectedPart.id} onClose={() => setCadOpen(false)} onSaved={() => { void loadProject(project!.id, true); setRevision(v=>v+1); flash('CAD 新版本已保存，请重新审核图纸'); }} />}
    </div>
}

createRoot(document.getElementById('root')!).render(<CloudSession><App /></CloudSession>)








