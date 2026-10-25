`timescale 1ns/1ps
// 固定8192点：去原始均值、对称Hann窗、负指数FFT。每级除以2，输出为FFT/N的Q12复数。
module fdw_fft8192_hann #(
    parameter HANN_FILE="fft_hann_q17.mem",
    parameter TWIDDLE_FILE="fft_twiddle_q18.mem"
) (
    input wire clk, input wire resetn, input wire clear, input wire start,
    input wire [31:0] cfg_frame_id,
    input wire [15:0] s_data, input wire s_valid, input wire s_last,
    output wire s_ready, output reg busy, output reg result_valid,
    output reg [31:0] result_frame_id,
    output reg signed [31:0] sum_x,
    output reg length_error, output reg format_error, output reg overflow,
    input wire read_request, input wire [12:0] read_index,
    output reg read_valid, output reg signed [31:0] read_real_q12,
    output reg signed [31:0] read_imag_q12
);
    localparam IDLE=0,CAPTURE=1,PREAD=2,PMUL=3,PWRITE=4,FREAD=5,
               FMUL=6,FADD=7,FBUTTER=8,FWRITE=9,FNEXT=10,HOLD=11;
    reg [3:0] state;
    reg [12:0] sample_index, prep_index, block_base, offset;
    reg [13:0] half_size;
    reg [12:0] tw_step;
    reg [11:0] tw_index;
    wire [12:0] address_a=block_base+offset;
    wire [12:0] address_b=address_a|half_size[12:0];
    (* ram_style="block" *) reg signed [15:0] raw_mem [0:8191];
    (* rom_style="block" *) reg [17:0] hann_mem [0:8191];
    (* rom_style="block" *) reg [39:0] twiddle_mem [0:4095];
    initial begin
        $readmemh(HANN_FILE,hann_mem);
        $readmemh(TWIDDLE_FILE,twiddle_mem);
    end
    reg signed [15:0] raw_read;
    reg [17:0] hann_read;
    wire signed [31:0] centered_numerator=($signed(raw_read)<<<13)-sum_x;
    reg signed [50:0] window_product;
    wire signed [50:0] initial_q12=(window_product+51'sd131072)>>>18;
    wire signed [31:0] a_real,a_imag,b_real,b_imag;
    reg signed [19:0] tw_real,tw_imag;
    reg signed [51:0] mul_rr,mul_ii,mul_ri,mul_ir;
    reg signed [52:0] rotate_real,rotate_imag;
    wire signed [52:0] rotated_real_q12=(rotate_real+53'sd131072)>>>18;
    wire signed [52:0] rotated_imag_q12=(rotate_imag+53'sd131072)>>>18;
    reg signed [35:0] next_a_real,next_a_imag,next_b_real,next_b_imag;
    reg read_pending;
    wire signed [15:0] decoded_x={{4{s_data[11]}},s_data[11:0]};
    assign s_ready=state==CAPTURE;
    function [12:0] reverse_bits;
        input [12:0] value;
        integer bit_index;
        begin
            for(bit_index=0;bit_index<13;bit_index=bit_index+1)
                reverse_bits[bit_index]=value[12-bit_index];
        end
    endfunction

    // 存储器不整体复位；只有完整帧、全部预处理与蝶形完成后才允许读取频谱。
    // 每个写端口使用独立进程，避免将频谱存储器错误展开为寄存器。
    always @(posedge clk) begin
        if(s_valid&&s_ready) raw_mem[sample_index]<=decoded_x;
        if(state==PREAD) begin
            raw_read<=raw_mem[prep_index]; hann_read<=hann_mem[prep_index];
        end
        if(state==FREAD) begin
            tw_real<=twiddle_mem[tw_index][39:20];
            tw_imag<=twiddle_mem[tw_index][19:0];
        end
    end
    wire [12:0] port_a_address=state==PWRITE?reverse_bits(prep_index):(state==HOLD?read_index:address_a);
    wire port_a_write=state==PWRITE||state==FWRITE;
    wire port_a_enable=port_a_write||state==FREAD||(state==HOLD&&read_request&&!length_error);
    wire [63:0] port_a_data=state==PWRITE?{32'd0,initial_q12[31:0]}:{next_a_imag[31:0],next_a_real[31:0]};
    // 两个端口均采用同一种读优先模板，写拍的旧值输出不参与蝶形。
    fdw_fft_complex_bram memory (
        .clk(clk),.ena(port_a_enable),.wea(port_a_write),.addra(port_a_address),
        .dina(port_a_data),.douta({a_imag,a_real}),
        .enb(state==FREAD||state==FWRITE),.web(state==FWRITE),.addrb(address_b),
        .dinb({next_b_imag[31:0],next_b_real[31:0]}),.doutb({b_imag,b_real})
    );

    always @(posedge clk) begin
        if(!resetn) begin
            state<=IDLE; busy<=0; result_valid<=0; result_frame_id<=0;
            sample_index<=0; prep_index<=0; sum_x<=0;
            length_error<=0; format_error<=0; overflow<=0;
            block_base<=0; offset<=0; half_size<=1; tw_step<=4096; tw_index<=0;
            window_product<=0; mul_rr<=0; mul_ii<=0; mul_ri<=0; mul_ir<=0;
            rotate_real<=0; rotate_imag<=0;
            next_a_real<=0; next_a_imag<=0; next_b_real<=0; next_b_imag<=0;
            read_pending<=0; read_valid<=0; read_real_q12<=0; read_imag_q12<=0;
        end else if(clear) begin
            state<=IDLE; busy<=0; result_valid<=0; sum_x<=0;
            length_error<=0; format_error<=0; overflow<=0; read_pending<=0; read_valid<=0;
        end else begin
            read_pending<=state==HOLD&&read_request&&!length_error;
            read_valid<=read_pending&&state==HOLD&&!length_error;
            if(read_pending&&state==HOLD) begin read_real_q12<=a_real; read_imag_q12<=a_imag; end
            case(state)
                IDLE: if(start) begin
                    busy<=1; result_valid<=0; result_frame_id<=cfg_frame_id;
                    sample_index<=0; sum_x<=0; length_error<=0; format_error<=0; overflow<=0;
                    state<=CAPTURE;
                end
                CAPTURE: if(s_valid&&s_ready) begin
                    sum_x<=sum_x+decoded_x;
                    if(s_data!=decoded_x) format_error<=1;
                    if(s_last||sample_index==8191) begin
                        if(!(s_last&&sample_index==8191)) begin
                            length_error<=1; busy<=0; result_valid<=1; state<=HOLD;
                        end else begin prep_index<=0; state<=PREAD; end
                    end else sample_index<=sample_index+1'b1;
                end
                PREAD: state<=PMUL;
                PMUL: begin window_product<=centered_numerator*$signed({1'b0,hann_read}); state<=PWRITE; end
                PWRITE: if(prep_index==8191) begin
                    block_base<=0; offset<=0; half_size<=1; tw_step<=4096; tw_index<=0; state<=FREAD;
                end else begin prep_index<=prep_index+1'b1; state<=PREAD; end
                FREAD: state<=FMUL;
                FMUL: begin
                    mul_rr<=b_real*tw_real; mul_ii<=b_imag*tw_imag;
                    mul_ri<=b_real*tw_imag; mul_ir<=b_imag*tw_real;
                    state<=FADD;
                end
                FADD: begin
                    rotate_real<=$signed({mul_rr[51],mul_rr})-$signed({mul_ii[51],mul_ii});
                    rotate_imag<=$signed({mul_ri[51],mul_ri})+$signed({mul_ir[51],mul_ir});
                    state<=FBUTTER;
                end
                FBUTTER: begin
                    // 每级缩放一次，保留Q12；加半个最低位后右移，平局向正无穷舍入。
                    next_a_real<=($signed(a_real)+rotated_real_q12+54'sd1)>>>1;
                    next_a_imag<=($signed(a_imag)+rotated_imag_q12+54'sd1)>>>1;
                    next_b_real<=($signed(a_real)-rotated_real_q12+54'sd1)>>>1;
                    next_b_imag<=($signed(a_imag)-rotated_imag_q12+54'sd1)>>>1;
                    state<=FWRITE;
                end
                FWRITE: begin
                    if(next_a_real>36'sd2147483647||next_a_real< -36'sd2147483648||
                       next_a_imag>36'sd2147483647||next_a_imag< -36'sd2147483648||
                       next_b_real>36'sd2147483647||next_b_real< -36'sd2147483648||
                       next_b_imag>36'sd2147483647||next_b_imag< -36'sd2147483648) overflow<=1;
                    state<=FNEXT;
                end
                FNEXT: begin
                    if({1'b0,offset}+1==half_size) begin
                        offset<=0; tw_index<=0;
                        if({1'b0,block_base}+(half_size<<1)==8192) begin
                            if(half_size==4096) begin busy<=0; result_valid<=1; state<=HOLD; end
                            else begin block_base<=0; half_size<=half_size<<1; tw_step<=tw_step>>1; state<=FREAD; end
                        end else begin block_base<=block_base+(half_size<<1); state<=FREAD; end
                    end else begin offset<=offset+1'b1; tw_index<=tw_index+tw_step; state<=FREAD; end
                end
                HOLD: begin end
                default: state<=IDLE;
            endcase
        end
    end
endmodule

// 8192个复数，每端口同步读取；不复位存储阵列，避免破坏BRAM推断。
module fdw_fft_complex_bram (
    input wire clk,input wire ena,input wire wea,input wire [12:0] addra,
    input wire [63:0] dina,output reg [63:0] douta,
    input wire enb,input wire web,input wire [12:0] addrb,
    input wire [63:0] dinb,output reg [63:0] doutb
);
    (* ram_style="block" *) reg [63:0] memory [0:8191];
    always @(posedge clk) if(ena) begin
        if(wea) memory[addra]<=dina;
        douta<=memory[addra];
    end
    always @(posedge clk) if(enb) begin
        if(web) memory[addrb]<=dinb;
        doutb<=memory[addrb];
    end
endmodule
