`timescale 1ns / 1ps

// 悟净端接收完整帧后使用的基础特征核，不包含网络、DMA或厂家原语。
// AXIS每拍为一个大端解码后的有符号ADC样本，16位字中的高4位必须符号扩展。
// 固定8192点；在悟净工程中接入前，必须先确定DMA、时钟、复位和寄存器映射。
module fdw_adc_stats_axis (
    input  wire                 clk,
    input  wire                 resetn,
    input  wire                 clear,
    input  wire [31:0]          frame_id,
    input  wire [31:0]          frame_flags,
    input  wire [15:0]          s_axis_tdata,
    input  wire                 s_axis_tvalid,
    input  wire                 s_axis_tlast,
    output wire                 s_axis_tready,
    output reg                  result_valid,
    output reg                  result_ok,
    output reg [31:0]           result_frame_id,
    output reg [31:0]           result_flags,
    output reg [31:0]           sample_count,
    output reg signed [31:0]    sum_counts,
    output reg [47:0]           sum_square_counts,
    output reg signed [15:0]    minimum_counts,
    output reg signed [15:0]    maximum_counts,
    output reg [15:0]           peak_to_peak_counts,
    output reg signed [31:0]    dc_q16,
    output reg [31:0]           ac_rms_q16,
    output reg [31:0]           saturation_count,
    output reg [31:0]           invalid_code_count,
    output reg                  length_error
);
    localparam COLLECT = 2'd0;
    localparam FINALIZE = 2'd1;
    localparam SQRT = 2'd2;
    localparam HOLD = 2'd3;
    reg [1:0] state;
    reg [63:0] radicand_shift;
    reg [63:0] remainder;
    reg [31:0] root;
    reg [5:0] sqrt_step;

    wire signed [11:0] sample = s_axis_tdata[11:0];
    wire signed [31:0] sample_extended = {{20{sample[11]}}, sample};
    wire signed [23:0] sample_squared = sample * sample;
    wire code_invalid = s_axis_tdata[15:12] != {4{sample[11]}};
    wire sample_saturated = (sample == -12'sd2048) || (sample == 12'sd2047);
    wire [31:0] count_next = sample_count + 32'd1;
    wire signed [63:0] sum_extended = {{32{sum_counts[31]}}, sum_counts};
    wire signed [63:0] sum_squared = sum_extended * sum_extended;
    wire [63:0] energy_n_squared = ({16'd0, sum_square_counts} << 13) - sum_squared;
    wire [63:0] shifted_remainder = {remainder[61:0], radicand_shift[63:62]};
    wire [63:0] trial = ({32'd0, root} << 2) | 64'd1;
    wire subtract_trial = shifted_remainder >= trial;
    wire [31:0] root_next = {root[30:0], subtract_trial};
    assign s_axis_tready = (state == COLLECT) && !clear && resetn;

    always @(posedge clk or negedge resetn) begin
        if (!resetn) begin
            state <= COLLECT;
            result_valid <= 0;
            result_ok <= 0;
            result_frame_id <= 0;
            result_flags <= 0;
            sample_count <= 0;
            sum_counts <= 0;
            sum_square_counts <= 0;
            minimum_counts <= 0;
            maximum_counts <= 0;
            peak_to_peak_counts <= 0;
            dc_q16 <= 0;
            ac_rms_q16 <= 0;
            saturation_count <= 0;
            invalid_code_count <= 0;
            length_error <= 0;
            radicand_shift <= 0;
            remainder <= 0;
            root <= 0;
            sqrt_step <= 0;
        end else if (clear) begin
            // 清除优先于握手和计算；也可用于中途撤销一帧。
            state <= COLLECT;
            result_valid <= 0;
            result_ok <= 0;
            result_frame_id <= 0;
            result_flags <= 0;
            sample_count <= 0;
            sum_counts <= 0;
            sum_square_counts <= 0;
            minimum_counts <= 0;
            maximum_counts <= 0;
            peak_to_peak_counts <= 0;
            dc_q16 <= 0;
            ac_rms_q16 <= 0;
            saturation_count <= 0;
            invalid_code_count <= 0;
            length_error <= 0;
            radicand_shift <= 0;
            remainder <= 0;
            root <= 0;
            sqrt_step <= 0;
        end else begin
            case (state)
                COLLECT: if (s_axis_tvalid && s_axis_tready) begin
                    // 无效拍不计点；末拍也必须参与所有累加。
                    sample_count <= count_next;
                    sum_counts <= sum_counts + sample_extended;
                    sum_square_counts <= sum_square_counts + {24'd0, sample_squared};
                    if (sample_count == 0) begin
                        result_frame_id <= frame_id;
                        result_flags <= frame_flags;
                        minimum_counts <= {{4{sample[11]}}, sample};
                        maximum_counts <= {{4{sample[11]}}, sample};
                    end else begin
                        if (sample_extended < minimum_counts)
                            minimum_counts <= {{4{sample[11]}}, sample};
                        if (sample_extended > maximum_counts)
                            maximum_counts <= {{4{sample[11]}}, sample};
                    end
                    if (sample_saturated) saturation_count <= saturation_count + 1;
                    if (code_invalid) invalid_code_count <= invalid_code_count + 1;
                    if (s_axis_tlast || count_next == 8192) begin
                        length_error <= (count_next != 8192) || !s_axis_tlast;
                        state <= FINALIZE;
                    end
                end
                FINALIZE: begin
                    // 均值保持精确Q16.16；先做N*S2-S1²，避免先截断均值造成RMS偏差。
                    dc_q16 <= sum_counts <<< 3;
                    peak_to_peak_counts <= maximum_counts - minimum_counts;
                    radicand_shift <= energy_n_squared << 6;
                    remainder <= 0;
                    root <= 0;
                    sqrt_step <= 0;
                    result_ok <= !length_error && (sample_count == 8192) &&
                                 (result_flags == 32'd8) && (saturation_count == 0) &&
                                 (invalid_code_count == 0);
                    state <= SQRT;
                end
                SQRT: begin
                    // 每周期处理两位被开方数；32周期得到64位整数的平方根。
                    remainder <= subtract_trial ? shifted_remainder - trial : shifted_remainder;
                    root <= root_next;
                    radicand_shift <= radicand_shift << 2;
                    sqrt_step <= sqrt_step + 1;
                    if (sqrt_step == 31) begin
                        ac_rms_q16 <= root_next;
                        result_valid <= 1;
                        state <= HOLD;
                    end
                end
                HOLD: begin
                    // 结果一直保持到clear，CPU读回之前不接受下一帧。
                    result_valid <= 1;
                end
                default: state <= COLLECT;
            endcase
        end
    end
endmodule
