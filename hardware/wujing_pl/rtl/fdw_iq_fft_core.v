`timescale 1ns/1ps
// IQ和FFT共享同一有效样本握手，只有两者都接收时才消费输入。
// resetn为本时钟域已同步的低有效复位，阵列控制采用同步复位以免异步改变BRAM地址。
module fdw_iq_fft_core (
    input wire clk, input wire resetn, input wire clear, input wire start,
    input wire [31:0] cfg_ftw, input wire [31:0] cfg_phase,
    input wire [31:0] cfg_frame_id, input wire [31:0] cfg_flags,
    input wire [323:0] cfg_inverse_q30,
    input wire [15:0] s_data, input wire s_valid, input wire s_last,
    output wire s_ready, output wire busy, output wire result_valid,
    output wire [31:0] result_frame_id, output reg [31:0] result_flags,
    output wire signed [31:0] fit_dc_q16, output wire signed [31:0] cosine_q16,
    output wire signed [31:0] sine_q16, output wire signed [31:0] sum_x,
    output wire signed [47:0] sum_xc_q16, output wire signed [47:0] sum_xs_q16,
    output wire [47:0] sum_xx,
    output wire length_error, output wire format_error, output wire config_error, output wire overflow,
    output reg [31:0] saturation_count,
    input wire fft_read_request, input wire [12:0] fft_read_index,
    output wire fft_read_valid, output wire signed [31:0] fft_real_q12,
    output wire signed [31:0] fft_imag_q12
);
    wire iq_ready,fft_ready,iq_busy,fft_busy,iq_done,fft_done;
    wire iq_length,fft_length,iq_overflow,fft_overflow;
    wire [31:0] fft_frame_id;
    wire signed [31:0] fft_sum;
    wire accepted=s_valid&&s_ready;
    wire valid_ftw=cfg_ftw>=32'h00100000&&cfg_ftw<32'h80000000;
    wire signed [15:0] decoded_x={{4{s_data[11]}},s_data[11:0]};
    assign s_ready=iq_ready&&fft_ready;
    assign busy=iq_busy||fft_busy;
    assign result_valid=iq_done&&(fft_done||config_error);
    assign length_error=iq_length||fft_length;
    assign overflow=iq_overflow||fft_overflow;
    always @(posedge clk) begin
        if(!resetn) begin result_flags<=0; saturation_count<=0; end
        else if(clear) saturation_count<=0;
        else begin
            if(start&&!busy&&!result_valid) begin result_flags<=cfg_flags; saturation_count<=0; end
            if(accepted&&(decoded_x==-2048||decoded_x==2047)) saturation_count<=saturation_count+1'b1;
        end
    end
    fdw_iq_lsq_axis iq (
        .clk(clk),.resetn(resetn),.clear(clear),.start(start),.cfg_ftw(cfg_ftw),.cfg_phase(cfg_phase),
        .cfg_frame_id(cfg_frame_id),.cfg_inverse_q30(cfg_inverse_q30),
        .s_data(decoded_x),.s_valid(accepted),.s_last(s_last),.s_ready(iq_ready),
        .busy(iq_busy),.result_valid(iq_done),.result_frame_id(result_frame_id),
        .fit_dc_q16(fit_dc_q16),.cosine_q16(cosine_q16),.sine_q16(sine_q16),
        .sum_x(sum_x),.sum_xc_q16(sum_xc_q16),.sum_xs_q16(sum_xs_q16),.sum_xx(sum_xx),
        .length_error(iq_length),.config_error(config_error),.overflow(iq_overflow)
    );
    fdw_fft8192_hann fft (
        .clk(clk),.resetn(resetn),.clear(clear),.start(start&&valid_ftw),.cfg_frame_id(cfg_frame_id),
        .s_data(s_data),.s_valid(accepted),.s_last(s_last),.s_ready(fft_ready),.busy(fft_busy),
        .result_valid(fft_done),.result_frame_id(fft_frame_id),.sum_x(fft_sum),
        .length_error(fft_length),.format_error(format_error),.overflow(fft_overflow),
        .read_request(fft_read_request),.read_index(fft_read_index),.read_valid(fft_read_valid),
        .read_real_q12(fft_real_q12),.read_imag_q12(fft_imag_q12)
    );
endmodule
