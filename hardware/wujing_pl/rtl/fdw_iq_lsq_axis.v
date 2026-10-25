`timescale 1ns/1ps
// 将逐样本IQ相关及非整周期三元拟合投影放在PL，矩阵系数仅在换频时由CPU配置。
module fdw_iq_lsq_axis #(
    parameter SIN_FILE = "iq_sin_q16.mem"
) (
    input wire clk, input wire resetn, input wire clear, input wire start,
    input wire [31:0] cfg_ftw, input wire [31:0] cfg_phase, input wire [31:0] cfg_frame_id,
    input wire [323:0] cfg_inverse_q30,
    input wire signed [15:0] s_data, input wire s_valid, input wire s_last,
    output wire s_ready, output reg busy, output reg result_valid,
    output reg [31:0] result_frame_id,
    output reg signed [31:0] fit_dc_q16, output reg signed [31:0] cosine_q16,
    output reg signed [31:0] sine_q16,
    output reg signed [31:0] sum_x, output reg signed [47:0] sum_xc_q16,
    output reg signed [47:0] sum_xs_q16, output reg [47:0] sum_xx,
    output reg length_error, output reg config_error, output reg overflow
);
    localparam IDLE=0, CAPTURE=1, DRAIN=2, PICK=3, MULTIPLY=4, ACCUMULATE=5, PROJECT=6, HOLD=7;
    reg [3:0] state;
    reg [13:0] index;
    reg [31:0] phase, ftw;
    reg signed [35:0] inverse [0:8];
    (* rom_style="block" *) reg signed [17:0] sin_table [0:4095];
    initial $readmemh(SIN_FILE, sin_table);
    wire [31:0] rounded_phase = phase + 32'h00080000;
    wire [11:0] sin_address = rounded_phase[31:20];
    wire [11:0] cos_address = sin_address + 12'd1024;
    reg signed [17:0] sin_value, cos_value;
    reg signed [15:0] x_pipe;
    reg valid_pipe, last_pipe, product_valid, product_last;
    reg signed [15:0] product_x;
    reg signed [33:0] product_c, product_s;
    reg [31:0] product_xx;
    reg [1:0] row, column;
    reg signed [31:0] projection_input;
    reg signed [35:0] projection_coefficient;
    reg signed [67:0] projection_product;
    reg signed [69:0] projection_sum;
    wire signed [69:0] projected = (projection_sum + 70'sd536870912) >>> 30;
    integer k;
    assign s_ready = state == CAPTURE;

    // 查表、乘法、累加分拍，最后一点沿同一有效位流水线排空。
    always @(posedge clk) begin
        if (s_valid && s_ready) begin
            sin_value <= sin_table[sin_address];
            cos_value <= sin_table[cos_address];
        end
    end
    always @(posedge clk) begin
        if (!resetn) begin
            valid_pipe<=0; product_valid<=0; last_pipe<=0; product_last<=0;
            x_pipe<=0; product_x<=0; product_c<=0; product_s<=0; product_xx<=0;
        end else if (clear || (start && state==IDLE)) begin
            valid_pipe<=0; product_valid<=0; last_pipe<=0; product_last<=0;
        end else begin
            valid_pipe <= s_valid && s_ready;
            last_pipe <= s_last || index==8191;
            if (s_valid && s_ready) x_pipe<=s_data;
            product_valid<=valid_pipe; product_last<=last_pipe;
            if (valid_pipe) begin
                product_x<=x_pipe;
                product_c<=x_pipe*cos_value;
                product_s<=x_pipe*sin_value;
                product_xx<=x_pipe*x_pipe;
            end
        end
    end

    always @(posedge clk) begin
        if (!resetn) begin
            state<=IDLE; busy<=0; result_valid<=0; index<=0; phase<=0; ftw<=0;
            result_frame_id<=0; fit_dc_q16<=0; cosine_q16<=0; sine_q16<=0;
            sum_x<=0; sum_xc_q16<=0; sum_xs_q16<=0; sum_xx<=0;
            length_error<=0; config_error<=0; overflow<=0;
            row<=0; column<=0; projection_input<=0; projection_coefficient<=0;
            projection_product<=0; projection_sum<=0;
            for (k=0;k<9;k=k+1) inverse[k]<=0;
        end else if (clear) begin
            state<=IDLE; busy<=0; result_valid<=0; index<=0;
            sum_x<=0; sum_xc_q16<=0; sum_xs_q16<=0; sum_xx<=0;
            length_error<=0; config_error<=0; overflow<=0;
        end else begin
            if (product_valid && busy) begin
                sum_x<=sum_x+product_x;
                sum_xc_q16<=sum_xc_q16+product_c;
                sum_xs_q16<=sum_xs_q16+product_s;
                sum_xx<=sum_xx+product_xx;
            end
            case (state)
                IDLE: if (start) begin
                    result_frame_id<=cfg_frame_id; result_valid<=0;
                    sum_x<=0; sum_xc_q16<=0; sum_xs_q16<=0; sum_xx<=0;
                    index<=0; phase<=cfg_phase; ftw<=cfg_ftw;
                    length_error<=0; overflow<=0;
                    for (k=0;k<9;k=k+1) inverse[k]<=cfg_inverse_q30[k*36+:36];
                    if (cfg_ftw<32'h00100000 || cfg_ftw>=32'h80000000) begin
                        config_error<=1; busy<=0; result_valid<=1; state<=HOLD;
                    end else begin
                        config_error<=0; busy<=1; state<=CAPTURE;
                    end
                end
                CAPTURE: if (s_valid && s_ready) begin
                    phase<=phase+ftw;
                    index<=index+1'b1;
                    if (s_last || index==8191) begin
                        length_error<=!(s_last && index==8191);
                        state<=DRAIN;
                    end
                end
                DRAIN: if (product_valid && product_last) begin
                    // 下一拍才读取最终累加值，避免漏掉最后一个样本。
                    row<=0; column<=0; projection_sum<=0; state<=PICK;
                end
                PICK: begin
                    case (column)
                        0: projection_input<=sum_x<<<3;
                        1: projection_input<=sum_xc_q16>>>13;
                        2: projection_input<=sum_xs_q16>>>13;
                    endcase
                    projection_coefficient<=inverse[row*3+column];
                    state<=MULTIPLY;
                end
                MULTIPLY: begin
                    projection_product<=projection_input*projection_coefficient;
                    state<=ACCUMULATE;
                end
                ACCUMULATE: begin
                    projection_sum<=projection_sum+projection_product;
                    if (column==2) state<=PROJECT;
                    else begin column<=column+1'b1; state<=PICK; end
                end
                PROJECT: begin
                    if (projected>70'sd2147483647 || projected< -70'sd2147483648) overflow<=1;
                    case (row)
                        0: fit_dc_q16<=projected[31:0];
                        1: cosine_q16<=projected[31:0];
                        2: sine_q16<=projected[31:0];
                    endcase
                    if (row==2) begin busy<=0; result_valid<=1; state<=HOLD; end
                    else begin row<=row+1'b1; column<=0; projection_sum<=0; state<=PICK; end
                end
                HOLD: begin end
                default: state<=IDLE;
            endcase
        end
    end
endmodule
