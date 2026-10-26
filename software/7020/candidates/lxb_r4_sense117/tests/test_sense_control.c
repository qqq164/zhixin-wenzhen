/* 离线寄存器和DMA模型，不代表真实继电器触点、模拟稳定或ADC实测。 */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "xiltimer.h"
#include "fdw_lxb_io.h"
#include "../src/fdw_adc_control.c"
#include "fdw_dac_control.h"

static XTime now;
static uint32_t gpio[4], pwm_regs[2][8], writes, invalidations;
static int block_write, delayed_dma;
static struct fdw_probe_status route;
static struct fdw_dac_status dac;

void XTime_GetTime(XTime *value) { *value = now; }
void usleep(unsigned long us) { now += (XTime)us * COUNTS_PER_SECOND / 1000000U; }
uint32_t Xil_In32(uintptr_t address)
{
    if (address >= FDW_LXB_GPIO_BASE && address < FDW_LXB_GPIO_BASE + 16U)
        return gpio[(address - FDW_LXB_GPIO_BASE) / 4U];
    if (address >= FDW_LXB_PWM_B_BASE && address < FDW_LXB_PWM_B_BASE + 32U)
        return pwm_regs[1][(address - FDW_LXB_PWM_B_BASE) / 4U];
    assert(address >= FDW_PWM_BASE_ADDRESS && address < FDW_PWM_BASE_ADDRESS + 32U);
    return pwm_regs[0][(address - FDW_PWM_BASE_ADDRESS) / 4U];
}
void Xil_Out32(uintptr_t address, uint32_t value)
{
    if (address == FDW_LXB_GPIO_BASE) {
        ++writes;
        if (!block_write) gpio[0] = value;
    } else if (address == FDW_LXB_GPIO_BASE + 8U) gpio[2] = value;
    else {
        unsigned ch = address >= FDW_LXB_PWM_B_BASE;
        uintptr_t base = ch ? FDW_LXB_PWM_B_BASE : FDW_PWM_BASE_ADDRESS;
        assert(address >= base && address < base + 32U);
        pwm_regs[ch][(address - base) / 4U] = value;
    }
}
void fdw_digital_invalidate(void) { ++invalidations; }
void fdw_dac_read_status(struct fdw_dac_status *value) { *value = dac; }
void fdw_probe_read_status(struct fdw_probe_status *value) { *value = route; }
int fdw_probe_set_mode(uint32_t mode, uint32_t trigger, uint32_t finite)
{ route.mode = mode; route.trigger_source = trigger; route.finite = finite; return 0; }
int fdw_probe_output_enable(uint32_t enabled) { route.output_enabled = enabled; return 0; }
int fdw_pwm_read_status(struct fdw_pwm_status *value) { memset(value, 0, sizeof(*value)); return 0; }
int fdw_adc_capture_init(void) { return 0; }
int fdw_adc_capture_set_trigger(uint32_t trigger) { (void)trigger; return 0; }
int fdw_adc_capture_start(uint32_t us) { (void)us; return 0; }
int fdw_adc_capture_poll(int16_t *samples, uint32_t count, uint32_t *otr)
{
    uint32_t i;
    if (delayed_dma) return 1;
    for (i = 0; i < count; ++i) samples[i] = (int16_t)(i % 4096U - 2048);
    *otr = 0U; return 0;
}
int fdw_adc_capture_cancel(void) { return 0; }
uint32_t fdw_adc_capture_frame_flags(void) { return 8U; }
uint32_t fdw_adc_capture_trigger_status(void) { return 0U; }

