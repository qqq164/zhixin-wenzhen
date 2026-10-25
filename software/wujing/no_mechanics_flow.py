"""无机械直连流程V1：悟净在线控制7020，真实采样及PL计算，端口/网络为明确模拟。"""
import cmath
import hashlib
import json
import math
from pathlib import Path
import socket
import struct
import time

from pl_runtime.phase_contract import validate_metadata, impedance


class TcpLink:
    """直接连接7020；只负责传输，不产生ADC样本或执行CPU解调。"""

    def __init__(self, host, port=5001):
        self.host = host
        self.port = port
        self.connections = 0
        self.last_endpoints = None

    def command(self, text):
        """每条命令按换行完整收回，拒绝超长或截断回复。"""
        with socket.create_connection((self.host, self.port), timeout=3) as connection:
            connection.settimeout(3)
            self.connections += 1
            self.last_endpoints = {'local': list(connection.getsockname()), 'peer': list(connection.getpeername())}
            connection.sendall((text+'\n').encode('ascii'))
            data = bytearray()
            while b'\n' not in data:
                part = connection.recv(512)
                if not part:
                    raise ConnectionError('7020回复在换行前结束')
                data.extend(part)
                if len(data) > 4096:
                    raise ValueError('7020回复超过长度上限')
        return json.loads(data.split(b'\n', 1)[0])


