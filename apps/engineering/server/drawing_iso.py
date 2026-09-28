"""A single millimetre scene drives SVG, PDF and DXF sheets.

The supported profiles are straight tubes, circular plates and stepped shafts.
Complex features remain explicit review notes, never invented geometry.
"""
import html
import math
import re
from pathlib import Path
import ezdxf
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from .drawing_standard import PRIMARY_STANDARD, PROFILE, choose_scale, dimensional_text, validate_geometry
from .manufacturing import geometry_blockers, manufacturing_summary

pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
FONT = 'STSong-Light'
DASH = {'CENTER': [8, 2, 1, 2], 'HIDDEN': [3, 2]}
WIDTH = {'OUTLINE': .7, 'FRAME': .7, 'HATCH': .25}


class Scene:
    def __init__(self):
        self.items = []

    def line(self, x1, y1, x2, y2, layer='DIMENSIONS'):
        self.items.append(dict(type='line', points=[(x1,y1),(x2,y2)], layer=layer))

    def poly(self, points, layer='OUTLINE', fill=False):
        self.items.append(dict(type='poly', points=points, layer=layer, fill=fill))

    def rect(self, x, y, w, h, layer='OUTLINE'):
        self.poly([(x,y),(x+w,y),(x+w,y+h),(x,y+h)], layer)

    def circle(self, x, y, radius):
        self.items.append(dict(type='circle', x=x,y=y,r=radius,layer='OUTLINE'))

    def text(self, x,y,value,size=3.5,align='left',layer='TEXT',rotation=0):
        # Deviation pairs use stacked limits, not a slash in the printed size.
        match=re.fullmatch(r'(Φ?[0-9.]+)([+-][0-9.]+)/([+-]?[0-9.]+)',str(value))
        if match:
            nominal,upper,lower=match.groups(); small=size*.7
            main_width=pdfmetrics.stringWidth(nominal,FONT,size)
            width=main_width+max(pdfmetrics.stringWidth(upper,FONT,small),pdfmetrics.stringWidth(lower,FONT,small))+1
            start=-width/2 if align=='center' else -width if align=='right' else 0
            theta=math.radians(rotation)
            for dx,dy,label,font_size in ((start,0,nominal,size),(start+main_width+1,-1.5,upper,small),(start+main_width+1,1.6,lower,small)):
                xx=x+dx*math.cos(theta)-dy*math.sin(theta);yy=y+dx*math.sin(theta)+dy*math.cos(theta)
                self.items.append(dict(type='text',x=xx,y=yy,value=label,size=font_size,align='left',layer=layer,rotation=rotation))
            return
        self.items.append(dict(type='text',x=x,y=y,value=str(value),size=size,align=align,layer=layer,rotation=rotation))

    def arrow(self, x,y,angle):
        # Tip lies on the measured boundary. Triangle extends INTO dimension span.
        ux,uy = math.cos(angle), math.sin(angle)
        length,half=3.2,.55
        self.poly([(x,y),(x+length*ux-half*uy,y+length*uy+half*ux),
                   (x+length*ux+half*uy,y+length*uy-half*ux)], 'DIMENSIONS', True)

    def dim(self, x1,x2,source_y,y,label):
        direction=1 if y>source_y else -1
        self.line(x1,source_y+direction,x1,y+direction*2); self.line(x2,source_y+direction,x2,y+direction*2)
        self.line(x1,y,x2,y)
        self.arrow(x1,y,0); self.arrow(x2,y,math.pi)
        self.text((x1+x2)/2,y-2,label,3.5,'center')

    def vdim(self,y1,y2,source_x,x,label):
        direction=1 if x>source_x else -1
        self.line(source_x+direction,y1,x+direction*2,y1);self.line(source_x+direction,y2,x+direction*2,y2)
        self.line(x,y1,x,y2);self.arrow(x,y1,math.pi/2);self.arrow(x,y2,-math.pi/2)
        self.text(x-2,(y1+y2)/2,label,3.5,'center',rotation=-90)

    def leader(self, x,y,tx,ty,label):
        self.line(x,y,tx,ty+1)
        self.arrow(x,y,math.atan2(ty+1-y,tx-x))
        self.text(tx,ty,label,3.5)

    def hatch_rect(self,x,y,w,h):
        if h <= 0: return
        # Clip 45-degree line segments mathematically, not with renderer masks.
        t=-h
        while t < w:
            start=max(0,-t); end=min(h,w-t)
            if end > start:
                self.line(x+t+start,y+h-start,x+t+end,y+h-end,'HATCH')
            t+=3
        self.rect(x,y,w,h)


