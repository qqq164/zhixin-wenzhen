`timescale 1ns / 1ps

// 在采样域为每次合法装载分配帧号，取消亦消耗号码，防止重装载后旧请求混入。
// 帧号在同一共同复位会话内不回绕；耗尽后只读现有帧，等待上层处理。
module fdw_adc_frame_record #(
    parameter integer MAX_SAMPLES = 1024,
    parameter integer FRAME_ID_WIDTH = 32
) (
    input  wire        sample_clk,
    input  wire        resetn,
    input  wire [11:0] adc_code,
    input  wire        adc_otr,
    input  wire        arm_valid,
    output wire        arm_ready,
    input  wire [31:0] cfg_sample_count,
    input  wire        cfg_wait_trigger,
    input  wire        trigger,
    input  wire        cancel,
    output wire        cfg_error,
    output wire        armed,
    output wire        busy,
    output wire        done,
    output wire        record_valid,
    output wire        cancelled,
    output wire        trigger_seen,
    output wire        sample_taken,
    output wire [31:0] captured_count,
    output wire [31:0] active_sample_count,
    output wire [31:0] record_id,
    output wire        id_exhausted,
    input  wire        rd_request,
    input  wire [31:0] rd_index,
    output wire        rd_valid,
    output wire        rd_error,
    output wire [15:0] rd_word
);
    reg [FRAME_ID_WIDTH-1:0] generation_id;
    wire recorder_ready;
    wire config_ok = cfg_sample_count != 0 && cfg_sample_count <= MAX_SAMPLES;
    assign record_id = {{(32-FRAME_ID_WIDTH){1'b0}}, generation_id};
    assign id_exhausted = &generation_id;
    assign arm_ready = recorder_ready && !id_exhausted;

    // 与记录核心同一拍接受，只有合法完整快照推进编号；错误请求保留旧帧。
    always @(posedge sample_clk) begin
        if (!resetn) generation_id <= 0;
        else if (arm_valid && arm_ready && config_ok) generation_id <= generation_id + 1'b1;
    end
    fdw_adc_record #(.MAX_SAMPLES(MAX_SAMPLES)) recorder (
        .sample_clk(sample_clk), .resetn(resetn), .adc_code(adc_code), .adc_otr(adc_otr),
        .arm_valid(arm_valid && !id_exhausted), .arm_ready(recorder_ready),
        .cfg_sample_count(cfg_sample_count), .cfg_wait_trigger(cfg_wait_trigger),
        .trigger(trigger), .cancel(cancel), .cfg_error(cfg_error), .armed(armed),
        .busy(busy), .done(done), .record_valid(record_valid), .cancelled(cancelled),
        .trigger_seen(trigger_seen), .sample_taken(sample_taken),
        .captured_count(captured_count), .active_sample_count(active_sample_count),
        .rd_request(rd_request), .rd_index(rd_index), .rd_valid(rd_valid),
        .rd_error(rd_error), .rd_word(rd_word)
    );
endmodule
