/* 固定小头部加原码流，不为每个连接复制整帧。多字节字段统一小端。 */
#include "fdw_bulk_frame.h"
#include "fdw_adc_control.h"
#include "fdw_phase_control.h"
#include <string.h>
uint32_t fdw_crc32(const void *data, size_t length)
{
    const uint8_t *bytes=data;
    uint32_t crc=0xFFFFFFFFU;
    size_t index;
    unsigned bit;
    for (index=0;index<length;++index) {
        crc^=bytes[index];
        for (bit=0;bit<8U;++bit) crc=(crc>>1)^((crc&1U)?0xEDB88320U:0U);
    }
    return ~crc;
}
static void put32(uint8_t *header,unsigned offset,uint32_t value)
{
    unsigned i;
    for (i=0;i<4U;++i) header[offset+i]=(uint8_t)(value>>(i*8U));
}
static void put64(uint8_t *header,unsigned offset,uint64_t value)
{
    put32(header,offset,(uint32_t)value);put32(header,offset+4U,(uint32_t)(value>>32));
}
int fdw_bulk_frame_open(uint32_t record,uint8_t *header,const int16_t **samples)
{
    struct fdw_adc_status adc;
    struct fdw_phase_status phase;
    int result;
    if (!header || !samples) return -1;
    fdw_adc_control_status(&adc);
    if (!record || record!=adc.record_id || adc.sample_count!=FDW_ADC_SAMPLE_COUNT) return -4;
    result=fdw_adc_control_pin(record,samples);
    if (result!=0) return result;
    memset(header,0,FDW_BULK_HEADER_BYTES);
    memcpy(header,"FDB4",4U);header[4]=1U;header[6]=FDW_BULK_HEADER_BYTES;
    put32(header,8U,record);put32(header,12U,adc.sample_count);
    put32(header,16U,adc.sample_count*2U);
    put32(header,20U,fdw_crc32(*samples,adc.sample_count*2U));
    put32(header,24U,adc.flags);put32(header,28U,FDW_ADC_SAMPLE_RATE_HZ);put32(header,32U,1U);
    if (fdw_phase_status(&phase)==0 && phase.record_id==record) {
        put32(header,32U,phase.interval_ticks);put32(header,36U,phase.metadata_version);
        put32(header,40U,phase.generation);put32(header,44U,phase.errors);
        put32(header,48U,phase.first_phase);put32(header,52U,phase.last_phase);
        put32(header,56U,phase.frequency_word);put32(header,60U,phase.tag);
        put64(header,64U,phase.first_tick);put64(header,72U,phase.last_tick);
        put32(header,80U,phase.inverted);put32(header,84U,phase.decimation_log2);
        put32(header,88U,phase.filter_id);put32(header,92U,record);
        put32(header,116U,phase.decimation_log2?2U:0U);
    }
    put32(header,96U,1U);put32(header,100U,adc.overrange_count);
    put32(header,104U,adc.trigger);put32(header,108U,adc.route_generation);put32(header,112U,adc.mode);
    put32(header,124U,fdw_crc32(header,124U));
    return 0;
}
void fdw_bulk_frame_close(void) { fdw_adc_control_unpin(); }
