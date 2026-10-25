"""在悟净CPU独立目录运行验证，不打开PL设备，不修改服务和启动分区。"""
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parent


def snapshot():
    """记录启动身份及网页状态，不要求网页停止。"""
    value = {'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
             'web_state': subprocess.run(['systemctl', 'show', 'fdw-circuitscout.service',
                                           '-p', 'ActiveState', '-p', 'MainPID'],
                                          stdout=subprocess.PIPE, universal_newlines=True).stdout.strip()}
    try:
        with urllib.request.urlopen('http://127.0.0.1:8001/', timeout=3) as response:
            value['http_status'] = response.status
    except Exception as error:
        value['http_error'] = str(error)
    return value


def save(name, value):
    """任何已有结果都不覆盖。"""
    with (ROOT / name).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
        stream.write('\n')


def main():
    """分别执行边界测试、理论预测、解析比较和历史PL特征适配比较。"""
    if platform.machine() not in ('aarch64', 'arm64'):
        raise RuntimeError('此入口须在悟净ARM64 CPU运行，不能把电脑结果当作板上执行')
    expected = json.loads((ROOT / 'upload_manifest.json').read_text(encoding='utf-8'))
    for name, digest in expected.items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest:
            raise RuntimeError('上传文件摘要不符：' + name)
    before = snapshot()
    save('before.json', before)
    commands = [
        [sys.executable, '-B', 'test_rc_network.py'],
        [sys.executable, '-B', 'run_rc_network.py', '--model', 'examples/model_rc_ladder.json',
         '--output', 'board_prediction.json'],
        [sys.executable, '-B', 'run_rc_network.py', '--model', 'examples/model_rc_ladder.json',
         '--measurements', 'analytic_measurements.json', '--absolute-tolerance', '1e-10',
         '--relative-tolerance', '1e-10', '--output', 'board_analytic_comparison.json'],
        [sys.executable, '-B', 'run_rc_network.py', '--model', 'historical_repeat_model.json',
         '--measurements', 'historical_pl_measurements.json', '--output', 'board_historical_comparison.json']]
    steps = []
    started = time.monotonic()
    for index, command in enumerate(commands):
        result = subprocess.run(command, cwd=str(ROOT), stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=30,
                                env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
        with (ROOT / ('step_' + str(index) + '.log')).open('xb') as stream:
            stream.write(result.stdout)
        steps.append({'step': index, 'exit_code': result.returncode})
        if result.returncode:
            raise RuntimeError('CPU验证步骤失败：' + str(index))
    after = snapshot()
    save('after.json', after)
    unchanged = all(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest
                    for name, digest in expected.items())
    result = {'passed': before == after and unchanged, 'machine': platform.machine(),
              'python': sys.version, 'executable': sys.executable, 'steps': steps,
              'cpu_total_seconds': time.monotonic() - started,
              'production_web_and_boot_identity_unchanged': before == after,
              'uploaded_inputs_unchanged': unchanged,
              'cpu_solver_executed': True, 'pl_executed': False, 'new_adc_capture': False,
              'dac_enabled': False, 'npu_executed': False, 'boot_modified': False,
              'physical_dut_measured': False}
    save('board_verification.json', result)
    print('FDW_RC_CPU_BOARD ' + json.dumps(result, ensure_ascii=False))
    if not result['passed']:
        raise RuntimeError('板上CPU验证或环境保持检查失败')


if __name__ == '__main__':
    main()
