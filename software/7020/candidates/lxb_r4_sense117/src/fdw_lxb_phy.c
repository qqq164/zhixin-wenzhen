/* 新板 RXDLY 下拉，使用 MDIO 显式启用两方向延时，匹配官方桥接 IP 约束。 */
#include "fdw_lxb_phy.h"

u16 fdw_lxb_phy_full_duplex(u16 advertisement, u32 gigabit)
{
    return advertisement & (u16)~(gigabit ? 0x0100U : 0x02a0U);
}

int fdw_lxb_phy_delays(XEmacPs *instance, u32 phy_address)
{
    u16 id1, id2, saved_page, value, readback;
    int result = -1;
    if (!instance || phy_address > 31U) return -1;
    if (XEmacPs_PhyRead(instance, phy_address, 2U, &id1) != XST_SUCCESS ||
        XEmacPs_PhyRead(instance, phy_address, 3U, &id2) != XST_SUCCESS ||
        id1 != 0x001cU || (id2 & 0xfff0U) != 0xc910U) return -2;
    if (XEmacPs_PhyRead(instance, phy_address, 0x1fU, &saved_page) != XST_SUCCESS) return -3;
    /* 保留其他测试配置位，修改扩展页 0xa4 中的强制延时和收发延时三位。 */
    if (XEmacPs_PhyWrite(instance, phy_address, 0x1fU, 7U) == XST_SUCCESS &&
        XEmacPs_PhyWrite(instance, phy_address, 0x1eU, 0xa4U) == XST_SUCCESS &&
        XEmacPs_PhyRead(instance, phy_address, 0x1cU, &value) == XST_SUCCESS &&
        XEmacPs_PhyWrite(instance, phy_address, 0x1cU, value | 0x3800U) == XST_SUCCESS &&
        XEmacPs_PhyRead(instance, phy_address, 0x1cU, &readback) == XST_SUCCESS &&
        (readback & 0x3800U) == 0x3800U) result = 0;
    /* 失败路径也恢复原页，避免干扰后续协商、链路检测和桥寄存器访问。 */
    if (XEmacPs_PhyWrite(instance, phy_address, 0x1fU, saved_page) != XST_SUCCESS) return -4;
    return result;
}
