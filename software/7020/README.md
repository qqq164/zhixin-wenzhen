# 7020裸机固件

原113固化对应源码入口：[candidates/lxb_r4_port/src](candidates/lxb_r4_port/src)。该源对应鹿小班CLG484迁移分支，含ADC/DMA、TCP整帧、DDS控制、数字采集、相位记录、继电器互锁、双IO与PHY适配。

`candidates/lxb_r4_port/tests`保留板级IO、PHY故障模型及真实TCP解析回归。`candidates/dig5_r4_lowrate_bulk/tests/test_tcp_server.c`作为该回归的旧板基线输入保留。

生产编译需要匹配新板的XSA、BSP和Vitis 2025.1。保留Xilinx/AMD来源代码的原版权声明。固件默认关闭DAC/PWM，探针输出为高阻；实际接线和继电器通路仍应按新板方案验收。

## SENSE117独立候选

新增[candidates/lxb_r4_sense117/src](candidates/lxb_r4_sense117/src)，仅修改采样选择与控制互锁相关C/头文件；RTL与原固化基线保留。新ELF/BOOT已准备，尚未加载或写入QSPI。新增协议、六组原生回归及实物验收边界见[SENSE117说明](../../docs/SENSE117连续激励与采样编排.md)。
