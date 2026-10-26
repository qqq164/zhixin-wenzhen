#include "fdw_adc_capture.h"

#include "sleep.h"
#include "xaxidma.h"
#include "xgpio.h"
#include "xil_cache.h"
#include "xil_printf.h"
#include "xparameters.h"
#include "xstatus.h"
#include "xiltimer.h"

#define FDW_CAPTURE_GPIO_START_CHANNEL 1U
#define FDW_CAPTURE_GPIO_STATUS_CHANNEL 2U
#define FDW_CAPTURE_START 0x1U
#define FDW_CAPTURE_CLEAR 0x2U
#define FDW_CAPTURE_CLEAR_ACK 0x4U
#define FDW_CAPTURE_ACTIVE_DONE 0x3U
#define FDW_CAPTURE_CONTROL_TIMEOUT_US 10000U
#define FDW_CAPTURE_DMA_TIMEOUT_US 100000U
#define FDW_CAPTURE_BYTES (FDW_ADC_SAMPLE_COUNT * sizeof(uint16_t))

static XAxiDma adc_dma;
static XGpio capture_gpio;
static int capture_available = 0;
static uint32_t capture_frame_flags = 0U;
static uint32_t capture_trigger_mode = 0U;
static uint32_t capture_trigger_status = 0U;
static int async_active;
static XTime async_start;
static uint32_t async_timeout_us;

int fdw_adc_capture_set_trigger(uint32_t trigger)
{
    if (trigger > 1U) return -10;
    if (async_active) return -20;
    if (!capture_available) return -16;
    capture_trigger_mode = trigger << 2;
    return 0;
}

uint32_t fdw_adc_capture_trigger_status(void) { return capture_trigger_status; }

/* DMA缓冲区按缓存行对齐，避免缓存维护影响相邻变量。 */
static uint16_t dma_rx_buffer[FDW_ADC_SAMPLE_COUNT]
    __attribute__((aligned(64)));

static int deadline_expired(XTime start, uint32_t timeout_us)
{
    XTime now;
    XTime_GetTime(&now);
    return (now - start) >= ((XTime)timeout_us * COUNTS_PER_SECOND / 1000000U);
}

static int wait_capture_status(uint32_t mask, uint32_t expected)
{
    XTime start;
    XTime_GetTime(&start);
    while ((XGpio_DiscreteRead(&capture_gpio,
                               FDW_CAPTURE_GPIO_STATUS_CHANNEL) & mask) != expected) {
        if (deadline_expired(start, FDW_CAPTURE_CONTROL_TIMEOUT_US))
            return -1;
    }
    return 0;
}

static int reset_dma(void)
{
    XTime start;
    XAxiDma_Reset(&adc_dma);
    XTime_GetTime(&start);
    while (XAxiDma_ResetIsDone(&adc_dma) == 0) {
        if (deadline_expired(start, FDW_CAPTURE_DMA_TIMEOUT_US))
            return -1;
    }
    XAxiDma_IntrDisable(&adc_dma, XAXIDMA_IRQ_ALL_MASK,
                        XAXIDMA_DEVICE_TO_DMA);
    return 0;
}

static int assert_capture_clear(void)
{
    XGpio_DiscreteWrite(&capture_gpio, FDW_CAPTURE_GPIO_START_CHANNEL,
                        FDW_CAPTURE_CLEAR | capture_trigger_mode);
    if (wait_capture_status(FDW_CAPTURE_CLEAR_ACK, FDW_CAPTURE_CLEAR_ACK) != 0)
        return -1;
    /* 保持足够多的写时钟周期，覆盖FIFO内部复位同步要求。 */
    usleep(10U);
    return 0;
}

static int release_capture_clear(void)
{
    XGpio_DiscreteWrite(&capture_gpio, FDW_CAPTURE_GPIO_START_CHANNEL, capture_trigger_mode);
    if (wait_capture_status(FDW_CAPTURE_CLEAR_ACK, 0U) != 0)
        return -1;
    usleep(10U);
    return wait_capture_status(FDW_CAPTURE_ACTIVE_DONE, 0U);
}

