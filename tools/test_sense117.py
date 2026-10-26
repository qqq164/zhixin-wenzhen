"""运行真实C候选与Python离线编排验证，记录每次独立尝试。"""
import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'outputs/checkpoint_117_sense_control'
SRC = ROOT / 'software/7020/candidates/lxb_r4_sense117/src'
TEST = SRC.parent / 'tests'
STUB = ROOT / 'tests/fixtures/7020_stubs'
PYTHON = ROOT / 'software/wujing/candidates/sense_sequence117'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--attempt', required=True)
    args = parser.parse_args()
    if not args.attempt.replace('_', '').isalnum(): raise ValueError('尝试名非法')
    out = BASE / args.attempt
    out.mkdir(parents=True, exist_ok=False)
    stubs = out / 'stubs'
    stubs.mkdir()
    (stubs/'xil_io.h').write_text('#include <stdint.h>\nuint32_t Xil_In32(uintptr_t);\nvoid Xil_Out32(uintptr_t,uint32_t);\n', encoding='ascii')
    (stubs/'sleep.h').write_text('void usleep(unsigned long);\n', encoding='ascii')
    cc = Path(os.environ.get('CC') or shutil.which('gcc') or 'D:/Xilinx/2025.1/Vitis/tps/win64/msys64/mingw64/bin/gcc.exe')
    env = dict(os.environ, PATH=str(cc.parent)+os.pathsep+os.environ['PATH'], PYTHONDONTWRITEBYTECODE='1')
    report = {'passed': False, 'hardware_sampled': False, 'tests': []}
    # 只读保护公开副本中的原迁移基线，不依赖未公开检查点。
    baseline = ROOT / 'software/7020/candidates/lxb_r4_port'
    hashes = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in baseline.rglob('*') if p.is_file() and '__pycache__' not in p.parts}

    def run(name, command, cwd=out):
        result = subprocess.run(command, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        (out/(name+'.log')).write_bytes(result.stdout)
        print(name, result.returncode, result.stdout.decode('utf-8', errors='replace')[-3500:])
        if result.returncode: raise RuntimeError(name+'失败')
        report['tests'].append({'name': name, 'passed': True})

    common = [str(cc), '-std=c11', '-D__USE_MINGW_ANSI_STDIO=1', '-Wall', '-Wextra', '-Werror', '-O2',
              '-I'+str(stubs), '-I'+str(STUB/'adc'), '-I'+str(STUB), '-I'+str(SRC)]
    try:
        run('sense_compile', common+[str(TEST/'test_sense_control.c'), str(SRC/'fdw_lxb_io.c'), '-o', str(out/'sense.exe')])
        run('sense_run', [str(out/'sense.exe')])
        # 原板级双PWM测试继续运行，新增依赖只提供无副作用的状态桩。
        text = (ROOT/'software/7020/candidates/lxb_r4_port/tests/test_lxb_io.c').read_text(encoding='utf-8')
        text = text.replace('#include <assert.h>', '#include <assert.h>\n#include "xiltimer.h"')
        text = text.replace('void usleep(unsigned long microseconds) { assert(microseconds == 10000U); ++sleeps; }',
                            'static XTime now;\nvoid XTime_GetTime(XTime *p) { *p=now; }\nvoid usleep(unsigned long us) { assert(us==10000U); ++sleeps; now+=us; }')
        text += '\nint fdw_adc_control_busy(void) { return 0; }\nint fdw_adc_control_transfer_busy(void) { return 0; }\nvoid fdw_adc_control_invalidate(void) { }\nvoid fdw_digital_invalidate(void) { }\n'
        (out/'test_legacy_io.c').write_text(text, encoding='utf-8')
        run('legacy_pwm_compile', common+[str(out/'test_legacy_io.c'), str(SRC/'fdw_lxb_io.c'), '-o', str(out/'legacy_io.exe')])
        run('legacy_pwm_run', [str(out/'legacy_io.exe')])
        # 真实TCP分段、背压、CRC和取消模型保留，再加新协议分派/忙时拒绝。
        text = (ROOT/'software/7020/candidates/dig5_r4_lowrate_bulk/tests/test_tcp_server.c').read_text(encoding='utf-8')
        for name in ('tcp_server.c', 'fdw_bulk_frame.c'):
            text = text.replace('../src/'+name, (SRC/name).as_posix())
        text += r'''
void fdw_digital_invalidate(void) { }
int fdw_lxb_io_init(void) { return 0; }
void fdw_lxb_io_halt(void) { }
void fdw_lxb_select_default_output(void) { }
uint32_t fdw_lxb_output_mask(void) { return 0; }
uint32_t fdw_lxb_relay_mask(void) { return 33; }
int fdw_lxb_relay_set(uint32_t mask) { return mask<128?0:-1; }
int fdw_lxb_pwm_set(uint32_t channel,uint32_t period,uint32_t high) { return channel<2&&period&&high<=period?0:-1; }
int fdw_lxb_pwm_enable(uint32_t channel,uint32_t enabled) { return channel<2&&enabled<2?0:-1; }
int fdw_lxb_pwm_status(uint32_t channel,struct fdw_lxb_pwm_status *status) { if(channel>1)return -1;memset(status,0,sizeof(*status));return 0; }
void fdw_lxb_sense_status(struct fdw_lxb_sense_status *s) { memset(s,0,sizeof(*s));s->generation=3;s->relay_mask=33;s->rs_nominal_ohm=100;s->selected=3; }
int fdw_lxb_sense_select(uint32_t path,uint32_t ms) { return path>=1&&path<=3&&ms>=10&&ms<=60000?0:-1; }
'''
        marker = '    assert(start_application() == 0);'
        cases = r'''
    command_contains("SENSE_STATUS\n", "\"sense_protocol\":1");
    command_contains("INFO\n", "\"firmware_revision\":\"R4-P1-IRQFP-SENSE117\"");
    command_contains("SENSE_SELECT VA 10\n", "\"ok\":true");
    command_contains("SENSE_SELECT VB 37\n", "\"ok\":true");
    command_contains("SENSE_SELECT PROBE 503\n", "\"ok\":true");
    command_contains("SENSE_SELECT VA 9\n", "\"ok\":false");
    command_contains("SENSE_SELECT VA 10 extra\n", "\"ok\":false");
    command_contains("SENSE_SELECT VA\n", "\"ok\":false");
    command_contains("SENSE_SELECT XX 10\n", "\"ok\":false");
    job_busy=1;
    command_contains("SENSE_SELECT VA 10\n", "\"error\":\"ADC_BUSY\"");
    command_contains("RELAY_SET 0\n", "\"error\":\"ADC_BUSY\"");
    command_contains("SENSE_STATUS\n", "\"ok\":true");
    job_busy=0;
'''
        assert text.count(marker) == 1
        text = text.replace(marker, cases+'\n'+marker)
        (out/'test_tcp.c').write_text(text, encoding='utf-8')
        run('tcp_compile', common+[str(out/'test_tcp.c'), '-o', str(out/'tcp.exe')])
        run('tcp_run', [str(out/'tcp.exe')])
        for name in ('adc_capture', 'adc_control', 'phase_control'):
            source = ROOT/'software/7020/candidates/dig5_r4_lowrate_bulk/tests'/('test_'+name+'.c')
            text = source.read_text(encoding='utf-8').replace('../src/fdw_'+name+'.c', (SRC/('fdw_'+name+'.c')).as_posix())
            if name == 'adc_control':
                text += '\nint fdw_lxb_sense_ready(void) { return 0; }\nvoid fdw_lxb_sense_status(struct fdw_lxb_sense_status *s) { s->generation=1;s->relay_mask=0;s->fault=0; }\n'
            target = out/('test_'+name+'.c')
            target.write_text(text, encoding='utf-8')
            # 原ADC测试自带int型usleep桩，不能混用板级void型模型头。
            legacy_common = [item for item in common if item != '-I'+str(stubs)]
            run(name+'_compile', legacy_common+[str(target), '-o', str(out/(name+'.exe'))])
            run(name+'_run', [str(out/(name+'.exe'))])
        run('python_sequence', [sys.executable, '-B', '-X', 'utf8', '-m', 'unittest', '-v', 'test_sense_sequence'], PYTHON)
        report['protected_unchanged'] = all(hashlib.sha256((ROOT/n).read_bytes()).hexdigest()==h for n,h in hashes.items())
        report['protected_count'] = len(hashes)
        if not report['protected_unchanged']: raise ValueError('保护基线发生变化')
        report['passed'] = True
    finally:
        (out/'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


if __name__ == '__main__':
    main()
