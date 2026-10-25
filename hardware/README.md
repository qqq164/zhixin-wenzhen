# FPGA硬件分类

## 7020实时激励与采集

当前源入口：[candidates/lxb_r4_port](7020/candidates/lxb_r4_port)。保留原目录层次，使BD与建工程脚本的相对路径一致。

- `rtl/`：ADC接收、CIC3抽取、DDS/DAC、数字采集、PWM、跨时钟握手和相位参考。
- `constrs/`：新板引脚、ADC/DAC外部时序、数字异步输入和网口时钟约束。
- `system.bd`、`create_project.tcl`：迁移工程输入及建工程入口。
- `tb_lxb_probe_io.sv`：双向探针IO仿真。

来源为110迁移源，最终生产配套为110/a3，113已完成QSPI与断电冷启动验收。建工程脚本是历史建工程入口，不能将其一次默认实现直接等同于已验收a3位流；本仓库没有重新综合或生成位流。

## 悟净PL计算

入口：[wujing_pl](wujing_pl)。保存自定义IQ/FFT、DDR读帧、控制接口、ROM表、CDC约束和仿真源。厂家SoC工程、工具链和SDK需在本地配套环境准备。
