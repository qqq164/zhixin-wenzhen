#ifndef FDW_LXB_PHY_H
#define FDW_LXB_PHY_H
#include "xemacps.h"

/* 在官方 PHY 软复位完成后配置并回读 RTL8211E 的收发时钟延时。 */
int fdw_lxb_phy_delays(XEmacPs *instance, u32 phy_address);
/* 官方 GMII/RGMII 桥仅使用全双工，清除 PHY 的半双工协商能力。 */
u16 fdw_lxb_phy_full_duplex(u16 advertisement, u32 gigabit);

#endif
