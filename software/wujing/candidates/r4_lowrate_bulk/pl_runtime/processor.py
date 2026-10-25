"""悟净PL任务入口候选：没有CPU算法回退，旧R2帧缺少相位元数据时拒绝处理。"""
import cmath
from .sdk_backend import FdwPlDspClient
from .phase_contract import validate_metadata, reference_phasor


class PlOnlyProcessor:
    """调用者持有已打开的SDK设备，离线配置包由电脑提前生成。"""

    def __init__(self, device, schedule):
        if schedule.get('schema') != 'FDW_PHASE_SWEEP_V1' or schedule.get('backend') != 'pl_only' or schedule.get('cpu_fallback') is not False:
            raise ValueError('正式入口仅接受禁止CPU回退的PL配置包')
        if schedule.get('required_pl_abi') != 0x20261014 or schedule.get('required_adc_metadata_version') != 2:
            raise ValueError('硬件ABI或元数据协议不符')
        self.schedule = schedule
        self.backend = FdwPlDspClient(device)
        self.backend.require_version()

    def process(self, samples, meta, point_index, flags, fft_bins=()):
        """CPU只做校验、搬运、握手和标量换算；逐样本IQ/FFT交给PL。"""
        point = self.schedule['points'][point_index]
        validate_metadata(meta, point)
        config = point['pl_config']
        if config['ftw'] != point['pl_ftw'] or config['phase_word'] != 0:
            raise ValueError('运行配置须使用实际抽取步进及零初相基底')
        if type(flags) is not int or flags & 4 or not flags & 8:
            raise ValueError('ADC帧未通过连续性检查')
        result = self.backend.compute(samples, config, meta['record_id'], flags, fft_bins)
        value = reference_phasor(result, meta, point)
        result.update({'backend': 'pl', 'cpu_fallback': False,
                       'dds_relative_real_imag_counts': [value.real, value.imag],
                       'dds_relative_amplitude_counts': abs(value),
                       'dds_relative_phase_radians': cmath.phase(value),
                       'digital_phase_record_consistent': True,
                       'phase_reference_hardware_verified': False,
                       'analog_phase_calibrated': False})
        return result
