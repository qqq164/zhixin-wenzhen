#ifndef FDW_DIGITAL_CONTROL_H
#define FDW_DIGITAL_CONTROL_H

#include <stdint.h>

/* 数字记录器地址；采样来源由模式路由提供。 */
#define FDW_DIGITAL_BASE_ADDRESS 0x43C10000U
#define FDW_DIGITAL_CHUNK_POINTS 32U

struct fdw_digital_status {
    uint32_t record_id;
    uint32_t flags;
    uint32_t interval_ticks;
    uint32_t sample_count;
    uint32_t captured_count;
    uint32_t capacity;
    uint32_t sample_width;
    uint32_t mode;
    uint32_t trigger_source;
    uint32_t finite;
    uint32_t route_generation;
};

int fdw_digital_control_init(void);
int fdw_digital_arm(uint32_t interval_ticks, uint32_t sample_count, uint32_t wait_trigger,
                    uint32_t *record_id);
void fdw_digital_cancel(void);
void fdw_digital_invalidate(void);
int fdw_digital_trigger(void);
void fdw_digital_read_status(struct fdw_digital_status *status);
int fdw_digital_read_samples(uint32_t record_id, uint32_t start, uint32_t count,
                             uint8_t *samples);

#endif
