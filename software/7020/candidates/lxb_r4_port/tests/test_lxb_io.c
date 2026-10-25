#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "fdw_lxb_io.h"
#include "fdw_probe_control.h"
#include "fdw_pwm_control.h"
#include "fdw_dac_control.h"

/* 原生模型只模拟寄存器，不把结果当作引脚电压或继电器触点实测。 */
static uint32_t registers[2][8], gpio[4];
static int block_commit;
static unsigned int sleeps;
static struct fdw_probe_status route;
static struct fdw_dac_status dac;

uint32_t Xil_In32(uintptr_t address)
{
    if (address >= FDW_LXB_GPIO_BASE && address < FDW_LXB_GPIO_BASE + 16U)
        return gpio[(address - FDW_LXB_GPIO_BASE) / 4U];
    if (address >= FDW_PWM_BASE_ADDRESS && address < FDW_PWM_BASE_ADDRESS + 32U)
        return registers[0][(address - FDW_PWM_BASE_ADDRESS) / 4U];
    assert(address >= FDW_LXB_PWM_B_BASE && address < FDW_LXB_PWM_B_BASE + 32U);
    return registers[1][(address - FDW_LXB_PWM_B_BASE) / 4U];
}

void Xil_Out32(uintptr_t address, uint32_t value)
{
    uint32_t channel, offset;
    if (address >= FDW_LXB_GPIO_BASE && address < FDW_LXB_GPIO_BASE + 16U) {
        gpio[(address - FDW_LXB_GPIO_BASE) / 4U] = value;
        return;
    }
    channel = address >= FDW_LXB_PWM_B_BASE ? 1U : 0U;
    offset = (address - (channel ? FDW_LXB_PWM_B_BASE : FDW_PWM_BASE_ADDRESS)) / 4U;
    assert(offset < 8U);
    registers[channel][offset] = value;
    if (offset == 0U) {
        registers[channel][0] = value & 1U;
        if ((value & 2U) && !block_commit) {
            registers[channel][4] = registers[channel][1];
            registers[channel][5] = registers[channel][2];
        }
    }
}

void usleep(unsigned long microseconds) { assert(microseconds == 10000U); ++sleeps; }
void fdw_dac_read_status(struct fdw_dac_status *status) { *status = dac; }
void fdw_probe_read_status(struct fdw_probe_status *status) { *status = route; }
int fdw_probe_set_mode(uint32_t mode, uint32_t trigger, uint32_t finite)
{
    fdw_lxb_io_halt();
    registers[0][0] = 0U;
    memset(&route, 0, sizeof(route));
    route.mode = mode; route.trigger_source = trigger; route.finite = finite;
    return 0;
}
int fdw_probe_output_enable(uint32_t enabled)
{
    assert(route.mode == 3U);
    fdw_lxb_select_default_output();
    route.output_enabled = enabled;
    return 0;
}

int main(void)
{
    struct fdw_lxb_pwm_status status;
    assert(fdw_lxb_io_init() == -1);
    registers[1][7] = 0x50574d31U;
    assert(fdw_lxb_io_init() == 0 && fdw_lxb_output_mask() == 0U);
    assert(fdw_lxb_pwm_set(2U, 100U, 50U) == -1);
    assert(fdw_lxb_pwm_set(0U, 0U, 0U) == -1);
    assert(fdw_lxb_pwm_set(0U, 100U, 101U) == -1);
    assert(fdw_lxb_pwm_enable(0U, 1U) == -2);
    assert(fdw_lxb_pwm_set(0U, 1000U, 100U) == 0);
    assert(fdw_lxb_pwm_set(1U, 2000U, 800U) == 0);
    assert(fdw_lxb_pwm_enable(0U, 1U) == 0);
    assert(fdw_lxb_output_mask() == 1U && registers[0][0] == 1U && registers[1][0] == 0U);
    assert(fdw_lxb_pwm_enable(1U, 1U) == 0);
    assert(fdw_lxb_output_mask() == 3U);
    assert(fdw_lxb_pwm_status(0U, &status) == 0 && status.period_ticks == 1000U && status.high_ticks == 100U);
    assert(fdw_lxb_pwm_status(1U, &status) == 0 && status.period_ticks == 2000U && status.high_ticks == 800U);
    assert(fdw_lxb_pwm_set(1U, 3000U, 500U) == -2);
    assert(fdw_lxb_relay_set(12U) == -2);
    assert(fdw_lxb_pwm_enable(0U, 0U) == 0);
    assert(fdw_lxb_output_mask() == 2U && registers[1][0] == 1U);
    dac.enabled = 1U;
    assert(fdw_lxb_pwm_enable(0U, 1U) == -2);
    assert(fdw_lxb_pwm_enable(1U, 0U) == 0);
    assert(fdw_lxb_relay_set(12U) == -2);
    dac.enabled = 0U; route.capture_active = 1U;
    assert(fdw_lxb_relay_set(12U) == -2);
    route.capture_active = 0U;
    assert(fdw_lxb_relay_set(128U) == -1);
    assert(fdw_lxb_relay_set(18U) == 0 && sleeps == 1U);
    assert(fdw_lxb_relay_mask() == 18U);
    block_commit = 1;
    assert(fdw_lxb_pwm_set(1U, 3000U, 500U) == -3);
    fdw_lxb_io_halt();
    assert(fdw_lxb_output_mask() == 0U && registers[1][0] == 0U);
    assert(fdw_lxb_relay_mask() == 18U);
    assert(fdw_lxb_pwm_status(2U, &status) == -1);
    assert(fdw_lxb_pwm_status(0U, NULL) == -1);
    puts("FDW_LXB_NATIVE_IO_PASS");
    return 0;
}
