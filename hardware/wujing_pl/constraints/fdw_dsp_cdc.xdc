# 仅约束已知跨域结构；对象数量检查由配套Tcl脚本执行。
# 同步器只排除第一级D端，第二级和本域逻辑继续接受真实时钟检查。
set_false_path -to [get_pins -hier -filter {NAME =~ U_fdw_dsp_top/reader/cmd_sync1_reg/D}]
set_false_path -to [get_pins -hier -filter {NAME =~ U_fdw_dsp_top/reader/cancel_sync1_reg/D}]
set_false_path -to [get_pins -hier -filter {NAME =~ U_fdw_dsp_top/reader/ack_sync1_reg/D}]
set_false_path -to [get_pins -hier -filter {NAME =~ U_fdw_dsp_top/reader/data_sync1_reg/D}]
set_false_path -to [get_pins -hier -filter {NAME =~ U_fdw_dsp_top/reader/cmd_ack_sync1_reg/D}]
set_false_path -to [get_pins -hier -filter {NAME =~ U_fdw_dsp_top/reader/idle_sync1_reg/D}]
set_false_path -to [get_pins -hier -filter {NAME =~ U_fdw_dsp_top/reader/fault_sync1_reg/D}]
# 异步复位只排除同步释放链的CLR端。
set_false_path -to [get_pins -hier -filter {NAME =~ U_fdw_dsp_top/reset_pipe_reg*/CLR || NAME =~ U_fdw_dsp_top/reader/*reset_pipe_reg*/CLR}]
# 数据和描述符在往返确认期间保持不变，仍需限制延迟和总线偏斜。
set_max_delay -datapath_only 20.0 -from [get_cells -hier -filter {IS_SEQUENTIAL && NAME =~ U_fdw_dsp_top/reader/payload_hold_reg*}] -to [get_cells -hier -filter {IS_SEQUENTIAL && NAME =~ U_fdw_dsp_top/reader/payload_gp_reg*}]
set_bus_skew 20.0 -from [get_cells -hier -filter {IS_SEQUENTIAL && NAME =~ U_fdw_dsp_top/reader/payload_hold_reg*}] -to [get_cells -hier -filter {IS_SEQUENTIAL && NAME =~ U_fdw_dsp_top/reader/payload_gp_reg*}]
set_max_delay -datapath_only 5.0 -from [get_cells -hier -filter {IS_SEQUENTIAL && NAME =~ U_fdw_dsp_top/reader/cmd_base_hold_reg*}] -to [get_cells -hier -filter {IS_SEQUENTIAL && NAME =~ U_fdw_dsp_top/reader/araddr_reg*}]
set_bus_skew 5.0 -from [get_cells -hier -filter {IS_SEQUENTIAL && NAME =~ U_fdw_dsp_top/reader/cmd_base_hold_reg*}] -to [get_cells -hier -filter {IS_SEQUENTIAL && NAME =~ U_fdw_dsp_top/reader/araddr_reg*}]
