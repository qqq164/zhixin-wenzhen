`timescale 1ns/1ps
// 接厂家用户寄存器与512位DDR口：计算在GP域，DDR读取在HP域；版本改变后旧驱动禁止启动。
module fdw_wujing_dsp_top (
    input wire [31:0] itf_ra_awaddr,input wire [31:0] itf_ra_awdata,input wire itf_ra_awvalid,output wire itf_ra_awready,
    input wire [31:0] itf_ra_araddr,input wire itf_ra_arvalid,output wire itf_ra_arready,
    output reg [31:0] itf_ra_rdata,output reg itf_ra_rvalid,input wire itf_ra_rready,
    output wire [31:0] itf_awaddr,output wire [511:0] itf_awdata,output wire itf_awvalid,input wire itf_awready,
    output wire [31:0] itf_araddr,output wire itf_arvalid,input wire itf_arready,
    input wire [511:0] itf_rdata,input wire itf_rvalid,output wire itf_rready,
    input wire clk,input wire ra_clk,input wire rst_n,input wire ra_rst_n
);
    wire common_resetn=rst_n&&ra_rst_n;
    (* ASYNC_REG="TRUE" *) reg [1:0] reset_pipe;
    always @(posedge ra_clk or negedge common_resetn)
        if(!common_resetn) reset_pipe<=0;else reset_pipe<={reset_pipe[0],1'b1};
    // 增加纯同步一拍，防止综合将异步复位链直接折入BRAM地址/使能组合逻辑。
    reg core_resetn=0;
    always @(posedge ra_clk) core_resetn<=reset_pipe[1];
    reg [31:0] cfg_base,cfg_id,cfg_flags,cfg_ftw,cfg_phase;
    reg [323:0] cfg_inverse;
    reg start_pulse,cancel_pulse,clear_pulse,bad_command,memory_error,reader_error_seen;
    wire reader_ready,reader_busy,reader_error,core_ready,core_busy,core_done;
    wire [15:0] sample_data;
    wire sample_valid,sample_last;
    wire [31:0] result_id,result_flags,saturation;
    wire signed [31:0] dc,cosine,sine,total;
    wire signed [47:0] corr_c,corr_s;
    wire [47:0] square;
    wire length_error,format_error,config_error,overflow;
    reg fft_request;
    reg [12:0] fft_index,fft_pending_index,fft_result_index;
    reg fft_pending,fft_result_valid;
    wire fft_valid;
    wire signed [31:0] fft_real,fft_imag;
    reg [31:0] fft_real_latched,fft_imag_latched;
    wire job_busy=reader_busy||core_busy||start_pulse;
    wire launch_ready=reader_ready&&!job_busy&&!core_done&&!cancel_pulse&&!clear_pulse&&!reader_error&&!memory_error;
    wire [17:0] write_offset=itf_ra_awaddr[17:0];
    wire [17:0] read_offset=itf_ra_araddr[17:0];
    assign itf_ra_awready=core_resetn;
    assign itf_ra_arready=core_resetn&&!itf_ra_rvalid;
    assign itf_awaddr=0;assign itf_awdata=0;assign itf_awvalid=0;
    fdw_ddr_frame_reader reader (
        .gp_clk(ra_clk),.hp_clk(clk),.resetn(common_resetn),.gp_start(start_pulse),
        .gp_cancel(cancel_pulse),.cfg_base(cfg_base),.gp_ready(reader_ready),
        .gp_busy(reader_busy),.gp_error(reader_error),.s_data(sample_data),.s_valid(sample_valid),
        .s_ready(core_ready),.s_last(sample_last),.araddr(itf_araddr),.arvalid(itf_arvalid),
        .arready(itf_arready),.rdata(itf_rdata),.rvalid(itf_rvalid),.rready(itf_rready)
    );
    fdw_iq_fft_core core (
        .clk(ra_clk),.resetn(core_resetn),.clear(clear_pulse||reader_error),.start(start_pulse),
        .cfg_ftw(cfg_ftw),.cfg_phase(cfg_phase),.cfg_frame_id(cfg_id),.cfg_flags(cfg_flags),.cfg_inverse_q30(cfg_inverse),
        .s_data(sample_data),.s_valid(sample_valid),.s_last(sample_last),.s_ready(core_ready),
        .busy(core_busy),.result_valid(core_done),.result_frame_id(result_id),.result_flags(result_flags),
        .fit_dc_q16(dc),.cosine_q16(cosine),.sine_q16(sine),.sum_x(total),.sum_xc_q16(corr_c),.sum_xs_q16(corr_s),
        .sum_xx(square),.length_error(length_error),.format_error(format_error),.config_error(config_error),
        .overflow(overflow),.saturation_count(saturation),.fft_read_request(fft_request),.fft_read_index(fft_index),
        .fft_read_valid(fft_valid),.fft_real_q12(fft_real),.fft_imag_q12(fft_imag)
    );
    integer coefficient_index;
    function [31:0] read_register(input [17:0] offset);
        begin
            case(offset)
                18'h004: read_register={22'd0,fft_result_valid,bad_command,memory_error,overflow,config_error,format_error,length_error,core_done,job_busy,launch_ready};
                18'h008: read_register=cfg_base;
                18'h00c: read_register=cfg_id;
                18'h010: read_register=cfg_flags;
                18'h014: read_register=cfg_ftw;
                18'h018: read_register=8192;
                18'h01c: read_register=cfg_phase;
                18'h020: read_register=dc;
                18'h024: read_register=cosine;
                18'h028: read_register=sine;
                18'h02c: read_register=total;
                18'h030: read_register=corr_c[31:0];
                18'h034: read_register={{16{corr_c[47]}},corr_c[47:32]};
                18'h038: read_register=corr_s[31:0];
                18'h03c: read_register={{16{corr_s[47]}},corr_s[47:32]};
                18'h040: read_register=square[31:0];
                18'h044: read_register={16'd0,square[47:32]};
                18'h048: read_register=saturation;
                18'h050: read_register=result_id;
                18'h054: read_register=result_flags;
                18'h060: read_register={19'd0,fft_index};
                18'h064: read_register=fft_real_latched;
                18'h068: read_register=fft_imag_latched;
                18'h06c: read_register={19'd0,fft_result_index};
                18'h080: read_register={31'd0,core_done};
                18'h0c0: read_register=32'h20261014;
                default: read_register=0;
            endcase
        end
    endfunction
    always @(posedge ra_clk) begin
        if(!core_resetn) begin
            cfg_base<=0;cfg_id<=0;cfg_flags<=0;cfg_ftw<=0;cfg_phase<=0;cfg_inverse<=0;
            start_pulse<=0;cancel_pulse<=0;clear_pulse<=0;bad_command<=0;memory_error<=0;reader_error_seen<=0;
            itf_ra_rvalid<=0;itf_ra_rdata<=0;
            fft_request<=0;fft_index<=0;fft_pending_index<=0;fft_result_index<=0;
            fft_pending<=0;fft_result_valid<=0;fft_real_latched<=0;fft_imag_latched<=0;
        end else begin
            start_pulse<=0;cancel_pulse<=0;clear_pulse<=0;fft_request<=0;
            reader_error_seen<=reader_error;
            if(reader_error&&!reader_error_seen) memory_error<=1;
            if(reader_error) begin fft_result_valid<=0;fft_pending<=0;end
            if(fft_valid&&fft_pending) begin
                fft_real_latched<=fft_real;fft_imag_latched<=fft_imag;fft_result_index<=fft_pending_index;
                fft_result_valid<=1;fft_pending<=0;
            end
            // 返回数据在CPU反压期间保持，不能从活跃配置组合直连到读返回口。
            if(itf_ra_rvalid&&itf_ra_rready) itf_ra_rvalid<=0;
            if(itf_ra_arvalid&&itf_ra_arready) begin
                itf_ra_rdata<=read_register(read_offset);itf_ra_rvalid<=1;
            end
            if(itf_ra_awvalid&&itf_ra_awready) begin
                if(write_offset==0) begin
                    if(itf_ra_awdata[1]) begin
                        cancel_pulse<=1;clear_pulse<=1;bad_command<=0;memory_error<=0;
                        fft_result_valid<=0;fft_pending<=0;
                    end else if(itf_ra_awdata[0]) begin
                        if(launch_ready&&cfg_base[5:0]==0&&cfg_base<=32'hffffc000&&cfg_ftw>=32'h00100000&&cfg_ftw<32'h80000000) begin
                            start_pulse<=1;bad_command<=0;memory_error<=0;fft_result_valid<=0;
                        end else bad_command<=1;
                    end
                end else if(write_offset==18'h060) begin
                    if(core_done&&!length_error&&!format_error&&!config_error&&!overflow&&!fft_pending&&itf_ra_awdata<8192) begin
                        fft_index<=itf_ra_awdata[12:0];fft_pending_index<=itf_ra_awdata[12:0];
                        fft_request<=1;fft_pending<=1;fft_result_valid<=0;
                    end else bad_command<=1;
                end else if(launch_ready) begin
                    case(write_offset)
                        18'h008:cfg_base<=itf_ra_awdata;
                        18'h00c:cfg_id<=itf_ra_awdata;
                        18'h010:cfg_flags<=itf_ra_awdata;
                        18'h014:cfg_ftw<=itf_ra_awdata;
                        18'h01c:cfg_phase<=itf_ra_awdata;
                        default: begin
                            if(write_offset>=18'h100&&write_offset<18'h148&&write_offset[1:0]==0) begin
                                coefficient_index=(write_offset-18'h100)>>3;
                                if(write_offset[2]) cfg_inverse[coefficient_index*36+32+:4]<=itf_ra_awdata[3:0];
                                else cfg_inverse[coefficient_index*36+:32]<=itf_ra_awdata;
                            end else bad_command<=1;
                        end
                    endcase
                end else bad_command<=1;
            end
        end
    end
endmodule
