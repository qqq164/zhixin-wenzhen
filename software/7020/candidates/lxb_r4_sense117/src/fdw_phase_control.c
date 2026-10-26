/* R4保留同源相位记录，倍率配置采用跨时钟握手，不改变25MHz物理时钟。 */
#include "fdw_phase_control.h"
#include "fdw_adc_control.h"
#include "fdw_dac_control.h"
#include "fdw_probe_control.h"
#include "xgpio.h"
#include "xparameters.h"
#include "xstatus.h"
#include "xiltimer.h"
#include "sleep.h"

static XGpio phase_gpio;
static struct fdw_phase_status saved;
static struct fdw_phase_job job;
static struct fdw_dac_status excitation;

static int expired(XTime start)
{
    XTime now;
    XTime_GetTime(&now);
    return now-start > (XTime)COUNTS_PER_SECOND/100U;
}

/* 请求位仅在邮箱空闲时翻转；不直接跨域采入多位配置。 */
static int exchange(uint32_t command, uint16_t *value)
{
    XTime start;
    uint32_t status,request;
    usleep(2U);
    XTime_GetTime(&start);
    do {
        status=XGpio_DiscreteRead(&phase_gpio,2U);
        if (expired(start)) return -31;
    } while ((status&0x60000000U)!=0x20000000U);
    request=(status^0x80000000U)&0x80000000U;
    XGpio_DiscreteWrite(&phase_gpio,1U,request|command);
    XTime_GetTime(&start);
    do {
        status=XGpio_DiscreteRead(&phase_gpio,2U);
        if (expired(start)) return -31;
    } while ((status&0xC0000000U)!=request);
    if (status&0x10000000U) return -32;
    if (value) *value=(uint16_t)(status>>8);
    return 0;
}
static int read_word(uint32_t index, uint16_t *value)
{
    if (index>31U || !value) return -30;
    return exchange(index<<8,value);
}
static int configure_rate(uint32_t log2)
{
    uint16_t confirmed;
    int result;
    if (!saved.ready || log2>16U) return -30;
    if (fdw_adc_control_busy()) return -20;
    result=exchange(0x40000000U|(log2<<16),&confirmed);
    if (result==0 && confirmed!=log2) return -35;
    return result;
}
int fdw_phase_configure_raw(void) { return configure_rate(0U); }
static uint32_t pair(const uint16_t *word,uint32_t index)
{
    return (uint32_t)word[index]|((uint32_t)word[index+1U]<<16);
}
int fdw_phase_control_init(void)
{
    uint16_t signature=0U;
    if (fdw_adc_control_busy()) return -20;
    saved.ready=0U;saved.record_id=0U;
    if (XGpio_Initialize(&phase_gpio,XPAR_CAPTURE_GPIO_BASEADDR)!=XST_SUCCESS) return -30;
    XGpio_SetDataDirection(&phase_gpio,1U,0U);
    XGpio_SetDataDirection(&phase_gpio,2U,0xFFFFFFFFU);
    XGpio_DiscreteWrite(&phase_gpio,1U,0U);
    if (read_word(0U,&signature)!=0 || signature!=0xD907U) return -33;
    saved.ready=1U;
    { int result=configure_rate(0U);if (result!=0) saved.ready=0U;return result; }
}

static int excitation_valid(struct fdw_dac_status *before)
{
    struct fdw_probe_status route;
    fdw_dac_read_status(before);fdw_probe_read_status(&route);
    if (!before->ready || !before->enabled || !before->configured || before->clipped ||
        before->busy || !before->tag || !before->frequency_word || route.mode!=0U) return -34;
    return 0;
}

