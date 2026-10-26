#include <assert.h>
#include <stdio.h>
#include "adc_test_platform.h"
#include "../src/fdw_adc_capture.c"

/* 执行真实驱动；替代的是GPIO、DMA、时间与缓存接口，不是被测驱动。 */
enum fault_type {
    NORMAL, CONFIG_MISSING, CONFIG_FAILURE, SCATTER_GATHER, GPIO_FAILURE,
    RESET_STUCK, CLEAR_ASSERT_STUCK, CLEAR_RELEASE_STUCK,
    TRANSFER_REJECTED, DMA_BUSY_ERROR, DMA_COMPLETE_ERROR, DMA_STUCK,
    DMA_SHORT, DMA_LONG, DONE_STUCK, FORMAT_BAD,
    EVENT_NOT_ACCEPTED, EVENT_MODE_MISSING, NO_EVENT
};
enum trace_type { FLUSH, INVALIDATE, DMA_ARM, START, CLEAR, RELEASE, DMA_RESET };
static enum fault_type fault;
static unsigned checks, scenarios, gpio_value, gpio_status, reset_calls, transfer_calls;
static unsigned busy_cycles, directions, trace_count;
static enum trace_type trace[128];
static UINTPTR dma_address;
static u32 dma_bytes, received_bytes;
static XTime model_time, capture_start_time;
static int dma_armed, dma_started, reset_pending, discontinuous, overrange;
static int recovery_reset_stuck;
static XAxiDma_Config model_config = {XPAR_ADC_DMA_BASEADDR};
#define CHECK(condition) do { ++checks; assert(condition); } while (0)

static void record(enum trace_type event)
{
    CHECK(trace_count < sizeof(trace) / sizeof(trace[0]));
    trace[trace_count++] = event;
}

static void prepare(enum fault_type next)
{
    ++scenarios;
    fault = next;
    gpio_value = gpio_status = reset_calls = transfer_calls = 0;
    busy_cycles = directions = trace_count = 0;
    dma_address = dma_bytes = received_bytes = 0;
    model_time = capture_start_time = 0;
    dma_armed = dma_started = reset_pending = discontinuous = overrange = 0;
    recovery_reset_stuck = 0;
}

