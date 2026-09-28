"""LibreDWG CLI adapter. Originals stay untouched; exports require a round-trip gate."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from collections import Counter

import ezdxf
from ezdxf import recover


class ConversionError(RuntimeError):
    pass


def executable(name):
    suffix='.exe' if os.name=='nt' else ''
    configured=os.getenv('LIBREDWG_BIN','')
    local=Path(__file__).resolve().parents[1]/'.tools/libredwg/win64'
    candidates=[Path(configured)/(name+suffix)] if configured else []
    found=shutil.which(name)
    if found:candidates.append(Path(found))
    if os.name=='nt':candidates.append(local/(name+suffix))
    return next((p.resolve() for p in candidates if p.is_file()),None)


def available():
    from .native_rpc import remote_enabled
    if remote_enabled(): return True
    return executable('dwg2dxf') is not None


def run(name, arguments, folder):
    exe=executable(name)
    if not exe:raise ConversionError('未安装 LibreDWG 转换工具，请配置 LIBREDWG_BIN')
    # Native parser receives only fixed ASCII filenames in a disposable workspace.
    # File logging avoids unbounded captured stdout/stderr on a malformed drawing.
    log=Path(folder)/(name+'.log')
    try:
        with log.open('wb') as stream:
            result=subprocess.run([str(exe),*arguments],cwd=folder,stdout=stream,stderr=stream,
                                  timeout=90,check=False)
    except subprocess.TimeoutExpired as exc:
        raise ConversionError('LibreDWG 转换超过 90 秒，已停止；原文件未修改') from exc
    if result.returncode:
        raise ConversionError(f'LibreDWG {name} 转换失败（退出码 {result.returncode}），原文件未修改')
    return log.stat().st_size>0


def load_checked(path):
    try:
        doc,audit=recover.readfile(path)
        if audit.errors:raise ConversionError('转换后的 DXF 存在无法修复的结构错误')
        if not any(len(layout) for layout in doc.layouts):
            raise ConversionError('转换结果不含可编辑图元，已拒绝空图纸')
        return doc,len(audit.fixes)
    except ConversionError:raise
    except (ValueError,OSError,ezdxf.DXFError) as exc:
        raise ConversionError('LibreDWG 输出无法解析，已拒绝无效图纸') from exc


def read_dwg(path):
    from .native_rpc import remote_enabled, call
    if remote_enabled():
        return ezdxf.readfile(call("dwg_read", (Path(path),), {}))
    with tempfile.TemporaryDirectory(prefix='dwg-import-') as folder:
        root=Path(folder)
        shutil.copyfile(path,root/'input.dwg')
        run('dwg2dxf',['-o','output.dxf','input.dwg'],root)
        doc,_=load_checked(root/'output.dxf')
        return doc


def signature(doc):
    # Compare graphic payload in every layout AND block, rather than file size or exit code.
    # Handles/owners change during format conversion; entity attributes must not disappear.
    ignored={'handle','owner','paperspace','plotstyle_handle','material_handle','visualstyle_handle'}
    def encode(value):
        if isinstance(value,float):return round(value,6)
        if isinstance(value,(str,int,bool)) or value is None:return value
        try:return [encode(v) for v in value]
        except TypeError:return str(value)
    def entity(e):
        attrs={k:encode(v) for k,v in e.dxf.all_existing_dxf_attribs().items() if k not in ignored}
        # Compare geometric values, text, annotation types and block references.
        for k in ('extrusion','thickness','color','linetype','lineweight','ltscale','invisible'):
            attrs[k]=encode(e.dxf.get(k,e.dxf.dxf_default_value(k)) if e.dxf.is_supported(k) else None)
        if e.dxftype()=='LWPOLYLINE':attrs['vertices']=encode(list(e.get_points()))
        if e.dxftype()=='MTEXT':attrs['content']=e.text
        if e.dxftype() in ('HATCH','SPLINE','MESH','LEADER'):
            from ezdxf.lldxf.tagwriter import TagCollector
            attrs['payload']=[(tag.code,encode(tag.value)) for tag in TagCollector.dxftags(e)
                              if tag.code not in (5,330,360)]
        if e.dxftype() in ('POLYLINE','INSERT'):
            attrs['children']=[entity(v) for v in (e.vertices if e.dxftype()=='POLYLINE' else e.attribs)]
        return json.dumps([e.dxftype(),attrs],sort_keys=True,ensure_ascii=False)
    return {b.name:Counter(entity(e) for e in b) for b in doc.blocks if len(b)}


def convert_dwg(dxf_path):
    from .native_rpc import remote_enabled, call
    if remote_enabled():
        try: return Path(call("dwg_write", (Path(dxf_path),), {}))
        except ValueError as exc: raise ConversionError(str(exc)) from exc
    source=Path(dxf_path)
    if not executable('dxf2dwg'):raise ConversionError('LibreDWG 写出工具不可用，已保留 DXF/PDF')
    with tempfile.TemporaryDirectory(prefix='dwg-export-') as folder:
        root=Path(folder)
        shutil.copyfile(source,root/'input.dxf')
        run('dxf2dwg',['--as','r2000','-o','output.dwg','input.dxf'],root)
        dwg=root/'output.dwg'
        if not dwg.is_file() or dwg.read_bytes()[:6]!=b'AC1015':
            raise ConversionError('LibreDWG 未生成有效的 R2000 DWG，已保留 DXF/PDF')
        run('dwg2dxf',['-o','roundtrip.dxf','output.dwg'],root)
        back,_=load_checked(root/'roundtrip.dxf')
        original=ezdxf.readfile(source)
        if (signature(original)!=signature(back) or original.units!=back.units
                or set(original.layouts.names())!=set(back.layouts.names())):
            raise ConversionError('DWG 回读校验不一致（图元、文字、尺寸或布局），已保留 DXF/PDF')
        target=source.with_suffix('.dwg')
        shutil.copyfile(dwg,target)
        return target
