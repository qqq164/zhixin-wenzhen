#include <assert.h>
#include <stdio.h>
#include "../src/fdw_adc_control.c"

/* 这里只替代旧 DMA 驱动的接口，验证帧号和 TCP 可读范围。 */
static int capture_result, init_result, capture_calls;
static struct fdw_probe_status route;
static struct fdw_pwm_status pwm;
static uint32_t trigger_mode;
static int route_changes_during_capture;
void fdw_probe_read_status(struct fdw_probe_status *status) { *status = route; }
int fdw_pwm_read_status(struct fdw_pwm_status *status) { *status = pwm; return 0; }
int fdw_adc_capture_set_trigger(uint32_t trigger) { trigger_mode = trigger; return 0; }
uint32_t fdw_adc_capture_trigger_status(void) { return trigger_mode ? 0x50U : 0U; }
int fdw_adc_capture_init(void) { return init_result; }
static int mock_finish(int16_t *samples, uint32_t count, uint32_t *otr)
{
    uint32_t i;
    ++capture_calls;
    if (route_changes_during_capture) ++route.generation;
    if (capture_result) return capture_result;
    for (i = 0; i < count; ++i) samples[i] = (int16_t)((int32_t)(i % 4096) - 2048);
    *otr = 3;
    return 0;
}
static int poll_delay;
int fdw_adc_capture_start(uint32_t timeout_us) { assert(timeout_us>=100000U);return 0; }
int fdw_adc_capture_poll(int16_t *samples,uint32_t count,uint32_t *otr)
{
    if (poll_delay) { --poll_delay;return 1; }
    return mock_finish(samples,count,otr);
}
int fdw_adc_capture_cancel(void) { return 0; }
uint32_t fdw_adc_capture_frame_flags(void) { return FDW_ADC_FLAG_CONTINUITY_CHECKED; }
int main(void)
{
    int16_t block[32];
    uint32_t id = 0;
    struct fdw_adc_status status;
    assert(fdw_adc_control_capture(8192, &id) == -16);
    assert(fdw_adc_control_init() == 0);
    assert(fdw_adc_control_capture(1, &id) == -1);
    assert(fdw_adc_control_capture(8192, 0) == -1);
    route.capture_active = 1;
    assert(fdw_adc_control_capture(8192, &id) == -2);
    route.capture_active = 0;
    assert(capture_calls == 0);
    assert(fdw_adc_control_capture(8192, &id) == 0 && id == 1);
    fdw_adc_control_status(&status);
    assert(status.record_id == 1 && status.sample_count == 8192 && status.flags == 8 && status.overrange_count == 3);
    assert(fdw_adc_control_read(id, 4095, 2, block) == 0 && block[0] == 2047 && block[1] == -2048);
    assert(fdw_adc_control_read(id, UINT32_MAX, 32, block) == -1);
    assert(fdw_adc_control_read(id, 8191, 2, block) == -1);
    assert(fdw_adc_control_read(id, 0, 33, block) == -1);
    assert(fdw_adc_control_read(id, 0, 1, 0) == -1);
    capture_result = -12;
    assert(fdw_adc_control_capture(8192, &id) == -12);
    assert(fdw_adc_control_read(1, 0, 1, block) == -4);
    capture_result = 0;
    assert(fdw_adc_control_capture(8192, &id) == 0 && id == 3);
    assert(fdw_adc_control_init() == 0);
    assert(fdw_adc_control_read(id, 0, 1, block) == -4);
    assert(fdw_adc_control_capture(8192, &id) == 0 && id == 4);
    /* 同步采集必须有连续PWM周期事件；非法请求不能启动DMA或消耗帧号。 */
    assert(fdw_adc_control_capture_sync(8192, &id) == -19);
    route.mode = 1; route.trigger_source = 1; route.pwm_active = 1;
    pwm.enabled = 1; pwm.period_ticks = 1000;
    route.finite = 1;
    assert(fdw_adc_control_capture_sync(8192, &id) == -19);
    route.finite = 0;
    pwm.period_ticks = 9000001;
    assert(fdw_adc_control_capture_sync(8192, &id) == -19);
    pwm.period_ticks = 1000;
    assert(fdw_adc_control_capture_sync(8192, &id) == 0 && id == 5 && trigger_mode == 1);
    fdw_adc_control_status(&status);
    assert(status.trigger == 1 && status.trigger_status == 0x50 && status.mode == 1);
    /* 成功切换、采集中路径变化、超时与重新初始化都不能发布旧帧。 */
    ++route.generation;
    assert(fdw_adc_control_read(id, 0, 1, block) == -4);
    assert(fdw_adc_control_capture(8192, &id) == 0 && id == 6 && trigger_mode == 0);
    fdw_adc_control_invalidate();
    assert(fdw_adc_control_read(id, 0, 1, block) == -4);
    route_changes_during_capture = 1;
    assert(fdw_adc_control_capture(8192, &id) == -4);
    route_changes_during_capture = 0;
    capture_result = -12;
    assert(fdw_adc_control_capture_sync(8192, &id) == -12);
    assert(fdw_adc_control_read(id, 0, 1, block) == -4);
    capture_result = 0;
    assert(fdw_adc_control_capture_sync(8192, &id) == 0 && id == 9);
    /* 两个传输同时锁帧时禁止覆盖；作废身份后字节仍保持，直到两把锁都释放。 */
    {
        const int16_t *pinned,*second;
        uint32_t job_id;
        assert(fdw_adc_control_pin(id,&pinned)==0 && pinned[0]==-2048);
        assert(fdw_adc_control_pin(id,&second)==0 && second==pinned);
        assert(fdw_adc_control_capture(8192,&job_id)==-20 && fdw_adc_control_init()==-20);
        fdw_adc_control_invalidate();fdw_adc_control_unpin();
        assert(pinned[8191]==2047 && fdw_adc_control_start(8192,5000000,&job_id)==-20);
        fdw_adc_control_unpin();poll_delay=2;
        assert(fdw_adc_control_start(8192,5000000,&job_id)==0 && job_id==10);
        assert(fdw_adc_control_busy() && fdw_adc_control_read(job_id,0,1,block)==-4);
        assert(fdw_adc_control_poll()==1 && fdw_adc_control_poll()==1);
        assert(fdw_adc_control_cancel()==0 && !fdw_adc_control_busy());
        assert(fdw_adc_control_poll()==-21 && fdw_adc_control_read(job_id,0,1,block)==-4);
        assert(fdw_adc_control_capture(8192,&id)==0 && id==11);
    }
    next_id = UINT32_MAX - 1;
    assert(fdw_adc_control_capture(8192, &id) == 0 && id == UINT32_MAX);
    assert(fdw_adc_control_capture(8192, &id) == -5);
    puts("FDW_ADC_CONTROL_PASS");
    return 0;
}
