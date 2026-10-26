/* 两路独立 PWM、三态输出许可与七继电器的板级控制。 */
#include "fdw_lxb_io.h"
#include "fdw_probe_control.h"
#include "fdw_pwm_control.h"
#include "fdw_dac_control.h"
#include "fdw_adc_control.h"
#include "fdw_digital_control.h"
#include "xil_io.h"
#include "xiltimer.h"
#include "sleep.h"

static uint32_t sense_generation = 1U, sense_fault;
static XTime sense_start, sense_delay;

void fdw_lxb_sense_status(struct fdw_lxb_sense_status *status)
{
    XTime now, elapsed, remaining;
    uint32_t mask = fdw_lxb_relay_mask();
    XTime_GetTime(&now);
    elapsed = now - sense_start;
    remaining = elapsed < sense_delay ? sense_delay - elapsed : 0U;
    status->generation = sense_generation;
    status->relay_mask = mask;
    status->selected = (mask & 16U) ? ((mask & 64U) ? FDW_SENSE_VA : FDW_SENSE_VB) : FDW_SENSE_PROBE;
    status->rs_nominal_ohm = (mask & 32U) ? 100U : 10000U;
    status->settling = remaining != 0U;
    status->remaining_ms = (uint32_t)((remaining * 1000U + COUNTS_PER_SECOND - 1U) / COUNTS_PER_SECOND);
    status->fault = sense_fault;
}

int fdw_lxb_sense_ready(void)
{
    struct fdw_lxb_sense_status status;
    fdw_lxb_sense_status(&status);
    if (status.fault) return -43;
    return status.settling ? -41 : 0;
}

static int write_relay(uint32_t mask, uint32_t wait_ms)
{
    if (sense_generation == UINT32_MAX) return -44;
    /* 先撤销旧帧身份，再改变通路；传输中的帧在调用前已被拒绝。 */
    fdw_adc_control_invalidate();
    fdw_digital_invalidate();
    ++sense_generation;
    Xil_Out32(FDW_LXB_GPIO_BASE, mask);
    XTime_GetTime(&sense_start);
    sense_delay = ((XTime)COUNTS_PER_SECOND * wait_ms + 999U) / 1000U;
    sense_fault = fdw_lxb_relay_mask() != mask;
    return sense_fault ? -43 : 0;
}

int fdw_lxb_sense_select(uint32_t selected, uint32_t wait_ms)
{
    struct fdw_dac_status dac;
    struct fdw_probe_status route;
    uint32_t mask, next;
    int ready;
    if (selected < FDW_SENSE_VA || selected > FDW_SENSE_PROBE || wait_ms < 10U || wait_ms > 60000U) return -1;
    if (fdw_adc_control_busy() || fdw_adc_control_transfer_busy()) return -20;
    ready = fdw_lxb_sense_ready();
    if (ready != 0) return ready;
    fdw_dac_read_status(&dac);
    fdw_probe_read_status(&route);
    mask = fdw_lxb_relay_mask();
    /* 首版只批准针1注入、针2测量；不借本接口换用途、量程或数字输出。 */
    if ((mask & 15U) != 1U || route.mode || route.output_enabled || route.capture_active ||
        route.settling || route.finite || fdw_lxb_output_mask()) return -42;
    if (dac.busy || dac.error_code || (dac.enabled && (!dac.ready || !dac.configured ||
        !dac.waveform_active || dac.clipped || !dac.tag || !dac.frequency_word))) return -42;
    next = mask & ~0x50U;
    if (selected != FDW_SENSE_PROBE) next |= 16U;
    if (selected == FDW_SENSE_VA) next |= 64U;
    if (((next ^ mask) & ~0x50U) != 0U) return -42;
    /* 重选同一通路不切触点、不消耗代际，已有稳定等待也不会被缩短。 */
    return next == mask ? 0 : write_relay(next, wait_ms);
}

static uint32_t pwm_base(uint32_t channel)
{
    return channel == 0U ? FDW_PWM_BASE_ADDRESS : FDW_LXB_PWM_B_BASE;
}

uint32_t fdw_lxb_output_mask(void)
{
    return Xil_In32(FDW_LXB_GPIO_BASE + 8U) & 3U;
}

uint32_t fdw_lxb_relay_mask(void)
{
    return Xil_In32(FDW_LXB_GPIO_BASE) & 127U;
}

void fdw_lxb_io_halt(void)
{
    /* 先让两路数字驱动高阻，再停止B；模拟探针的继电器位置保持不变。 */
    Xil_Out32(FDW_LXB_GPIO_BASE + 8U, 0U);
    Xil_Out32(FDW_LXB_PWM_B_BASE, 0U);
}

