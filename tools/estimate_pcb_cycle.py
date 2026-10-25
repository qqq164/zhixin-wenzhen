"""根据93实测日志预算五网络PCB周期；机械/判定预算明确为假设，不操作硬件。"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    """保留实测与假设的边界，不将未接入的机械和故障模型写成已验收。"""
    output = ROOT/'outputs/checkpoint_94_pcb_cycle_and_phase_budget'
    output.mkdir(exist_ok=False)
    source = ROOT/'outputs/checkpoint_93_no_mechanics_online_flow_20261017/attempt_01/result/flow_result.json'
    flow = json.loads(source.read_text(encoding='utf-8'))
    if not flow['passed']:
        raise ValueError('预算须使用已通过的在线实测')
    events = flow['events']
    total = (events[-1]['monotonic_ns']-events[0]['monotonic_ns'])/1e9
    waits = sum(row['elapsed_seconds'] for row in events if row['stage'] == 'SETTLING_WAIT_END')
    receive, pl_section = 0.0, 0.0
    for frame in flow['frames']:
        name = frame['name']
        index = next(i for i, row in enumerate(events) if row['stage'] == 'ADC_RECEIVED' and row.get('name') == name)
        arm = max(i for i in range(index) if events[i]['stage'] == 'ADC_ARM')
        start = next(row for row in events if row['stage'] == 'PL_COMPUTE_START' and row.get('name') == name)
        end = next(row for row in events if row['stage'] == 'PL_COMPUTE_END' and row.get('name') == name)
        receive += (events[index]['monotonic_ns']-events[arm]['monotonic_ns'])/1e9
        pl_section += (end['monotonic_ns']-start['monotonic_ns'])/1e9
    networks, frequencies = 5, 6
    measurement = networks*total
    estimate_min = 15+networks*1+measurement+1
    estimate_max = 30+networks*3+measurement+3
    extra_moves = networks*frequencies*2
    result = {'client_date': '2026-10-17', 'source': str(source),
              'user_networks_per_board': networks, 'frequencies_per_network': frequencies,
              'measured': {'one_network_seconds': total, 'one_network_frames': len(flow['frames']),
                           'one_frame_sample_window_seconds': 8192/25_000_000,
                           'all_sample_windows_seconds': len(flow['frames'])*8192/25_000_000,
                           'receive_sections_seconds': receive,
                           'pl_submit_compute_read_sections_seconds': pl_section,
                           'explicit_waits_seconds': waits,
                           'other_control_logging_save_seconds': total-receive-pl_section-waits},
              'assumptions': {'setup_fixture_and_alignment_seconds': [15, 30],
                              'initial_move_contact_per_network_seconds': [1, 3],
                              'lightweight_judgment_and_report_seconds': [1, 3],
                              'fault_classifier_integrated': False, 'mechanical_cycle_measured': False,
                              'fixed_contacts_with_electronic_va_vb_selection': True,
                              'each_network_independent_run_and_logging': True,
                              'test_plan_coordinates_and_reference_prepared': True},
              'five_network_electrical_seconds': measurement,
              'estimated_board_cycle_seconds': [estimate_min, estimate_max],
              'alternative_mechanical_va_vb': {'extra_moves': extra_moves,
                                              'assumed_seconds_per_extra_move': [0.5, 1.5],
                                              'extra_seconds': [extra_moves*0.5, extra_moves*1.5],
                                              'estimated_board_cycle_seconds': [estimate_min+extra_moves*0.5, estimate_max+extra_moves*1.5]},
              'estimate_is_not_full_system_acceptance': True,
              'low_frequency_capture_enabled': False}
    (output/'estimate.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
