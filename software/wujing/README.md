# 悟净端采集、特征与求解

| 分类 | 文件 |
|---|---|
| 模拟/数字控制与采集 | `adc_client.py`、`analog_dig5_client.py`、`digital_client.py`及相关客户端 |
| 原码/数字特征 | `adc_features.py`、`digital_features.py` |
| PL实际运行端 | `pl_runtime/sdk_backend.py`、`processor.py`、`phase_contract.py` |
| R4完整帧与低频 | `candidates/r4_lowrate_bulk/r4_capture_client.py`、`r4_board_validation.py` |
| CPU RC节点求解 | `candidates/rc_network_cpu_v1/rc_network.py`、`run_rc_network.py` |
| 离线回归 | `tests/`和RC求解器自带测试 |
| 历史无机械在线流程 | `no_mechanics_flow.py`、`run_no_mechanics_flow.py` |

RC求解器执行已知R/C参数下的正向预测和复相量比较。未知参数反演、通用故障定位尚未加入。CPU不重新承担整帧IQ/FFT主计算。

无机械在线流程保留作R3直连历史参考；不作为当前新板真实DUT的正式测量入口。`r4_board_validation.py`用于配套直连验收，运行前必须确认实际接线与输出条件。