class NoMechanicsLoopbackFlow:
    """仅支持已接好的DAC/ADC直连；不控制电机，不冒充物理测点或真实DUT。"""

    def __init__(self, folder, schedule, processor, transport=None):
        self.folder = Path(folder)
        self.schedule = schedule
        self.processor = processor
        self.transport = transport or TcpLink('192.168.126.50')
        self.matched = False
        self.cleaning = False
        self.ids = set()
        self.report = {'flow_version': 'NO_MECH_ONLINE_V1', 'passed': False,
                       'mode': 'DAC_ADC_LOOPBACK_VIRTUAL_NETWORK', 'controller': '悟净CPU',
                       'data_path': '7020 TCP直接到悟净DDR/PL，无电脑中转',
                       'mechanical_actions_simulated': True, 'physical_switch_exercised': False,
                       'physical_dut_measured': False, 'physical_current_measured': False,
                       'absolute_voltage_calibrated': False, 'cpu_reference_executed': False,
                       'cpu_fallback': False, 'npu_executed': False, 'classification_executed': False,
                       'frames': [], 'points': [], 'events': [], 'commands': [], 'command_counts': {}}
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder/'capture').mkdir(exist_ok=True)

    def save(self):
        """原子替换进度，异常与原码不被后续结果覆盖。"""
        temporary = self.folder/'flow_result.tmp'
        temporary.write_text(json.dumps(self.report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        temporary.replace(self.folder/'flow_result.json')

    def event(self, stage, **details):
        """软件测点动作、硬件采样、PL计算分别标记，保留单调时钟。"""
        self.report['events'].append(dict(stage=stage, monotonic_ns=time.monotonic_ns(), **details))
        self.save()
        print('FDW_FLOW_STAGE '+stage+' '+json.dumps(details, ensure_ascii=False), flush=True)

    def cancelled(self):
        """取消文件仅中止当前在线任务，收尾仍必须撤销激励并排空PL。"""
        if not self.cleaning and (self.folder/'cancel.request').exists():
            self.report['cancelled'] = True
            raise RuntimeError('用户取消本次在线任务')

    def command(self, text):
        """每条控制结果关联任务；原始样本块单独存文件和摘要。"""
        self.cancelled()
        started = time.monotonic_ns()
        answer = self.transport.command(text)
        kind = text.split()[0]
        self.report['command_counts'][kind] = self.report['command_counts'].get(kind, 0)+1
        if kind != 'ADC_READ':
            self.report['commands'].append({'command': text, 'started_ns': started,
                                            'ended_ns': time.monotonic_ns(), 'reply': answer})
        if not answer.get('ok'):
            raise RuntimeError('7020命令失败：'+text+' '+str(answer))
        return answer

    def preflight(self):
        """先校验硬件版本和空闲，再允许发送激励配置。"""
        if self.schedule.get('flow_profile') != 'NO_MECH_ONLINE_V1' or self.schedule.get('capture_profile') != 'RAW25M_PHASE':
            raise ValueError('仅接受无机械直连/25MHz相位配置')
        if self.schedule.get('physical_dut_connected') is not False:
            raise ValueError('本入口只用于虚拟网络直连，不能当作真实DUT测量入口')
        info = self.command('INFO')
        if (info.get('implementation_revision') != 'DIG5-R3-AMP_PHASE' or
                info.get('phase_metadata_version') != 1 or not info.get('adc_ready') or
                not info.get('dds_ready') or info.get('mode') != 0):
            raise ValueError('7020版本、采集就绪状态或模式不符')
        self.processor.backend.require_version()
        initial = self.processor.backend.read(4)
        if initial != 1:
            raise RuntimeError('悟净PL不是空闲状态，禁止并发任务')
        self.matched = True
        self.report['initial_info'] = info
        self.report['initial_pl_status'] = hex(initial)
        self.command('HALT')
        self.event('PREFLIGHT_PASSED', hardware_revision=info['implementation_revision'], pl_abi='0x20261014')

    def capture(self, index, port, quiet=False):
        """真实ADC帧在悟净直接接收、落盘，再交给悟净PL，无样本回放输入。"""
        point = self.schedule['points'][index]
        self.event('ADC_ARM', point=index, port=port, quiet=quiet)
        excitation = self.command('DAC_STATUS')
        if not quiet and (not excitation['enabled'] or excitation['busy'] or excitation['clipped'] or excitation['error_code']):
            raise RuntimeError('DAC激励状态无效')
        record = self.command('ADC_CAPTURE 8192' if quiet else 'ADC_CAPTURE_PHASE 8192')['record_id']
        meta = None if quiet else self.command('ADC_PHASE_STATUS')
        status = self.command('ADC_STATUS')
        if record == 0 or record in self.ids or status['record_id'] != record or status['sample_count'] != 8192 or status['flags'] != 8 or status['overrange_count']:
            raise ValueError('ADC帧身份、长度或连续性不符')
        if meta is not None:
            validate_metadata(meta, point)
            if meta['record_id'] != record or meta['dac_tag'] != excitation['tag'] or meta['inverted']:
                raise ValueError('相位参考不属于本次激励/采样')
        name = 'frame_{:02d}'.format(len(self.report['frames']))
        payload = bytearray()
        blocks = []
        try:
            for start in range(0, 8192, 32):
                reply = self.command('ADC_READ {} {} 32'.format(record, start))
                data = bytes.fromhex(reply['hex'])
                if reply['record_id'] != record or reply['start'] != start or reply['count'] != 32 or len(data) != 64:
                    raise ValueError('ADC回传块身份或长度不符')
                payload.extend(data)
                blocks.append({'start': start, 'sha256': hashlib.sha256(data).hexdigest()})
        except Exception:
            (self.folder/'capture'/(name+'.partial.bin')).write_bytes(payload)
            self.report['partial_frame'] = {'name': name, 'received_bytes': len(payload), 'record_id': record}
            raise
        samples = struct.unpack('<8192h', payload)
        if not all(-2048 < value < 2047 for value in samples):
            raise ValueError('ADC存在满量程或疑似失电原码')
        after = self.command('DAC_STATUS')
        keys = ('enabled', 'tag', 'frequency_word', 'phase_word', 'gain_q11', 'offset_code')
        if any(after[key] != excitation[key] for key in keys) or after['busy'] or after['clipped'] or after['error_code']:
            raise ValueError('采集期间激励改变')
        (self.folder/'capture'/(name+'.bin')).write_bytes(payload)
        row = {'name': name, 'point_index': index, 'port': port, 'quiet': quiet,
               'record_id': record, 'binary_file': name+'.bin',
               'input_sha256': hashlib.sha256(payload).hexdigest(), 'metadata': meta,
               'status': status, 'excitation': excitation, 'chunks': blocks, 'pl_computed': False}
        self.ids.add(record)
        self.report['frames'].append(row)
        self.event('ADC_RECEIVED', name=name, record_id=record, real_samples=True)
        self.event('PL_COMPUTE_START', name=name, point=index)
        if quiet:
            actual = self.processor.backend.compute(samples, point['pl_config'], record, status['flags'], ())
            row['phase_eligible'] = False
        else:
            center = round(point['actual_frequency_hz']*8192/25_000_000)
            bins = [center-1, center, center+1]
            actual = self.processor.process(samples, meta, index, status['flags'], bins)
            row['phase_eligible'] = True
        row['actual'] = actual
        row['pl_computed'] = True
        (self.folder/'capture'/(name+'.json')).write_text(json.dumps(row, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        self.event('PL_COMPUTE_END', name=name, backend='pl', cpu_fallback=False)
        return row

    def set_virtual_port(self, index, port, gain, phase):
        """模拟测点选择和接触，实际通过DAC参数产生虚拟端口波形。"""
        self.event('PROBE_ROUTE_SIMULATED', point=index, port=port, physical_motion=False, physical_switch=False)
        point = self.schedule['points'][index]
        self.command('DAC_SET {} {} 2048 {} 0'.format(int(point['requested_frequency_hz']), gain, phase))
        self.command('DAC_ENABLE 1')
        wait = self.schedule['switch_wait_seconds'][index % len(self.schedule['switch_wait_seconds'])]
        self.event('SETTLING_WAIT_START', point=index, port=port, planned_seconds=wait)
        start = time.monotonic_ns()
        time.sleep(wait)
        self.cancelled()
        self.event('SETTLING_WAIT_END', point=index, port=port, elapsed_seconds=(time.monotonic_ns()-start)/1e9)
        return self.capture(index, port)

    @staticmethod
    def virtual_vector(row):
        """保留真实PL回传幅相，显式加回已编程的虚拟端口相位，不冒充未知DUT相移。"""
        return complex(*row['actual']['dds_relative_real_imag_counts'])*cmath.exp(2j*math.pi*row['excitation']['phase_word']/(1 << 32))

    def solve_point(self, index, before, middle, after, model):
        """只用已测相量计算H/Z；理论值仅用于最后的验证误差，不替代测量值。"""
        va_before, vb, va_after = [self.virtual_vector(row) for row in (before, middle, after)]
        ticks = [row['metadata']['first_tick'] for row in (before, middle, after)]
        if not ticks[0] < ticks[1] < ticks[2]:
            raise ValueError('端口采样时间戳顺序错误')
        drift = va_after/va_before
        amp_drift = 100*abs(abs(drift)-1)
        phase_drift = abs(math.degrees(cmath.phase(drift)))
        if amp_drift >= 2 or phase_drift >= 1:
            raise ValueError('Va回读漂移超限，拒绝跨帧阻抗')
        weight = (ticks[1]-ticks[0])/(ticks[2]-ticks[0])
        va = va_before*(1-weight)+va_after*weight
        h = vb/va
        z = impedance(va, vb, self.schedule['virtual_rs_ohm'], 1)
        target_z = complex(*model['expected_z_real_imag'])
        target_h = complex(*model['expected_h_real_imag'])
        z_error = 100*abs(z/target_z-1)
        z_phase_error = abs(math.degrees(cmath.phase(z/target_z)))
        h_amp_error = 100*abs(abs(h)/abs(target_h)-1)
        h_phase_error = abs(math.degrees(cmath.phase(h/target_h)))
        result = {'point_index': index, 'frequency_hz': self.schedule['points'][index]['actual_frequency_hz'],
                  'frame_names': [row['name'] for row in (before, middle, after)],
                  'actual_a_b_gap_seconds': (ticks[1]-ticks[0])/25e6,
                  'actual_b_a_gap_seconds': (ticks[2]-ticks[1])/25e6,
                  'va_return_amplitude_drift_percent': amp_drift, 'va_return_phase_drift_degrees': phase_drift,
                  'transfer_real_imag': [h.real, h.imag], 'transfer_gain_db': 20*math.log10(abs(h)),
                  'transfer_phase_degrees': math.degrees(cmath.phase(h)),
                  'virtual_z_real_imag_ohm': [z.real, z.imag], 'virtual_z_magnitude_ohm': abs(z),
                  'virtual_z_phase_degrees': math.degrees(cmath.phase(z)),
                  'virtual_current_real_imag_code_per_ohm': [(va-vb).real/self.schedule['virtual_rs_ohm'], (va-vb).imag/self.schedule['virtual_rs_ohm']],
                  'physical_current_ampere_available': False,
                  'complex_z_error_percent': z_error, 'z_phase_error_degrees': z_phase_error,
                  'transfer_amplitude_error_percent': h_amp_error, 'transfer_phase_error_degrees': h_phase_error,
                  'passed': z_error < 5 and z_phase_error < 3 and h_amp_error < 2 and h_phase_error < 1}
        self.report['points'].append(result)
        self.event('POINT_RESULT_SAVED', point=index, passed=result['passed'])

    def run(self):
        """完整顺序运行，异常和取消均执行停波；不回退CPU算法。"""
        try:
            self.event('TASK_CREATED', version='NO_MECH_ONLINE_V1')
            self.preflight()
            self.capture(0, 'quiet_before', quiet=True)
            for index, model in enumerate(self.schedule['virtual_network_plan']):
                self.event('POINT_BEGIN', point=index)
                before = self.set_virtual_port(index, 'Va_before', 256, 0)
                middle = self.set_virtual_port(index, 'Vb', model['gain_b'], model['phase_b_word'])
                after = self.set_virtual_port(index, 'Va_after', 256, 0)
                self.solve_point(index, before, middle, after, model)
                self.command('HALT')
                self.event('PROBE_LIFT_SIMULATED', point=index, physical_motion=False)
            self.command('HALT')
            self.capture(0, 'quiet_after', quiet=True)
            self.report['passed'] = all(row['passed'] for row in self.report['points']) and len(self.report['points']) == len(self.schedule['points'])
        except Exception as error:
            self.report['error'] = str(error)
        finally:
            self.cleaning = True
            if self.matched:
                try:
                    self.command('HALT')
                    self.report['final_dac_status'] = self.command('DAC_STATUS')
                    self.report['final_mode_status'] = self.command('MODE_STATUS')
                    if self.report['final_dac_status']['enabled'] or self.report['final_mode_status']['output_enabled']:
                        raise RuntimeError('收尾输出许可仍开启')
                    self.report['final_pl_status'] = hex(self.processor.backend.clear_and_drain())
                    self.report['quarantined_buffers'] = len(self.processor.backend.quarantine)
                    if self.report['final_pl_status'] != '0x1' or self.report['quarantined_buffers']:
                        raise RuntimeError('PL未回到空闲或仍有DDR隔离缓冲')
                except Exception as error:
                    self.report['cleanup_error'] = str(error)
                    self.report['passed'] = False
            if hasattr(self.transport, 'connections'):
                self.report['direct_tcp_connections'] = self.transport.connections
                self.report['last_tcp_endpoints'] = self.transport.last_endpoints
            self.event('TASK_STOPPED', passed=self.report['passed'])
        return self.report