static int collect_metadata(uint32_t count,uint32_t record,uint32_t log2,
                            const struct fdw_dac_status *before)
{
    struct fdw_dac_status after;
    uint16_t words[30],generation_low,generation_high;
    uint32_t index,interval=1U<<log2;
    int result;
    for (index=0U;index<30U;++index) {
        result=read_word(index,&words[index]);
        if (result!=0) goto invalid;
    }
    result=read_word(2U,&generation_low);
    if (result==0) result=read_word(3U,&generation_high);
    if (result!=0) goto invalid;
    fdw_dac_read_status(&after);
    if (words[0]!=0xD907U || words[1]!=2U || words[26]!=2U || words[27]!=log2 ||
        words[28]!=(log2?1U:0U) || words[29]!=(log2?2U:0U) ||
        generation_low!=words[2] || generation_high!=words[3] ||
        pair(words,4U)!=count || pair(words,22U)!=interval-1U ||
        !after.enabled || after.clipped || after.busy ||
        after.tag!=before->tag || pair(words,12U)!=before->tag ||
        after.frequency_word!=before->frequency_word || pair(words,10U)!=before->frequency_word) {
        result=-35;goto invalid;
    }
    saved.generation=pair(words,2U);saved.sample_count=pair(words,4U);saved.errors=words[1]>>2;
    saved.first_phase=pair(words,6U);saved.last_phase=pair(words,8U);
    saved.frequency_word=pair(words,10U);saved.tag=pair(words,12U);saved.inverted=words[24];
    saved.first_tick=(uint64_t)pair(words,14U)|((uint64_t)pair(words,16U)<<32);
    saved.last_tick=(uint64_t)pair(words,18U)|((uint64_t)pair(words,20U)<<32);
    saved.interval_ticks=interval;saved.decimation_log2=log2;
    saved.filter_id=words[28];saved.metadata_version=2U;
    if (!saved.generation || saved.last_tick-saved.first_tick!=(uint64_t)(count-1U)*interval ||
        saved.last_phase!=saved.first_phase+(count-1U)*interval*saved.frequency_word) {
        result=-36;goto invalid;
    }
    saved.record_id=record;
    return 0;
invalid:
    fdw_adc_control_invalidate();saved.record_id=0U;
    return result;
}

int fdw_phase_capture(uint32_t count,uint32_t *id)
{
    struct fdw_dac_status before;
    uint32_t record=0U;
    int result;
    if (!saved.ready || count!=FDW_ADC_SAMPLE_COUNT || !id) return -30;
    if (fdw_adc_control_busy()) return -20;
    result=excitation_valid(&before);
    if (result==0) result=configure_rate(0U);
    if (result!=0) return result;
    result=fdw_adc_control_capture(count,&record);
    if (result!=0) return result;
    saved.record_id=0U;
    result=collect_metadata(count,record,0U,&before);
    if (result==0) *id=record;
    return result;
}

int fdw_phase_job_start(uint32_t count,uint32_t log2,uint32_t *id)
{
    uint32_t record;
    uint64_t timeout;
    struct fdw_dac_status before;
    int result;
    if (!saved.ready || count!=FDW_ADC_SAMPLE_COUNT || log2>16U || !id) return -30;
    if (fdw_adc_control_busy()) return -20;
    result=excitation_valid(&before);
    if (result==0 && (uint64_t)before.frequency_word*(1U<<log2)>=0x80000000ULL) return -37;
    if (result==0) result=configure_rate(log2);
    if (result!=0) return result;
    /* 包含滤波预热、整帧时间和1秒调度余量，最大倍率仍小于30秒。 */
    timeout=((uint64_t)(count+4U)*(1U<<log2)*1000000U+FDW_ADC_SAMPLE_RATE_HZ-1U)/FDW_ADC_SAMPLE_RATE_HZ+1000000U;
    result=fdw_adc_control_start(count,(uint32_t)timeout,&record);
    if (result!=0) return result;
    saved.record_id=0U;excitation=before;
    job.job_id=record;job.state=1U;job.record_id=0U;job.code=0;
    job.timeout_us=(uint32_t)timeout;job.decimation_log2=log2;
    *id=record;
    return 0;
}
void fdw_phase_job_service(void)
{
    int result;
    if (job.state!=1U) return;
    result=fdw_adc_control_poll();
    if (result==1) return;
    if (result==0) result=collect_metadata(FDW_ADC_SAMPLE_COUNT,job.job_id,job.decimation_log2,&excitation);
    job.code=result;
    job.state=result==0?2U:3U;
    job.record_id=result==0?job.job_id:0U;
    /* 异常任务明确停波，正常结束及命令TCP关闭保持连续激励。 */
    if (result!=0) { saved.record_id=0U;(void)fdw_dac_halt(); }
}
int fdw_phase_job_cancel(uint32_t id)
{
    int result;
    if (!id || id!=job.job_id) return -4;
    if (job.state!=1U) return -21;
    result=fdw_adc_control_cancel();
    saved.record_id=0U;job.record_id=0U;job.code=result==0?-22:result;job.state=4U;
    (void)fdw_dac_halt();
    return result;
}
void fdw_phase_job_status(struct fdw_phase_job *status) { *status=job; }
int fdw_phase_status(struct fdw_phase_status *status)
{
    struct fdw_adc_status adc;
    if (!status) return -30;
    fdw_adc_control_status(&adc);
    if (!saved.record_id || saved.record_id!=adc.record_id) return -4;
    *status=saved;return 0;
}
