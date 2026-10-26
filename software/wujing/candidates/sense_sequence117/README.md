# SENSE117 连续激励采样编排候选

2026-10-26。适用于鹿小班XC7Z020 CLG484的 **LXB-R4-SENSE117** 固件。
当前是独立候选，旧113 QSPI固化程序和悟净网页没有被替换。模块导入不会连接设备、采样或加载SDK。

## 接口与流程

- `configure_analog_route(rs_nominal_ohm, confirm_lifted)`：HALT后必须收到抬针确认才设置针1=DAC、针2=ADC；Rs只能100Ω或10kΩ。确认函数要接真实运动反馈或人工确认，本模块没有电机驱动。
- 完成落针/接触后，外层主任务按原有接口设置DAC频率/幅度并启波。本模块不擅自设置激励。
- `capture_group(...)`：等待显式配置的DUT稳定时间，依次VA→VB→PROBE→VA复读；`include_probe=False`使用VA→VB→VA。每帧收齐、CRC/身份校验后才切下一路。
- `process_frame(decoded, point)`可调用现有悟净`PlOnlyProcessor.process`，逐样本IQ/FFT仍在PL。若提供该回调，会用现有DDS参考标量换算计算VA前后幅度漂移与包裹相位差；`reference_limits`由测量配方明确给出，超限立即停波、整组失败。
- 只收原码时，参考帧会完整保存，尚未计算漂移。固定等待和VA重复一致都不等于整个DUT已完成稳态/精度验收，结果保留`measurement_qualified=false`，不会直接给故障结论。
- 成功采完一组会保留连续激励以便下一组；**外层整次任务必须用`finally: flow.stop()`停波**。异常和取消自动HALT，保持继电器原位。

## 7020新增协议

| 命令 | 含义 |
|---|---|
| `SENSE_SELECT VA 37` | 只改K5/K7，等待37ms；立即返回，通过状态轮询等待完成 |
| `SENSE_SELECT VB 37` | 同上 |
| `SENSE_SELECT PROBE 37` | ADC接针2测点，K7统一归零 |
| `SENSE_STATUS` | 当前路径代际、七位mask、通路、Rs标称档、剩余等待和写回错误 |
| `ADC_SENSE_STATUS` | 已完成record_id及采集时锁定的路径代际/mask |

selected：1=VA、2=VB、3=PROBE。允许等待10～60000ms，**10ms只是原有调试下限，不是实物稳定保证**。SENSE_SELECT不执行DAC_SET、DAC_ENABLE或HALT，不改频率/幅度/相位/tag。

K1～K4/K6仍走旧RELAY_SET停波保护；ADC采集或二进制帧传输锁定期间两种切换都拒绝。纯采样只允许针1=DAC/针2=ADC、数字输出关闭、模拟模式空闲和无DAC错误。GPIO读回只验证寄存器，不代表继电器触点反馈。

错误码：-20采集/传输忙，-41选通计时中，-42针路/模式/激励条件不符，-43控制寄存器写回失败，-44代际耗尽需停机检查；其他原有错误码保持。写回失败后ADC拒绝；停止激励后可用RELAY_SET重新配置，不能用SENSE_SELECT掩盖错误。

128字节FDB4头和8192个int16负载保持原协议。新增身份通过ADC_SENSE_STATUS与record_id关联，任务报告记录Rs实测值、校准ID、测点/组号、前后状态和原始packet摘要。校准ID只是关联字段，不能代替真实校准证据。

## 使用方式

在包含本模块的独立目录中导入；外层任务提供已核验的频点配置和PL处理器。以下只是调用结构，不应在未完成测头接线验收前执行：

```python
from sense_sequence import SenseSequence

flow = SenseSequence()
try:
    # 实际抬针反馈、接触确认和DAC启波由整机主任务提供。
    # point来自已保存的R4频点配方，必须含滤波增益、相位和PL配置。
    report = flow.capture_group(
        folder=新的结果目录, point=point, group_id=任务组号, node_id=实际节点名,
        rs_nominal_ohm=100, rs_measured_ohm=电阻实测值,
        calibration_id=实际校准版本或明确的未校准标记,
        path_wait_ms=实测确定的通路等待毫秒,
        dut_wait_seconds=按网络确定的稳态等待秒,
        process_frame=lambda frame, p: processor.process(
            frame['samples'], frame['metadata'], point_index, frame['flags']),
        reference_limits=配方确定的幅度相位漂移限值)
finally:
    flow.stop()
```

漂移限值格式为`{'relative_amplitude': 相对幅度差, 'phase_degrees': 度}`，这里不预设正式误差指标。
结果不直接兼容模型整张量；A/B/C/A_check命名、标定和特征合同仍按现有模型导出接口对接，不能拿采集完成冒充故障诊断完成。

## 验证边界

离线测试使用合成帧和模拟寄存器。需要先临时加载新7020 ELF，以真实测量头验证已知电阻/RC的连续选通，再考虑替换BOOT并冷启动。此次不改变DDS/ADC/CIC/IQ/FFT RTL、PL引脚或时序约束，也不自动更新固化介质。
