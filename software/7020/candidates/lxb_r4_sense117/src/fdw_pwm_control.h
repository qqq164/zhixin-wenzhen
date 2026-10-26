#ifndef FDW_PWM_CONTROL_H
#define FDW_PWM_CONTROL_H

#include <stdint.h>

/* 地址与新 system BD 一致；只允许配套的新 bitstream 使用此固件。 */
#define FDW_PWM_BASE_ADDRESS 0x43C00000U
#define FDW_PWM_CLOCK_HZ 100000000U

struct fdw_pwm_status {
    uint32_t enabled;
    uint32_t flags;
    uint32_t period_ticks;
    uint32_t high_ticks;
    uint32_t cycle_count;
};

int fdw_pwm_control_init(void);
int fdw_pwm_configure(uint32_t period_ticks, uint32_t high_ticks);
int fdw_pwm_enable(uint32_t enabled);
int fdw_pwm_read_status(struct fdw_pwm_status *status);

#endif
