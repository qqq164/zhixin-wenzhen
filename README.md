# 智芯问诊

**基于FPGA的双模飞针电路检测与诊断系统**

项目面向PCB调试与故障排查，结合双飞针定位、模拟扫频测量、两路数字检测与电路模型分析。Zynq-7020负责实时激励、采样和传输，悟净LITE负责调度、PL端IQ/FFT计算与CPU端RC节点方程求解。

本仓库按实际工作模块分类整理，状态基准为 **2026-10-26**。当前已有经过验证的子链路和算法，整机自动测量及真实DUT故障判断仍在对接中。

## 已做工作的分类

| 分类 | 当前内容 | 入口 |
|---|---|---|
| FPGA硬件逻辑 | 鹿小班7020 RTL/引脚约束/BD；ADC、DDS/DAC、低频抽取、数字采集、相位参考；悟净IQ/FFT核 | [hardware](hardware/README.md) |
| 7020裸机固件 | ADC/DMA、整帧TCP、DDS配置、双IO/PWM、继电器互锁与PHY适配 | [software/7020](software/7020/README.md) |
| 悟净采集与分析 | 客户端、帧校验、数字特征、PL驱动、相位契约、RC节点预测和相量比较 | [software/wujing](software/wujing/README.md) |
| 网页界面 | CircuitScout汉化界面、KiCad导入/选点、串口和探针控制入口 | [web/circuitscout](web/circuitscout/README.md) |
| 测量头与模拟前端 | 端口电流/阻抗与节点响应方案、采样选择、Rs与继电器接线参考 | [analog_frontend](analog_frontend/README.md) |
| 机械结构 | 侧壁连接支架V8/V11的可编辑实体、STEP和修改后STL | [mechanical](mechanical/README.md) |

## 2026-10-26新增

- **SENSE117候选**：保留原停波保护，新增K5/K7连续激励下的采样选择、采集/传输互锁、通路身份绑定及VA→VB→C→VA复读编排。悟净候选已存入独立SD目录；新7020固件已准备，尚未加载或固化。
- **PL结果到模型接口**：固定六项特征、100Ω/10kΩ条件、JSON/CSV/小端float32导出，版本为`pl-lsq8192-dds-mean-v1`。训练端按本定义训练并组装最终模型输入。
- [SENSE117实施与验证边界](docs/SENSE117连续激励与采样编排.md) · [模型格式对接](docs/PL结果与模型输入格式对接.md) · [直接下载模型对接包](deliverables/PL结果_模型输入格式对接包_20261026.zip)

这些新增模块尚未接成网页统一主任务；格式测试和模拟测试不代表实物精度或NPU诊断已经验收。

## 从哪里开始

- [项目简介与两种测量模式](docs/项目简介与方案.md)
- [当前进度与验证范围](docs/当前进度.md)
- [测量流程与故障特征](docs/测量流程与故障特征.md)
- [队友对接与下一步](docs/队友对接.md)
- [运行与验证](docs/运行与验证.md)
- [源码版本与来源](docs/源码版本与来源.md)
- [验证摘要](verification/历史验收摘要.json)

## 数据链路

```mermaid
flowchart LR
    A[悟净CPU：任务与配置] --> B[7020：DDS/DAC与ADC/数字采集]
    B -->|完整帧 TCP| C[悟净CPU：校验与DDR准备]
    C --> D[悟净PL：IQ/FFT]
    D --> E[CPU：复相量与RC模型比较]
    E --> G[CPU：六项特征导出]
    G --> F[结果存档与训练端接口]
```

网页、运动控制、真实测量头和上述数据链路的统一自动任务仍待完成。

## 本地离线验证

安装Python 3.8及以上版本后，在仓库根目录执行：

```bash
python -B tools/run_public_checks.py
```

该入口只运行既有离线测试，不连接板卡或启动激励。实际板上测量需要匹配的位流、BSP、SDK、接线和校准条件，详见运行说明。

## 授权与第三方来源

仓库内第三方代码保留原版权声明。CircuitScout相关部分沿用其MIT许可证；其他组件依各自原有声明使用。项目自有部分的整体开源许可证尚未指定，详见[第三方与授权说明](THIRD_PARTY_NOTICES.md)。