def wrap(value, width=370, size=3.5):
    lines=[]; current=''
    for char in str(value):
        if char=='\n' or pdfmetrics.stringWidth(current+char,FONT,size) > width:
            lines.append(current);current='' if char=='\n' else char
        else: current+=char
    if current: lines.append(current)
    return lines or ['']


def title(scene,part,scale,page,total):
    scene.rect(20,10,390,277,'FRAME')
    scene.text(25,17,f'{PRIMARY_STANDARD["code"]} | 单位 mm | 成品尺寸与放量分别列示',2.5)
    scene.text(405,17,'已审核' if part.get('geometry',{}).get('approval_status')=='approved' else '工程草案 / 待审核',3.5,'right','REVIEW')
    x,y=230,247
    scene.rect(x,y,180,40,'TITLE')
    for yy in (261,274): scene.line(x,yy,410,yy,'TITLE')
    for xx in (310,365): scene.line(xx,y,xx,287,'TITLE')
    cells=[(233,251,'名称',part.get('name') or '待编制',74),
           (313,251,'图号',part.get('drawing_no') or '待编制',47),
           (368,251,'版本 / 张次',f'A/0   {page}/{total}',38),
           (233,265,'材料',part.get('material') or '待确认',74),
           (313,265,'比例',scale,47),(368,265,'幅面 / 单位','A3 / mm',38),
           (233,278,'标准依据',PRIMARY_STANDARD['code'],74),
           (313,278,'视图配置','第一角法',47),(368,278,'制图状态','已审核' if part.get('geometry',{}).get('approval_status') == 'approved' else '待审核',38)]
    for xx,yy,label,value,width in cells:
        scene.text(xx,yy,label,2.5)
        # Long identifiers wrap instead of spilling into the next title cell.
        for n,line in enumerate(wrap(value,width,3.0)[:2]): scene.text(xx,yy+5+n*3.5,line,3)
    scene.rect(20,247,210,40,'TITLE')
    scene.line(20,256,230,256,'TITLE');scene.line(43,247,43,287,'TITLE')
    scene.text(31,253,'工序',3,'center');scene.text(133,253,'核心工艺路径',3,'center')
    route=((part.get('geometry') or {}).get('manufacturing') or {}).get('core_route') or []
    for i in range(3):
        yy=256+i*10
        if i:scene.line(20,yy,230,yy,'TITLE')
        scene.text(31,yy+6,str(i+1) if i<len(route) else '—',3,'center')
        # Route names only; full operations remain in the process sheet.
        if i<len(route):
            lines=wrap(route[i],179,3)
            for j,line in enumerate(lines[:2]):scene.text(47,yy+4+j*3.5,line,3)


