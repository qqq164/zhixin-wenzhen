"""生成明确标为合成的接口样例，不冒充实板测量。"""
import cmath
import copy
import math

from model_export import CONTRACT, ORDER


def example_condition(frames=16, rs=100, condition_id='synthetic_20k_rs100'):
    """用已知复数结果演示旧PL结果到F6的字段映射。"""
    frequency_word = round(20000 * (1 << 32) / 25000000)
    request = {'schema': 'zhixin.pl-condition/v1', 'condition': {
        'condition_id': condition_id, 'point_id': 'SYNTHETIC_C',
        'frontend_profile_id': 'SYNTHETIC_ONLY', 'acquisition_session_id': 'synthetic_session',
        'rs_nominal_ohm': rs, 'rs_measured_ohm': None,
        'requested_frequency_hz': 20000, 'frequency_tolerance_ppm': 10,
        'origin': 'synthetic_test_vector', 'scope': 'synthetic'}, 'channels': {}}
    phasors = {'A': 100 + 0j, 'B': 80 * cmath.exp(-1j * math.pi / 6),
               'C': 50 * cmath.exp(1j * math.pi / 4), 'A_check': 100 + 0j}
    record = 0
    for name, value in phasors.items():
        request['channels'][name] = []
        for average in range(frames):
            record += 1
            first_tick = record * 10000
            first_phase = (first_tick * frequency_word) & 0xffffffff
            meta = {'metadata_version': 2, 'generation': record, 'record_id': record,
                    'sample_count': 8192, 'complete': True, 'errors': 0,
                    'dds_ftw': frequency_word, 'dac_tag': 1, 'inverted': False,
                    'interval_ticks': 1, 'first_tick': first_tick, 'last_tick': first_tick + 8191,
                    'first_phase_word': first_phase,
                    'last_phase_word': (first_phase + 8191 * frequency_word) & 0xffffffff,
                    'raw_clock_hz': 25000000, 'decimation_log2': 0, 'filter_id': 0,
                    'pipeline_tick_compensation': 0}
            actual = {'abi': 0x20261014, 'frame_id': record, 'flags': 8,
                      'backend': 'pl', 'cpu_fallback': False,
                      'digital_phase_record_consistent': True,
                      'dds_relative_real_imag_counts': [value.real, value.imag],
                      'dc_counts': 20.0 if name == 'C' else 0.0,
                      'residual_rms_counts': 5.0 if name == 'C' else 1.0,
                      'saturation_count': 0, 'phase_reference_hardware_verified': False}
            request['channels'][name].append({'metadata': meta, 'actual': actual,
                                               'status': {'flags': 8, 'overrange_count': 0}})
    return request


def example_training_profile(frames=16):
    """只规定训练输入约定，不声明已有训练好的模型。"""
    return {'feature_contract': CONTRACT, 'order': copy.copy(ORDER),
            'rs_nominal_ohm': [100, 10000], 'frames_per_channel': frames,
            'frontend_profile_id': 'SYNTHETIC_ONLY', 'trained_and_validated': False}
