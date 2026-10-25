# PHY 接收时钟在千兆模式为 125MHz，百兆/十兆更慢。
# 顶层主时钟必须早于官方 IP 的派生时钟和输入延时约束建立。
create_clock -name lxb_rgmii_rxc -period 8.000 [get_ports eth_rxc]
