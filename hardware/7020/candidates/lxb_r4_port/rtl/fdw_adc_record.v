`timescale 1ns / 1ps

// ADC 原码的同步窗口记录。输入、装载和触发必须已在 sample_clk 域中稳定。
// 此模块不生成物理 ADC 时钟，不处理引脚接收相位、跨域、标定或降采样。
module fdw_adc_record #(
    parameter integer MAX_SAMPLES = 1024
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
    input  wire        rd_request,
    input  wire [31:0] rd_index,
    output wire        rd_valid,
    output wire        rd_error,
    output wire [15:0] rd_word
);
    // 新同步原码格式：bit15=OTR，bit14:12=0，bit11:0=ADC 原码。
    // 不继承旧 AXIS 格式的标记／不连续位，后续帧协议须明确格式版本。
    // 每个窗口内连续保存每拍原码；读回只在完整帧后进行，不反压 ADC。
    fdw_digital_capture #(.SAMPLE_WIDTH(16), .MAX_SAMPLES(MAX_SAMPLES)) recorder (
        .clk(sample_clk), .resetn(resetn), .sample_in({adc_otr, 3'b000, adc_code}),
        .arm_valid(arm_valid), .arm_ready(arm_ready), .cfg_interval_ticks(32'd1),
        .cfg_sample_count(cfg_sample_count), .cfg_wait_trigger(cfg_wait_trigger),
        .trigger(trigger), .cancel(cancel), .cfg_error(cfg_error),
        .armed(armed), .busy(busy), .done(done), .record_valid(record_valid),
        .cancelled(cancelled), .trigger_seen(trigger_seen), .sample_taken(sample_taken),
        .captured_count(captured_count), .active_interval_ticks(),
        .active_sample_count(active_sample_count), .rd_request(rd_request),
        .rd_index(rd_index), .rd_valid(rd_valid), .rd_error(rd_error), .rd_data(rd_word)
    );
endmodule
