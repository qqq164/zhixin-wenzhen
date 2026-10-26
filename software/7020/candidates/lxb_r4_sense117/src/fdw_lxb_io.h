#ifndef FDW_LXB_IO_H
#define FDW_LXB_IO_H
#include <stdint.h>

/* 鹿小班新增寄存器，不改变原 R4 采集及相位地址。 */
#define FDW_LXB_GPIO_BASE 0x41220000U
#define FDW_LXB_PWM_B_BASE 0x43C40000U

struct fdw_lxb_pwm_status {
    uint32_t enabled, output_enabled, period_ticks, high_ticks, flags;
};
/* 采样选择只影响K5/K7，状态回读不代表触点反馈或模拟稳定验收。 */
#define FDW_SENSE_VA 1U
#define FDW_SENSE_VB 2U
#define FDW_SENSE_PROBE 3U
struct fdw_lxb_sense_status {
    uint32_t generation, relay_mask, selected, rs_nominal_ohm;
    uint32_t settling, remaining_ms, fault;
};
void fdw_lxb_sense_status(struct fdw_lxb_sense_status *status);
int fdw_lxb_sense_select(uint32_t selected, uint32_t wait_ms);
int fdw_lxb_sense_ready(void);
int fdw_lxb_io_init(void);
void fdw_lxb_io_halt(void);
void fdw_lxb_select_default_output(void);
uint32_t fdw_lxb_output_mask(void);
uint32_t fdw_lxb_relay_mask(void);
int fdw_lxb_relay_set(uint32_t mask);
int fdw_lxb_pwm_set(uint32_t channel, uint32_t period, uint32_t high);
int fdw_lxb_pwm_enable(uint32_t channel, uint32_t enabled);
int fdw_lxb_pwm_status(uint32_t channel, struct fdw_lxb_pwm_status *status);
#endif