static int recover_capture(void)
{
    int clear_status;
    int dma_status;
    capture_available = 0;
    clear_status = assert_capture_clear();
    dma_status = reset_dma();
    if (clear_status != 0 || dma_status != 0 || release_capture_clear() != 0) {
        /* 失败后保持清理电平，不允许残留数据被下一次请求当作新帧。 */
        XGpio_DiscreteWrite(&capture_gpio, FDW_CAPTURE_GPIO_START_CHANNEL,
                            FDW_CAPTURE_CLEAR | capture_trigger_mode);
        xil_printf("ADC recovery failed: clear=%d, dma=%d\r\n", clear_status, dma_status);
        return -1;
    }
    capture_available = 1;
    return 0;
}

static int capture_failed(int code)
{
    async_active = 0;
    capture_frame_flags = 0U;
    capture_trigger_status = 0U;
    (void)recover_capture();
    return code;
}

uint32_t fdw_adc_capture_frame_flags(void)
{
    return capture_frame_flags;
}


int fdw_adc_capture_init(void)
{
    XAxiDma_Config *dma_config;
    int status;
    if (async_active) return -20;
    capture_available = 0;
    capture_frame_flags = 0U;
    capture_trigger_mode = 0U;
    capture_trigger_status = 0U;

    dma_config = XAxiDma_LookupConfig(XPAR_ADC_DMA_BASEADDR);
    if (dma_config == NULL) {
        xil_printf("ADC init failed: DMA config not found\r\n");
        return -1;
    }

    status = XAxiDma_CfgInitialize(&adc_dma, dma_config);
    if (status != XST_SUCCESS) {
        xil_printf("ADC init failed: DMA init status=%d\r\n", status);
        return -2;
    }

    if (XAxiDma_HasSg(&adc_dma)) {
        xil_printf("ADC init failed: DMA is not in simple mode\r\n");
        return -3;
    }

    status = XGpio_Initialize(&capture_gpio, XPAR_CAPTURE_GPIO_BASEADDR);
    if (status != XST_SUCCESS) {
        xil_printf("ADC init failed: GPIO init status=%d\r\n", status);
        return -5;
    }

    /* 通道1扩展为start/clear/event_mode；通道2保留旧低3位，增加触发状态。 */
    XGpio_SetDataDirection(&capture_gpio,
                           FDW_CAPTURE_GPIO_START_CHANNEL, 0x0U);
    XGpio_SetDataDirection(&capture_gpio,
                           FDW_CAPTURE_GPIO_STATUS_CHANNEL, 0xFFFFFFFFU);
    if (recover_capture() != 0)
        return -4;

    xil_printf("ADC capture ready: 12-bit, %lu samples, %lu Hz\r\n",
               (unsigned long)FDW_ADC_SAMPLE_COUNT,
               (unsigned long)FDW_ADC_SAMPLE_RATE_HZ);
    return 0;
}


int fdw_adc_capture_start(uint32_t timeout_us)
{
    int status;

    if (async_active) return -20;
    if (timeout_us < 100000U || timeout_us > 30000000U) return -10;
    capture_frame_flags = 0U;
    capture_trigger_status = 0U;
    if (!capture_available)
        return -16;
    /* 每次先确认局部清理完成，避免上次异常残留或过早重触发。 */
    if (assert_capture_clear() != 0 || release_capture_clear() != 0)
        return capture_failed(-17);

    /* 先准备DMA，再启动PL采集，保证第一个样本不会因未就绪而丢失。 */
    Xil_DCacheFlushRange((UINTPTR)dma_rx_buffer, FDW_CAPTURE_BYTES);
    Xil_DCacheInvalidateRange((UINTPTR)dma_rx_buffer, FDW_CAPTURE_BYTES);

    status = XAxiDma_SimpleTransfer(&adc_dma,
                                    (UINTPTR)dma_rx_buffer,
                                    FDW_CAPTURE_BYTES,
                                    XAXIDMA_DEVICE_TO_DMA);
    if (status != XST_SUCCESS) {
        xil_printf("ADC capture failed: DMA arm status=%d\r\n", status);
        return capture_failed(-11);
    }

    XGpio_DiscreteWrite(&capture_gpio,
                        FDW_CAPTURE_GPIO_START_CHANNEL, FDW_CAPTURE_START | capture_trigger_mode);
    usleep(1U);
    XGpio_DiscreteWrite(&capture_gpio,
                        FDW_CAPTURE_GPIO_START_CHANNEL, capture_trigger_mode);

    XTime_GetTime(&async_start);
    async_timeout_us = timeout_us;
    async_active = 1;
    return 0;
}

