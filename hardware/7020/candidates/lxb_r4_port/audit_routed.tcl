# 审查已完成路由的新板设计；只去掉实现时额外增加的保持裕量。
set audit_root {C:/Users/33525/FDW_NEXT/outputs/checkpoint_110_hellofpga_migration_audit}
if {![info exists audit_out]} {set audit_out [file join $audit_root implementation_02]}
if {[file exists [file join $audit_out timing_metrics.txt]]} {error "本轮审查已存在，禁止覆盖。"}
file mkdir $audit_out
write_xdc -type timing [file join $audit_out timing_with_guard.xdc]

# 用户及官方 IP 的原 XDC 没有显式时钟不确定度；零值恢复原保持约束。
# 仍保留器件自动分析的时钟树、抖动及所有外部输入输出预算。
if {[file exists [file join $audit_out original_timing.xdc]]} {
    reset_timing
    read_xdc [file join $audit_out original_timing.xdc]
} else {
    set_clock_uncertainty -hold 0.000 [all_clocks]
}
write_xdc -type timing [file join $audit_out timing_actual.xdc]
report_timing_summary -delay_type min_max -max_paths 20 -report_unconstrained -file [file join $audit_out timing_summary.rpt]
report_timing -delay_type min -max_paths 30 -file [file join $audit_out hold_paths.rpt]
report_cdc -details -file [file join $audit_out cdc.rpt]
report_drc -file [file join $audit_out drc.rpt]
report_methodology -file [file join $audit_out methodology.rpt]
report_clocks -file [file join $audit_out clocks.rpt]
report_clock_interaction -file [file join $audit_out clock_interaction.rpt]
report_io -file [file join $audit_out io.rpt]
report_exceptions -file [file join $audit_out exceptions.rpt]
report_utilization -file [file join $audit_out utilization.rpt]
set f [open [file join $audit_out actual_pins.csv] w]
puts $f {port,direction,pin,iostandard,bank}
foreach p [lsort [get_ports]] {
    set pin [get_property PACKAGE_PIN $p]
    set bank ""
    if {$pin ne "" && [llength [get_package_pins -quiet $pin]] == 1} {
        set bank [get_property BANK [get_package_pins $pin]]
    }
    puts $f [join [list $p [get_property DIRECTION $p] $pin [get_property IOSTANDARD $p] $bank] ,]
}
close $f
set f [open [file join $audit_out endpoint_counts.txt] w]
set sync_d [get_pins -hier -filter {NAME =~ */probe_router/inst/digital_meta_reg*/D}]
set adc_regs [get_pins -hier -filter {NAME =~ */adc_capture/inst/filter_input_code_reg*/D || NAME =~ */adc_capture/inst/filter_input_overrange_reg/D}]
set rxc [get_clocks -of_objects [get_ports eth_rxc]]
puts $f "digital_sync_first_stage=[llength $sync_d]"
puts $f "adc_sample_inputs=[llength $adc_regs]"
puts $f "rgmii_rx_primary_clock=$rxc"
puts $f "rgmii_rx_period=[get_property PERIOD $rxc]"
puts $f "probe_iobufs=[llength [get_cells -hier -filter {REF_NAME == IOBUF && NAME =~ */probe_pads/*}]]"
close $f
report_timing -from [get_ports {adc_data[*] adc_otr}] -to $adc_regs -delay_type min_max -max_paths 30 -file [file join $audit_out adc_lowrate_input_paths.rpt]
write_checkpoint [file join $audit_out system_wrapper_routed_actual.dcp]
set wns [get_property SLACK [get_timing_paths -delay_type max -max_paths 1]]
set whs [get_property SLACK [get_timing_paths -delay_type min -max_paths 1]]
set f [open [file join $audit_out timing_metrics.txt] w]
puts $f "WNS=$wns\nWHS=$whs"
close $f
if {$wns <= 0.2 || $whs <= 0.2} {error "实际时序未达到0.2ns余量，禁止生成位流。"}
if {[llength $sync_d] != 2 || [llength $rxc] != 1 || [llength $adc_regs] != 13} {error "数字同步器、低频入口或PHY时钟对象数量不符。"}
if {[llength [get_drc_violations -quiet -filter {SEVERITY == Error || SEVERITY == {Critical Warning}}]]} {error "DRC存在错误或严重警告。"}
puts "LXB_ROUTED_TIMING_AUDIT_READY WNS=$wns WHS=$whs"
