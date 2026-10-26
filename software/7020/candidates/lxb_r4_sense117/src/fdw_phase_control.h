#ifndef FDW_PHASE_CONTROL_H
#define FDW_PHASE_CONTROL_H
#include <stdint.h>
struct fdw_phase_status {
    uint32_t ready, record_id, generation, sample_count, errors;
    uint32_t first_phase, last_phase, frequency_word, tag, inverted;
    uint64_t first_tick, last_tick;
    uint32_t interval_ticks, decimation_log2, filter_id, metadata_version;
};
/* 任务状态：0空闲，1采集中，2成功，3失败，4取消。 */
struct fdw_phase_job {
    uint32_t job_id, state, record_id, timeout_us, decimation_log2;
    int code;
};
int fdw_phase_control_init(void);
int fdw_phase_capture(uint32_t count, uint32_t *id);
int fdw_phase_status(struct fdw_phase_status *status);
int fdw_phase_configure_raw(void);
int fdw_phase_job_start(uint32_t count, uint32_t log2, uint32_t *id);
int fdw_phase_job_cancel(uint32_t id);
void fdw_phase_job_service(void);
void fdw_phase_job_status(struct fdw_phase_job *status);
#endif
