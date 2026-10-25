# 在已打开的整板综合或实现设计中执行，避免XDC解析器忽略控制语句。
set synchronizer_checks {}
foreach stem {cmd cancel ack data cmd_ack idle fault} {
    set first [get_pins -hier -filter "NAME =~ U_fdw_dsp_top/reader/${stem}_sync1_reg/D"]
    if {[llength $first] != 1} {error "同步器约束未匹配: $stem"}
    lappend synchronizer_checks [list $stem [llength $first]]
}
set reset_clr [get_pins -hier -filter {NAME =~ U_fdw_dsp_top/reset_pipe_reg*/CLR || NAME =~ U_fdw_dsp_top/reader/*reset_pipe_reg*/CLR}]
if {[llength $reset_clr] < 6} {error "复位链约束未匹配"}
set payload_source [get_cells -hier -filter {IS_SEQUENTIAL && NAME =~ U_fdw_dsp_top/reader/payload_hold_reg*}]
set payload_target [get_cells -hier -filter {IS_SEQUENTIAL && NAME =~ U_fdw_dsp_top/reader/payload_gp_reg*}]
set desc_source [get_cells -hier -filter {IS_SEQUENTIAL && NAME =~ U_fdw_dsp_top/reader/cmd_base_hold_reg*}]
set desc_target [get_cells -hier -filter {IS_SEQUENTIAL && NAME =~ U_fdw_dsp_top/reader/araddr_reg*}]
if {[llength $payload_source] != 512 || [llength $payload_target] != 512 || [llength $desc_source] < 26 || [llength $desc_target] < 26} {
    error "邮箱总线约束未匹配"
}
puts "FDW_CDC_OBJECTS_VERIFIED $synchronizer_checks reset=[llength $reset_clr] payload=[llength $payload_source]/[llength $payload_target] address=[llength $desc_source]/[llength $desc_target]"
