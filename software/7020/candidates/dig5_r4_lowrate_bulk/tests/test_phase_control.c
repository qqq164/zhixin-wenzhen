#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "../src/fdw_phase_control.c"

/* 只模拟寄存器、DAC状态和帧身份，用于检验拒绝路径；不作为板上相位证据。 */
static uint16_t words[30];
static uint32_t gpio_status=0x20000000U,adc_record,next_record;
static XTime clock_now;
static int fault,capture_result,busy,delayed;
static uint32_t configured_log2,pending_record;
static struct fdw_dac_status dac;
static struct fdw_probe_status route;
void XTime_GetTime(XTime *time){clock_now+=100;*time=clock_now;}
int usleep(unsigned long usec){clock_now+=usec;return 0;}
int XGpio_Initialize(XGpio *gpio,UINTPTR address){gpio->BaseAddr=address;return 0;}
void XGpio_SetDataDirection(XGpio *gpio,unsigned channel,u32 direction){(void)gpio;(void)channel;(void)direction;}
u32 XGpio_DiscreteRead(XGpio *gpio,unsigned channel){(void)gpio;(void)channel;return gpio_status;}
void XGpio_DiscreteWrite(XGpio *gpio,unsigned channel,u32 data)
{
    uint32_t index=(data>>8)&31U;
    (void)gpio;(void)channel;
    if(fault==8)return;
    if (data&0x40000000U) {
        uint32_t requested=(data>>16)&31U;
        gpio_status=(data&0x80000000U)|0x20000000U;
        if (busy || requested>16U) gpio_status|=0x10000000U;
        else { configured_log2=requested;gpio_status|=requested<<8; }
    } else gpio_status=(data&0x80000000U)|0x20000000U|((uint32_t)words[index]<<8);
    if(fault==7&&index==26)++words[2];
}
void fdw_dac_read_status(struct fdw_dac_status *status){*status=dac;}
void fdw_probe_read_status(struct fdw_probe_status *status){*status=route;}
void fdw_adc_control_status(struct fdw_adc_status *status){memset(status,0,sizeof(*status));status->record_id=adc_record;}
void fdw_adc_control_invalidate(void){adc_record=0;}
int fdw_adc_control_capture(uint32_t count,uint32_t *id)
{
    uint32_t last_phase;
    uint64_t first_tick=0xFFFFFFF0ULL,last_tick=first_tick+(8191ULL<<configured_log2);
    (void)count;
    if(capture_result)return capture_result;
    adc_record=busy?pending_record:++next_record;*id=adc_record;
    memset(words,0,sizeof(words));words[0]=0xD907U;words[1]=2;words[2]=1;words[4]=8192;
    words[6]=0xFFF0U;words[7]=0xFFFFU;
    last_phase=0xFFFFFFF0U+(8191U<<configured_log2)*dac.frequency_word;
    words[8]=(uint16_t)last_phase;words[9]=(uint16_t)(last_phase>>16);
    words[10]=(uint16_t)dac.frequency_word;words[11]=(uint16_t)(dac.frequency_word>>16);
    words[12]=(uint16_t)dac.tag;words[13]=(uint16_t)(dac.tag>>16);words[26]=2;words[27]=(uint16_t)configured_log2;words[28]=configured_log2?1:0;words[29]=configured_log2?2:0;
    words[22]=(uint16_t)((1U<<configured_log2)-1U);
    for(unsigned i=0;i<4;i++){words[14+i]=(uint16_t)(first_tick>>(16*i));words[18+i]=(uint16_t)(last_tick>>(16*i));}
    if(fault==1)words[1]|=4;
    if(fault==2)++dac.tag;
    if(fault==3)++words[18];
    if(fault==4)++words[8];
    if(fault==5)words[0]=0;
    if(fault==6)words[22]=1;
    return 0;
}
int fdw_adc_control_busy(void) { return busy; }
int fdw_adc_control_start(uint32_t count,uint32_t timeout,uint32_t *id)
{
    assert(count==8192 && timeout>=1000000U && timeout<30000000U);
    if (busy) return -20;
    busy=1;delayed=2;adc_record=0;pending_record=++next_record;*id=pending_record;return 0;
}
int fdw_adc_control_poll(void)
{
    uint32_t id;
    int result;
    if (delayed) { --delayed;return 1; }
    result=fdw_adc_control_capture(8192,&id);busy=0;return result;
}
int fdw_adc_control_cancel(void) { busy=0;adc_record=0;return 0; }
int fdw_dac_halt(void) { dac.enabled=0;return 0; }
int main(void)
{
    struct fdw_phase_status actual;
    uint32_t id=0;
    assert(fdw_phase_capture(8192,&id)==-30);
    assert(fdw_phase_control_init()==-33);
    words[0]=0xD907U;assert(fdw_phase_control_init()==0);
    dac.ready=dac.enabled=dac.configured=1;dac.tag=7;dac.frequency_word=0x01000001U;
    assert(fdw_phase_capture(1,&id)==-30);
    assert(fdw_phase_capture(8192,0)==-30);
    route.mode=3;assert(fdw_phase_capture(8192,&id)==-34);route.mode=0;
    assert(fdw_phase_capture(8192,&id)==0);
    assert(fdw_phase_status(&actual)==0&&actual.record_id==id);
    assert(actual.first_tick==0xFFFFFFF0ULL&&actual.last_tick==0x100001FEFULL);
    for(fault=1;fault<=8;fault++){
        dac.tag=7;
        assert(fdw_phase_capture(8192,&id)<0);
        assert(fdw_phase_status(&actual)==-4&&adc_record==0);
    }
    fault=0;capture_result=-12;assert(fdw_phase_capture(8192,&id)==-12);
    capture_result=0;assert(fdw_phase_capture(8192,&id)==0);
    fdw_adc_control_invalidate();assert(fdw_phase_status(&actual)==-4);
    dac.frequency_word=172;
    for (uint32_t exponent=2;exponent<=16;exponent+=2) {
        struct fdw_phase_job current;
        dac.enabled=1;dac.tag=7;
        assert(fdw_phase_job_start(8192,exponent,&id)==0 && busy);
        assert(fdw_phase_job_start(8192,exponent,&id)==-20);
        fdw_phase_job_service();fdw_phase_job_status(&current);
        assert(current.state==1 && fdw_phase_status(&actual)==-4);
        fdw_phase_job_service();fdw_phase_job_service();fdw_phase_job_status(&current);
        assert(current.state==2 && current.record_id==id && !busy && dac.enabled);
        assert(fdw_phase_status(&actual)==0 && actual.interval_ticks==(1U<<exponent));
        assert(actual.last_tick-actual.first_tick==(8191ULL<<exponent));
    }
    dac.frequency_word=0x7fffffffU;
    assert(fdw_phase_job_start(8192,1,&id)==-37 && !busy);
    dac.frequency_word=172;
    assert(fdw_phase_job_start(8192,17,&id)==-30);
    assert(fdw_phase_job_start(8192,14,&id)==0);
    assert(fdw_phase_job_cancel(id-1)==-4 && busy);
    assert(fdw_phase_job_cancel(id)==0 && !busy && !dac.enabled);
    assert(fdw_phase_status(&actual)==-4);
    dac.enabled=1;assert(fdw_phase_job_start(8192,14,&id)==0);
    capture_result=-12;delayed=0;fdw_phase_job_service();
    { struct fdw_phase_job current;fdw_phase_job_status(&current);assert(current.state==3 && current.code==-12 && !dac.enabled); }
    puts("FDW_PHASE_CONTROL_NATIVE_PASS fault_cases=8 lowrate_cases=8 cancel_timeout=passed");return 0;
}
