`timescale 1ns / 1ps

// 独立 ADC 原码控制准备模块；adc_code 与 adc_otr 须已在 sample_clk 域稳定。
// 不产生物理 ADC 时钟、不接模拟前端、不提供 AXI／DMA／网络接口。
module fdw_adc_prepare #(
    parameter integer MAX_SAMPLES = 1024,
    parameter integer FRAME_ID_WIDTH = 32
) (
    input wire control_clk,
    input wire sample_clk,
    input wire arstn,
    input wire [11:0] adc_code,
    input wire adc_otr,
    input wire command_valid,
    output wire command_ready,
    input wire [31:0] command_id,
    input wire command_cancel,
    input wire command_wait_trigger,
    input wire [31:0] command_sample_count,
    output wire result_valid,
    input wire result_ready,
    output wire [31:0] result_command_id,
    output wire [2:0] result_code,
    output wire [31:0] result_record_id,
    output wire [31:0] result_captured_count,
    output wire result_record_valid,
    output wire result_armed,
    output wire result_busy,
    input wire trigger_valid,
    output wire trigger_ready,
    input wire status_valid,
    output wire status_ready,
    output wire status_response_valid,
    input wire status_response_ready,
    output wire [127:0] status_response,
    input wire read_valid,
    output wire read_ready,
    input wire [31:0] read_record_id,
    input wire [31:0] read_index,
    output wire read_response_valid,
    input wire read_response_ready,
    output wire read_response_error,
    output wire [15:0] read_response_word,
    // 以下调试输出只属于采样域，可用于同域 ILA，不能直接由控制域采样。
    output wire [127:0] sample_debug_status,
    output wire sample_debug_taken,
    output wire sample_debug_trigger,
    output wire sample_debug_cancel
);
    (* ASYNC_REG = "TRUE", SHREG_EXTRACT = "NO" *) reg [1:0] sample_release;
    reg sample_reset_ready;
    wire sample_resetn = arstn && sample_reset_ready;
    wire core_sample_taken;
    wire arm_valid, arm_ready, arm_wait, cancel, raw_trigger;
    reg trigger;
    wire [31:0] arm_count;
    wire cfg_error, armed, busy, done, record_valid, cancelled, trigger_seen, exhausted;
    wire [31:0] record_id, captured_count, active_count;
    wire raw_rd_request, rd_valid, rd_error;
    wire [31:0] raw_rd_index;
    reg rd_request;
    reg [31:0] rd_index;
    wire [15:0] rd_word;
    wire snapshot_taken;
    wire [31:0] flags = {26'b0, cfg_error, exhausted, done, busy, armed, record_valid};
    assign sample_debug_status = {record_id, captured_count, active_count, flags};
    assign sample_debug_trigger = trigger;
    assign sample_debug_cancel = cancel;
    // 复位／本地恢复期间的存储内容无效，调试标志也不发布恢复前的旧样点。
    assign sample_debug_taken = arstn && sample_release[1] && core_sample_taken;
    always @(posedge sample_clk or negedge arstn) begin
        if (!arstn) sample_release <= 0;
        else sample_release <= {sample_release[0], 1'b1};
    end
    // BRAM 使用同步复位状态，避免异步复位寄存器直接驱动 RAM 的控制端。
    // 共同复位即使发生在采样停钟期间，异步释放链也会使恢复后的同步状态重新清零。
    always @(posedge sample_clk) begin
        if (!arstn) sample_reset_ready <= 0;
        else sample_reset_ready <= sample_release[1];
    end
    // BRAM 控制端前增加同步寄存级，异步复位的跨域寄存器不直接驱动地址和事件。
    // 触发与读请求均多一拍；帧身份桥支持可变本地读延迟，仍在回应时核对身份。
    always @(posedge sample_clk) begin
        if (!sample_resetn) begin
            trigger <= 0;
            rd_request <= 0;
            rd_index <= 0;
        end else begin
            trigger <= raw_trigger;
            rd_request <= raw_rd_request;
            if (raw_rd_request) rd_index <= raw_rd_index;
        end
    end
    fdw_adc_command_cdc #(.MAX_SAMPLES(MAX_SAMPLES)) commands (
        .control_clk(control_clk), .sample_clk(sample_clk), .arstn(arstn),
        .request_valid(command_valid), .request_ready(command_ready), .request_command_id(command_id),
        .request_cancel(command_cancel), .request_wait_trigger(command_wait_trigger),
        .request_sample_count(command_sample_count), .response_valid(result_valid), .response_ready(result_ready),
        .response_command_id(result_command_id), .response_code(result_code), .response_record_id(result_record_id),
        .response_captured_count(result_captured_count), .response_record_valid(result_record_valid),
        .response_armed(result_armed), .response_busy(result_busy),
        .arm_valid(arm_valid), .arm_ready(arm_ready), .arm_sample_count(arm_count), .arm_wait_trigger(arm_wait),
        .cancel(cancel), .record_cfg_error(cfg_error), .record_id_exhausted(exhausted), .record_id(record_id),
        .captured_count(captured_count), .record_valid(record_valid), .armed(armed), .busy(busy)
    );
    fdw_event_cdc triggers (
        .src_clk(control_clk), .dst_clk(sample_clk), .arstn(arstn),
        .src_valid(trigger_valid), .src_ready(trigger_ready), .dst_pulse(raw_trigger)
    );
    fdw_adc_frame_record #(.MAX_SAMPLES(MAX_SAMPLES), .FRAME_ID_WIDTH(FRAME_ID_WIDTH)) recorder (
        .sample_clk(sample_clk), .resetn(sample_resetn), .adc_code(adc_code), .adc_otr(adc_otr),
        .arm_valid(arm_valid), .arm_ready(arm_ready), .cfg_sample_count(arm_count), .cfg_wait_trigger(arm_wait),
        .trigger(trigger), .cancel(cancel), .cfg_error(cfg_error), .armed(armed), .busy(busy), .done(done),
        .record_valid(record_valid), .cancelled(cancelled), .trigger_seen(trigger_seen),
        .sample_taken(core_sample_taken), .captured_count(captured_count), .active_sample_count(active_count),
        .record_id(record_id), .id_exhausted(exhausted), .rd_request(rd_request), .rd_index(rd_index),
        .rd_valid(rd_valid), .rd_error(rd_error), .rd_word(rd_word)
    );
    fdw_cdc_frame_readback reads (
        .control_clk(control_clk), .sample_clk(sample_clk), .arstn(arstn),
        .request_valid(read_valid), .request_ready(read_ready), .request_record_id(read_record_id),
        .request_index(read_index), .response_valid(read_response_valid), .response_ready(read_response_ready),
        .response_error(read_response_error), .response_data(read_response_word),
        .local_record_id(record_id), .local_record_valid(record_valid), .local_record_busy(armed || busy),
        .local_rd_request(raw_rd_request), .local_rd_index(raw_rd_index), .local_rd_valid(rd_valid),
        .local_rd_error(rd_error), .local_rd_data(rd_word)
    );
    fdw_cdc_snapshot #(.WIDTH(128)) statuses (
        .control_clk(control_clk), .sample_clk(sample_clk), .arstn(arstn),
        .request_valid(status_valid), .request_ready(status_ready), .local_data(sample_debug_status),
        .snapshot_taken(snapshot_taken), .response_valid(status_response_valid),
        .response_ready(status_response_ready), .response_data(status_response)
    );
endmodule
