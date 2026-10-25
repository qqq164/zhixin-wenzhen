"""DIG5模拟采集：7020内部DDS驱动DAC902，保存ADC原码和激励配置身份。"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import time
from pwm_client import command


def capture(host, output, frequency_hz=100000, gain_q11=64, offset_code=2048,
            phase_word=0, invert=0, frames=3, settle_seconds=0.05):
    """调用者已确认模块供电及接线后执行；异常和正常结束都撤销外部输出。"""
    if output.exists() and any(output.iterdir()):
        raise FileExistsError('采集输出必须使用新的空目录')
    if not (0 <= frequency_hz < 12500000 and 0 <= gain_q11 <= 2048
            and 0 <= offset_code <= 4095 and 0 <= phase_word <= 0xffffffff
            and invert in (0, 1) and 1 <= frames <= 100 and 0 <= settle_seconds <= 10):
        raise ValueError('DAC参数、帧数或稳定时间越界')
    output.mkdir(parents=True, exist_ok=True)
    report = {'passed': False, 'hardware_revision': 'DIG5', 'source': 'DAC902',
              'host': host, 'voltage_calibrated': False, 'phase_reference_verified': False,
              'adc_conversion_verified': False, 'dac_analog_output_verified': False,
              'wujing_pl_executed': False, 'npu_executed': False,
              'commands': [], 'frames': []}

    def run(text):
        reply = command(host, text)
        report['commands'].append({'command': text, 'response': {key: value for key, value in reply.items() if key != 'hex'}})
        if not reply.get('ok'):
            raise RuntimeError('命令失败：{}：{}'.format(text, reply))
        return reply

    try:
        run('HALT')
        info = run('INFO')
        if info.get('hardware_revision') != 'DIG5' or info.get('dac_model') != 'DAC902' or not info.get('dds_ready') or not info.get('adc_ready'):
            raise RuntimeError('需要匹配的DIG5 ADC/DAC902整套配套')
        run('DAC_SET {} {} {} {} {}'.format(frequency_hz, gain_q11, offset_code, phase_word, invert))
        configured = run('DAC_STATUS')
        if not configured['configured'] or configured['enabled']:
            raise RuntimeError('配置后应保持外部管脚断开')
        run('DAC_ENABLE 1')
        time.sleep(settle_seconds)
        expected_word = ((frequency_hz << 32) + 12500000) // 25000000
        expected = {'frequency_word': expected_word, 'phase_word': phase_word,
                    'gain_q11': gain_q11, 'offset_code': offset_code, 'tag': configured['tag']}
        for index in range(frames):
            before = run('DAC_STATUS')
            if not before['ready'] or not before['enabled'] or any(before[key] != value for key, value in expected.items()):
                raise RuntimeError('激励配置或输出状态与本次条件不符')
            record_id = run('ADC_CAPTURE 8192')['record_id']
            status = run('ADC_STATUS')
            if status['record_id'] != record_id or status['sample_count'] != 8192 or status['flags'] != 8 or status['encoding'] != 'signed16_le':
                raise RuntimeError('ADC记录身份、长度或连续性检查失败')
            data = bytearray()
            for start in range(0, 8192, 32):
                block = run('ADC_READ {} {} 32'.format(record_id, start))
                if block['record_id'] != record_id or block['start'] != start or block['count'] != 32:
                    raise RuntimeError('ADC分块身份不符')
                chunk = bytes.fromhex(block['hex'])
                if len(chunk) != 64:
                    raise RuntimeError('ADC分块字节数不符')
                data.extend(chunk)
            if len(data) != 16384:
                raise RuntimeError('ADC整帧长度不符')
            values = struct.unpack('<8192h', data)
            if not all(-2048 <= value <= 2047 for value in values):
                raise RuntimeError('ADC码值范围不符')
            after = run('DAC_STATUS')
            if not after['enabled'] or any(after[key] != value for key, value in expected.items()):
                raise RuntimeError('采集和回收期间激励条件发生改变，拒绝保存为有效帧')
            path = output / 'adc_{:02d}.bin'.format(index)
            path.write_bytes(data)
            report['frames'].append({'record_id': record_id, 'sample_count': 8192,
                'sample_rate_hz': status['sample_rate_hz'], 'encoding': status['encoding'],
                'sha256': hashlib.sha256(data).hexdigest(), 'binary_path': str(path),
                'status': status, 'excitation_before': before, 'excitation_after': after,
                'minimum_code': min(values), 'maximum_code': max(values)})
        report['passed'] = True
    except Exception as error:
        report['error'] = str(error)
        raise
    finally:
        try:
            run('HALT')
            report['final_dac_status'] = run('DAC_STATUS')
            report['final_mode_status'] = run('MODE_STATUS')
            if report['final_dac_status']['enabled'] or report['final_mode_status']['output_enabled'] or report['final_mode_status']['mode']:
                raise RuntimeError('结束状态仍有输出许可')
        except Exception as error:
            report['cleanup_error'] = str(error)
            report['passed'] = False
        (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='192.168.126.50')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--frequency-hz', default=100000, type=int)
    parser.add_argument('--gain-q11', default=64, type=int)
    parser.add_argument('--offset-code', default=2048, type=int)
    parser.add_argument('--phase-word', default=0, type=lambda value: int(value, 0))
    parser.add_argument('--invert', default=0, type=int)
    parser.add_argument('--frames', default=3, type=int)
    args = parser.parse_args()
    result = capture(args.host, args.output, args.frequency_hz, args.gain_q11,
                     args.offset_code, args.phase_word, args.invert, args.frames)
    print('FDW_DIG5_ANALOG_CAPTURE=' + json.dumps({'passed': result['passed'], 'frames': len(result['frames'])}))
    if not result['passed']:
        raise SystemExit(1)
