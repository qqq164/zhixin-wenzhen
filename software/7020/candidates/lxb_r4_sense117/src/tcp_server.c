/* 两板 TCP 诊断与 PWM 控制；保留旧网段、回显端口和采集服务端口。 */
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "fdw_pwm_control.h"
#include "fdw_digital_control.h"
#include "fdw_probe_control.h"
#include "fdw_adc_control.h"
#include "fdw_dac_control.h"
#include "fdw_phase_control.h"
#include "fdw_bulk_frame.h"
#include "fdw_lxb_io.h"
#include "lwip/err.h"
#include "lwip/mem.h"
#include "lwip/pbuf.h"
#include "lwip/tcp.h"
#include "xil_printf.h"
#include "xiltimer.h"

#define ECHO_PORT 7U
#define CONTROL_PORT 5001U
#define COMMAND_CAPACITY 96U
#define RESPONSE_CAPACITY 1024U
#define MAX_CONNECTIONS 4U

/* 一条控制连接处理一个命令，避免未定义的流水请求改变激励顺序。 */
struct command_connection {
    struct tcp_pcb *pcb;
    XTime created;
    char command[COMMAND_CAPACITY];
    char response[RESPONSE_CAPACITY];
    u16_t command_length;
    uint32_t response_length;
    uint32_t queued;
    uint32_t acknowledged;
    uint8_t binary_header[FDW_BULK_HEADER_BYTES];
    const int16_t *frame;
    unsigned char binary;
    unsigned char complete;
};

static unsigned int active_connections;
static err_t control_poll(void *arg, struct tcp_pcb *pcb);
static err_t control_recv(void *arg, struct tcp_pcb *pcb, struct pbuf *p, err_t err);
static err_t control_sent(void *arg, struct tcp_pcb *pcb, u16_t length);
static void control_error(void *arg, err_t err);

static void release_frame(struct command_connection *state)
{
    if (state->binary) { fdw_bulk_frame_close();state->binary=0U;state->frame=NULL; }
}

static err_t abort_connection(struct command_connection *state)
{
    struct tcp_pcb *pcb = state->pcb;
    tcp_arg(pcb, NULL);
    tcp_err(pcb, NULL);
    release_frame(state);
    mem_free(state);
    --active_connections;
    tcp_abort(pcb);
    return ERR_ABRT;
}

static err_t close_connection(struct command_connection *state)
{
    struct tcp_pcb *pcb = state->pcb;
    err_t result;
    tcp_arg(pcb, NULL);
    tcp_recv(pcb, NULL);
    tcp_sent(pcb, NULL);
    tcp_poll(pcb, NULL, 0);
    tcp_err(pcb, NULL);
    result = tcp_close(pcb);
    if (result == ERR_OK) {
        release_frame(state);
        mem_free(state);
        --active_connections;
    } else {
        /* 内存不足时恢复回调，由定时回调重试关闭。 */
        tcp_arg(pcb, state);
        tcp_recv(pcb, control_recv);
        tcp_sent(pcb, control_sent);
        tcp_poll(pcb, control_poll, 2);
        tcp_err(pcb, control_error);
    }
    return ERR_OK;
}

static int parse_number(const char **text, uint32_t *value)
{
    const char *start = *text;
    char *end;
    unsigned long long number;
    while (*start == ' ') ++start;
    if (*start < '0' || *start > '9') return -1;
    errno = 0;
    number = strtoull(start, &end, 10);
    if (errno == ERANGE || number > UINT32_MAX) return -1;
    *value = (uint32_t)number;
    *text = end;
    return 0;
}

static int at_end(const char *text)
{
    while (*text == ' ') ++text;
    return *text == '\0';
}

/* 长采集期间只接受读命令及显式停波/取消，禁止半帧改变激励和路由。 */
static int allowed_while_busy(const char *command)
{
    static const char *const reads[]={"PING","INFO","LXB_STATUS","RELAY_STATUS","SENSE_STATUS","ADC_SENSE_STATUS","ADC_JOB_STATUS","ADC_PHASE_STATUS",
        "ADC_STATUS","DAC_STATUS","MODE_STATUS","PWM_STATUS","PWM_BURST_STATUS","DIG_STATUS",
        "HALT","DAC_ENABLE 0"};
    unsigned i;
    uint32_t enabled;
    const char *parameters=command+((strlen(command)>=11U)?11U:0U);
    if (strncmp(command,"DAC_ENABLE ",11)==0 && parse_number(&parameters,&enabled)==0 &&
        enabled==0U && at_end(parameters)) return 1;
    for (i=0U;i<sizeof(reads)/sizeof(reads[0]);++i) if (strcmp(command,reads[i])==0) return 1;
    return strncmp(command,"ADC_JOB_CANCEL ",15)==0 || strncmp(command,"ADC_READ ",9)==0 ||
           strncmp(command,"ADC_FRAME ",10)==0 || strncmp(command,"DIG_READ ",9)==0;
}
static void cancel_running_job(void)
{
    struct fdw_phase_job job;
    fdw_phase_job_status(&job);
    if (job.state==1U) (void)fdw_phase_job_cancel(job.job_id);
}