int main(void)
{
    struct fdw_lxb_sense_status sense;
    struct fdw_adc_status adc;
    const int16_t *pinned;
    uint32_t id, before, mask, selected;
    int16_t sample;
    pwm_regs[1][7] = 0x50574d31U;
    assert(fdw_lxb_io_init() == 0 && fdw_adc_control_init() == 0);
    assert(fdw_lxb_sense_select(1U, 10U) == -42);
    assert(fdw_lxb_relay_set(33U) == 0);
    assert(fdw_lxb_sense_ready() == 0);
    dac.ready = dac.configured = dac.enabled = dac.waveform_active = 1U;
    dac.tag = 7U; dac.frequency_word = 1717987U;
    assert(fdw_lxb_relay_set(113U) == -2);
    assert(fdw_lxb_sense_select(0U, 10U) == -1);
    assert(fdw_lxb_sense_select(1U, 9U) == -1);
    assert(fdw_lxb_sense_select(1U, 60001U) == -1);
    before = writes;
    assert(fdw_lxb_sense_select(FDW_SENSE_VA, 37U) == 0 && writes == before + 1U);
    fdw_lxb_sense_status(&sense);
    assert(sense.relay_mask == 113U && sense.selected == FDW_SENSE_VA && sense.rs_nominal_ohm == 100U);
    assert(sense.settling && sense.remaining_ms == 37U);
    assert(fdw_adc_control_start(8192U, 100000U, &id) == -41);
    assert(fdw_adc_control_capture(8192U, &id) == -41);
    assert(fdw_adc_control_capture_sync(8192U, &id) == -41);
    assert(fdw_lxb_sense_select(FDW_SENSE_VB, 10U) == -41);
    usleep(36999U); assert(fdw_lxb_sense_ready() == -41);
    usleep(1U); assert(fdw_lxb_sense_ready() == 0);
    delayed_dma = 1;
    assert(fdw_adc_control_start(8192U, 100000U, &id) == 0);
    assert(fdw_lxb_sense_select(FDW_SENSE_VB, 10U) == -20);
    assert(fdw_lxb_relay_set(49U) == -20 && fdw_adc_control_poll() == 1);
    delayed_dma = 0;
    assert(fdw_adc_control_poll() == 0);
    fdw_adc_control_status(&adc);
    assert(adc.record_id == id && adc.relay_mask == 113U && adc.sense_generation == sense.generation);
    assert(fdw_adc_control_pin(id, &pinned) == 0);
    assert(fdw_lxb_sense_select(FDW_SENSE_VB, 10U) == -20);
    assert(fdw_lxb_relay_set(49U) == -20);
    fdw_adc_control_unpin();
    assert(fdw_lxb_sense_select(FDW_SENSE_VB, 10U) == 0);
    assert(fdw_adc_control_read(id, 0U, 1U, &sample) == -4);
    usleep(10000U);
    assert(fdw_adc_control_capture(8192U, &id) == 0);
    /* 模拟配置被外部改写，不能把一帧错误归到另一个通路。 */
    gpio[0] = 113U;
    assert(fdw_adc_control_read(id, 0U, 1U, &sample) == -4);
    assert(fdw_adc_control_start(8192U, 100000U, &id) == 0);
    gpio[0] = 49U;
    assert(fdw_adc_control_poll() == -4);
    /* 遍历所有控制字：连续激励只能保留针路和量程，只改K5/K7。 */
    for (mask = 0U; mask < 128U; ++mask) {
        gpio[0] = mask;
        for (selected = 1U; selected <= 3U; ++selected) {
            uint32_t original = gpio[0];
            int result = fdw_lxb_sense_select(selected, 10U);
            if ((mask & 15U) == 1U) {
                assert(result == 0 && ((gpio[0] ^ original) & ~0x50U) == 0U);
                usleep(10000U);
            } else assert(result == -42 && gpio[0] == original);
        }
    }
    gpio[0] = 33U;
    route.mode = 3U; assert(fdw_lxb_sense_select(1U, 10U) == -42); route.mode = 0U;
    route.capture_active = 1U; assert(fdw_lxb_sense_select(1U, 10U) == -42); route.capture_active = 0U;
    route.output_enabled = 1U; assert(fdw_lxb_sense_select(1U, 10U) == -42); route.output_enabled = 0U;
    gpio[2] = 1U; assert(fdw_lxb_sense_select(1U, 10U) == -42); gpio[2] = 0U;
    dac.busy = 1U; assert(fdw_lxb_sense_select(1U, 10U) == -42); dac.busy = 0U;
    dac.clipped = 1U; assert(fdw_lxb_sense_select(1U, 10U) == -42); dac.clipped = 0U;
    dac.error_code = 1U; assert(fdw_lxb_sense_select(1U, 10U) == -42); dac.error_code = 0U;
    before = writes;
    assert(fdw_lxb_sense_select(3U, 10U) == 0 && writes == before);
    block_write = 1;
    assert(fdw_lxb_sense_select(1U, 10U) == -43);
    usleep(10000U);
    assert(fdw_adc_control_start(8192U, 100000U, &id) == -43);
    assert(fdw_lxb_sense_select(2U, 10U) == -43);
    block_write = 0; dac.enabled = 0U;
    assert(fdw_lxb_relay_set(33U) == 0 && fdw_lxb_sense_ready() == 0);
    /* HALT不能为了看似复位而把两针切到共享ADC。 */
    fdw_lxb_io_halt(); assert(gpio[0] == 33U);
    assert(invalidations > 0U);
    puts("FDW_SENSE117_REAL_IO_ADC_PASS");
    return 0;
}
