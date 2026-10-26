"""连续激励下的单ADC选通编排；调用已有R4采集及PL接口，不执行CPU解调。"""
import contextlib
import cmath
import hashlib
import json
import math
from pathlib import Path
import time

from r4_capture_client import command, capture_job
from phase_contract import validate_metadata, reference_phasor

PATHS = {'VA': 1, 'VB': 2, 'PROBE': 3}
DAC_KEYS = ('tag', 'frequency_word', 'gain_q11', 'offset_code', 'phase_word')


@contextlib.contextmanager
def process_lock():
    """悟净Linux上同一编排器只允许一个任务；其他旧客户端仍需退出控制。"""
    import fcntl
    with open('/run/lock/fdw_sense117.lock', 'a') as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class SenseSequence:
    """只接管已配置、已接触的单频测量组，不擅自控制运动或启波。"""

    def __init__(self, host='192.168.126.50', send=None, capture=None,
                 clock=time.monotonic, sleep=time.sleep, lock=process_lock):
        self.send = send or (lambda text: command(text, host))
        self.capture = capture or (lambda log2: capture_job(log2, host))
        self.clock, self.sleep, self.lock = clock, sleep, lock

    def stop(self):
        """停波停采集，保持继电器位置；不冒充探针抬起或物理断开。"""
        return self.send('HALT')

    def configure_analog_route(self, rs_nominal_ohm, confirm_lifted):
        """正常换路：先停输出，再由运动控制方确认抬针，最后设置针路和Rs。

        confirm_lifted必须接真实到位反馈或人工确认，不能用固定延时假装抬针。
        本模块没有电机接口，不负责发出实际抬针动作。
        """
        if rs_nominal_ohm not in (100, 10000) or not callable(confirm_lifted):
            raise ValueError('量程或抬针确认入口错误')
        with self.lock():
            info = self.send('INFO')
            if info.get('board_revision') != 'LXB-R4-SENSE117' or info.get('sense_protocol') != 1:
                raise ValueError('必须先使用SENSE117配套')
            self.stop()
            if confirm_lifted() is not True:
                raise RuntimeError('没有抬针到位确认，保持继电器原位')
            self.send('RELAY_SET {}'.format(33 if rs_nominal_ohm == 100 else 1))
            state = self._wait_path(10)
            self._sense_identity(state, rs_nominal_ohm, 'PROBE')
            return state

    @staticmethod
    def _dac_identity(dac):
        if (not all(dac.get(k) == 1 for k in ('ready', 'enabled', 'configured', 'waveform_active'))
                or dac.get('busy') or dac.get('clipped') or dac.get('error_code')
                or not dac.get('tag') or not dac.get('frequency_word')):
            raise ValueError('DAC未连续稳定运行或存在错误')
        return tuple(dac[k] for k in DAC_KEYS)

    @staticmethod
    def _sense_identity(sense, rs, path=None):
        if (sense.get('sense_protocol') != 1 or sense.get('fault') or sense.get('settling')
                or not sense.get('generation') or sense.get('rs_nominal_ohm') != rs
                or sense.get('relay_mask', 0) & 15 != 1):
            raise ValueError('采样路由、量程或稳定状态错误')
        if path and sense.get('selected') != PATHS[path]:
            raise ValueError('ADC选通没有到达请求支路')
        return sense['generation'], sense['relay_mask']

    def _wait_path(self, wait_ms):
        deadline = self.clock() + wait_ms / 1000.0 + 2.0
        while True:
            state = self.send('SENSE_STATUS')
            if state.get('fault'):
                raise ValueError('继电器控制寄存器回读失败')
            if not state.get('settling'):
                return state
            if self.clock() >= deadline:
                raise TimeoutError('选通稳定计时未完成')
            self.sleep(min(.02, max(.001, state.get('remaining_ms', 1) / 1000.0)))

    def capture_group(self, folder, point, group_id, node_id, rs_nominal_ohm,
                      rs_measured_ohm, calibration_id, path_wait_ms, dut_wait_seconds,
                      include_probe=True, process_frame=None, reference_limits=None):
        """VA→VB→可选C→VA复读；成功保持激励供后续组，失败立即HALT。

        process_frame可调用现有PlOnlyProcessor.process；传入值不是模型或故障判定。
        Rs实测值与校准ID仅作身份记录，不把字符串存在当校准已通过。
        """
        if rs_nominal_ohm not in (100, 10000):
            raise ValueError('只支持实际100Ω/10kΩ档位')
        if (not isinstance(rs_measured_ohm, (int, float)) or isinstance(rs_measured_ohm, bool)
                or not math.isfinite(rs_measured_ohm) or rs_measured_ohm <= 0):
            raise ValueError('必须明确记录实际使用的正值Rs')
        if type(path_wait_ms) is not int or not 10 <= path_wait_ms <= 60000:
            raise ValueError('路径等待必须为10至60000ms；10ms未完成模拟验收')
        if (not isinstance(dut_wait_seconds, (int, float)) or isinstance(dut_wait_seconds, bool)
                or not math.isfinite(dut_wait_seconds) or not 0 <= dut_wait_seconds <= 600):
            raise ValueError('DUT等待必须是0至600秒的显式配置')
        if not all(isinstance(v, str) and v.strip() for v in (group_id, node_id, calibration_id)):
            raise ValueError('任务、节点与校准身份不能为空')
        log2 = point.get('decimation_log2')
        if type(log2) is not int or not 0 <= log2 <= 16:
            raise ValueError('缺少合法抽取配置')
        if reference_limits is not None:
            for key in ('relative_amplitude', 'phase_degrees'):
                value = reference_limits.get(key)
                if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                    raise ValueError('参考漂移限值必须明确且为正数')
            if process_frame is None:
                raise ValueError('参考漂移判断需要实际PL结果')
        folder = Path(folder)
        with self.lock():
            info = self.send('INFO')
            if info.get('board_revision') != 'LXB-R4-SENSE117' or info.get('sense_protocol') != 1:
                raise ValueError('7020尚未运行SENSE117配套，禁止沿用旧互锁')
            folder.mkdir(parents=True, exist_ok=False)
            report = {'schema': 'fdw-sense-group/1', 'group_id': group_id, 'node_id': node_id,
                      'rs_nominal_ohm': rs_nominal_ohm, 'rs_measured_ohm': rs_measured_ohm,
                      'calibration_id': calibration_id, 'point': point, 'path_wait_ms': path_wait_ms,
                      'dut_wait_seconds': dut_wait_seconds, 'frames': [], 'events': [],
                      'acquisition_complete': False, 'measurement_qualified': False,
                      'analog_calibrated': False, 'steady_state_verified': False,
                      'relay_contact_feedback': False, 'dac_left_enabled': False}

            def save():
                temporary = folder / 'group.tmp'
                temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
                temporary.replace(folder / 'group.json')

            def send(text):
                answer = self.send(text)
                report['events'].append({'at_monotonic': self.clock(), 'command': text, 'reply': answer})
                save()
                return answer

            matched = True
            try:
                excitation = send('DAC_STATUS')
                identity = self._dac_identity(excitation)
                if excitation['frequency_word'] != point['dds_ftw']:
                    raise ValueError('实际DDS频率与任务不符')
                route = send('MODE_STATUS')
                if route.get('mode') != 0 or route.get('output_enabled') or route.get('capture_active') or route.get('settling'):
                    raise ValueError('数字通路未处于模拟测量空闲状态')
                self._sense_identity(self._wait_path(path_wait_ms), rs_nominal_ohm)
                self.sleep(dut_wait_seconds)
                previous = None
                records = set()
                paths = ['VA', 'VB'] + (['PROBE'] if include_probe else []) + ['VA']
                for index, path in enumerate(paths):
                    if self._dac_identity(send('DAC_STATUS')) != identity:
                        raise ValueError('组内激励配置发生变化')
                    send('SENSE_SELECT {} {}'.format(path, path_wait_ms))
                    before = self._wait_path(path_wait_ms)
                    sense_id = self._sense_identity(before, rs_nominal_ohm, path)
                    decoded, packet = self.capture(log2)
                    stem = '{:02d}_{}'.format(index, path)
                    (folder / (stem + '.packet')).write_bytes(packet)
                    meta = decoded['metadata']
                    row = {'path': path, 'role': 'reference_check' if index == len(paths)-1 else 'measurement',
                           'group_id': group_id, 'measurement_point': node_id if path == 'PROBE' else path,
                           'rs_measured_ohm': rs_measured_ohm, 'calibration_id': calibration_id,
                           'record_id': meta['record_id'], 'sense_before': before, 'metadata': meta,
                           'packet': stem + '.packet', 'packet_sha256': hashlib.sha256(packet).hexdigest()}
                    report['frames'].append(row)
                    save()
                    validate_metadata(meta, point)
                    if decoded['flags'] != 8 or decoded['overrange_count'] or decoded['mode'] != 0:
                        raise ValueError('采集不连续、过量程或模式错误')
                    if min(decoded['samples']) <= -2048 or max(decoded['samples']) >= 2047:
                        raise ValueError('ADC存在满量程码')
                    if meta['dac_tag'] != excitation['tag'] or meta['record_id'] in records:
                        raise ValueError('激励身份变化或重复旧帧')
                    binding = send('ADC_SENSE_STATUS')
                    if (binding.get('record_id'), binding.get('sense_generation'), binding.get('relay_mask')) != (meta['record_id'], *sense_id):
                        raise ValueError('原码帧不属于本次采样通路')
                    after = send('SENSE_STATUS')
                    if self._sense_identity(after, rs_nominal_ohm, path) != sense_id:
                        raise ValueError('采集前后通路改变')
                    if self._dac_identity(send('DAC_STATUS')) != identity:
                        raise ValueError('帧后激励配置发生变化')
                    if previous:
                        delta = meta['first_tick'] - previous['first_tick']
                        if delta <= 0 or delta <= previous['last_tick'] - previous['first_tick']:
                            raise ValueError('重复采集或时钟重置')
                        if (meta['first_phase_word'] - previous['first_phase_word']) & 0xffffffff != (delta * point['dds_ftw']) & 0xffffffff:
                            raise ValueError('跨帧DDS相位与实际时间差不连续')
                        if meta['inverted'] != previous['inverted']:
                            raise ValueError('组内激励反相身份改变')
                    records.add(meta['record_id'])
                    previous = meta
                    row['sense_after'] = after
                    row['binding'] = binding
                    if process_frame is not None:
                        row['pl_result'] = process_frame(decoded, point)
                    save()
                if process_frame is not None:
                    first, last = report['frames'][0], report['frames'][-1]
                    a = reference_phasor(first['pl_result'], first['metadata'], point)
                    b = reference_phasor(last['pl_result'], last['metadata'], point)
                    ratio = b / a
                    drift = {'relative_amplitude': abs(abs(ratio)-1),
                             'phase_degrees': abs(math.degrees(cmath.phase(ratio))),
                             'limits': reference_limits, 'within_limits': None}
                    if reference_limits is not None:
                        drift['within_limits'] = all(drift[k] <= reference_limits[k]
                                                     for k in ('relative_amplitude', 'phase_degrees'))
                    report['reference_drift'] = drift
                    save()
                    if drift['within_limits'] is False:
                        raise ValueError('VA回读漂移超过配置限值，本组不可用于测量结果')
                report['acquisition_complete'] = True
                report['dac_left_enabled'] = True
                save()
                return report
            except BaseException as error:
                report['error'] = str(error)
                # 即便收包/CRC失败，也保存客户端带出的失败字节。
                if getattr(error, 'packet', None):
                    (folder / 'failure.packet').write_bytes(error.packet)
                if matched:
                    try:
                        report['halt_reply'] = self.stop()
                    except Exception as cleanup_error:
                        report['halt_error'] = str(cleanup_error)
                save()
                raise
