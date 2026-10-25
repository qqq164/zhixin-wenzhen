"""运行既有离线回归，检查公开整理后的导入路径及算法行为。"""
from pathlib import Path
import json
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    """只运行离线测试，不连接板卡、加载SDK或启动激励。"""
    environment = dict(os.environ)
    paths = [ROOT / 'software/wujing', ROOT / 'tools']
    environment['PYTHONPATH'] = os.pathsep.join(str(path) for path in paths)
    environment['PYTHONDONTWRITEBYTECODE'] = '1'
    environment['PYTHONIOENCODING'] = 'utf-8'
    cases = [
        ('CPU_RC求解器', [sys.executable, '-B', 'software/wujing/candidates/rc_network_cpu_v1/test_rc_network.py']),
        ('采集客户端与特征', [sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'software/wujing/tests', '-p', 'test_*.py', '-v']),
        ('R4整帧与相位', [sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tools', '-p', 'test_r4_client.py', '-v']),
    ]
    report = {'类型': '公开整理后的离线回归', '新硬件测量': False, 'checks': [], 'passed': True}
    for name, command in cases:
        print('\n运行：' + name, flush=True)
        result = subprocess.run(command, cwd=ROOT, env=environment)
        report['checks'].append({'name': name, 'passed': result.returncode == 0})
        if result.returncode != 0:
            report['passed'] = False
    destination = ROOT / 'verification/公开整理检查.json'
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
