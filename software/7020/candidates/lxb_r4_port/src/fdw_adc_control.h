#ifndef FDW_ADC_CONTROL_H
#define FDW_ADC_CONTROL_H
#include <stdint.h>
#include "fdw_adc_capture.h"

struct fdw_adc_status {
    uint32_t ready;
    int last_code;
    uint32_t record_id;
    uint32_t sample_count;
    uint32_t flags;
    uint32_t overrange_count;
    uint32_t trigger;
    uint32_t trigger_status;
    uint32_t mode;
    uint32_t route_generation;
};

int fdw_adc_control_init(void);
int fdw_adc_control_capture(uint32_t count, uint32_t *id);
int fdw_adc_control_capture_sync(uint32_t count, uint32_t *id);
void fdw_adc_control_invalidate(void);
void fdw_adc_control_status(struct fdw_adc_status *status);
int fdw_adc_control_read(uint32_t id, uint32_t start, uint32_t count, int16_t *samples);
/* 每次启动消耗唯一号码，完成之前不发布为可读取帧。 */
int fdw_adc_control_start(uint32_t count, uint32_t timeout_us, uint32_t *id);
int fdw_adc_control_poll(void);
int fdw_adc_control_cancel(void);
int fdw_adc_control_busy(void);
int fdw_adc_control_pin(uint32_t id, const int16_t **samples);
void fdw_adc_control_unpin(void);
#endif
