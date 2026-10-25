`timescale 1ns / 1ps

// 7020 内部的正弦 DDS；所有配置必须在 sample_clk 域原子提交。
// 本核不直接接收 PS 异步总线，跨时钟配置握手由后续接口负责。
module fdw_dac902_dds #(
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
    output reg         cfg_accepted,
    output reg         cfg_rejected,
    output reg  [11:0] sample_code,
    output reg  [31:0] sample_phase,
    output reg  [31:0] sample_tag,
    output reg         sample_active,
    output reg         sample_clipped,
    output reg         sample_valid
);
    // 32 位相位环；高 12 位访问 4096 点正弦表。
    reg [31:0] phase_accumulator;
    reg [31:0] frequency_word;
    reg [31:0] phase_offset;
    reg [11:0] gain_q11;
    reg [11:0] offset_code;
    reg [31:0] active_tag;
    reg active_enable;
    reg active_invert;
    wire [31:0] lookup_phase = phase_accumulator + phase_offset;
    wire config_legal = !cfg_frequency_word[31] && cfg_gain_q11 <= 12'd2048;

    (* rom_style = "block" *) reg signed [11:0] sine_rom [0:4095];
    reg signed [11:0] rom_sample;
    initial $readmemh(ROM_FILE, sine_rom);
    // ROM 读口不带复位，便于推断片内块 RAM；有效标记负责清空流水线。
    always @(posedge sample_clk)
        rom_sample <= sine_rom[lookup_phase[31:20]];

    reg v0, v1, v2;
    reg [31:0] p0, p1, p2;
    reg [31:0] tag0, tag1, tag2;
    reg enabled0, enabled1, enabled2;
    reg invert0, invert1, invert2;
    reg [11:0] gain0, offset0, offset1;
    reg signed [24:0] product1;
    reg signed [15:0] code2;
    wire signed [24:0] rounded_product = product1 >= 0
        ? product1 + 25'sd1024 : product1 + 25'sd1023;
    wire signed [24:0] scaled_sample = rounded_product >>> 11;
    wire signed [24:0] unbounded_code = $signed({1'b0, offset1}) + scaled_sample;
    wire [11:0] bounded_code = code2 < 0 ? 12'd0
        : code2 > 16'sd4095 ? 12'd4095 : code2[11:0];

    always @(posedge sample_clk or negedge aresetn) begin
        if (!aresetn) begin
            phase_accumulator <= 0;
            frequency_word <= 0;
            phase_offset <= 0;
            gain_q11 <= 0;
            offset_code <= SAFE_CODE;
            active_tag <= 0;
            active_enable <= 0;
            active_invert <= 0;
            cfg_accepted <= 0;
            cfg_rejected <= 0;
            sample_code <= SAFE_CODE;
            sample_phase <= 0;
            sample_tag <= 0;
            sample_active <= 0;
            sample_clipped <= 0;
            sample_valid <= 0;
            v0 <= 0; v1 <= 0; v2 <= 0;
            p0 <= 0; p1 <= 0; p2 <= 0;
            tag0 <= 0; tag1 <= 0; tag2 <= 0;
            enabled0 <= 0; enabled1 <= 0; enabled2 <= 0;
            invert0 <= 0; invert1 <= 0; invert2 <= 0;
            gain0 <= 0; offset0 <= SAFE_CODE; offset1 <= SAFE_CODE;
            product1 <= 0;
            code2 <= SAFE_CODE;
        end else begin
            cfg_accepted <= 0;
            cfg_rejected <= cfg_valid && !config_legal;
            if (cfg_valid && config_legal) begin
                // 配置整体生效，清掉旧流水数据，避免新旧参数混合。
                cfg_accepted <= 1;
                frequency_word <= cfg_frequency_word;
                phase_offset <= cfg_phase_offset;
                gain_q11 <= cfg_gain_q11;
                offset_code <= cfg_offset_code;
                active_tag <= cfg_tag;
                active_enable <= cfg_enable;
                active_invert <= cfg_invert;
                if (cfg_restart)
                    phase_accumulator <= 0;
                v0 <= 0; v1 <= 0; v2 <= 0;
                sample_valid <= 0;
                // 禁用立即回到中间码；它仍需模块实际零伏标定。
                if (!cfg_enable) begin
                    sample_code <= SAFE_CODE;
                    sample_active <= 0;
                    sample_clipped <= 0;
                end
            end else begin
                // 非法配置被拒绝，原波形继续运行；停用时相位保持。
                if (active_enable)
                    phase_accumulator <= phase_accumulator + frequency_word;

                v0 <= 1;
                p0 <= lookup_phase;
                tag0 <= active_tag;
                enabled0 <= active_enable;
                invert0 <= active_invert;
                gain0 <= gain_q11;
                offset0 <= offset_code;

                v1 <= v0;
                p1 <= p0;
                tag1 <= tag0;
                enabled1 <= enabled0;
                invert1 <= invert0;
                offset1 <= offset0;
                product1 <= $signed(rom_sample) * $signed({1'b0, gain0});

                v2 <= v1;
                p2 <= p1;
                tag2 <= tag1;
                enabled2 <= enabled1;
                invert2 <= invert1;
                code2 <= unbounded_code;

                sample_valid <= v2;
                if (v2) begin
                    sample_phase <= p2;
                    sample_tag <= tag2;
                    sample_active <= enabled2;
                    sample_clipped <= enabled2 && (code2 < 0 || code2 > 16'sd4095);
                    sample_code <= !enabled2 ? SAFE_CODE
                        : invert2 ? 12'd4095 - bounded_code : bounded_code;
                end
            end
        end
    end
endmodule
