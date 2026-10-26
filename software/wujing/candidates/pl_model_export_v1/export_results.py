"""导出现有PL报告或同条件六项特征；输出文件不覆盖，板端程序不运行。"""
import argparse
import csv
import hashlib
import json
from pathlib import Path

from model_export import export_condition, export_report, model_input, require


def main():
    """JSON保留完整身份，CSV方便训练人员检查或整理数据。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('report', 'condition', 'session'))
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--csv', type=Path)
    parser.add_argument('--training-profile', type=Path)
    parser.add_argument('--model-json', type=Path)
    parser.add_argument('--f32', type=Path)
    parser.add_argument('--scope', choices=('unspecified', 'direct_loopback', 'virtual_network', 'real_dut'), default='unspecified')
    args = parser.parse_args()
    paths = [p for p in (args.output, args.csv, args.model_json, args.f32) if p is not None]
    require(len({p.resolve() for p in paths}) == len(paths), '各输出文件路径必须不同')
    for path in paths:
        if path is not None and path.exists():
            raise FileExistsError('拒绝覆盖已有导出：' + str(path))
    raw = args.input.read_bytes()
    source = json.loads(raw.decode('utf-8-sig'))
    if args.mode == 'report':
        require(args.model_json is None and args.f32 is None, '历史单点报告不能直接构造模型六列输入')
        result = export_report(source, hashlib.sha256(raw).hexdigest(), args.scope)
        rows = []
        for row in result['frames']:
            rows.append({'name': row['name'], 'frequency_hz': row['frequency_hz'],
                         'record_id': row['record_id'], 'first_tick': row['first_tick'],
                         'real_counts': row['phasor_counts'][0], 'imag_counts': row['phasor_counts'][1],
                         'dc_counts': row['dc_counts'], 'ls_residual_rms_counts': row['ls_residual_rms_counts']})
    else:
        profile = json.loads(args.training_profile.read_text(encoding='utf-8-sig')) if args.training_profile else None
        if args.mode == 'session':
            require(source.get('schema') == 'zhixin.pl-session/v1'
                    and isinstance(source.get('conditions'), list) and source['conditions'], '会话输入格式不符')
            results = [export_condition(item, profile) for item in source['conditions']]
            model, binary = model_input(results)
            result = {'schema': 'zhixin.pl-feature-session/v1', 'conditions': results}
        else:
            result = export_condition(source, profile)
            results = [result]
            model, binary = model_input(results)
        rows = [dict(condition_id=item['condition']['condition_id'],
                     point_id=item['condition']['point_id'],
                     rs_nominal_ohm=item['condition']['rs_nominal_ohm'],
                     frequency_hz=item['actual_frequency_hz'], interval_ticks=item['interval_ticks'],
                     frames=item['frames'], frontend_profile_id=item['condition']['frontend_profile_id'],
                     origin=item['condition']['origin'], feature_contract=item['feature_contract'],
                     **{'F' + str(i): v for i, v in enumerate(item['value'])}) for item in results]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, allow_nan=False, indent=2)
        stream.write('\n')
    if args.csv:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        with args.csv.open('x', encoding='utf-8-sig', newline='') as stream:
            if rows:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
    if args.model_json:
        args.model_json.parent.mkdir(parents=True, exist_ok=True)
        with args.model_json.open('x', encoding='utf-8') as stream:
            json.dump(model, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.write('\n')
    if args.f32:
        args.f32.parent.mkdir(parents=True, exist_ok=True)
        with args.f32.open('xb') as stream:
            stream.write(binary)
    print(json.dumps({'output': str(args.output), 'schema': result['schema'],
                      'rows': len(rows), 'npu_executed': False}, ensure_ascii=False))


if __name__ == '__main__':
    main()
