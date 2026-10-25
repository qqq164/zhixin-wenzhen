`timescale 1ns / 1ps

// 双向命令与结果邮箱，控制端消费明确结果后才允许下一条命令。
// 共同复位丢弃在途操作；采样域执行器独占记录器的装载和取消端口。
module fdw_adc_command_cdc #(
    parameter integer MAX_SAMPLES = 1024
) (
    input wire control_clk,
    input wire sample_clk,
    input wire arstn,
    input wire request_valid,
    output wire request_ready,
    input wire [31:0] request_command_id,
    input wire request_cancel,
    input wire request_wait_trigger,
    input wire [31:0] request_sample_count,
    output wire response_valid,
    input wire response_ready,
    output wire [31:0] response_command_id,
    output wire [2:0] response_code,
    output wire [31:0] response_record_id,
    output wire [31:0] response_captured_count,
    output wire response_record_valid,
    output wire response_armed,
    output wire response_busy,
    output wire arm_valid,
    input wire arm_ready,
    output wire [31:0] arm_sample_count,
    output wire arm_wait_trigger,
    output wire cancel,
    input wire record_cfg_error,
    input wire record_id_exhausted,
    input wire [31:0] record_id,
    input wire [31:0] captured_count,
    input wire record_valid,
    input wire armed,
    input wire busy
);
    (* ASYNC_REG = "TRUE", SHREG_EXTRACT = "NO" *) reg [1:0] control_release;
    (* ASYNC_REG = "TRUE", SHREG_EXTRACT = "NO" *) reg [1:0] sample_release;
    reg inflight;
    wire mailbox_ready, command_valid, command_ready;
    wire [65:0] command_data;
    wire result_valid, result_ready;
    wire [31:0] result_id, result_frame, result_count;
    wire [2:0] result_code;
    wire result_complete, result_armed, result_busy;
    wire [101:0] response_data;
    wire sample_resetn = arstn && sample_release[1];
    always @(posedge control_clk or negedge arstn) begin
        if (!arstn) control_release <= 0;
        else control_release <= {control_release[0], 1'b1};
    end
    always @(posedge sample_clk or negedge arstn) begin
        if (!arstn) sample_release <= 0;
        else sample_release <= {sample_release[0], 1'b1};
    end
    assign request_ready = arstn && control_release[1] && !inflight && mailbox_ready;
    assign {response_command_id, response_code, response_record_id, response_captured_count,
            response_record_valid, response_armed, response_busy} = response_data;
    always @(posedge control_clk or negedge arstn) begin
        if (!arstn) inflight <= 0;
        else if (!control_release[1]) inflight <= 0;
        else begin
            if (request_valid && request_ready) inflight <= 1;
            if (response_valid && response_ready) inflight <= 0;
        end
    end
    fdw_cdc_mailbox #(.WIDTH(66)) requests (
        .src_clk(control_clk), .dst_clk(sample_clk), .arstn(arstn),
        .src_valid(request_valid && control_release[1] && !inflight), .src_ready(mailbox_ready),
        .src_data({request_command_id, request_cancel, request_wait_trigger, request_sample_count}),
        .dst_valid(command_valid), .dst_ready(command_ready), .dst_data(command_data)
    );
    fdw_adc_command #(.MAX_SAMPLES(MAX_SAMPLES)) executor (
        .clk(sample_clk), .resetn(sample_resetn), .command_valid(command_valid), .command_ready(command_ready),
        .command_id(command_data[65:34]), .command_cancel(command_data[33]),
        .command_wait_trigger(command_data[32]), .command_sample_count(command_data[31:0]),
        .result_valid(result_valid), .result_ready(result_ready), .result_command_id(result_id),
        .result_code(result_code), .result_record_id(result_frame), .result_captured_count(result_count),
        .result_record_valid(result_complete), .result_armed(result_armed), .result_busy(result_busy),
        .arm_valid(arm_valid), .arm_ready(arm_ready), .arm_sample_count(arm_sample_count),
        .arm_wait_trigger(arm_wait_trigger), .cancel(cancel), .record_cfg_error(record_cfg_error),
        .record_id_exhausted(record_id_exhausted), .record_id(record_id), .captured_count(captured_count),
        .record_valid(record_valid), .armed(armed), .busy(busy)
    );
    fdw_cdc_mailbox #(.WIDTH(102)) replies (
        .src_clk(sample_clk), .dst_clk(control_clk), .arstn(arstn),
        .src_valid(result_valid), .src_ready(result_ready),
        .src_data({result_id, result_code, result_frame, result_count, result_complete, result_armed, result_busy}),
        .dst_valid(response_valid), .dst_ready(response_ready), .dst_data(response_data)
    );
endmodule
