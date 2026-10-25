"""独立CPU求解入口；不导入SDK、不接网口采集、不改网页服务或启动镜像。"""
import argparse
import json
from pathlib import Path

from rc_network import NetworkError, compare_measurements, predict_sweep


def main():
    """输出文件必须不存在，防止覆盖既有结果。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--frequencies', type=float, nargs='+', default=[1, 10, 100, 1000, 10000, 100000])
    parser.add_argument('--measurements', type=Path)
    parser.add_argument('--absolute-tolerance', type=float, default=0.01)
    parser.add_argument('--relative-tolerance', type=float, default=0.05)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        model = json.loads(args.model.read_text(encoding='utf-8-sig'))
        if args.measurements:
            measurements = json.loads(args.measurements.read_text(encoding='utf-8-sig'))
            result = compare_measurements(model, measurements, args.absolute_tolerance, args.relative_tolerance)
        else:
            result = predict_sweep(model, args.frequencies)
        with args.output.open('x', encoding='utf-8') as stream:
            json.dump(result, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.write('\n')
    except (NetworkError, OSError, ValueError) as error:
        parser.exit(2, 'CPU求解失败：' + str(error) + '\n')
    print('FDW_RC_CPU_PASS ' + str(args.output))


if __name__ == '__main__':
    main()
