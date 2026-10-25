# 鹿小班独立迁移工程；保留最终 R4 采集、抽取和相位数据通路。
set here [file dirname [file normalize [info script]]]
set root [file normalize [file join $here ../../../..]]
set out [file join $root outputs lxb110_a2]
if {[file exists [file join $out p LXB_R4.xpr]]} {error "迁移工程已存在，禁止覆盖。"}
file mkdir [file join $out sources]
set stream [open [file join $here system.bd] r]
set bd_text [read $stream]
close $stream
set bd_text [string map [list lxb110_a1 lxb110_a2] $bd_text]
set stream [open [file join $out sources system.bd] w]
puts -nonewline $stream $bd_text
close $stream
set_param general.maxThreads 4
create_project LXB_R4 [file join $out p] -part xc7z020clg484-2
set_property target_language Verilog [current_project]
set_property simulator_language Mixed [current_project]
add_files -norecurse [glob [file join $here rtl *.v] [file join $here rtl *.mem]]
add_files -norecurse [file join $out sources system.bd]
update_compile_order -fileset sources_1
open_bd_design [get_files */system.bd]

proc detach_pin {path} {
    set pin [get_bd_pins $path]
    foreach net [get_bd_nets -quiet -of_objects $pin] {disconnect_bd_net $net $pin}
}
proc export_pin {internal name direction {width 1}} {
    if {$width == 1} {
        set port [create_bd_port -dir $direction $name]
    } else {
        set port [create_bd_port -dir $direction -from [expr {$width-1}] -to 0 $name]
    }
    connect_bd_net $port [get_bd_pins $internal]
    return $port
}

# 新板为单颗 256M x16 DDR3、512MiB；采用同几何组织的保守官方速度档。
set ps [get_bd_cells processing_system7_0]
set_property -dict [list \
    CONFIG.PCW_UIPARAM_DDR_PARTNO {MT41K256M16 RE-125} \
    CONFIG.PCW_UIPARAM_DDR_MEMORY_TYPE {DDR 3} \
    CONFIG.PCW_UIPARAM_DDR_BUS_WIDTH {16 Bit} \
    CONFIG.PCW_DDR_RAM_HIGHADDR {0x1FFFFFFF} \
    CONFIG.PCW_PRESET_BANK1_VOLTAGE {LVCMOS 3.3V} \
    CONFIG.PCW_ENET0_ENET0_IO {EMIO} \
    CONFIG.PCW_ENET0_GRP_MDIO_IO {EMIO} \
    CONFIG.PCW_UART0_UART0_IO {EMIO} \
    CONFIG.PCW_SD0_GRP_CD_IO {MIO 47} \
    CONFIG.PCW_EN_CLK1_PORT {1} \
    CONFIG.PCW_FPGA1_PERIPHERAL_FREQMHZ {200.000000}] $ps
foreach i {1 2 3 4 5 6 40 41 42 43 44 45 47} {
    set_property CONFIG.PCW_MIO_${i}_IOTYPE {LVCMOS 3.3V} $ps
}
set_property CONFIG.ROM_FILE [file join $here rtl fdw_sine12_4096.mem] [get_bd_cells dac902_control]

# 原单独 PWM 端口改为 A 通道内部波形，实际输出使用两路 IOBUF。
detach_pin probe_router/digital_in
delete_bd_objs [get_bd_ports digital_in]
delete_bd_objs [get_bd_ports pwm_out]
create_bd_cell -type module -reference fdw_lxb_probe_io probe_pads
create_bd_cell -type module -reference fdw_pwm_axi pwm_control_b
create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 lxb_gpio
set_property -dict [list CONFIG.C_GPIO_WIDTH 7 CONFIG.C_ALL_OUTPUTS 1 \
    CONFIG.C_DOUT_DEFAULT 0x00000000 CONFIG.C_IS_DUAL 1 CONFIG.C_GPIO2_WIDTH 2 \
    CONFIG.C_ALL_OUTPUTS_2 1 CONFIG.C_DOUT_DEFAULT_2 0x00000000] [get_bd_cells lxb_gpio]
