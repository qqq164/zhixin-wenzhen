# 7020裸机固件

当前入口：[candidates/lxb_r4_port/src](candidates/lxb_r4_port/src)。该源对应鹿小班CLG484迁移分支，含ADC/DMA、TCP整帧、DDS控制、数字采集、相位记录、继电器互锁、双IO与PHY适配。

`candidates/lxb_r4_port/tests`保留板级IO、PHY故障模型及真实TCP解析回归。`candidates/dig5_r4_lowrate_bulk/tests/test_tcp_server.c`作为该回归的旧板基线输入保留。

生产编译需要匹配新板的XSA、BSP和Vitis 2025.1。保留Xilinx/AMD来源代码的原版权声明。固件默认关闭DAC/PWM，探针输出为高阻；实际接线和继电器通路仍应按新板方案验收。
