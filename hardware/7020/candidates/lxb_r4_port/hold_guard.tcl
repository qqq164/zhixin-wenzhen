# 仅用于实现时增加保持优化余量，最终验收移除此附加量后报告原始约束时序。
# 不修改 ADC、DAC、网口的外部输入输出时序预算。
set_clock_uncertainty -hold 0.225 [all_clocks]
