# 外部数字输入没有与 FCLK 固定的相位关系，只屏蔽进入第一级同步寄存器的路径。
# 第二级同步及后续采集均继续分析真实时序，不能屏蔽整个采集模块。
set fdw_meta_d [get_pins -hierarchical -filter {NAME =~ */probe_router/digital_meta_reg*/D || NAME =~ */probe_router/inst/digital_meta_reg*/D}]
# 对象数量和实际例外由构建Tcl独立检查，XDC只保留约束命令。
set_false_path -from [get_ports {probe_io[*]}] -to $fdw_meta_d