def make_scenes(part):
    g=part.get('geometry') or {}; scene=Scene(); scale='—'
    blockers=geometry_blockers(g)
    # Invalid allowances do not prevent viewing finished geometry.
    shape_errors=geometry_blockers({**g,'manufacturing':{}})
    if shape_errors:
        scene.text(35,75,'几何待补充，尚不能生成完整制造轮廓',5)
        for i,line in enumerate(shape_errors): scene.text(35,87+6*i,line)
    elif g.get('shape_type') in ('tube','plate'):
        tube=g['shape_type']=='tube'
        length=g['overall_length_mm'] if tube else g['thickness_mm']
        od,bore=g['outer_diameter_mm'],g['inner_diameter_mm']
        # Long tubes use a broken view: cross-section remains readable at a
        # conventional scale, and all displayed axial dimensions stay true size.
        ratio,scale=choose_scale(od,od,85,85)
        broken=tube and length*ratio>285
        display_length=285 if broken else min(length*ratio,285)
        if not broken:
            ratio,scale=choose_scale(length,od,285,85)
            display_length=length*ratio
        r,ri=od*ratio/2,bore*ratio/2
        x1=75; x2=x1+display_length; cy=112
        left_end=(x1+x2)/2-4; right_start=left_end+8
        intervals=[(x1,left_end),(right_start,x2)] if broken else [(x1,x2)]
        for start,end in intervals:
            if ri:
                scene.hatch_rect(start,cy-r,end-start,r-ri)
                scene.hatch_rect(start,cy+ri,end-start,r-ri)
            else: scene.hatch_rect(start,cy-r,end-start,2*r)
        scene.line(x1-5,cy,x2+5,cy,'CENTER')
        if broken:
            for xx in (left_end,right_start):
                pts=[(xx,cy-r-4),(xx,cy-3),(xx+2,cy-1),(xx-2,cy+1),(xx,cy+3),(xx,cy+r+4)]
                for first,last in zip(pts,pts[1:]):scene.line(*first,*last,'DIMENSIONS')
            scale+='（断开）'
        scene.dim(x1,x2,cy-r,cy-r-23,dimensional_text(length,g.get('length_tolerance','')))
        outer_label=dimensional_text(od,g.get('outer_tolerance',''),'Φ')
        if g.get('outer_reference'): outer_label='('+outer_label+')'
        scene.vdim(cy-r,cy+r,x1,x1-27,outer_label)
        if ri:scene.vdim(cy-ri,cy+ri,x1,x1-14,dimensional_text(bore,g.get('inner_tolerance',''),'Φ'))
        for zone in g.get('machining_zones') or []:
            depth=zone.get('length_mm')
            if not isinstance(depth,(float,int)) or depth<=0 or depth>length/2:continue
            offset=depth*ratio
            # A break only removes an unfeatured middle region. If a machining
            # boundary falls inside the omitted region, don't imply its position.
            if broken and offset>left_end-x1-3:continue
            endpoint=x1+offset if zone.get('end')=='left' else x2-offset
            scene.line(endpoint,cy-ri,endpoint,cy+ri,'OUTLINE')
            scene.dim(x1,endpoint,cy-r,cy-r-10,dimensional_text(depth)) if zone.get('end')=='left' else scene.dim(endpoint,x2,cy-r,cy-r-10,dimensional_text(depth))
        chamfer=g.get('chamfer_mm')
        if isinstance(chamfer,(int,float)) and chamfer>0:
            scene.leader(x1,cy-ri,x1+8,cy-ri+12,'C'+str(chamfer))
            scene.leader(x2,cy-ri,x2+8,cy-ri+12,'C'+str(chamfer))
        scene.text((x1+x2)/2,cy+r+12,'纵向全剖视图' if tube else '全剖主视图',3.5,'center')
    else:
        spans=g['segments']; length=sum(s['length_mm'] for s in spans); diameter=max(s['diameter_mm'] for s in spans)
        ratio,scale=choose_scale(length,diameter,305,75)
        x,cy=45,90; upper=[];lower=[]
        for span in spans:
            end=x+span['length_mm']*ratio; r=span['diameter_mm']*ratio/2
            upper.extend([(x,cy-r),(end,cy-r)]);lower.extend([(x,cy+r),(end,cy+r)])
            x=end
        scene.poly(upper+list(reversed(lower)))
        scene.line(38,cy,x+7,cy,'CENTER')
        x=45; bottom=cy+diameter*ratio/2
        for i,span in enumerate(spans):
            end=x+span['length_mm']*ratio
            uncertainty='*' if span.get('basis') in ('差值推算','历史样例估算','推算','待确认') else ''
            scene.dim(x,end,bottom,bottom+12+(i%3)*10,dimensional_text(span['length_mm'],span.get('length_tolerance',''))+uncertainty)
            label=dimensional_text(span['diameter_mm'],span.get('diameter_tolerance',''),'Φ')+str(span.get('fit') or '')+uncertainty
            # Keep a full dimension table; also annotate diameters where the span
            # has enough paper space for a legible leader without label collisions.
            scene.text((x+end)/2,cy-span['diameter_mm']*ratio/2-4,str(i+1),2.5,'center')
            label_width = pdfmetrics.stringWidth(label, FONT, 3.5)
            if end-x >= label_width+8:
                scene.leader((x+end)/2,cy-span['diameter_mm']*ratio/2,
                             (x+end-label_width)/2,cy-diameter*ratio/2-12-(i%2)*8,label)
            x=end
        if len(spans)>1: scene.dim(45,x,bottom,bottom+45,dimensional_text(length))
    # Reasoning, uncertainty and process histories belong to the review panel,
    # not to the manufacturing drawing. Only actual drawing requirements print.
    notes=[]
    for item in (g.get('drawing_notes') if g.get('drawing_notes') is not None else g.get('technical_requirements')) or []:
        value=str(item).strip()
        if value and value not in notes:notes.append(value)
    for allowance in (g.get('manufacturing') or {}).get('allowances') or []:
        if allowance.get('calculation'):notes.append('毛坯留量：'+allowance['calculation'])
    # Overflow is paginated identically for all formats, never truncated.
    # End projection supplements the longitudinal section, with an explicit scale.
    note_width = 370
    if not shape_errors and g.get('shape_type') in ('tube', 'plate'):
        end_ratio, end_scale = choose_scale(od, od, 48, 48)
        cx, ey = 367, 206
        scene.circle(cx, ey, od*end_ratio/2)
        if bore: scene.circle(cx, ey, bore*end_ratio/2)
        scene.line(cx-28, ey, cx+28, ey, 'CENTER')
        scene.line(cx, ey-28, cx, ey+28, 'CENTER')
        scene.text(cx, 240, '端视图  '+end_scale, 3, 'center')
        note_width = 285
    scenes=[scene]; y=183
    for note in notes:
        for line in wrap(note,note_width,3.5):
            if y>236:
                scene=Scene();scenes.append(scene);y=30
                scene.text(25,23,'技术要求 / 制造放量（续页）',5)
            scene.text(27,y,line);y+=5.2
        y+=1.5
    for i,s in enumerate(scenes,1): title(s,part,scale,i,len(scenes))
    return scenes


