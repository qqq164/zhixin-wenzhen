`timescale 1ns / 1ps

// R4低频分支：三级CIC低通后按2的幂抽取；ADC与DDS物理时钟不改变。
// 输出参考对应滤波窗口末端。理论群延迟为3*(R-1)/2个原始时钟，必须入档。
// CIC不是理想抗混叠滤波器；带外抑制度和模拟前端仍须按测量频带验收。
module fdw_cic3_decimator #(
    parameter integer MAX_LOG2 = 20
)(
    input wire sample_clk,
    input wire aresetn,
    input wire clear,
    input wire configure,
    input wire [4:0] config_log2,
    input wire signed [11:0] input_code,
    input wire input_overrange,
    input wire [31:0] input_phase,
    input wire [31:0] input_tag,
    input wire input_reference_valid,
    output reg config_error,
    output reg [4:0] active_log2,
    output reg signed [11:0] output_code,
    output reg output_overrange,
    output reg output_valid,
    output reg [31:0] output_phase,
    output reg [31:0] output_tag,
    output reg output_reference_valid
);
    localparam integer WIDTH = 14 + 3*MAX_LOG2;
    reg signed [WIDTH-1:0] integrator1, integrator2, integrator3;
    reg signed [WIDTH-1:0] delay1, delay2, delay3;
    reg [MAX_LOG2-1:0] counter;
    reg [1:0] warmup;
    reg [1:0] overrange_history;
    reg interval_overrange, interval_reference_valid;
    reg [31:0] interval_tag;
    wire [MAX_LOG2:0] ratio = {{MAX_LOG2{1'b0}},1'b1} << active_log2;
    wire signed [WIDTH-1:0] extended_input = {{(WIDTH-12){input_code[11]}}, input_code};
    wire signed [WIDTH-1:0] next1 = integrator1 + extended_input;
    wire signed [WIDTH-1:0] next2 = integrator2 + next1;
    wire signed [WIDTH-1:0] next3 = integrator3 + next2;
    wire signed [WIDTH-1:0] comb1 = next3 - delay1;
    wire signed [WIDTH-1:0] comb2 = comb1 - delay2;
    wire signed [WIDTH-1:0] comb3 = comb2 - delay3;
    wire [6:0] scale_shift = 3*active_log2;
    wire signed [WIDTH-1:0] half_unit = active_log2 == 0 ? 0 :
        $signed({{(WIDTH-1){1'b0}},1'b1}) << (scale_shift-1'b1);
    wire signed [WIDTH-1:0] rounded = comb3 + half_unit - (comb3[WIDTH-1] ? 1'b1 : 1'b0);
    wire signed [WIDTH-1:0] normalized = rounded >>> scale_shift;

    // 积分器采用模2^WIDTH算术；正常回绕不是溢出故障，梳状差分恢复有效结果。
    // 修改抽取率时必须清滤波历史并丢弃三个输出区间，避免瞬态混入记录。
    always @(posedge sample_clk) begin
        if (!aresetn || clear || configure) begin
            integrator1 <= 0; integrator2 <= 0; integrator3 <= 0;
            delay1 <= 0; delay2 <= 0; delay3 <= 0;
            counter <= 0; warmup <= 0; interval_overrange <= 0; overrange_history <= 0;
            interval_reference_valid <= 1; interval_tag <= input_tag;
            output_valid <= 0; output_code <= 0; output_overrange <= 0;
            output_phase <= 0; output_tag <= 0; output_reference_valid <= 0;
            if (!aresetn) begin active_log2 <= 0; config_error <= 0; end
            else if (configure) begin
                config_error <= config_log2 > MAX_LOG2;
                if (config_log2 <= MAX_LOG2) active_log2 <= config_log2;
            end
        end else begin
            output_valid <= 0;
            if (!config_error && active_log2 == 0) begin
                // R=1逐点直通，保持原始码，不施加CIC延迟或舍入。
                output_code <= input_code; output_overrange <= input_overrange;
                output_valid <= 1; output_phase <= input_phase;
                output_tag <= input_tag; output_reference_valid <= input_reference_valid;
            end else if (!config_error && (!input_reference_valid || input_tag != interval_tag)) begin
                // 旧配置的滤波历史也会影响后续两段输出；换配置后重新预热，不能只标坏当前一点。
                integrator1 <= 0; integrator2 <= 0; integrator3 <= 0;
                delay1 <= 0; delay2 <= 0; delay3 <= 0;
                counter <= 0; warmup <= 0; overrange_history <= 0;
                interval_overrange <= 0; interval_reference_valid <= 1; interval_tag <= input_tag;
            end else if (!config_error) begin
                integrator1 <= next1; integrator2 <= next2; integrator3 <= next3;
                interval_overrange <= interval_overrange || input_overrange;
                interval_reference_valid <= interval_reference_valid && input_reference_valid && input_tag == interval_tag;
                if (counter == ratio-1'b1) begin
                    counter <= 0;
                    delay1 <= next3; delay2 <= comb1; delay3 <= comb2;
                    overrange_history <= {overrange_history[0], interval_overrange || input_overrange};
                    interval_overrange <= 0; interval_reference_valid <= 1;
                    interval_tag <= input_tag;
                    if (warmup < 3) warmup <= warmup + 1'b1;
                    else begin
                        output_code <= normalized < -2048 ? 12'sh800 :
                                       normalized > 2047 ? 12'sd2047 : normalized[11:0];
                        output_overrange <= (|overrange_history) || interval_overrange || input_overrange || normalized < -2048 || normalized > 2047;
                        output_valid <= 1; output_phase <= input_phase;
                        output_tag <= input_tag;
                        output_reference_valid <= interval_reference_valid && input_reference_valid && input_tag == interval_tag;
                    end
                end else counter <= counter + 1'b1;
            end
        end
    end
endmodule
