/* 新 PWM 控制寄存器访问，不访问旧 DDS 或 ADC 地址。 */
#include "fdw_pwm_control.h"
#include "xil_io.h"

#define CONTROL_OFFSET 0x00U
#define PERIOD_OFFSET 0x04U
#define HIGH_OFFSET 0x08U
#define STATUS_OFFSET 0x0CU
#define ACTIVE_PERIOD_OFFSET 0x10U
#define ACTIVE_HIGH_OFFSET 0x14U
#define CYCLE_COUNT_OFFSET 0x18U
#define IDENTIFIER_OFFSET 0x1CU

static uint32_t read_register(uint32_t offset)
{
    return Xil_In32(FDW_PWM_BASE_ADDRESS + offset);
}

static void write_register(uint32_t offset, uint32_t value)
{
    Xil_Out32(FDW_PWM_BASE_ADDRESS + offset, value);
}

int fdw_pwm_control_init(void)
{
    if (read_register(IDENTIFIER_OFFSET) != 0x50574D31U) {
        return -1;
    }
    /* 上电保持输出关闭，并清除旧事件；不主动加载测试参数。 */
    write_register(CONTROL_OFFSET, 0x100U);
    return 0;
}

int fdw_pwm_configure(uint32_t period_ticks, uint32_t high_ticks)
{
    uint32_t enabled;
    if (period_ticks == 0U || high_ticks > period_ticks) {
        return -1;
    }
    if ((read_register(STATUS_OFFSET) & 2U) != 0U) {
        return -2;
    }
    enabled = read_register(CONTROL_OFFSET) & 1U;
    write_register(PERIOD_OFFSET, period_ticks);
    write_register(HIGH_OFFSET, high_ticks);
    /* 提交返回的是已接收，运行中的真正生效时间仍由周期边界决定。 */
    write_register(CONTROL_OFFSET, enabled | 0x102U);
    return 0;
}

int fdw_pwm_enable(uint32_t enabled)
{
    if (enabled > 1U) return -1;
    if (enabled != 0U && read_register(ACTIVE_PERIOD_OFFSET) == 0U) return -2;
    write_register(CONTROL_OFFSET, enabled);
    return 0;
}

int fdw_pwm_read_status(struct fdw_pwm_status *status)
{
    uint32_t before, after, high, attempt;
    if (status == 0) return -1;
    status->enabled = read_register(CONTROL_OFFSET) & 1U;
    status->flags = read_register(STATUS_OFFSET);
    status->cycle_count = read_register(CYCLE_COUNT_OFFSET);
    /* 同一 CPU 回调不并发写配置；检测读取期间的单次周期边界更新。
       只改变高电平且周期相同时，读到任一完整有效高电平都能组成合法参数对。
       状态位和计数仍是诊断读数，不把它们称作同拍硬件快照。 */
    for (attempt = 0U; attempt < 4U; ++attempt) {
        before = read_register(ACTIVE_PERIOD_OFFSET);
        high = read_register(ACTIVE_HIGH_OFFSET);
        after = read_register(ACTIVE_PERIOD_OFFSET);
        if (before == after && high <= after) {
            status->period_ticks = after;
            status->high_ticks = high;
            return 0;
        }
    }
    /* 异常持续变化或非法读数明确报错，不输出看似合法的混合参数。 */
    status->period_ticks = 0U;
    status->high_ticks = 0U;
    return -2;
}