create_bd_cell -type ip -vlnv xilinx.com:ip:xlslice:1.0 output_permit
set_property -dict [list CONFIG.DIN_WIDTH 32 CONFIG.DIN_FROM 3 CONFIG.DIN_TO 3 CONFIG.DOUT_WIDTH 1] [get_bd_cells output_permit]
connect_bd_net [get_bd_pins probe_router/status_word] [get_bd_pins output_permit/Din]
connect_bd_net [get_bd_pins output_permit/Dout] [get_bd_pins probe_pads/permit]
connect_bd_net [get_bd_pins lxb_gpio/gpio2_io_o] [get_bd_pins probe_pads/output_mask]
connect_bd_net [get_bd_pins probe_router/pwm_out] [get_bd_pins probe_pads/pwm_a]
connect_bd_net [get_bd_pins pwm_control_b/pwm_out] [get_bd_pins probe_pads/pwm_b]
connect_bd_net [get_bd_pins probe_pads/digital_in] [get_bd_pins probe_router/digital_in]
export_pin probe_pads/probe_io probe_io IO 2

# 把新增 PWM 的活动状态加入原路由互锁，防止运行时切换采集模式。
detach_pin probe_router/pwm_active
create_bd_cell -type ip -vlnv xilinx.com:ip:util_vector_logic:2.0 all_pwm_activity
set_property -dict [list CONFIG.C_OPERATION or CONFIG.C_SIZE 1] [get_bd_cells all_pwm_activity]
connect_bd_net [get_bd_pins pwm_activity_or/Res] [get_bd_pins all_pwm_activity/Op1]
connect_bd_net [get_bd_pins pwm_control_b/activity] [get_bd_pins all_pwm_activity/Op2]
connect_bd_net [get_bd_pins all_pwm_activity/Res] [get_bd_pins probe_router/pwm_active]

# 新增寄存器不改变原 R4 的 DMA、采集、DDS 和相位接口地址。
set_property CONFIG.NUM_MI 9 [get_bd_cells ps7_0_axi_periph]
foreach {master slave} {M07_AXI lxb_gpio/S_AXI M08_AXI pwm_control_b/S_AXI} {
    connect_bd_intf_net [get_bd_intf_pins ps7_0_axi_periph/$master] [get_bd_intf_pins $slave]
}
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK0] \
    [get_bd_pins lxb_gpio/s_axi_aclk] [get_bd_pins pwm_control_b/s_axi_aclk] \
    [get_bd_pins ps7_0_axi_periph/M07_ACLK] [get_bd_pins ps7_0_axi_periph/M08_ACLK]
connect_bd_net [get_bd_pins rst_ps7_0_100M/peripheral_aresetn] \
    [get_bd_pins lxb_gpio/s_axi_aresetn] [get_bd_pins pwm_control_b/s_axi_aresetn] [get_bd_pins probe_pads/resetn]
connect_bd_net [get_bd_pins rst_ps7_0_100M/interconnect_aresetn] \
    [get_bd_pins ps7_0_axi_periph/M07_ARESETN] [get_bd_pins ps7_0_axi_periph/M08_ARESETN]
assign_bd_address -offset 0x41220000 -range 0x00010000 -target_address_space [get_bd_addr_spaces processing_system7_0/Data] [get_bd_addr_segs lxb_gpio/S_AXI/Reg]
assign_bd_address -offset 0x43C40000 -range 0x00001000 -target_address_space [get_bd_addr_spaces processing_system7_0/Data] [get_bd_addr_segs pwm_control_b/S_AXI/reg0]
for {set i 0} {$i < 7} {incr i} {
    set name relay_bit_$i
    create_bd_cell -type ip -vlnv xilinx.com:ip:xlslice:1.0 $name
    set_property -dict [list CONFIG.DIN_WIDTH 7 CONFIG.DIN_FROM $i CONFIG.DIN_TO $i CONFIG.DOUT_WIDTH 1] [get_bd_cells $name]
    connect_bd_net [get_bd_pins lxb_gpio/gpio_io_o] [get_bd_pins $name/Din]
    export_pin $name/Dout k[expr {$i+1}]_ctrl O
}

