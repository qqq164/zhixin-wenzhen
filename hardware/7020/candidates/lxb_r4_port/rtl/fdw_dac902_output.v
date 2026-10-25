`timescale 1ns / 1ps

// DAC902 并行输出包装：数据在正沿更新，DAC 外送时钟在负沿上升。
// 25MHz 下相邻数据变化与 DAC 锁存相隔约半周期；板级时序仍须实测。
module fdw_dac902_output #(
    parameter ROM_FILE = "fdw_sine12_4096.mem",
    parameter [11:0] SAFE_CODE = 12'd2048
)(
    input  wire        sample_clk,
    input  wire        aresetn,
    input  wire        cfg_valid,
    input  wire        cfg_enable,
    input  wire        cfg_restart,
    input  wire        cfg_invert,
    input  wire [31:0] cfg_frequency_word,
    input  wire [31:0] cfg_phase_offset,
    input  wire [11:0] cfg_gain_q11,
    input  wire [11:0] cfg_offset_code,
    input  wire [31:0] cfg_tag,
    output wire        cfg_accepted,
    output wire        cfg_rejected,
    output wire [11:0] dac_data,
    output wire        dac_clk,
    output wire [31:0] sample_phase,
    output wire [31:0] sample_tag,
    output wire        sample_active,
    output wire        sample_clipped,
    output wire        sample_valid
);
    fdw_dac902_dds #(.ROM_FILE(ROM_FILE), .SAFE_CODE(SAFE_CODE)) dds_core (
        .sample_clk(sample_clk), .aresetn(aresetn),
        .cfg_valid(cfg_valid), .cfg_enable(cfg_enable),
        .cfg_restart(cfg_restart), .cfg_invert(cfg_invert),
        .cfg_frequency_word(cfg_frequency_word), .cfg_phase_offset(cfg_phase_offset),
        .cfg_gain_q11(cfg_gain_q11), .cfg_offset_code(cfg_offset_code), .cfg_tag(cfg_tag),
        .cfg_accepted(cfg_accepted), .cfg_rejected(cfg_rejected),
        .sample_code(dac_data), .sample_phase(sample_phase), .sample_tag(sample_tag),
        .sample_active(sample_active), .sample_clipped(sample_clipped), .sample_valid(sample_valid)
    );

    // 运行时始终送时钟，停用只归中；复位才将外送时钟拉低。
    ODDR #(.DDR_CLK_EDGE("SAME_EDGE"), .INIT(1'b0), .SRTYPE("ASYNC")) dac_clk_oddr (
        .Q(dac_clk), .C(sample_clk), .CE(1'b1),
        .D1(1'b0), .D2(1'b1), .R(~aresetn), .S(1'b0)
    );
endmodule
