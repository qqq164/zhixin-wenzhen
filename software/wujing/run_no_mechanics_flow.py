"""悟净无机械流程入口；只打开SDK和调度在线任务，CPU参考模块不进入运行包。"""
import hashlib
import json
from pathlib import Path
import sys
import time

from icraft import xrt, zg330backend
from pl_runtime.processor import PlOnlyProcessor
from no_mechanics_flow import NoMechanicsLoopbackFlow

ROOT = Path(__file__).resolve().parent
device = None
processor = None
flow = None
try:
    manifest = json.loads((ROOT/'upload_manifest.json').read_text(encoding='utf-8'))
    for name, expected in manifest.items():
        if hashlib.sha256((ROOT/name).read_bytes()).hexdigest() != expected:
            raise ValueError('上传包摘要不符：'+name)
    schedule = json.loads((ROOT/'schedule.json').read_text(encoding='utf-8'))
    device = xrt.Device.Open('axi://zg330aiu?npu=0x40000000&dma=0x80000000')
    processor = PlOnlyProcessor(device, schedule)
    flow = NoMechanicsLoopbackFlow(ROOT/'result', schedule, processor)
    flow.report['boot_id'] = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    flow.run()
    forbidden = ('fdw_pl_dsp_reference', 'fdw_dsp_analyze', 'fdw_point_features', 'numpy')
    if any(name in sys.modules for name in forbidden):
        raise RuntimeError('正式在线入口导入了CPU参考算法')
    flow.report['cpu_reference_modules_loaded'] = []
except Exception as error:
    if flow is not None:
        flow.report['passed'] = False
        flow.report['runner_error'] = str(error)
        flow.save()
    else:
        (ROOT/'runner_error.json').write_text(json.dumps({'passed': False, 'error': str(error)}, ensure_ascii=False), encoding='utf-8')
    print('FDW_FLOW_RUNNER_ERROR '+str(error), flush=True)
finally:
    if processor is not None:
        while processor.backend.quarantine:
            print('FDW_FLOW_DDR_QUARANTINE_HELD', flush=True)
            request = ROOT/'retry_drain.request'
            if request.exists():
                request.unlink()
                try:
                    processor.backend.release_quarantine()
                except Exception as error:
                    print('FDW_FLOW_DRAIN_RETRY '+str(error), flush=True)
            time.sleep(0.2)
    if device is not None:
        xrt.Device.Close(device)
        if flow is not None:
            flow.report['device_closed'] = True
            flow.save()
    print('FDW_NO_MECH_FLOW_END '+str(flow is not None and flow.report['passed']), flush=True)
if flow is None or not flow.report['passed']:
    raise SystemExit(1)
