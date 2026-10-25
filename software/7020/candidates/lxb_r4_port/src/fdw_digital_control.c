/* 只读取完整数字记录，先检查身份和边界，避免向 AXI 发非法请求。 */
#include "fdw_digital_control.h"
#include "fdw_probe_control.h"
#include "xil_io.h"

static uint32_t current_record_id;
static uint32_t resource_capacity;
static uint32_t resource_width;
static uint32_t next_record_id;
static struct fdw_probe_status frame_route;

static uint32_t read_register(uint32_t offset)
{
    return Xil_In32(FDW_DIGITAL_BASE_ADDRESS + offset);
}

static void write_register(uint32_t offset, uint32_t value)
{
    Xil_Out32(FDW_DIGITAL_BASE_ADDRESS + offset, value);
}

int fdw_digital_control_init(void)
{
    if (read_register(0x20U) != 0x44494735U) return -1;
    resource_capacity = read_register(0x24U);
    resource_width = read_register(0x28U);
    /* 诊断回传每点一个字节；更宽的数据格式后续单独扩展。 */
    if (resource_capacity == 0U || resource_width == 0U || resource_width > 8U) return -1;
    current_record_id = 0U;
    next_record_id = 0U;
    frame_route.mode = 0U;
    write_register(0x00U, 0x102U);
    return 0;
}

int fdw_digital_arm(uint32_t interval_ticks, uint32_t sample_count, uint32_t wait_trigger,
                    uint32_t *record_id)
{
    struct fdw_probe_status route;
    if (interval_ticks == 0U || sample_count == 0U || sample_count > resource_capacity ||
        wait_trigger > 1U || record_id == 0) return -1;
    if ((read_register(0x10U) & 0x83U) != 0U) return -2;
    fdw_probe_read_status(&route);
    if (route.mode == 0U || route.settling) return -3;
    if (next_record_id == UINT32_MAX) return -5;
    write_register(0x04U, interval_ticks);
    write_register(0x08U, sample_count);
    write_register(0x0cU, wait_trigger);
    write_register(0x00U, 0x101U);
    /* 帧序号只在本次启动中有意义，不能代替跨重启的会话标识。 */
    current_record_id = ++next_record_id;
    frame_route = route;
    *record_id = current_record_id;
    return 0;
}

void fdw_digital_invalidate(void)
{
    /* 切换使旧帧不可读，但不能复用本次启动中已经分配的号码。 */
    current_record_id = 0U;
    frame_route.mode = 0U;
    frame_route.trigger_source = 0U;
    frame_route.finite = 0U;
    frame_route.generation = 0U;
}

void fdw_digital_cancel(void)
{
    write_register(0x00U, 2U);
}

int fdw_digital_trigger(void)
{
    if ((read_register(0x10U) & 1U) == 0U) return -3;
    write_register(0x00U, 4U);
    return 0;
}

void fdw_digital_read_status(struct fdw_digital_status *status)
{
    status->record_id = current_record_id;
    status->flags = read_register(0x10U);
    status->interval_ticks = read_register(0x18U);
    status->sample_count = read_register(0x1cU);
    status->captured_count = read_register(0x14U);
    status->capacity = resource_capacity;
    status->sample_width = resource_width;
    status->mode = frame_route.mode;
    status->trigger_source = frame_route.trigger_source;
    status->finite = frame_route.finite;
    status->route_generation = frame_route.generation;
}

int fdw_digital_read_samples(uint32_t record_id, uint32_t start, uint32_t count,
                             uint8_t *samples)
{
    uint32_t available, index;
    if (record_id == 0U || record_id != current_record_id) return -4;
    if ((read_register(0x10U) & 0x0fU) != 0x0cU) return -3;
    available = read_register(0x14U);
    if (samples == 0 || count == 0U || count > FDW_DIGITAL_CHUNK_POINTS ||
        start >= available || count > available - start) return -1;
    /* 裸机 TCP 回调逐个执行；本函数不让出执行，索引写和数据读不会交错。 */
    for (index = 0U; index < count; ++index) {
        write_register(0x2cU, start + index);
        samples[index] = (uint8_t)read_register(0x30U);
    }
    return 0;
}
