/* 先让管脚高阻，再停止各模块，最后提交新模式；不自动启用外部激励。 */
#include "fdw_probe_control.h"
#include "fdw_pwm_control.h"
#include "fdw_digital_control.h"
#include "fdw_lxb_io.h"
#include "xil_io.h"

static uint32_t selected_finite;

static uint32_t read_route(void)
{
    return Xil_In32(FDW_PROBE_BASE_ADDRESS + 8U);
}

static int request_route(uint32_t mode, uint32_t trigger, uint32_t finite, uint32_t permit)
{
    uint32_t attempt, status;
    uint32_t toggle = (read_route() ^ 0x80U) & 0x80U;
    Xil_Out32(FDW_PROBE_BASE_ADDRESS, mode | (permit << 3) | (trigger << 4) |
              toggle | (finite << 8));
    /* 有界 MMIO 轮询，硬件不应答时返回错误，不永久阻塞网络回调。 */
    for (attempt = 0U; attempt < 100000U; ++attempt) {
        status = read_route();
        if ((status & 0x80U) == toggle) {
            if ((status & 0xf00U) != 0U) return -(int)((status >> 8) & 15U) - 10;
            if ((status & 0x1000U) == 0U) {
                selected_finite = finite;
                fdw_digital_invalidate();
                return 0;
            }
        }
    }
    return -20;
}

void fdw_probe_read_status(struct fdw_probe_status *status)
{
    uint32_t word = read_route();
    status->mode = word & 7U;
    status->output_enabled = (word >> 3) & 1U;
    status->trigger_source = (word >> 4) & 7U;
    status->finite = selected_finite;
    status->settling = (word >> 12) & 1U;
    status->inputs = (word >> 13) & 3U;
    status->pwm_active = (word >> 15) & 1U;
    status->capture_active = (word >> 16) & 1U;
    /* 代际计数会回绕，只用于诊断；记录身份使用软件帧号。 */
    status->generation = word >> 17;
    status->result = (word >> 8) & 15U;
}

int fdw_probe_halt(void)
{
    uint32_t attempt;
    fdw_lxb_io_halt();
    int result = request_route(0U, 0U, 0U, 0U);
    /* 即使路由应答异常也停止发生器和记录器，保留错误结果。 */
    (void)fdw_pwm_enable(0U);
    Xil_Out32(FDW_BURST_BASE_ADDRESS + 4U, 2U);
    fdw_digital_cancel();
    fdw_digital_invalidate();
    if (result != 0) return result;
    for (attempt = 0U; attempt < 100000U; ++attempt) {
        if ((read_route() & 0x19000U) == 0U &&
            (Xil_In32(FDW_BURST_BASE_ADDRESS + 8U) & 3U) == 0U) return 0;
    }
    return -20;
}

int fdw_probe_control_init(void)
{
    /* 调用前必须先通过配套数字标识检查，避免用旧硬件访问新增地址。 */
    if (Xil_In32(FDW_BURST_BASE_ADDRESS) != 0x42555231U) return -1;
    selected_finite = 0U;
    return fdw_probe_halt();
}

int fdw_probe_set_mode(uint32_t mode, uint32_t trigger_source, uint32_t finite)
{
    int result;
    if (mode > 3U || trigger_source > 5U || finite > 1U ||
        (mode == 0U && (trigger_source != 0U || finite != 0U)) ||
        (mode == 2U && (trigger_source == 1U || finite != 0U))) return -1;
    result = fdw_probe_halt();
    if (result != 0 || mode == 0U) return result;
    return request_route(mode, trigger_source, finite, 0U);
}

int fdw_probe_output_enable(uint32_t enabled)
{
    struct fdw_probe_status status;
    if (enabled > 1U) return -1;
    /* 关闭输出同时进入 STOP，忙时也能立即让管脚高阻。 */
    if (enabled == 0U) return fdw_probe_halt();
    fdw_probe_read_status(&status);
    if (status.mode != 3U || status.settling || status.capture_active || status.pwm_active)
        return -2;
    fdw_lxb_select_default_output();
    if (status.output_enabled) return 0;
    return request_route(status.mode, status.trigger_source, status.finite, 1U);
}

int fdw_probe_pwm_enable(uint32_t enabled)
{
    struct fdw_probe_status status;
    if (enabled > 1U) return -1;
    if (enabled == 0U) return fdw_pwm_enable(0U);
    fdw_probe_read_status(&status);
    if ((status.mode != 1U && status.mode != 3U) || status.finite || status.settling ||
        (status.mode == 3U && !status.output_enabled)) return -2;
    return fdw_pwm_enable(1U);
}

int fdw_probe_burst(uint32_t period, uint32_t high, uint32_t cycles)
{
    struct fdw_probe_status status;
    if (period == 0U || high > period || cycles == 0U) return -1;
    fdw_probe_read_status(&status);
    if ((status.mode != 1U && status.mode != 3U) || !status.finite || status.settling ||
        status.pwm_active || (status.mode == 3U && !status.output_enabled)) return -2;
    Xil_Out32(FDW_BURST_BASE_ADDRESS + 0x10U, period);
    Xil_Out32(FDW_BURST_BASE_ADDRESS + 0x14U, high);
    Xil_Out32(FDW_BURST_BASE_ADDRESS + 0x18U, cycles);
    Xil_Out32(FDW_BURST_BASE_ADDRESS + 0x0cU, 0x30fU);
    Xil_Out32(FDW_BURST_BASE_ADDRESS + 4U, 1U);
    return 0;
}

void fdw_probe_read_burst(struct fdw_burst_status *status)
{
    /* 此接口是运行诊断读取，并不声称五个字段来自同拍快照。 */
    status->flags = Xil_In32(FDW_BURST_BASE_ADDRESS + 8U);
    status->period_ticks = Xil_In32(FDW_BURST_BASE_ADDRESS + 0x20U);
    status->high_ticks = Xil_In32(FDW_BURST_BASE_ADDRESS + 0x24U);
    status->cycles = Xil_In32(FDW_BURST_BASE_ADDRESS + 0x28U);
    status->completed = Xil_In32(FDW_BURST_BASE_ADDRESS + 0x2cU);
}

const char *fdw_probe_source(uint32_t mode, uint32_t finite)
{
    if (mode == 1U) return finite ? "internal_burst" : "internal_pwm";
    if (mode == 2U || mode == 3U) return "external_digital";
    return "none";
}
