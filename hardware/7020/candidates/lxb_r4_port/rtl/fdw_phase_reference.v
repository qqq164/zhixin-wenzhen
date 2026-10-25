`timescale 1ns / 1ps

// DIG6离线候选：记录真正写入采集帧的样本所对应的数字DDS参考。
// 所有输入属于ADC接收时钟域；DDS侧信号必须先通过真实相关时钟路径交付。
// 本模块不把ADC流水延迟或模拟线缆延迟冒充已校准的零相位。
module fdw_phase_reference #(
    parameter integer FRAME_SAMPLES = 8192
)(
    input wire sample_clk,
    input wire aresetn,
    input wire clear,
    input wire arm,
    input wire sample_commit,
    input wire frame_complete,
    input wire stream_discontinuous,
    input wire reference_valid,
    input wire reference_inverted,
    input wire [31:0] reference_phase,
    input wire [31:0] reference_ftw,
    input wire [31:0] reference_tag,
    input wire [19:0] interval_minus_one,
    output reg active,
    output reg done,
    output reg [7:0] errors,
    output reg [31:0] generation,
    output reg [31:0] accepted_count,
    output reg [31:0] first_phase,
    output reg [31:0] last_phase,
    output reg [31:0] frame_ftw,
    output reg [31:0] frame_tag,
    output reg [19:0] frame_interval_minus_one,
    output reg frame_inverted,
    output reg [63:0] first_tick,
    output reg [63:0] last_tick
);
    reg [63:0] raw_tick;
    reg [31:0] expected_phase;
    reg [63:0] expected_tick;
    wire [20:0] interval_ticks = {1'b0, frame_interval_minus_one} + 21'd1;
    wire [52:0] full_phase_step = frame_ftw * interval_ticks;
    wire [31:0] phase_step = full_phase_step[31:0];

    // errors：0参考失效，1配置改变，2样点间隔错误，3相位步进错误，
    // 4流不连续，5长度错误，6重复装载/代数耗尽，7完成后仍有样本。
    always @(posedge sample_clk) begin
        if (!aresetn) begin
            raw_tick <= 0;
            active <= 0; done <= 0; errors <= 0; generation <= 0;
            accepted_count <= 0; first_phase <= 0; last_phase <= 0;
            frame_ftw <= 0; frame_tag <= 0; frame_interval_minus_one <= 0;
            frame_inverted <= 0; first_tick <= 0; last_tick <= 0;
            expected_phase <= 0; expected_tick <= 0;
        end else begin
            raw_tick <= raw_tick + 1'b1;
            if (clear) begin
                active <= 0; done <= 0; errors <= 0; accepted_count <= 0;
                first_phase <= 0; last_phase <= 0; first_tick <= 0; last_tick <= 0;
            end else if (arm) begin
                if (active || generation == 32'hffffffff) errors[6] <= 1;
                else begin
                    generation <= generation + 1'b1;
                    active <= 1; done <= 0; errors <= 0; accepted_count <= 0;
                    first_phase <= 0; last_phase <= 0; first_tick <= 0; last_tick <= 0;
                    frame_ftw <= reference_ftw; frame_tag <= reference_tag;
                    frame_interval_minus_one <= interval_minus_one;
                    frame_inverted <= reference_inverted;
                    if (!reference_valid) errors[0] <= 1;
                    // 装载和首样本必须是不同的时钟沿，禁止悄悄漏掉首样本。
                    if (sample_commit || frame_complete) errors[5] <= 1;
                end
            end else if (active) begin
                if (!reference_valid) errors[0] <= 1;
                if (reference_ftw != frame_ftw || reference_tag != frame_tag ||
                    reference_inverted != frame_inverted ||
                    interval_minus_one != frame_interval_minus_one) errors[1] <= 1;
                if (stream_discontinuous) errors[4] <= 1;
                if (sample_commit) begin
                    if (accepted_count == 0) begin
                        first_phase <= reference_phase;
                        first_tick <= raw_tick;
                    end else begin
                        if (raw_tick != expected_tick) errors[2] <= 1;
                        if (reference_phase != expected_phase) errors[3] <= 1;
                    end
                    expected_phase <= reference_phase + phase_step;
                    expected_tick <= raw_tick + interval_ticks;
                    last_phase <= reference_phase; last_tick <= raw_tick;
                    accepted_count <= accepted_count + 1'b1;
                    if (accepted_count >= FRAME_SAMPLES) errors[5] <= 1;
                end
                if (frame_complete) begin
                    active <= 0; done <= 1;
                    if (accepted_count + (sample_commit ? 1 : 0) != FRAME_SAMPLES)
                        errors[5] <= 1;
                end
            end else if (sample_commit) errors[7] <= 1;
        end
    end
endmodule