static void prepare_response(struct command_connection *state)
{
    uint32_t period, high, enabled, interval, count, mode, record_id, start;
    struct fdw_pwm_status status;
    struct fdw_digital_status digital;
    struct fdw_probe_status route;
    struct fdw_burst_status burst;
    struct fdw_adc_status adc;
    struct fdw_dac_status dac;
    const char *parameters;
    int result = -1;
    if (fdw_adc_control_busy() && !allowed_while_busy(state->command)) {
        snprintf(state->response,sizeof(state->response),"{\"ok\":false,\"code\":-20,\"error\":\"ADC_BUSY\"}\n");
    } else if (strncmp(state->command,"ADC_FRAME ",10)==0) {
        parameters=state->command+10;
        if (parse_number(&parameters,&record_id)==0 && at_end(parameters))
            result=fdw_bulk_frame_open(record_id,state->binary_header,&state->frame);
        if (result==0) {
            state->binary=1U;
            state->response_length=FDW_BULK_HEADER_BYTES+2U*FDW_ADC_SAMPLE_COUNT;
            return;
        }
        snprintf(state->response,sizeof(state->response),"{\"ok\":false,\"code\":%d}\n",result);
    } else if (strncmp(state->command,"ADC_JOB_START ",14)==0) {
        parameters=state->command+14;record_id=0U;
        if (parse_number(&parameters,&count)==0 && *parameters==' ' &&
            parse_number(&parameters,&interval)==0 && at_end(parameters))
            result=fdw_phase_job_start(count,interval,&record_id);
        snprintf(state->response,sizeof(state->response),"{\"ok\":%s,\"code\":%d,\"job_id\":%lu}\n",result==0?"true":"false",result,(unsigned long)record_id);
    } else if (strcmp(state->command,"ADC_JOB_STATUS")==0) {
        struct fdw_phase_job job;
        fdw_phase_job_status(&job);
        snprintf(state->response,sizeof(state->response),
            "{\"ok\":true,\"job_id\":%lu,\"state\":%lu,\"record_id\":%lu,\"code\":%d,\"timeout_us\":%lu,\"decimation_log2\":%lu}\n",
            (unsigned long)job.job_id,(unsigned long)job.state,(unsigned long)job.record_id,job.code,
            (unsigned long)job.timeout_us,(unsigned long)job.decimation_log2);
    } else if (strncmp(state->command,"ADC_JOB_CANCEL ",15)==0) {
        parameters=state->command+15;
        if (parse_number(&parameters,&record_id)==0 && at_end(parameters)) result=fdw_phase_job_cancel(record_id);
        snprintf(state->response,sizeof(state->response),"{\"ok\":%s,\"code\":%d}\n",result==0?"true":"false",result);
    } else if (strcmp(state->command, "PING") == 0) {
        snprintf(state->response, sizeof(state->response), "{\"ok\":true,\"pong\":true}\n");
    } else if (strcmp(state->command, "SENSE_STATUS") == 0) {
        struct fdw_lxb_sense_status sense;
        fdw_lxb_sense_status(&sense);
        snprintf(state->response, sizeof(state->response),
            "{\"ok\":true,\"sense_protocol\":1,\"generation\":%lu,\"relay_mask\":%lu,\"selected\":%lu,\"rs_nominal_ohm\":%lu,\"settling\":%lu,\"remaining_ms\":%lu,\"fault\":%lu,\"relay_feedback\":false,\"analog_settling_qualified\":false}\n",
            (unsigned long)sense.generation, (unsigned long)sense.relay_mask, (unsigned long)sense.selected,
            (unsigned long)sense.rs_nominal_ohm, (unsigned long)sense.settling, (unsigned long)sense.remaining_ms,
            (unsigned long)sense.fault);
    } else if (strcmp(state->command, "ADC_SENSE_STATUS") == 0) {
        fdw_adc_control_status(&adc);
        snprintf(state->response, sizeof(state->response),
            "{\"ok\":%s,\"record_id\":%lu,\"sense_generation\":%lu,\"relay_mask\":%lu}\n",
            adc.record_id ? "true" : "false", (unsigned long)adc.record_id,
            (unsigned long)adc.sense_generation, (unsigned long)adc.relay_mask);
    } else if (strncmp(state->command, "SENSE_SELECT ", 13) == 0) {
        uint32_t selected = 0U;
        parameters = state->command + 13;
        if (strncmp(parameters, "VA ", 3) == 0) { selected = FDW_SENSE_VA; parameters += 3; }
        else if (strncmp(parameters, "VB ", 3) == 0) { selected = FDW_SENSE_VB; parameters += 3; }
        else if (strncmp(parameters, "PROBE ", 6) == 0) { selected = FDW_SENSE_PROBE; parameters += 6; }
        if (selected && parse_number(&parameters, &interval) == 0 && at_end(parameters))
            result = fdw_lxb_sense_select(selected, interval);
        snprintf(state->response, sizeof(state->response), "{\"ok\":%s,\"code\":%d}\n",
            result == 0 ? "true" : "false", result);
    } else if (strcmp(state->command, "LXB_STATUS") == 0 || strcmp(state->command, "RELAY_STATUS") == 0) {
        snprintf(state->response, sizeof(state->response),
                 "{\"ok\":true,\"board_revision\":\"LXB-R4-SENSE117\",\"relay_mask\":%lu,\"io_output_mask\":%lu,\"pwm_channels\":2,\"probe_a\":\"Y18\",\"probe_b\":\"AA18\",\"relay_feedback\":false}\n",
                 (unsigned long)fdw_lxb_relay_mask(), (unsigned long)fdw_lxb_output_mask());
    } else if (strncmp(state->command, "RELAY_SET ", 10) == 0) {
        parameters = state->command + 10;
        if (parse_number(&parameters, &enabled) == 0 && at_end(parameters)) result = fdw_lxb_relay_set(enabled);
        if (result == 0) { fdw_adc_control_invalidate(); fdw_digital_invalidate(); }
        snprintf(state->response, sizeof(state->response), "{\"ok\":%s,\"code\":%d,\"relay_mask\":%lu}\n",
                 result == 0 ? "true" : "false", result, (unsigned long)fdw_lxb_relay_mask());
    } else if (strncmp(state->command, "IO_PWM_SET ", 11) == 0) {
        uint32_t channel;
        parameters = state->command + 11;
        if (parse_number(&parameters, &channel) == 0 && *parameters == ' ' &&
            parse_number(&parameters, &period) == 0 && *parameters == ' ' &&
            parse_number(&parameters, &high) == 0 && at_end(parameters)) result = fdw_lxb_pwm_set(channel, period, high);
        snprintf(state->response, sizeof(state->response), "{\"ok\":%s,\"code\":%d}\n", result == 0 ? "true" : "false", result);
    } else if (strncmp(state->command, "IO_PWM_ENABLE ", 14) == 0) {
        uint32_t channel;
        parameters = state->command + 14;
        if (parse_number(&parameters, &channel) == 0 && *parameters == ' ' &&
            parse_number(&parameters, &enabled) == 0 && at_end(parameters)) result = fdw_lxb_pwm_enable(channel, enabled);
        if (result == 0) { fdw_adc_control_invalidate(); fdw_digital_invalidate(); }
        snprintf(state->response, sizeof(state->response), "{\"ok\":%s,\"code\":%d}\n", result == 0 ? "true" : "false", result);
    } else if (strncmp(state->command, "IO_PWM_STATUS ", 14) == 0) {
        uint32_t channel;
        struct fdw_lxb_pwm_status io_pwm;
        parameters = state->command + 14;
        if (parse_number(&parameters, &channel) == 0 && at_end(parameters)) result = fdw_lxb_pwm_status(channel, &io_pwm);
        if (result == 0) snprintf(state->response, sizeof(state->response),
            "{\"ok\":true,\"channel\":%lu,\"enabled\":%lu,\"output_enabled\":%lu,\"period_ticks\":%lu,\"high_ticks\":%lu,\"flags\":%lu,\"clock_hz\":100000000}\n",
            (unsigned long)channel, (unsigned long)io_pwm.enabled, (unsigned long)io_pwm.output_enabled,
            (unsigned long)io_pwm.period_ticks, (unsigned long)io_pwm.high_ticks, (unsigned long)io_pwm.flags);
        else snprintf(state->response, sizeof(state->response), "{\"ok\":false,\"code\":%d}\n", result);
    } else if (strcmp(state->command, "INFO") == 0) {
        fdw_probe_read_status(&route);
        fdw_adc_control_status(&adc);
        fdw_dac_read_status(&dac);
        snprintf(state->response, sizeof(state->response),
                 "{\"ok\":true,\"project\":\"FDW_PWM\",\"protocol\":2,\"hardware_revision\":\"DIG5\",\"system\":\"ADC_DAC902_DDS_2IO\",\"implementation_revision\":\"DIG5-R4-LOWRATE-BULK\",\"phase_metadata_version\":2,\"bulk_frame_version\":1,\"lowrate_max_log2\":16,\"nonblocking_capture\":true,\"sense_protocol\":1,\"firmware_revision\":\"R4-P1-IRQFP-SENSE117\",\"clock_hz\":%lu,\"adc_ready\":%s,\"adc_pwm_sync\":true,\"dds_ready\":%s,\"dac_model\":\"DAC902\",\"dac_output_enabled\":%s,\"external_pwm_pin\":true,\"pwm_channels\":2,\"digital_bidirectional\":true,\"board_revision\":\"LXB-R4-SENSE117\",\"external_output_enabled\":%s,\"mode\":%lu,\"digital_source\":\"%s\"}\n",
                 (unsigned long)FDW_PWM_CLOCK_HZ, adc.ready ? "true" : "false", dac.ready ? "true" : "false",
                 dac.enabled ? "true" : "false", route.output_enabled ? "true" : "false",
                 (unsigned long)route.mode, fdw_probe_source(route.mode, route.finite));
    } else if (strncmp(state->command,"ADC_CAPTURE_PHASE ",18)==0) {
        parameters=state->command+18;record_id=0U;
        if (parse_number(&parameters,&count)==0 && at_end(parameters)) result=fdw_phase_capture(count,&record_id);
        snprintf(state->response,sizeof(state->response),"{\"ok\":%s,\"code\":%d,\"record_id\":%lu}\n",result==0?"true":"false",result,(unsigned long)record_id);
    } else if (strcmp(state->command,"ADC_PHASE_STATUS")==0) {
        struct fdw_phase_status phase;
        result=fdw_phase_status(&phase);
        if (result==0) snprintf(state->response,sizeof(state->response),
            "{\"ok\":true,\"metadata_version\":2,\"generation\":%lu,\"record_id\":%lu,\"sample_count\":%lu,\"complete\":true,\"errors\":%lu,\"dds_ftw\":%lu,\"dac_tag\":%lu,\"inverted\":%s,\"interval_ticks\":%lu,\"decimation_log2\":%lu,\"filter_id\":%lu,\"pipeline_tick_compensation\":%lu,\"first_tick\":%llu,\"last_tick\":%llu,\"first_phase_word\":%lu,\"last_phase_word\":%lu,\"raw_clock_hz\":25000000,\"analog_calibrated\":false}\n",
            (unsigned long)phase.generation,(unsigned long)phase.record_id,(unsigned long)phase.sample_count,(unsigned long)phase.errors,
            (unsigned long)phase.frequency_word,(unsigned long)phase.tag,phase.inverted?"true":"false",
            (unsigned long)phase.interval_ticks,(unsigned long)phase.decimation_log2,(unsigned long)phase.filter_id,(unsigned long)(phase.decimation_log2?2U:0U),
            (unsigned long long)phase.first_tick,(unsigned long long)phase.last_tick,(unsigned long)phase.first_phase,(unsigned long)phase.last_phase);
        else snprintf(state->response,sizeof(state->response),"{\"ok\":false,\"code\":%d}\n",result);
    } else if (strcmp(state->command, "DAC_STATUS") == 0) {
        fdw_dac_read_status(&dac);
        snprintf(state->response, sizeof(state->response),
                 "{\"ok\":%s,\"ready\":%lu,\"enabled\":%lu,\"waveform_active\":%lu,\"configured\":%lu,\"busy\":%lu,\"clipped\":%lu,\"error_code\":%lu,\"tag\":%lu,\"frequency_word\":%lu,\"phase_word\":%lu,\"gain_q11\":%lu,\"offset_code\":%lu,\"update_clock_hz\":25000000,\"calibrated\":false}\n",
                 dac.ready ? "true" : "false", (unsigned long)dac.ready, (unsigned long)dac.enabled,
                 (unsigned long)dac.waveform_active, (unsigned long)dac.configured, (unsigned long)dac.busy,
                 (unsigned long)dac.clipped, (unsigned long)dac.error_code, (unsigned long)dac.tag,
                 (unsigned long)dac.frequency_word, (unsigned long)dac.phase_word,
                 (unsigned long)dac.gain_q11, (unsigned long)dac.offset_code);
    } else if (strncmp(state->command, "DAC_SET ", 8) == 0) {
        uint32_t frequency, gain, offset, phase, invert;
        parameters = state->command + 8;
        if (parse_number(&parameters, &frequency) == 0 && *parameters == ' ' &&
            parse_number(&parameters, &gain) == 0 && *parameters == ' ' &&
            parse_number(&parameters, &offset) == 0 && *parameters == ' ' &&
            parse_number(&parameters, &phase) == 0 && *parameters == ' ' &&
            parse_number(&parameters, &invert) == 0 && at_end(parameters))
            result = fdw_dac_configure(frequency, gain, offset, phase, invert);
        if (result == 0) fdw_adc_control_invalidate();
        snprintf(state->response, sizeof(state->response), "{\"ok\":%s,\"code\":%d}\n",
                 result == 0 ? "true" : "false", result);
    } else if (strncmp(state->command, "DAC_ENABLE ", 11) == 0) {
        parameters = state->command + 11;
        if (parse_number(&parameters, &enabled) == 0 && at_end(parameters))
            { if (enabled==0U) cancel_running_job();result = fdw_dac_enable(enabled); }
        if (result == 0) fdw_adc_control_invalidate();
        snprintf(state->response, sizeof(state->response), "{\"ok\":%s,\"code\":%d}\n",
                 result == 0 ? "true" : "false", result);
    } else if (strcmp(state->command, "MODE_STATUS") == 0) {
        fdw_probe_read_status(&route);
        snprintf(state->response, sizeof(state->response),
                 "{\"ok\":true,\"mode\":%lu,\"output_enabled\":%lu,\"trigger_source\":%lu,\"finite\":%lu,\"settling\":%lu,\"inputs\":%lu,\"pwm_active\":%lu,\"capture_active\":%lu,\"generation\":%lu,\"result\":%lu}\n",
                 (unsigned long)route.mode, (unsigned long)route.output_enabled,
                 (unsigned long)route.trigger_source, (unsigned long)route.finite,
                 (unsigned long)route.settling, (unsigned long)route.inputs,
                 (unsigned long)route.pwm_active, (unsigned long)route.capture_active,
                 (unsigned long)route.generation, (unsigned long)route.result);
    } else if (strncmp(state->command, "MODE_SET ", 9) == 0) {
        parameters = state->command + 9;
        if (parse_number(&parameters, &mode) == 0 && *parameters == ' ' &&
            parse_number(&parameters, &start) == 0 && *parameters == ' ' &&
            parse_number(&parameters, &enabled) == 0 && at_end(parameters))
            { result = fdw_dac_halt(); if (result == 0) result = fdw_probe_set_mode(mode, start, enabled); }
        if (result == 0) fdw_adc_control_invalidate();
        snprintf(state->response, sizeof(state->response), "{\"ok\":%s,\"code\":%d}\n",
                 result == 0 ? "true" : "false", result);
    } else if (strcmp(state->command, "HALT") == 0 ||
               strncmp(state->command, "OUTPUT_ENABLE ", 14) == 0) {
        if (strcmp(state->command, "HALT") == 0) {
            int dac_result;
            cancel_running_job();
            dac_result = fdw_dac_halt();
            result = fdw_probe_halt();
            if (dac_result != 0) result = dac_result;
        }
        else {
            parameters = state->command + 14;
            if (parse_number(&parameters, &enabled) == 0 && at_end(parameters))
                result = fdw_probe_output_enable(enabled);
        }
        if (result == 0) fdw_adc_control_invalidate();
        snprintf(state->response, sizeof(state->response), "{\"ok\":%s,\"code\":%d}\n",
                 result == 0 ? "true" : "false", result);
    } else if (strncmp(state->command, "PWM_BURST ", 10) == 0) {
        parameters = state->command + 10;
        if (parse_number(&parameters, &period) == 0 && *parameters == ' ' &&
            parse_number(&parameters, &high) == 0 && *parameters == ' ' &&
            parse_number(&parameters, &count) == 0 && at_end(parameters))
            result = fdw_probe_burst(period, high, count);
        snprintf(state->response, sizeof(state->response), "{\"ok\":%s,\"accepted\":%s,\"code\":%d}\n",
                 result == 0 ? "true" : "false", result == 0 ? "true" : "false", result);
    } else if (strcmp(state->command, "PWM_BURST_STATUS") == 0) {
        fdw_probe_read_burst(&burst);
        snprintf(state->response, sizeof(state->response),
                 "{\"ok\":true,\"flags\":%lu,\"period_ticks\":%lu,\"high_ticks\":%lu,\"cycles\":%lu,\"completed\":%lu}\n",
                 (unsigned long)burst.flags, (unsigned long)burst.period_ticks,
                 (unsigned long)burst.high_ticks, (unsigned long)burst.cycles,
                 (unsigned long)burst.completed);
    } else if (strcmp(state->command, "PWM_STATUS") == 0) {
        result = fdw_pwm_read_status(&status);
        if (result != 0) {
            snprintf(state->response, sizeof(state->response),
                     "{\"ok\":false,\"error\":\"PWM_STATUS_UNSTABLE\",\"code\":%d}\n", result);
        } else snprintf(state->response, sizeof(state->response),
                 "{\"ok\":true,\"enabled\":%lu,\"flags\":%lu,\"period_ticks\":%lu,\"high_ticks\":%lu,\"cycle_count\":%lu}\n",
                 (unsigned long)status.enabled, (unsigned long)status.flags,
                 (unsigned long)status.period_ticks, (unsigned long)status.high_ticks,
                 (unsigned long)status.cycle_count);
    } else if (strncmp(state->command, "PWM_SET ", 8) == 0) {
        parameters = state->command + 8;
        if (parse_number(&parameters, &period) == 0 && *parameters == ' ' &&
            parse_number(&parameters, &high) == 0 && at_end(parameters)) {
            result = fdw_pwm_configure(period, high);
        }
        snprintf(state->response, sizeof(state->response),
                 "{\"ok\":%s,\"accepted\":%s,\"code\":%d}\n",
                 result == 0 ? "true" : "false", result == 0 ? "true" : "false", result);
    } else if (strncmp(state->command, "PWM_ENABLE ", 11) == 0) {
        parameters = state->command + 11;
        if (parse_number(&parameters, &enabled) == 0 && at_end(parameters))
            result = fdw_probe_pwm_enable(enabled);
        snprintf(state->response, sizeof(state->response), "{\"ok\":%s,\"code\":%d}\n",
                 result == 0 ? "true" : "false", result);
    } else if (strcmp(state->command, "DIG_STATUS") == 0) {
        fdw_digital_read_status(&digital);
        snprintf(state->response, sizeof(state->response),
                 "{\"ok\":true,\"record_id\":%lu,\"flags\":%lu,\"interval_ticks\":%lu,\"sample_count\":%lu,\"captured_count\":%lu,\"capacity\":%lu,\"sample_width\":%lu,\"source\":\"%s\",\"mode\":%lu,\"trigger_source\":%lu,\"finite\":%lu,\"route_generation\":%lu}\n",
                 (unsigned long)digital.record_id, (unsigned long)digital.flags,
                 (unsigned long)digital.interval_ticks, (unsigned long)digital.sample_count,
                 (unsigned long)digital.captured_count, (unsigned long)digital.capacity,
                 (unsigned long)digital.sample_width,
                 fdw_probe_source(digital.mode, digital.finite), (unsigned long)digital.mode,
                 (unsigned long)digital.trigger_source, (unsigned long)digital.finite,
                 (unsigned long)digital.route_generation);
    } else if (strncmp(state->command, "DIG_ARM ", 8) == 0) {
        parameters = state->command + 8;
        record_id = 0U;
        if (parse_number(&parameters, &interval) == 0 && *parameters == ' ' &&
            parse_number(&parameters, &count) == 0 && *parameters == ' ' &&
            parse_number(&parameters, &mode) == 0 && at_end(parameters))
            result = fdw_digital_arm(interval, count, mode, &record_id);
        snprintf(state->response, sizeof(state->response),
                 "{\"ok\":%s,\"code\":%d,\"record_id\":%lu}\n",
                 result == 0 ? "true" : "false", result, (unsigned long)record_id);
    } else if (strcmp(state->command, "DIG_CANCEL") == 0) {
        fdw_digital_cancel();
        snprintf(state->response, sizeof(state->response), "{\"ok\":true}\n");
    } else if (strcmp(state->command, "DIG_TRIGGER") == 0) {
        result = fdw_digital_trigger();
        snprintf(state->response, sizeof(state->response), "{\"ok\":%s,\"code\":%d}\n",
                 result == 0 ? "true" : "false", result);
    } else if (strncmp(state->command, "DIG_READ ", 9) == 0) {
        uint8_t samples[FDW_DIGITAL_CHUNK_POINTS];
        char hexadecimal[FDW_DIGITAL_CHUNK_POINTS * 2U + 1U];
        static const char digits[] = "0123456789abcdef";
        uint32_t index;
        parameters = state->command + 9;
        if (parse_number(&parameters, &record_id) == 0 && *parameters == ' ' &&
            parse_number(&parameters, &start) == 0 && *parameters == ' ' &&
            parse_number(&parameters, &count) == 0 && at_end(parameters))
            result = fdw_digital_read_samples(record_id, start, count, samples);
        if (result == 0) {
            for (index = 0U; index < count; ++index) {
                hexadecimal[index * 2U] = digits[samples[index] >> 4];
                hexadecimal[index * 2U + 1U] = digits[samples[index] & 15U];
            }
            hexadecimal[count * 2U] = '\0';
            snprintf(state->response, sizeof(state->response),
                     "{\"ok\":true,\"record_id\":%lu,\"start\":%lu,\"count\":%lu,\"hex\":\"%s\"}\n",
                     (unsigned long)record_id, (unsigned long)start, (unsigned long)count, hexadecimal);
        } else {
            snprintf(state->response, sizeof(state->response), "{\"ok\":false,\"code\":%d}\n", result);
        }
    } else if (strcmp(state->command, "ADC_STATUS") == 0) {
        struct fdw_phase_status phase;
        uint32_t ratio=1U;
        fdw_adc_control_status(&adc);
        if (fdw_phase_status(&phase)==0) ratio=phase.interval_ticks;
        snprintf(state->response, sizeof(state->response),
                 "{\"ok\":true,\"ready\":%lu,\"code\":%d,\"record_id\":%lu,\"sample_count\":%lu,\"baseline_points\":%lu,\"sample_rate_hz\":%s,\"raw_clock_hz\":25000000,\"interval_ticks\":%lu,\"sample_rate_numerator_hz\":25000000,\"sample_rate_denominator\":%lu,\"flags\":%lu,\"overrange_count\":%lu,\"encoding\":\"signed16_le\",\"trigger\":\"%s\",\"trigger_status\":%lu,\"mode\":%lu,\"route_generation\":%lu,\"calibrated\":false}\n",
                 (unsigned long)adc.ready, adc.last_code, (unsigned long)adc.record_id,
                 (unsigned long)adc.sample_count, (unsigned long)FDW_ADC_SAMPLE_COUNT,
                 ratio==1U?"25000000":"null", (unsigned long)ratio,(unsigned long)ratio,(unsigned long)adc.flags,
                 (unsigned long)adc.overrange_count, adc.trigger ? "pwm_period_event" : "software_immediate",
                 (unsigned long)adc.trigger_status, (unsigned long)adc.mode, (unsigned long)adc.route_generation);
    } else if (strcmp(state->command, "ADC_INIT") == 0 || strncmp(state->command, "ADC_CAPTURE ", 12) == 0 ||
               strncmp(state->command, "ADC_CAPTURE_SYNC ", 17) == 0) {
        record_id = 0U;
        if (strcmp(state->command, "ADC_INIT") == 0) { result = fdw_adc_control_init();if (result==0) result=fdw_phase_control_init(); }
        else {
            uint32_t synchronous = strncmp(state->command, "ADC_CAPTURE_SYNC ", 17) == 0;
            parameters = state->command + (synchronous ? 17 : 12);
            if (parse_number(&parameters, &count) == 0 && at_end(parameters)) {
                result=fdw_phase_configure_raw();
                if (result==0) result = synchronous ? fdw_adc_control_capture_sync(count, &record_id) :
                                       fdw_adc_control_capture(count, &record_id);
            }
        }
        snprintf(state->response, sizeof(state->response), "{\"ok\":%s,\"code\":%d,\"record_id\":%lu}\n",
                 result == 0 ? "true" : "false", result, (unsigned long)record_id);
    } else if (strncmp(state->command, "ADC_READ ", 9) == 0) {
        int16_t samples[32];
        char hexadecimal[129];
        static const char digits[] = "0123456789abcdef";
        uint32_t index, word;
        parameters = state->command + 9;
        if (parse_number(&parameters, &record_id) == 0 && *parameters == ' ' &&
            parse_number(&parameters, &start) == 0 && *parameters == ' ' &&
            parse_number(&parameters, &count) == 0 && at_end(parameters))
            result = fdw_adc_control_read(record_id, start, count, samples);
        if (result == 0) {
            for (index = 0; index < count; ++index) {
                word = (uint16_t)samples[index];
                hexadecimal[index * 4U] = digits[(word >> 4) & 15U];
                hexadecimal[index * 4U + 1U] = digits[word & 15U];
                hexadecimal[index * 4U + 2U] = digits[word >> 12];
                hexadecimal[index * 4U + 3U] = digits[(word >> 8) & 15U];
            }
            hexadecimal[count * 4U] = '\0';
            snprintf(state->response, sizeof(state->response),
                     "{\"ok\":true,\"record_id\":%lu,\"start\":%lu,\"count\":%lu,\"hex\":\"%s\"}\n",
                     (unsigned long)record_id, (unsigned long)start, (unsigned long)count, hexadecimal);
        } else snprintf(state->response, sizeof(state->response), "{\"ok\":false,\"code\":%d}\n", result);
    } else if (strcmp(state->command, "GET_ADC") == 0) {
        fdw_adc_control_status(&adc);
        snprintf(state->response, sizeof(state->response), "{\"ok\":false,\"error\":\"%s\"}\n",
                 adc.ready ? "USE_ADC_CAPTURE_AND_READ" : "ADC_NOT_READY");
    } else {
        snprintf(state->response, sizeof(state->response), "{\"ok\":false,\"error\":\"UNKNOWN_COMMAND\"}\n");
    }
    state->response_length = (uint32_t)strlen(state->response);
}