/* 每轮网络主循环只轮询一次，长采集期间继续响应查询与取消。 */
int fdw_adc_capture_poll(int16_t *samples, uint32_t sample_count,
                         uint32_t *overrange_count)
{
    uint32_t index, dma_status, received_bytes, otr_count = 0U;
    uint16_t packed_word, raw_code;
    if (!async_active) return -21;
    if (!samples || sample_count != FDW_ADC_SAMPLE_COUNT) return capture_failed(-10);
    dma_status = XAxiDma_ReadReg(adc_dma.RegBase, XAXIDMA_RX_OFFSET + XAXIDMA_SR_OFFSET);
    if ((dma_status & XAXIDMA_ERR_ALL_MASK) != 0U) return capture_failed(-13);
    if (XAxiDma_Busy(&adc_dma, XAXIDMA_DEVICE_TO_DMA) != 0) {
        if (deadline_expired(async_start, async_timeout_us)) return capture_failed(-12);
        return 1;
    }
    /* 同时检查DMA错误及实际长度，防止旧1024点bit被误当成8192点完整帧。 */
    capture_trigger_status = XGpio_DiscreteRead(&capture_gpio,
                                               FDW_CAPTURE_GPIO_STATUS_CHANNEL) & 0x78U;
    if (capture_trigger_mode != 0U &&
        (capture_trigger_status & (FDW_ADC_TRIGGER_EVENT_ACCEPTED | FDW_ADC_TRIGGER_MODE_ENABLED)) !=
        (FDW_ADC_TRIGGER_EVENT_ACCEPTED | FDW_ADC_TRIGGER_MODE_ENABLED))
        return capture_failed(-18);
    dma_status = XAxiDma_ReadReg(adc_dma.RegBase,
                                 XAXIDMA_RX_OFFSET + XAXIDMA_SR_OFFSET);
    received_bytes = XAxiDma_ReadReg(adc_dma.RegBase,
                                     XAXIDMA_RX_OFFSET + XAXIDMA_BUFFLEN_OFFSET);
    if ((dma_status & XAXIDMA_ERR_ALL_MASK) != 0U ||
        received_bytes != FDW_CAPTURE_BYTES) {
        xil_printf("ADC capture invalid: status=0x%lx, bytes=%lu, expected=%lu\r\n",
                   (unsigned long)dma_status, (unsigned long)received_bytes,
                   (unsigned long)FDW_CAPTURE_BYTES);
        return capture_failed(-13);
    }
    if (wait_capture_status(FDW_CAPTURE_ACTIVE_DONE, 0x2U) != 0)
        return capture_failed(-17);
    Xil_DCacheInvalidateRange((UINTPTR)dma_rx_buffer, FDW_CAPTURE_BYTES);

    for (index = 0U; index < FDW_ADC_SAMPLE_COUNT; ++index) {
        packed_word = dma_rx_buffer[index];
        if ((packed_word & 0x3000U) != 0x2000U) {
            xil_printf("ADC capture failed: unsupported packed format\r\n");
            return capture_failed(-14);
        }
        if ((packed_word & 0x4000U) != 0U)
            capture_frame_flags |= FDW_ADC_FLAG_DISCONTINUOUS;
        raw_code = packed_word & 0x0FFFU;
        samples[index] = (int16_t)((int32_t)raw_code - 2048);
        if ((packed_word & 0x8000U) != 0U) {
            ++otr_count;
        }
    }
    capture_frame_flags |= FDW_ADC_FLAG_CONTINUITY_CHECKED;

    if (overrange_count != NULL) {
        *overrange_count = otr_count;
    }
    async_active = 0;
    return 0;
}

int fdw_adc_capture_cancel(void)
{
    if (!async_active) return 0;
    (void)capture_failed(-22);
    return capture_available ? 0 : -16;
}

/* 保留旧接口及其100ms超时，短帧兼容路径复用相同校验。 */
int fdw_adc_capture_frame(int16_t *samples, uint32_t sample_count,
                          uint32_t *overrange_count)
{
    int result;
    if (!samples || sample_count != FDW_ADC_SAMPLE_COUNT) return -10;
    result = fdw_adc_capture_start(FDW_CAPTURE_DMA_TIMEOUT_US);
    if (result != 0) return result;
    do { result = fdw_adc_capture_poll(samples, sample_count, overrange_count); } while (result == 1);
    return result;
}
