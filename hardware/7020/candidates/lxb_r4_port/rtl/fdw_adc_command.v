`timescale 1ns / 1ps

// 采样域单条命令执行器；结果保持到实际消费，不能把邮箱接收当作装载成功。
// 只允许本模块写记录器的装载与取消；触发可来自独立同步事件。
module fdw_adc_command #(
    parameter integer MAX_SAMPLES = 1024
) (
    input wire clk,
    input wire resetn,
    input wire command_valid,
    output wire command_ready,
    input wire [31:0] command_id,
    input wire command_cancel,
    input wire command_wait_trigger,
    input wire [31:0] command_sample_count,
    output reg result_valid,
    input wire result_ready,
    output reg [31:0] result_command_id,
    output reg [2:0] result_code,
    output reg [31:0] result_record_id,
    output reg [31:0] result_captured_count,
    output reg result_record_valid,
    output reg result_armed,
    output reg result_busy,
    output reg arm_valid,
    input wire arm_ready,
    output reg [31:0] arm_sample_count,
    output reg arm_wait_trigger,
    output reg cancel,
    input wire record_cfg_error,
    input wire record_id_exhausted,
    input wire [31:0] record_id,
    input wire [31:0] captured_count,
    input wire record_valid,
    input wire armed,
    input wire busy
);
    // 编码仅属本阶段接口：0 成功，1 参数非法，2 忙，3 帧号耗尽，4 执行异常。
    localparam [1:0] IDLE = 0, APPLY = 1, OBSERVE = 2;
    reg [1:0] state;
    reg [31:0] accepted_id;
    assign command_ready = resetn && state == IDLE && !result_valid;

    task publish;
        input [31:0] identity;
        input [2:0] code;
        begin
            result_valid <= 1;
            result_command_id <= identity;
            result_code <= code;
            result_record_id <= record_id;
            result_captured_count <= captured_count;
            result_record_valid <= record_valid;
            result_armed <= armed;
            result_busy <= busy;
        end
    endtask

    always @(posedge clk) begin
        if (!resetn) begin
            state <= IDLE;
            accepted_id <= 0;
            result_valid <= 0;
            result_command_id <= 0;
            result_code <= 0;
            result_record_id <= 0;
            result_captured_count <= 0;
            result_record_valid <= 0;
            result_armed <= 0;
            result_busy <= 0;
            arm_valid <= 0;
            arm_sample_count <= 0;
            arm_wait_trigger <= 0;
            cancel <= 0;
        end else begin
            arm_valid <= 0;
            cancel <= 0;
            if (result_valid && result_ready) result_valid <= 0;
            case (state)
                IDLE: if (command_valid && command_ready) begin
                    accepted_id <= command_id;
                    if (command_cancel) begin
                        cancel <= 1;
                        state <= APPLY;
                    end else if (command_sample_count == 0 || command_sample_count > MAX_SAMPLES) begin
                        publish(command_id, 3'd1);
                    end else if (record_id_exhausted) begin
                        publish(command_id, 3'd3);
                    end else if (!arm_ready) begin
                        publish(command_id, 3'd2);
                    end else begin
                        arm_valid <= 1;
                        arm_sample_count <= command_sample_count;
                        arm_wait_trigger <= command_wait_trigger;
                        state <= APPLY;
                    end
                end
                // 本拍记录器实际消费上一拍的装载／取消，下一拍观察其更新后的结果。
                APPLY: state <= OBSERVE;
                OBSERVE: begin
                    publish(accepted_id, record_cfg_error ? 3'd4 : 3'd0);
                    state <= IDLE;
                end
                default: state <= IDLE;
            endcase
        end
    end
endmodule