static err_t flush_response(struct command_connection *state)
{
    err_t result;
    u16_t count;
    if (!state->complete) return ERR_OK;
    if (state->acknowledged == state->response_length) return close_connection(state);
    while (state->queued < state->response_length && tcp_sndbuf(state->pcb) > 0U) {
        const uint8_t *data;
        uint32_t remaining=state->response_length-state->queued;
        if (state->binary) {
            if (state->queued<FDW_BULK_HEADER_BYTES) {
                data=state->binary_header+state->queued;
                remaining=FDW_BULK_HEADER_BYTES-state->queued;
            } else data=(const uint8_t *)state->frame+state->queued-FDW_BULK_HEADER_BYTES;
        } else data=(const uint8_t *)state->response+state->queued;
        count=(u16_t)(remaining>1460U?1460U:remaining);
        if (count>tcp_sndbuf(state->pcb)) count=tcp_sndbuf(state->pcb);
        result=tcp_write(state->pcb,data,count,TCP_WRITE_FLAG_COPY);
        if (result == ERR_MEM) break;
        if (result != ERR_OK) return abort_connection(state);
        state->queued += count;
    }
    result = tcp_output(state->pcb);
    if (result != ERR_OK && result != ERR_MEM) return abort_connection(state);
    return ERR_OK;
}

