# PL计算结果与模型输入适配V1

本次范围是接口和导出格式：保留既有FDB4采集和PL计算，把结果整理成每个测量条件的六项特征。只使用Python 3.8+标准库，不连接板卡、SDK或NPU，不修改现有网页和运行程序。

## 交给训练端的数据

固定顺序为：

| 列 | 数值 |
|---|---|
| F0 | Re(C/A) |
| F1 | Im(C/A) |
| F2 | DC_C / abs(A) |
| F3 | residual_C_RMS / abs(A) |
| F4 | Re(B/A) |
| F5 | Im(B/A) |

A=Rs之前，B=Rs之后，C=当前测点，A_check是重复的A参考。Rs实装档位固定100Ω/10kΩ。

特征定义标识为`pl-lsq8192-dds-mean-v1`。顺序与队友包的六项排列一致，计算定义沿用现有PL：每帧8192点最小二乘基波/DC拟合；同条件复相量平均后求比值；F3的分子为逐帧拟合残差功率平均后开方。这与队友旧` sense-periodic4-f64-v1 `的4周期重采样、对齐波形平均并不等价，因此以独立版本交给队友重新训练，不能只改旧模型的接口标签。

详细机器可读定义见`FEATURE_CONTRACT.json`。输出的`value`固定六个数，`available`固定六项；测点、频率、Rs和来源位于`condition`等字段。未进行标定时保留ADC_counts作为通道统计单位，六项比值仍按记录值导出；实际标定信息可以单独附带。

## 程序怎样调用

现有主任务取得同条件A/B/C的`metadata`、`actual`及`status`后，按`examples/condition_input.json`组织输入：

```python
from model_export import export_condition, model_input

# request由现有测量任务填写，不在适配器中重新采样。
features = export_condition(request)
model_json, float32_bytes = model_input([features])
```

`model_json`为逐条件float32六列矩阵及身份信息，`float32_bytes`为相同数据的小端连续字节。训练端负责按测点、频率、Rs拼接、处理掩码和归一化，组成其模型最终张量。六列矩阵不是擅自假定的470维NPU输入。

## 命令行导出

在本目录运行，下列例子均为明确标注的合成数据：

```bash
python -B export_results.py condition --input examples/condition_input.json --output outputs/features.json --csv outputs/features.csv --model-json outputs/model_input.json --f32 outputs/model_input.f32
```

多个条件使用`session`模式，输入为`{"schema":"zhixin.pl-session/v1","conditions":[单条件输入,...]}`；输出会保留条件次序，拒绝重复condition_id。

训练端可提供`--training-profile`，按示例`examples/training_profile.json`固定版本、顺序、前端、Rs和重复帧数。不需要先有训练好的模型，即可核对接口；不同前端或采集配方分别使用相应配置。

历史R4报告可直接导出已有单帧结果：

```bash
python -B export_results.py report --input report.json --scope direct_loopback --output outputs/frame_results.json --csv outputs/frame_results.csv
```

历史直连报告没有真实A/B/C和Rs身份，导出器保留空值，不把一个测点复制成三个通道。F6接口示例与历史真实结果分别保存。

## 文件格式

- `features.json`：完整单条件F6、计算版本、测点、频率、Rs、通道统计和原帧身份。
- `features.csv`：一条件一行，固定元数据列后接F0～F5。
- `model_input.json`：`dtype=float32`、`shape=[条件数,6]`、每条件value/available及身份。
- `model_input.f32`：按JSON中条件顺序排列，每行6个小端float32，每行24字节。没有自定义隐藏文件头。
- `output.schema.json`：单条件结果JSON Schema。

JSON禁止NaN/Infinity，64位采样tick保留十进制字符串；缺少通道、重复物理帧、不同激励混组或顺序不符会明确报错。实物资格信息仅作为元数据保留，本次不开展实板验证，也不把未知状态填成已通过。

## 格式检查

```bash
python -B test_model_export.py
```

这只检查数值映射、字段、顺序、CSV/JSON/float32互相对应及命令行行为，不运行NPU或连接仪器。