void XTime_GetTime(XTime *time)
{
    /* 确定性推进模型时间，使全部异常等待有可核对的截止时间。 */
    model_time += 1000;
    *time = model_time;
}
int usleep(unsigned long usec) { model_time += usec; return 0; }
XAxiDma_Config *XAxiDma_LookupConfig(UINTPTR address)
{
    CHECK(address == XPAR_ADC_DMA_BASEADDR);
    return fault == CONFIG_MISSING ? NULL : &model_config;
}
int XAxiDma_CfgInitialize(XAxiDma *dma, XAxiDma_Config *config)
{
    CHECK(config == &model_config);
    dma->RegBase = config->BaseAddr;
    return fault == CONFIG_FAILURE ? -1 : XST_SUCCESS;
}
int XAxiDma_HasSg(XAxiDma *dma)
{
    CHECK(dma->RegBase == XPAR_ADC_DMA_BASEADDR);
    return fault == SCATTER_GATHER;
}
int XGpio_Initialize(XGpio *gpio, UINTPTR address)
{
    CHECK(address == XPAR_CAPTURE_GPIO_BASEADDR);
    gpio->BaseAddr = address;
    return fault == GPIO_FAILURE ? -1 : XST_SUCCESS;
}
void XGpio_SetDataDirection(XGpio *gpio, unsigned channel, u32 direction)
{
    CHECK(gpio->BaseAddr == XPAR_CAPTURE_GPIO_BASEADDR);
    CHECK((channel == 1 && direction == 0) || (channel == 2 && direction == UINT32_MAX));
    ++directions;
}
void XAxiDma_Reset(XAxiDma *dma)
{
    CHECK(dma->RegBase == XPAR_ADC_DMA_BASEADDR);
    ++reset_calls;
    reset_pending = 1;
    dma_armed = dma_started = 0;
    record(DMA_RESET);
}
int XAxiDma_ResetIsDone(XAxiDma *dma)
{
    CHECK(dma->RegBase == XPAR_ADC_DMA_BASEADDR);
    if (fault == RESET_STUCK || (recovery_reset_stuck && reset_calls > 1)) return 0;
    reset_pending = 0;
    return 1;
}
void XAxiDma_IntrDisable(XAxiDma *dma, u32 mask, int direction)
{
    CHECK(!reset_pending && dma->RegBase == XPAR_ADC_DMA_BASEADDR);
    CHECK(mask == XAXIDMA_IRQ_ALL_MASK && direction == XAXIDMA_DEVICE_TO_DMA);
}
void XGpio_DiscreteWrite(XGpio *gpio, unsigned channel, u32 data)
{
    CHECK(gpio->BaseAddr == XPAR_CAPTURE_GPIO_BASEADDR && channel == 1);
    CHECK((data & ~7U) == 0);
    gpio_value = data;
    if (data & 2U) {
        record(CLEAR);
        gpio_status = fault == CLEAR_ASSERT_STUCK ? 0U : 4U;
    } else if (data & 1U) {
        record(START);
        /* 捕获首点之前必须先把完整DMA缓冲提交给接收端。 */
        CHECK(dma_armed && !reset_pending);
        dma_started = 1;
        capture_start_time = model_time;
        gpio_status = (data & 4U) ? 0x48U : 1U;
        busy_cycles = 0;
    } else {
        record(RELEASE);
        if (!dma_started) gpio_status = fault == CLEAR_RELEASE_STUCK ? 4U : 0U;
    }
}
u32 XGpio_DiscreteRead(XGpio *gpio, unsigned channel)
{
    CHECK(gpio->BaseAddr == XPAR_CAPTURE_GPIO_BASEADDR && channel == 2);
    return gpio_status;
}
static void complete_frame(void)
{
    uint16_t *buffer = (uint16_t *)dma_address;
    unsigned i;
    for (i = 0; i < FDW_ADC_SAMPLE_COUNT; ++i) {
        u32 packed = 0x2000U | (i % 4096U);
        if (discontinuous && i == 19U) packed |= 0x4000U;
        if (overrange && (i == 0U || i == 4095U || i == 8191U)) packed |= 0x8000U;
        if (fault == FORMAT_BAD && i == 4096U) packed &= ~0x2000U;
        buffer[i] = (uint16_t)packed;
    }
    received_bytes = fault == DMA_SHORT ? dma_bytes - 2U :
                     fault == DMA_LONG ? dma_bytes + 2U : dma_bytes;
    gpio_status = fault == DONE_STUCK ? 1U : 2U;
    if (gpio_value & 4U) {
        if (fault != EVENT_NOT_ACCEPTED) gpio_status |= 0x10U;
        if (fault != EVENT_MODE_MISSING) gpio_status |= 0x40U;
    }
    dma_armed = 0;
}
int XAxiDma_SimpleTransfer(XAxiDma *dma, UINTPTR address, u32 bytes, int direction)
{
    CHECK(dma->RegBase == XPAR_ADC_DMA_BASEADDR && direction == XAXIDMA_DEVICE_TO_DMA);
    CHECK(bytes == 16384U && address % 64U == 0);
    CHECK((gpio_status & 7U) == 0U && (gpio_value & 3U) == 0U);
    ++transfer_calls;
    record(DMA_ARM);
    if (fault == TRANSFER_REJECTED) return -1;
    dma_address = address;
    dma_bytes = bytes;
    dma_armed = 1;
    return XST_SUCCESS;
}
int XAxiDma_Busy(XAxiDma *dma, int direction)
{
    CHECK(dma->RegBase == XPAR_ADC_DMA_BASEADDR && direction == XAXIDMA_DEVICE_TO_DMA);
    CHECK(dma_started);
    if (fault == DMA_STUCK || fault == NO_EVENT || fault == DMA_BUSY_ERROR) return 1;
    if (++busy_cycles <= 2) return 1;
    complete_frame();
    return 0;
}
u32 XAxiDma_ReadReg(UINTPTR base, u32 offset)
{
    CHECK(base == XPAR_ADC_DMA_BASEADDR);
    if (offset == XAXIDMA_RX_OFFSET + XAXIDMA_SR_OFFSET)
        return fault == DMA_BUSY_ERROR || fault == DMA_COMPLETE_ERROR ? 0x10U : 0U;
    CHECK(offset == XAXIDMA_RX_OFFSET + XAXIDMA_BUFFLEN_OFFSET);
    return received_bytes;
}
void Xil_DCacheFlushRange(UINTPTR address, u32 bytes)
{
    CHECK(address % 64U == 0 && bytes == 16384U);
    record(FLUSH);
}
void Xil_DCacheInvalidateRange(UINTPTR address, u32 bytes)
{
    CHECK(address % 64U == 0 && bytes == 16384U);
    record(INVALIDATE);
}