static err_t control_recv(void *arg, struct tcp_pcb *pcb, struct pbuf *p, err_t err)
{
    struct command_connection *state = arg;
    struct pbuf *part;
    u16_t index;
    int invalid = 0;
    if (p == NULL) {
        if (!state->complete) return close_connection(state);
        return flush_response(state);
    }
    if (err != ERR_OK) { pbuf_free(p); return abort_connection(state); }
    tcp_recved(pcb, p->tot_len);
    for (part = p; part != NULL && !invalid; part = part->next) {
        const unsigned char *bytes = part->payload;
        for (index = 0U; index < part->len; ++index) {
            unsigned char byte = bytes[index];
            if (state->complete) {
                if (byte != '\r' && byte != '\n') invalid = 1;
            } else if (byte == '\r') {
                continue;
            } else if (byte == '\n') {
                state->command[state->command_length] = '\0';
                state->complete = 1U;
            } else if (byte >= 32U && byte <= 126U && state->command_length < COMMAND_CAPACITY - 1U) {
                state->command[state->command_length++] = (char)byte;
            } else {
                invalid = 1;
            }
            if (invalid) break;
        }
    }
    pbuf_free(p);
    if (invalid) return abort_connection(state);
    if (state->complete && state->response_length == 0U) prepare_response(state);
    return flush_response(state);
}

