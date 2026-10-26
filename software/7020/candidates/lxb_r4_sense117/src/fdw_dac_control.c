/* DIG5：CPU只写配置，逐点正弦由7020 PL内的DDS产生。 */
#include "fdw_dac_control.h"
#include "fdw_probe_control.h"
#include "fdw_digital_control.h"
#include "xil_io.h"

static uint32_t command_shadow, ready, configured, configured_tag;

static uint32_t read_status(void)
{
    return Xil_In32(FDW_DAC_BASE_ADDRESS + 8U);
}

static void disconnect_pins(void)
{
    command_shadow &= ~0x40000000U;
    Xil_Out32(FDW_DAC_BASE_ADDRESS, command_shadow);
}

static int transact(uint32_t operation, uint32_t argument, uint32_t *reply)
{
    uint32_t attempt, status, request;
    request = (command_shadow ^ 0x80000000U) & 0x80000000U;
    command_shadow = request | (command_shadow & 0x40000000U) |
                     (operation << 27) | argument;
    Xil_Out32(FDW_DAC_BASE_ADDRESS, command_shadow);
    for (attempt = 0U; attempt < 100000U; ++attempt) {
        status = read_status();
        if ((status & 0x80000000U) == request && (status & 0x40000000U) == 0U) {
            if (reply != 0) *reply = status;
            return (status & 0x08000000U) ? -(int)((status >> 20) & 15U) - 10 : 0;
        }
    }
    /* 不应答时先撤销输出许可；旧任务恢复后也不能继续驱动模块。 */
    disconnect_pins();
    configured = 0U;
    ready = 0U;
    return -20;
}

static int query(uint32_t selector, uint32_t *value)
{
    uint32_t status = 0U;
    int result = transact(0U, selector, &status);
    if (result == 0) *value = status & 0xffffU;
    return result;
}

static int query_word(uint32_t selector, uint32_t *value)
{
    uint32_t low, high;
    int result = query(selector, &low);
    if (result != 0) return result;
    result = query(selector + 1U, &high);
    if (result == 0) *value = low | (high << 16);
    return result;
}

int fdw_dac_halt(void)
{
    uint32_t attempt;
    disconnect_pins();
    for (attempt = 0U; attempt < 100000U; ++attempt) {
        if ((read_status() & 0x10080000U) == 0x00080000U) return 0;
    }
    return -20;
}

int fdw_dac_control_init(void)
{
    uint32_t identity = 0U, attempt;
    /* 数字DIG5标识已先核验，随后才访问新增GPIO地址。 */
    command_shadow = Xil_In32(FDW_DAC_BASE_ADDRESS);
    ready = 0U;
    configured = 0U;
    disconnect_pins();
    for (attempt = 0U; attempt < 100000U; ++attempt) {
        if (read_status() & 0x20000000U) break;
    }
    if (attempt == 100000U) return -20;
    if (query(0U, &identity) != 0 || identity != 0xd905U) return -1;
    ready = 1U;
    return fdw_dac_halt();
}

int fdw_dac_configure(uint32_t frequency_hz, uint32_t gain_q11,
                      uint32_t offset_code, uint32_t phase_word, uint32_t invert)
{
    uint32_t ftw, before_tag, after_tag, actual_ftw, actual_phase, actual_gain, actual_offset;
    struct fdw_probe_status route;
    struct fdw_digital_status digital;
    int result;
    if (!ready) return -2;
    if (frequency_hz > FDW_DAC_CLOCK_HZ / 2U - 1U || gain_q11 > 2048U ||
        offset_code > 4095U || invert > 1U) return -1;
    fdw_probe_read_status(&route);
    fdw_digital_read_status(&digital);
    if (route.mode != 0U || route.capture_active || route.pwm_active ||
        route.settling || (digital.flags & 1U)) return -3;
    result = fdw_dac_halt();
    if (result != 0) return result;
    configured = 0U;
    ftw = (uint32_t)((((uint64_t)frequency_hz << 32) + FDW_DAC_CLOCK_HZ / 2U) / FDW_DAC_CLOCK_HZ);
    if ((result = query_word(1U, &before_tag)) != 0) return result;
    if ((result = transact(1U, ftw & 0x07ffffffU, 0)) != 0) return result;
    if ((result = transact(2U, ftw >> 27, 0)) != 0) return result;
    if ((result = transact(3U, phase_word & 0x07ffffffU, 0)) != 0) return result;
    if ((result = transact(4U, phase_word >> 27, 0)) != 0) return result;
    if ((result = transact(5U, (1U << 26) | (invert << 25) | (1U << 24) |
                           (offset_code << 12) | gain_q11, 0)) != 0) return result;
    if ((result = transact(6U, 0U, 0)) != 0) return result;
    if ((result = query_word(1U, &after_tag)) != 0) return result;
    if ((result = query_word(3U, &actual_ftw)) != 0) return result;
    if ((result = query_word(5U, &actual_phase)) != 0) return result;
    if ((result = query(7U, &actual_gain)) != 0) return result;
    if ((result = query(8U, &actual_offset)) != 0) return result;
    if (after_tag != before_tag + 1U || actual_ftw != ftw || actual_phase != phase_word ||
        actual_gain != gain_q11 || actual_offset != offset_code) return -4;
    configured = 1U;
    configured_tag = after_tag;
    return 0;
}

int fdw_dac_enable(uint32_t enabled)
{
    uint32_t attempt, actual_tag;
    struct fdw_probe_status route;
    if (!ready) return -2;
    if (enabled > 1U) return -1;
    if (!enabled) return fdw_dac_halt();
    fdw_probe_read_status(&route);
    if (!configured || route.mode != 0U || route.capture_active || route.pwm_active || route.settling)
        return -3;
    if (query_word(1U, &actual_tag) != 0) return -20;
    if (actual_tag != configured_tag || (read_status() & 0x04000000U) == 0U) {
        configured = 0U;
        disconnect_pins();
        return -4;
    }
    if (read_status() & 0x10000000U) return 0;
    if (fdw_dac_halt() != 0) return -20;
    command_shadow |= 0x40000000U;
    Xil_Out32(FDW_DAC_BASE_ADDRESS, command_shadow);
    for (attempt = 0U; attempt < 100000U; ++attempt) {
        if (read_status() & 0x10000000U) return 0;
    }
    disconnect_pins();
    return -20;
}

void fdw_dac_read_status(struct fdw_dac_status *status)
{
    uint32_t word;
    int result;
    *status = (struct fdw_dac_status){0};
    status->ready = ready;
    status->configured = configured;
    if (!ready) return;
    word = read_status();
    status->busy = (word >> 30) & 1U;
    status->enabled = (word >> 28) & 1U;
    status->error_code = (word >> 20) & 15U;
    status->waveform_active = (word >> 26) & 1U;
    status->clipped = (word >> 25) & 1U;
    result = query(0U, &status->hardware_code);
    if (!result) result = query_word(1U, &status->tag);
    if (!result) result = query_word(3U, &status->frequency_word);
    if (!result) result = query_word(5U, &status->phase_word);
    if (!result) result = query(7U, &status->gain_q11);
    if (!result) result = query(8U, &status->offset_code);
    if (result != 0 || status->hardware_code != 0xd905U) status->ready = 0U;
}