def svg_preview_iso(part):
    scenes=make_scenes(part); out=[f'<svg xmlns="http://www.w3.org/2000/svg" width="420mm" height="{297*len(scenes)}mm" viewBox="0 0 420 {297*len(scenes)}">']
    for page,scene in enumerate(scenes):
        out.append(f'<g transform="translate(0 {page*297})"><rect width="420" height="297" fill="white"/>')
        for a in scene.items:
            layer=a['layer']; color='#8a4e18' if layer=='REVIEW' else '#111'
            attrs=f'stroke="{color}" stroke-width="{WIDTH.get(layer,.35)}" fill="none"'
            if layer in DASH: attrs+=' stroke-dasharray="'+' '.join(map(str,DASH[layer]))+'"'
            if a['type']=='text':
                anchor={'left':'start','center':'middle','right':'end'}[a['align']]
                out.append(f'<text x="{a["x"]}" y="{a["y"]}" font-family="Microsoft YaHei,sans-serif" font-size="{a["size"]}" fill="{color}" text-anchor="{anchor}" transform="rotate({a.get('rotation',0)} {a["x"]} {a["y"]})">{html.escape(a["value"])}</text>')
            elif a['type']=='line':
                (x1,y1),(x2,y2)=a['points'];out.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" {attrs}/>')
            elif a['type']=='circle': out.append(f'<circle cx="{a["x"]}" cy="{a["y"]}" r="{a["r"]}" {attrs}/>')
            else:
                points=' '.join(f'{x},{y}' for x,y in a['points'])
                if a.get('fill'): attrs=attrs.replace('fill="none"',f'fill="{color}"')
                out.append(f'<polygon points="{points}" {attrs}/>')
        out.append('</g>')
    return ''.join(out)+'</svg>'


def draw_pdf_iso(part,output):
    c=canvas.Canvas(str(output),pagesize=(420*mm,297*mm))
    c.setTitle(f"{part.get('name','')} - {PRIMARY_STANDARD['code']}")
    for scene in make_scenes(part):
        for a in scene.items:
            layer=a['layer'];c.setStrokeColorRGB(0,0,0);c.setFillColorRGB(0,0,0)
            c.setLineWidth(WIDTH.get(layer,.35)*mm);c.setDash([v*mm for v in DASH.get(layer,[])])
            if a['type']=='text':
                c.setFont(FONT,a['size']*mm)
                fn={'left':c.drawString,'center':c.drawCentredString,'right':c.drawRightString}[a['align']]
                c.saveState();c.translate(a['x']*mm,(297-a['y'])*mm);c.rotate(-a.get('rotation',0));fn(0,0,a['value']);c.restoreState()
            elif a['type']=='line':
                (x1,y1),(x2,y2)=a['points'];c.line(x1*mm,(297-y1)*mm,x2*mm,(297-y2)*mm)
            elif a['type']=='circle':c.circle(a['x']*mm,(297-a['y'])*mm,a['r']*mm)
            else:
                path=c.beginPath(); x,y=a['points'][0];path.moveTo(x*mm,(297-y)*mm)
                for x,y in a['points'][1:]:path.lineTo(x*mm,(297-y)*mm)
                path.close();c.drawPath(path,stroke=1,fill=int(a.get('fill',False)))
        c.showPage()
    c.save()