static err_t control_sent(void *arg, struct tcp_pcb *pcb, u16_t length)
{
    struct command_connection *state = arg;
    (void)pcb;
    if ((unsigned int)state->acknowledged + length > state->queued) return abort_connection(state);
    state->acknowledged += length;
    return flush_response(state);
}

static err_t control_poll(void *arg, struct tcp_pcb *pcb)
{
    struct command_connection *state = arg;
    XTime now;
    (void)pcb;
    XTime_GetTime(&now);
    if (now - state->created > (XTime)COUNTS_PER_SECOND * (state->binary?30U:10U)) return abort_connection(state);
    return flush_response(state);
}

static void control_error(void *arg, err_t err)
{
    (void)err;
    if (arg != NULL) { release_frame(arg);mem_free(arg); --active_connections; }
}

static err_t control_accept(void *arg, struct tcp_pcb *pcb, err_t err)
{
    struct command_connection *state;
    (void)arg;
    if (err != ERR_OK || active_connections >= MAX_CONNECTIONS) { tcp_abort(pcb); return ERR_ABRT; }
    state = mem_malloc(sizeof(*state));
    if (state == NULL) { tcp_abort(pcb); return ERR_ABRT; }
    memset(state, 0, sizeof(*state));
    state->pcb = pcb;
    XTime_GetTime(&state->created);
    ++active_connections;
    tcp_arg(pcb, state);
    tcp_recv(pcb, control_recv);
    tcp_sent(pcb, control_sent);
    tcp_poll(pcb, control_poll, 2);
    tcp_err(pcb, control_error);
    return ERR_OK;
}

