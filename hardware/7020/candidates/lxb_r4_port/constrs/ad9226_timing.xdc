# 外送时钟与接收时钟来自同一MMCM，ODDR引脚在OOC模块展开后才能查询。
create_generated_clock -name adc_output_clock -source [get_pins -hierarchical -filter {NAME =~ */adc_clk_oddr/C}] -divide_by 1 [get_ports adc_clk]

# 芯片输出延迟为3.5～7ns；最大值另留3ns外部往返预算，尚非实测线缆延迟。
# 必须确认DRVDD和负载满足手册条件，并以实物走线验收替换临时预算。
set_input_delay -clock adc_output_clock -min 3.500 [get_ports {adc_data[*] adc_otr}]
set_input_delay -clock adc_output_clock -max 10.000 [get_ports {adc_data[*] adc_otr}]

# 禁用Clock Wizard重复输入主时钟XDC后，保留其相移分析模式。
set_property PHASESHIFT_MODE WAVEFORM [get_cells -hierarchical -filter {REF_NAME == MMCME2_ADV}]