void fdw_lxb_select_default_output(void)
{
    /* 兼容旧 OUTPUT_ENABLE：未选通道时默认 A，不额外占用旧 PWM 管脚。 */
    if (fdw_lxb_output_mask() == 0U) Xil_Out32(FDW_LXB_GPIO_BASE + 8U, 1U);
}

int fdw_lxb_io_init(void)
{
    if (Xil_In32(FDW_LXB_PWM_B_BASE + 0x1cU) != 0x50574d31U) return -1;
    fdw_lxb_io_halt();
    Xil_Out32(FDW_LXB_PWM_B_BASE, 0x100U);
    Xil_Out32(FDW_LXB_GPIO_BASE, 0U);
    sense_generation = 1U; sense_fault = 0U; sense_delay = 0U;
    return 0;
}

int fdw_lxb_relay_set(uint32_t mask)
{
    struct fdw_dac_status dac;
    struct fdw_probe_status route;
    if (mask > 127U) return -1;
    if (fdw_adc_control_busy() || fdw_adc_control_transfer_busy()) return -20;
    fdw_dac_read_status(&dac);
    fdw_probe_read_status(&route);
    if (dac.enabled || dac.busy || route.capture_active || fdw_lxb_output_mask()) return -2;
    if (fdw_lxb_sense_ready() == -41) return -41;
    if (write_relay(mask, 10U) != 0) return -43;
    /* 暂用 10ms 选通稳定等待；实际继电器接触及模拟稳定时间仍需实物验证。 */
    usleep(10000U);
    sense_fault = fdw_lxb_relay_mask() != mask;
    return sense_fault ? -43 : 0;
}

int fdw_lxb_pwm_set(uint32_t channel, uint32_t period, uint32_t high)
{
    uint32_t base, attempt;
    if (channel > 1U || period == 0U || high > period) return -1;
    base = pwm_base(channel);
    if ((Xil_In32(base) & 1U) || (Xil_In32(base + 0x0cU) & 2U)) return -2;
    Xil_Out32(base + 4U, period);
    Xil_Out32(base + 8U, high);
    Xil_Out32(base, 0x102U);
    for (attempt = 0U; attempt < 100000U; ++attempt) {
        if (!(Xil_In32(base + 0x0cU) & 2U) && Xil_In32(base + 0x10U) == period &&
            Xil_In32(base + 0x14U) == high) return 0;
    }
    return -3;
}

int fdw_lxb_pwm_enable(uint32_t channel, uint32_t enabled)
{
    uint32_t base, previous_mask;
    int result;
    struct fdw_probe_status route;
    struct fdw_dac_status dac;
    if (channel > 1U || enabled > 1U) return -1;
    base = pwm_base(channel);
    previous_mask = fdw_lxb_output_mask();
    if (!enabled) {
        Xil_Out32(FDW_LXB_GPIO_BASE + 8U, previous_mask & ~(1U << channel));
        Xil_Out32(base, 0U);
        return 0;
    }
    fdw_dac_read_status(&dac);
    fdw_probe_read_status(&route);
    if (dac.enabled || dac.busy || route.capture_active || !Xil_In32(base + 0x10U)) return -2;
    if (route.mode != 3U) {
        result = fdw_probe_set_mode(3U, 0U, 0U);
        if (result) return result;
        fdw_probe_read_status(&route);
        previous_mask = 0U;
    }
    if (route.settling || route.finite) return -3;
    /* 选好具体通道再允许全局输出，避免顺带驱动另一根探针。 */
    Xil_Out32(FDW_LXB_GPIO_BASE + 8U, previous_mask | (1U << channel));
    if (!route.output_enabled) {
        result = fdw_probe_output_enable(1U);
        if (result) {
            Xil_Out32(FDW_LXB_GPIO_BASE + 8U, previous_mask);
            return result;
        }
    }
    Xil_Out32(base, 1U);
    return 0;
}

int fdw_lxb_pwm_status(uint32_t channel, struct fdw_lxb_pwm_status *status)
{
    uint32_t base;
    struct fdw_probe_status route;
    if (channel > 1U || !status) return -1;
    base = pwm_base(channel);
    fdw_probe_read_status(&route);
    status->enabled = Xil_In32(base) & 1U;
    status->output_enabled = route.output_enabled && ((fdw_lxb_output_mask() >> channel) & 1U);
    status->flags = Xil_In32(base + 0x0cU);
    status->period_ticks = Xil_In32(base + 0x10U);
    status->high_ticks = Xil_In32(base + 0x14U);
    return 0;
}
