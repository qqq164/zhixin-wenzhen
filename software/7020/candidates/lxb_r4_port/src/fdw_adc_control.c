/* DMA保持原有原码路径，增加触发条件与帧所属模式的身份检查。 */
#include "fdw_adc_control.h"
#include "fdw_probe_control.h"
#include "fdw_pwm_control.h"

static int16_t frame[FDW_ADC_SAMPLE_COUNT];
static uint32_t next_id;
static uint32_t pending_id;
static uint32_t frame_pins;
static int capture_busy;
static struct fdw_adc_status state;

void fdw_adc_control_invalidate(void)
{
    if (state.record_id != 0U) state.last_code = -4;
    state.record_id = 0U;
    state.sample_count = 0U;
    state.flags = 0U;
    state.overrange_count = 0U;
    state.trigger_status = 0U;
    state.trigger = 0U;
    state.mode = 0U;
    state.route_generation = 0U;
}

static void check_frame_route(void)
{
    struct fdw_probe_status route;
    fdw_probe_read_status(&route);
    if (state.record_id != 0U &&
        (route.generation != state.route_generation || route.mode != state.mode))
        fdw_adc_control_invalidate();
}

int fdw_adc_control_init(void)
{
    if (capture_busy || frame_pins) return -20;
    fdw_adc_control_invalidate();
    state.last_code = fdw_adc_capture_init();
    state.ready = state.last_code == 0;
    /* 手动重新初始化不能复用本次启动中已经分配的 ADC 号码。 */
    return state.last_code;
}

static int start_frame(uint32_t count, uint32_t trigger, uint32_t timeout_us, uint32_t *id)
{
    struct fdw_probe_status route;
    struct fdw_pwm_status pwm;
    int result;
    if (count != FDW_ADC_SAMPLE_COUNT || id == 0) return -1;
    if (capture_busy || frame_pins) return -20;
    if (!state.ready) return -16;
    if (next_id == UINT32_MAX) return -5;
    fdw_probe_read_status(&route);
    if (route.capture_active || route.settling) return -2;
    if (trigger != 0U) {
        /* 首轮仅支持已运行的连续PWM；有限激励需要后续异步布防接口。 */
        if ((route.mode != 1U && route.mode != 3U) || route.finite ||
            route.trigger_source != 1U || !route.pwm_active) return -19;
        if (fdw_pwm_read_status(&pwm) != 0 || !pwm.enabled || pwm.period_ticks == 0U ||
            pwm.period_ticks > 9000000U) return -19;
    }
    result = fdw_adc_capture_set_trigger(trigger);
    if (result != 0) return result;
    ++next_id;
    fdw_adc_control_invalidate();
    state.trigger = trigger;
    state.mode = route.mode;
    state.route_generation = route.generation;
    result = fdw_adc_capture_start(timeout_us);
    state.last_code = result;
    if (result != 0) { if (result == -16) state.ready = 0U;return result; }
    pending_id = next_id;
    capture_busy = 1;
    *id = pending_id;
    return 0;
}

int fdw_adc_control_start(uint32_t count, uint32_t timeout_us, uint32_t *id)
{
    return start_frame(count, 0U, timeout_us, id);
}

int fdw_adc_control_poll(void)
{
    struct fdw_probe_status route;
    int result;
    if (!capture_busy) return -21;
    result = fdw_adc_capture_poll(frame, FDW_ADC_SAMPLE_COUNT, &state.overrange_count);
    if (result == 1) return 1;
    capture_busy = 0;
    state.last_code = result;
    if (result != 0) return result;
    fdw_probe_read_status(&route);
    if (route.generation != state.route_generation || route.mode != state.mode) {
        state.last_code = -4;
        return -4;
    }
    state.record_id = pending_id;
    state.sample_count = FDW_ADC_SAMPLE_COUNT;
    state.flags = fdw_adc_capture_frame_flags();
    state.trigger_status = fdw_adc_capture_trigger_status();
    return 0;
}

int fdw_adc_control_cancel(void)
{
    int result;
    if (!capture_busy) return 0;
    result = fdw_adc_capture_cancel();
    capture_busy = 0;
    fdw_adc_control_invalidate();
    state.last_code = result == 0 ? -22 : result;
    return result;
}

int fdw_adc_control_busy(void) { return capture_busy; }

/* 旧接口保持短帧同步行为，新任务使用上面的非阻塞接口。 */
static int capture_frame(uint32_t count, uint32_t trigger, uint32_t *id)
{
    uint32_t record;
    int result;
    if (!id) return -1;
    result = start_frame(count, trigger, 100000U, &record);
    if (result != 0) return result;
    do { result = fdw_adc_control_poll(); } while (result == 1);
    if (result == 0) *id = record;
    return result;
}
int fdw_adc_control_capture(uint32_t count, uint32_t *id) { return capture_frame(count, 0U, id); }
int fdw_adc_control_capture_sync(uint32_t count, uint32_t *id) { return capture_frame(count, 1U, id); }

/* 帧锁只保护固定缓冲区。取消和停波仍可执行，锁释放前不能覆盖原码。 */
int fdw_adc_control_pin(uint32_t id, const int16_t **samples)
{
    check_frame_route();
    if (!samples || !id || id != state.record_id || capture_busy) return -4;
    if (frame_pins >= 4U) return -20;
    ++frame_pins;
    *samples = frame;
    return 0;
}
void fdw_adc_control_unpin(void) { if (frame_pins) --frame_pins; }

void fdw_adc_control_status(struct fdw_adc_status *status) { check_frame_route(); *status = state; }

int fdw_adc_control_read(uint32_t id, uint32_t start, uint32_t count, int16_t *samples)
{
    uint32_t index;
    check_frame_route();
    if (!id || id != state.record_id) return -4;
    if (!samples || !count || count > 32U || start >= state.sample_count ||
        count > state.sample_count - start) return -1;
    for (index = 0U; index < count; ++index) samples[index] = frame[start + index];
    return 0;
}