def draw_dxf_iso(part,output):
    g=part.get('geometry') or {}
    if geometry_blockers({**g,'manufacturing':{}}): return False
    doc=ezdxf.new('R2018',setup=True);doc.units=ezdxf.units.MM
    for name in ('OUTLINE','DIMENSIONS','TEXT','REVIEW','HATCH','TITLE','FRAME','CENTER','HIDDEN'):
        doc.layers.new(name,dxfattribs={'color':7,'lineweight':round(WIDTH.get(name,.35)*100),'linetype':name if name in DASH else 'CONTINUOUS'})
    # Sheet layouts contain exactly the same scene as SVG/PDF. Model space is 1:1.
    for i,scene in enumerate(make_scenes(part),1):
        sheet=doc.layouts.new(f'A3-{i}')
        sheet.page_setup(size=(420,297),margins=(0,0,0,0),units='mm')
        for a in scene.items:
            attrs={'layer':a['layer']}
            if a['type']=='text':
                t=sheet.add_text(a['value'],dxfattribs={**attrs,'height':a['size'],'rotation':-a.get('rotation',0)})
                from ezdxf.enums import TextEntityAlignment
                align={'left':TextEntityAlignment.LEFT,'center':TextEntityAlignment.CENTER,'right':TextEntityAlignment.RIGHT}[a['align']]
                t.set_placement((a['x'],297-a['y']),align=align)
            elif a['type']=='circle':sheet.add_circle((a['x'],297-a['y']),a['r'],dxfattribs=attrs)
            elif a['type']=='line':sheet.add_line(*[(x,297-y) for x,y in a['points']],dxfattribs=attrs)
            elif a.get('fill'):
                pts=[(x,297-y) for x,y in a['points']];sheet.add_solid(pts,dxfattribs=attrs)
            else:sheet.add_lwpolyline([(x,297-y) for x,y in a['points']],close=True,dxfattribs=attrs)
    msp=doc.modelspace(); shape=g.get('shape_type')
    if shape in ('tube','plate'):
        length=g['overall_length_mm'] if shape=='tube' else g['thickness_mm'];od=g['outer_diameter_mm'];bore=g['inner_diameter_mm']
        for y1,y2 in ((-od/2,-bore/2),(bore/2,od/2)):
            msp.add_lwpolyline([(0,y1),(length,y1),(length,y2),(0,y2)],close=True,dxfattribs={'layer':'OUTLINE'})
        center=(-od,0)
        msp.add_circle(center,od/2,dxfattribs={'layer':'OUTLINE'})
        if bore:msp.add_circle(center,bore/2,dxfattribs={'layer':'OUTLINE'})
        msp.add_linear_dim(base=(0,-od/2-15),p1=(0,-od/2),p2=(length,-od/2),dimstyle='ISO-25',dxfattribs={'layer':'DIMENSIONS'}).render()
    else:
        spans=g['segments'];x=0;top=[]
        for span in spans:
            r=span['diameter_mm']/2; top.extend([(x,r),(x+span['length_mm'],r)]);x+=span['length_mm']
        msp.add_lwpolyline(top+[(x,-y) for x,y in reversed(top)],close=True,dxfattribs={'layer':'OUTLINE'})
        od=max(s['diameter_mm'] for s in spans);x=0;length=sum(s['length_mm'] for s in spans)
        for i,span in enumerate(spans):
            dim=msp.add_linear_dim(base=(x,-od/2-15-i%2*10),p1=(x,-span['diameter_mm']/2),p2=(x+span['length_mm'],-span['diameter_mm']/2),
                                  text=dimensional_text(span['length_mm'],span.get('length_tolerance','')),dimstyle='ISO-25',dxfattribs={'layer':'DIMENSIONS'})
            dim.render();x+=span['length_mm']
        msp.add_linear_dim(base=(0,-od/2-40),p1=(0,-od/2),p2=(length,-od/2),dimstyle='ISO-25',dxfattribs={'layer':'DIMENSIONS'}).render()
    msp.add_line((-10,0),(length+10,0),dxfattribs={'layer':'CENTER'})
    doc.saveas(output);return True