/* 诊断回显沿用旧工程原理，只用于少量通信自检。 */
static err_t echo_recv(void *arg, struct tcp_pcb *pcb, struct pbuf *p, err_t err)
{
    struct pbuf *part;
    (void)arg;
    if (p == NULL) { tcp_recv(pcb, NULL); return tcp_close(pcb); }
    if (err != ERR_OK) { pbuf_free(p); return err; }
    tcp_recved(pcb, p->tot_len);
    for (part = p; part != NULL; part = part->next) {
        err = tcp_write(pcb, part->payload, part->len, TCP_WRITE_FLAG_COPY);
        if (err != ERR_OK) break;
    }
    pbuf_free(p);
    if (err == ERR_OK) err = tcp_output(pcb);
    if (err != ERR_OK) { tcp_abort(pcb); return ERR_ABRT; }
    return ERR_OK;
}

static err_t echo_accept(void *arg, struct tcp_pcb *pcb, err_t err)
{
    (void)arg;
    if (err != ERR_OK) { tcp_abort(pcb); return ERR_ABRT; }
    tcp_recv(pcb, echo_recv);
    return ERR_OK;
}

static int listen_on(uint16_t port, tcp_accept_fn accept)
{
    struct tcp_pcb *pcb = tcp_new_ip_type(IPADDR_TYPE_ANY);
    struct tcp_pcb *listener;
    if (pcb == NULL) return -1;
    if (tcp_bind(pcb, IP_ANY_TYPE, port) != ERR_OK) { tcp_close(pcb); return -2; }
    listener = tcp_listen(pcb);
    if (listener == NULL) { tcp_close(pcb); return -3; }
    tcp_accept(listener, accept);
    return 0;
}

