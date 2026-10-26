#ifndef FDW_PROBE_CONTROL_H
#define FDW_PROBE_CONTROL_H

#include <stdint.h>

#define FDW_PROBE_BASE_ADDRESS 0x43C20000U
#define FDW_BURST_BASE_ADDRESS 0x43C30000U

/* 这些模式控制 FPGA 数字路由；板外模拟选择器仍须另行实现。 */
struct fdw_probe_status {
    uint32_t mode;
    uint32_t output_enabled;
    uint32_t trigger_source;
    uint32_t finite;
    uint32_t settling;
    uint32_t inputs;
    uint32_t pwm_active;
    uint32_t capture_active;
    uint32_t generation;
    uint32_t result;
};

struct fdw_burst_status {
    uint32_t flags;
    uint32_t period_ticks;
    uint32_t high_ticks;
    uint32_t cycles;
    uint32_t completed;
};

int fdw_probe_control_init(void);
int fdw_probe_set_mode(uint32_t mode, uint32_t trigger_source, uint32_t finite);
int fdw_probe_halt(void);
int fdw_probe_output_enable(uint32_t enabled);
int fdw_probe_pwm_enable(uint32_t enabled);
int fdw_probe_burst(uint32_t period, uint32_t high, uint32_t cycles);
void fdw_probe_read_status(struct fdw_probe_status *status);
void fdw_probe_read_burst(struct fdw_burst_status *status);
const char *fdw_probe_source(uint32_t mode, uint32_t finite);

#endif
