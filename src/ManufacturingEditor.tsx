export type Allowance = { label?: string; dimension: string; kind: 'external' | 'bore' | 'axial'; per_side_mm: number | null; faces: number; basis: string; reason: string; blank_mm?: number | null; calculation?: string; error?: string }
export type ManufacturingPlan = { delivery_state?: string; blank_type?: string; core_route?: string[]; allowances?: Allowance[] }

type Props = { value: ManufacturingPlan; onChange: (next: ManufacturingPlan) => void; dimensions: { value: string; label: string }[] }
export function ManufacturingEditor({ value, onChange, dimensions }: Props) {
  const items = value.allowances || []
  function update(index: number, patch: Partial<Allowance>) {
    onChange({ ...value, allowances: items.map((item, i) => i === index ? { ...item, ...patch, calculation: '', blank_mm: null } : item) })
  }
  return <div className="inspector-section manufacturing-editor">
    <div className="section-title">制造方案与加工放量</div>
    <p className="hint">成品尺寸保持独立。外圆加余量，内孔减余量，轴向按加工端面数加余量；保存后自动校算。</p>
    <label className="field-label">本部件交付状态<textarea value={value.delivery_state || ''} onChange={e => onChange({ ...value, delivery_state: e.target.value })} placeholder="例如：筒体精加工及检验合格，交上层组件装配" /></label>
    <label className="field-label">毛坯类型<input value={value.blank_type || ''} onChange={e => onChange({ ...value, blank_type: e.target.value })} placeholder="无缝管、圆钢、锻件、铸件等" /></label>
    <label className="field-label">核心工艺路径（每行一步）<textarea value={(value.core_route || []).join('\n')} onChange={e => onChange({ ...value, core_route: e.target.value.split('\n') })} /></label>
    {items.map((item, index) => <div className="segment-card" key={index}>
      <div className="segment-head"><b>放量 {index + 1}</b><button type="button" aria-label={`删除放量 ${index + 1}`} onClick={() => onChange({ ...value, allowances: items.filter((_, i) => i !== index) })}>×</button></div>
      <label className="field-label">要素<select value={item.dimension} onChange={e => {
        const field = e.target.value
        update(index, { dimension: field, kind: field === 'inner_diameter_mm' ? 'bore' : field.endsWith('length_mm') || field === 'thickness_mm' ? 'axial' : 'external', faces: 2 })
      }}>{dimensions.map(d => <option key={d.value} value={d.value}>{d.label}</option>)}</select></label>
      <div className="segment-inputs"><label>单边余量 mm<input type="number" min="0" step="0.1" value={item.per_side_mm ?? ''} onChange={e => update(index, { per_side_mm: e.target.value === '' ? null : Number(e.target.value) })} /></label>
      <label>加工面数<select value={item.faces} disabled={item.kind !== 'axial'} onChange={e => update(index, { faces: Number(e.target.value) })}><option value={1}>单端</option><option value={2}>双端 / 直径两侧</option></select></label></div>
      <label className="field-label">依据<select value={item.basis} onChange={e => update(index, { basis: e.target.value })}>{['工艺建议', '用户指定', '图纸标注', '企业历史方案'].map(x => <option key={x}>{x}</option>)}</select></label>
      <label className="field-label">工艺理由<textarea value={item.reason} onChange={e => update(index, { reason: e.target.value })} placeholder="材料、毛坯、后续精加工和变形控制依据" /></label>
      <p className="hint">{item.error || item.calculation || '保存参数后计算毛坯尺寸'}</p>
    </div>)}
    <button type="button" className="add-row" disabled={!dimensions.length} onClick={() => onChange({ ...value, allowances: [...items, { dimension: dimensions[0]?.value || 'outer_diameter_mm', kind: 'external', faces: 2, per_side_mm: null, basis: '工艺建议', reason: '' }] })}>＋ 添加加工放量</button>
  </div>
}