# PHY 通过 PL 的 RGMII 与 MDIO 接 PS GEM0，桥寄存器使用空闲 MDIO 地址 8。
create_bd_cell -type ip -vlnv xilinx.com:ip:gmii_to_rgmii:4.1 ethernet_bridge
set_property -dict [list CONFIG.SupportLevel Include_Shared_Logic_in_Core \
    CONFIG.C_EXTERNAL_CLOCK false CONFIG.C_PHYADDR 8 CONFIG.RGMII_TXC_SKEW 0] [get_bd_cells ethernet_bridge]
connect_bd_intf_net [get_bd_intf_pins processing_system7_0/GMII_ETHERNET_0] [get_bd_intf_pins ethernet_bridge/GMII]
connect_bd_intf_net [get_bd_intf_pins processing_system7_0/MDIO_ETHERNET_0] [get_bd_intf_pins ethernet_bridge/MDIO_GEM]
connect_bd_net [get_bd_pins processing_system7_0/FCLK_CLK1] [get_bd_pins ethernet_bridge/clkin]
connect_bd_net [get_bd_pins rst_ps7_0_100M/peripheral_reset] [get_bd_pins ethernet_bridge/tx_reset] [get_bd_pins ethernet_bridge/rx_reset]
foreach {pin port direction width} {
    rgmii_txd eth_txd O 4 rgmii_txc eth_txc O 1 rgmii_tx_ctl eth_tx_ctl O 1
    rgmii_rxd eth_rxd I 4 rgmii_rxc eth_rxc I 1 rgmii_rx_ctl eth_rx_ctl I 1
    mdio_phy_mdc eth_mdc O 1
} {export_pin ethernet_bridge/$pin $port $direction $width}
create_bd_cell -type module -reference fdw_lxb_mdio mdio_pad
foreach suffix {i o t} {connect_bd_net [get_bd_pins ethernet_bridge/mdio_phy_$suffix] [get_bd_pins mdio_pad/mdio_$suffix]}
export_pin mdio_pad/mdio eth_mdio IO
export_pin processing_system7_0/UART0_TX uart_tx O
export_pin processing_system7_0/UART0_RX uart_rx I
# PHY 复位串联 R46 在图纸标为 NC，不假定 H17 能实际控制复位。
if {[llength [get_bd_pins -quiet processing_system7_0/ENET0_EXT_INTIN]]} {
    create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 no_phy_interrupt
    set_property CONFIG.CONST_VAL 0 [get_bd_cells no_phy_interrupt]
    connect_bd_net [get_bd_pins no_phy_interrupt/dout] [get_bd_pins processing_system7_0/ENET0_EXT_INTIN]
}

validate_bd_design
save_bd_design
write_bd_tcl [file join $out lxb_system.tcl]
generate_target all [get_files */system.bd]
set duplicate [get_files -quiet -all */system_adc_clk_wiz_0.xdc]
if {[llength $duplicate] == 1} {
    set_property USED_IN_SYNTHESIS false $duplicate
    set_property USED_IN_IMPLEMENTATION false $duplicate
}
add_files -norecurse [make_wrapper -files [get_files */system.bd] -top]
set_property top system_wrapper [get_filesets sources_1]
foreach file [glob [file join $here constrs *.xdc]] {
    add_files -fileset constrs_1 -norecurse $file
    if {[file tail $file] eq "ethernet_clock.xdc"} {
        set_property PROCESSING_ORDER EARLY [get_files $file]
        set_property USED_IN_SYNTHESIS false [get_files $file]
    }
    if {[string match *_timing.xdc $file]} {
        set_property USED_IN_SYNTHESIS false [get_files $file]
        set_property PROCESSING_ORDER LATE [get_files $file]
    }
}
update_compile_order -fileset sources_1
set_property STEPS.OPT_DESIGN.TCL.PRE [file join $here hold_guard.tcl] [get_runs impl_1]
write_hw_platform -fixed -file [file join $out lxb_r4_platform.xsa]
puts "FDW_LXB_PROJECT_CREATED=[file join $out p LXB_R4.xpr]"
exit
