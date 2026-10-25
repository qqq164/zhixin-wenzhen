# ODDR的外送上升沿位于内部采样负沿，25MHz时与正沿数据相隔半周期。
create_generated_clock -name dac_output_clock -source [get_pins -hierarchical -filter {NAME =~ */dac_clk_oddr/C}] -divide_by 1 -invert [get_ports dac_clk]
# 芯片建立1ns、保持1.5ns；两侧额外各预算3ns，尚未实测线缆与模块偏斜。
set_output_delay -clock dac_output_clock -max 4.000 [get_ports {dac_data[*]}]
set_output_delay -clock dac_output_clock -min -4.500 [get_ports {dac_data[*]}]
# 两域来自同一PS/MMCM参考，保留真实时钟关系和全部数据路径分析。
# 不以整组异步时钟例外覆盖控制邮箱、确认邮箱或第二同步级。