static int16_t samples[FDW_ADC_SAMPLE_COUNT];
static void ready(void)
{
    prepare(NORMAL);
    CHECK(fdw_adc_capture_init() == 0 && capture_available && directions == 2);
    trace_count = 0;
}
static void success(unsigned event_mode, int use_overrange, int use_discontinuous)
{
    unsigned i;
    u32 otr = UINT32_MAX;
    ready();
    CHECK(fdw_adc_capture_set_trigger(event_mode) == 0);
    overrange = use_overrange;
    discontinuous = use_discontinuous;
    CHECK(fdw_adc_capture_frame(samples, 8192, &otr) == 0);
    for (i = 0; i < 8192; ++i) CHECK(samples[i] == (int16_t)((int)(i % 4096U) - 2048));
    CHECK(otr == (use_overrange ? 3U : 0U));
    CHECK(fdw_adc_capture_frame_flags() == (use_discontinuous ? 12U : 8U));
    CHECK(fdw_adc_capture_trigger_status() == (event_mode ? 0x50U : 0U));
    CHECK(trace_count == 8 && trace[0] == CLEAR && trace[1] == RELEASE);
    CHECK(trace[2] == FLUSH && trace[3] == INVALIDATE && trace[4] == DMA_ARM);
    CHECK(trace[5] == START && trace[6] == RELEASE && trace[7] == INVALIDATE);
    CHECK(gpio_value == event_mode * 4U && reset_calls == 1);
}
static void capture_error(enum fault_type injection, int expected, unsigned event_mode)
{
    unsigned resets;
    ready();
    CHECK(fdw_adc_capture_set_trigger(event_mode) == 0);
    fault = injection;
    resets = reset_calls;
    CHECK(fdw_adc_capture_frame(samples, 8192, NULL) == expected);
    CHECK(fdw_adc_capture_frame_flags() == 0U && fdw_adc_capture_trigger_status() == 0U);
    CHECK(reset_calls == resets + 1U);
    if (injection == DMA_STUCK || injection == NO_EVENT) {
        CHECK(model_time - capture_start_time >= 100000U);
        CHECK(model_time - capture_start_time <= 110000U);
    }
    if (injection == CLEAR_ASSERT_STUCK || injection == CLEAR_RELEASE_STUCK) {
        CHECK(!capture_available && gpio_value == (2U | event_mode * 4U));
        CHECK(fdw_adc_capture_frame(samples, 8192, NULL) == -16);
    } else {
        CHECK(capture_available && gpio_value == event_mode * 4U);
        /* 异常必须允许后续恢复到旧立即模式，不能重放上次帧状态。 */
        fault = NORMAL;
        trace_count = 0;
        CHECK(fdw_adc_capture_set_trigger(0) == 0);
        CHECK(fdw_adc_capture_frame(samples, 8192, NULL) == 0);
        CHECK(fdw_adc_capture_frame_flags() == 8U && fdw_adc_capture_trigger_status() == 0U);
    }
}
int main(void)
{
    static const enum fault_type init_faults[] = {
        CONFIG_MISSING, CONFIG_FAILURE, SCATTER_GATHER, GPIO_FAILURE, RESET_STUCK
    };
    static const int init_errors[] = {-1,-2,-3,-5,-4};
    unsigned i;
    prepare(NORMAL);
    CHECK(fdw_adc_capture_set_trigger(1) == -16);
    CHECK(fdw_adc_capture_frame(samples, 8192, NULL) == -16);
    for (i = 0; i < sizeof(init_faults) / sizeof(init_faults[0]); ++i) {
        prepare(init_faults[i]);
        CHECK(fdw_adc_capture_init() == init_errors[i]);
        CHECK(fdw_adc_capture_frame(samples, 8192, NULL) == -16);
    }
    success(0, 0, 0);
    success(1, 1, 0);
    success(0, 1, 1);
    success(1, 0, 1);
    ready();
    CHECK(fdw_adc_capture_frame(NULL, 8192, NULL) == -10);
    CHECK(fdw_adc_capture_frame(samples, 8191, NULL) == -10 && transfer_calls == 0);
    CHECK(fdw_adc_capture_set_trigger(1) == 0);
    CHECK(fdw_adc_capture_set_trigger(2) == -10 && capture_trigger_mode == 4U);
    capture_error(TRANSFER_REJECTED, -11, 1);
    capture_error(DMA_BUSY_ERROR, -13, 0);
    capture_error(DMA_COMPLETE_ERROR, -13, 1);
    capture_error(DMA_STUCK, -12, 0);
    capture_error(NO_EVENT, -12, 1);
    capture_error(DMA_SHORT, -13, 0);
    capture_error(DMA_LONG, -13, 1);
    capture_error(DONE_STUCK, -17, 0);
    capture_error(FORMAT_BAD, -14, 1);
    capture_error(EVENT_NOT_ACCEPTED, -18, 1);
    capture_error(EVENT_MODE_MISSING, -18, 1);
    capture_error(CLEAR_ASSERT_STUCK, -17, 0);
    capture_error(CLEAR_RELEASE_STUCK, -17, 1);
    /* 事件等待超时后若DMA复位再次超时，保持清理并禁止下一帧误用残留数据。 */
    ready();
    CHECK(fdw_adc_capture_set_trigger(1) == 0);
    fault = NO_EVENT;
    recovery_reset_stuck = 1;
    CHECK(fdw_adc_capture_frame(samples, 8192, NULL) == -12);
    CHECK(!capture_available && gpio_value == 6U && model_time >= 200000U);
    CHECK(fdw_adc_capture_frame_flags() == 0U && fdw_adc_capture_trigger_status() == 0U);
    CHECK(fdw_adc_capture_frame(samples, 8192, NULL) == -16);
    fault = NORMAL;
    recovery_reset_stuck = 0;
    CHECK(fdw_adc_capture_init() == 0);
    CHECK(fdw_adc_capture_frame(samples, 8192, NULL) == 0);
    CHECK(fdw_adc_capture_frame_flags() == 8U && fdw_adc_capture_trigger_status() == 0U);
    /* 长任务轮询不能等待完整窗口；取消后必须重新清理并可采新帧。 */
    ready();fault=DMA_STUCK;
    CHECK(fdw_adc_capture_start(5000000U)==0);
    CHECK(fdw_adc_capture_start(5000000U)==-20);
    CHECK(fdw_adc_capture_init()==-20 && fdw_adc_capture_set_trigger(0)==-20);
    for (i=0;i<8U;++i) CHECK(fdw_adc_capture_poll(samples,8192,NULL)==1);
    CHECK(model_time-capture_start_time<50000U);
    CHECK(fdw_adc_capture_cancel()==0 && !async_active);
    CHECK(fdw_adc_capture_poll(samples,8192,NULL)==-21);
    fault=NORMAL;trace_count=0;
    CHECK(fdw_adc_capture_start(5000000U)==0);
    CHECK(fdw_adc_capture_poll(samples,8192,NULL)==1);
    CHECK(fdw_adc_capture_poll(samples,8192,NULL)==1);
    CHECK(fdw_adc_capture_poll(samples,8192,NULL)==0);
    ready();fault=DMA_STUCK;
    CHECK(fdw_adc_capture_start(5000000U)==0);
    model_time+=5000000U;
    CHECK(fdw_adc_capture_poll(samples,8192,NULL)==-12 && !async_active);
    CHECK(fdw_adc_capture_start(99999U)==-10);
    CHECK(fdw_adc_capture_start(30000001U)==-10);
    printf("FDW_ADC_CAPTURE_PASS scenarios=%u checks=%u\n", scenarios, checks);
    return 0;
}
