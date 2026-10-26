/* 用带故障注入的 MDIO 模型检查延时配置、位保留及失败后的页恢复。 */
#include <assert.h>
#include <stdio.h>
#include "fdw_lxb_phy.h"

static unsigned call_count, fail_at, writes, wrong_id, bad_readback;
static u16 page, extension, delay_word;

static int access_failed(void)
{
    return ++call_count == fail_at;
}

int XEmacPs_PhyRead(XEmacPs *instance, u32 address, u32 reg, u16 *value)
{
    (void)instance;
    assert(address == 3U);
    if (access_failed()) return 1;
    if (reg == 2U) *value = wrong_id ? 0xffffU : 0x001cU;
    else if (reg == 3U) *value = 0xc915U;
    else if (reg == 0x1fU) *value = page;
    else {
        assert(reg == 0x1cU && page == 7U && extension == 0xa4U);
        *value = bad_readback ? (delay_word & ~0x3800U) : delay_word;
    }
    return 0;
}

int XEmacPs_PhyWrite(XEmacPs *instance, u32 address, u32 reg, u16 value)
{
    (void)instance;
    assert(address == 3U);
    ++writes;
    if (access_failed()) return 1;
    if (reg == 0x1fU) page = value;
    else if (reg == 0x1eU) { assert(page == 7U); extension = value; }
    else { assert(reg == 0x1cU && page == 7U); delay_word = value; }
    return 0;
}

static void reset_model(void)
{
    call_count = fail_at = writes = wrong_id = bad_readback = 0;
    page = 0U;
    extension = 0U;
    delay_word = 0x857U;
}

int main(void)
{
    XEmacPs instance = {0};
    unsigned fault;
    assert(fdw_lxb_phy_full_duplex(0xffffU, 0U) == 0xfd5fU);
    assert(fdw_lxb_phy_full_duplex(0xffffU, 1U) == 0xfeffU);
    assert(fdw_lxb_phy_full_duplex(0x0140U, 0U) == 0x0140U);
    assert(fdw_lxb_phy_full_duplex(0x0200U, 1U) == 0x0200U);
    reset_model();
    assert(fdw_lxb_phy_delays(&instance, 3U) == 0);
    assert(delay_word == (0x857U | 0x3800U));
    assert(page == 0U && call_count == 9U);
    for (fault = 1U; fault <= 9U; ++fault) {
        reset_model();
        fail_at = fault;
        assert(fdw_lxb_phy_delays(&instance, 3U) != 0);
        if (fault != 9U) assert(page == 0U);
    }
    reset_model();
    wrong_id = 1;
    assert(fdw_lxb_phy_delays(&instance, 3U) == -2 && writes == 0U);
    reset_model();
    bad_readback = 1;
    assert(fdw_lxb_phy_delays(&instance, 3U) != 0 && page == 0U);
    reset_model();
    assert(fdw_lxb_phy_delays(0, 3U) != 0);
    assert(fdw_lxb_phy_delays(&instance, 32U) != 0 && call_count == 0U);
    puts("FDW_LXB_PHY_MODEL_PASS");
    return 0;
}