int transfer_data(void) { fdw_phase_job_service();return 0; }

void print_app_header(void)
{
    xil_printf("\r\nDIG5: ADC + PL DDS/DAC902 + 2IO, echo=7 control=5001\r\n");
}

int start_application(void)
{
    if (fdw_pwm_control_init() != 0) { xil_printf("PWM identifier mismatch\r\n"); return -1; }
    if (fdw_lxb_io_init() != 0) { xil_printf("LXB IO identifier mismatch\r\n"); return -10; }
    if (fdw_digital_control_init() != 0) { xil_printf("Digital identifier mismatch\r\n"); return -4; }
    if (fdw_dac_control_init() != 0) xil_printf("DAC902 control not ready, output remains disconnected\r\n");
    if (fdw_probe_control_init() != 0) { xil_printf("Probe mode initialization failed\r\n"); return -5; }
    /* ADC 故障保留诊断 TCP 服务，可用 ADC_INIT 重试接收链路初始化。 */
    if (fdw_adc_control_init() != 0) xil_printf("ADC initialization failed, diagnostic TCP remains available\r\n");
    if (fdw_phase_control_init()!=0) xil_printf("Phase metadata unavailable, phase capture rejected\r\n");
    if (listen_on(ECHO_PORT, echo_accept) != 0) return -2;
    if (listen_on(CONTROL_PORT, control_accept) != 0) return -3;
    xil_printf("FDW_PWM_TCP_READY\r\n");
    return 0;
}
