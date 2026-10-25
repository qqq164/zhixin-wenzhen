#ifndef FDW_DAC_CONTROL_H
#define FDW_DAC_CONTROL_H
#include <stdint.h>

#define FDW_DAC_BASE_ADDRESS 0x41210000U
#define FDW_DAC_CLOCK_HZ 25000000U

/* 所有幅度与偏置使用数字码，尚无板端电压标定。 */
struct fdw_dac_status {
    uint32_t ready, enabled, waveform_active, clipped, busy;
    uint32_t hardware_code, error_code, tag, frequency_word, phase_word;
    uint32_t gain_q11, offset_code, configured;
};

int fdw_dac_control_init(void);
int fdw_dac_configure(uint32_t frequency_hz, uint32_t gain_q11,
                      uint32_t offset_code, uint32_t phase_word, uint32_t invert);
int fdw_dac_enable(uint32_t enabled);
int fdw_dac_halt(void);
void fdw_dac_read_status(struct fdw_dac_status *status);
#endif
