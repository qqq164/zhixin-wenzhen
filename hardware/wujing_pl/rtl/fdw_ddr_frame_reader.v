`timescale 1ns/1ps
// 只读一帧：256个64字节块，完整32位地址，小端16位样本；每次仅一个DDR请求在途。
// GP和HP使用共同异步复位源，各域独立同步释放。取消/超时仍排空已发出的请求。
module fdw_ddr_frame_reader #(
    parameter MAX_WAIT_CYCLES=1000000
) (
    input wire gp_clk,input wire hp_clk,input wire resetn,
    input wire gp_start,input wire gp_cancel,input wire [31:0] cfg_base,
    output wire gp_ready,output reg gp_busy,output reg gp_error,
    output wire [15:0] s_data,output wire s_valid,input wire s_ready,output wire s_last,
    output reg [31:0] araddr,output wire arvalid,input wire arready,
    input wire [511:0] rdata,input wire rvalid,output wire rready
);
    (* ASYNC_REG="TRUE" *) reg [1:0] gp_reset_pipe,hp_reset_pipe;
    always @(posedge gp_clk or negedge resetn)
        if(!resetn) gp_reset_pipe<=0; else gp_reset_pipe<={gp_reset_pipe[0],1'b1};
    always @(posedge hp_clk or negedge resetn)
        if(!resetn) hp_reset_pipe<=0; else hp_reset_pipe<={hp_reset_pipe[0],1'b1};
    wire gp_resetn=gp_reset_pipe[1],hp_resetn=hp_reset_pipe[1];
    reg cmd_toggle,cancel_hold,ack_toggle;
    reg [31:0] cmd_base_hold;
    reg cmd_ack,data_toggle,fault_toggle;
    reg [511:0] payload_hold;
    localparam H_IDLE=0,H_LATCH=1,H_REQUEST=2,H_RESPONSE=3,H_ACK=4,H_END=5;
    reg [2:0] hp_state;
    reg [7:0] block_index;
    reg [31:0] wait_count;
    reg abort_pending;
    (* ASYNC_REG="TRUE" *) reg cmd_sync1,cmd_sync2,cancel_sync1,cancel_sync2;
    (* ASYNC_REG="TRUE" *) reg ack_sync1,ack_sync2;
    (* ASYNC_REG="TRUE" *) reg data_sync1,data_sync2,cmd_ack_sync1,cmd_ack_sync2;
    (* ASYNC_REG="TRUE" *) reg idle_sync1,idle_sync2,fault_sync1,fault_sync2;
    always @(posedge hp_clk) begin
        if(!hp_resetn) begin cmd_sync1<=0;cmd_sync2<=0;cancel_sync1<=0;cancel_sync2<=0;ack_sync1<=0;ack_sync2<=0;end
        else begin
            cmd_sync1<=cmd_toggle;cmd_sync2<=cmd_sync1;
            cancel_sync1<=cancel_hold;cancel_sync2<=cancel_sync1;
            ack_sync1<=ack_toggle;ack_sync2<=ack_sync1;
        end
    end
    always @(posedge gp_clk) begin
        if(!gp_resetn) begin
            data_sync1<=0;data_sync2<=0;cmd_ack_sync1<=0;cmd_ack_sync2<=0;
            idle_sync1<=0;idle_sync2<=0;fault_sync1<=0;fault_sync2<=0;
        end else begin
            data_sync1<=data_toggle;data_sync2<=data_sync1;
            cmd_ack_sync1<=cmd_ack;cmd_ack_sync2<=cmd_ack_sync1;
            idle_sync1<=hp_state==H_IDLE;idle_sync2<=idle_sync1;
            fault_sync1<=fault_toggle;fault_sync2<=fault_sync1;
        end
    end
    // 请求VALID一旦发出即保持到READY，即使取消也不撤掉一个尚未握手的请求。
    assign arvalid=hp_state==H_REQUEST;
    assign rready=hp_state==H_RESPONSE;
    always @(posedge hp_clk) begin
        if(!hp_resetn) begin
            hp_state<=H_IDLE;cmd_ack<=0;data_toggle<=0;payload_hold<=0;
            fault_toggle<=0;araddr<=0;block_index<=0;wait_count<=0;abort_pending<=0;
        end else begin
            if(cancel_sync2) abort_pending<=1;
            if(hp_state==H_REQUEST||hp_state==H_RESPONSE) begin
                if(wait_count<MAX_WAIT_CYCLES) wait_count<=wait_count+1'b1;
                if(wait_count==MAX_WAIT_CYCLES-1&&!abort_pending&&!cancel_sync2) begin
                    fault_toggle<=~fault_toggle;abort_pending<=1;
                end
            end else wait_count<=0;
            case(hp_state)
                H_IDLE: if(cmd_sync2!=cmd_ack) hp_state<=H_LATCH;
                H_LATCH: begin
                    // 配置在命令往返握手期间保持稳定，额外等待一拍后采集多位总线。
                    araddr<=cmd_base_hold;block_index<=0;cmd_ack<=cmd_sync2;
                    abort_pending<=cancel_sync2;
                    hp_state<=cancel_sync2?H_END:H_REQUEST;
                end
                H_REQUEST: if(arready) begin hp_state<=H_RESPONSE;wait_count<=0;end
                H_RESPONSE: if(rvalid) begin
                    if(abort_pending||cancel_sync2||wait_count==MAX_WAIT_CYCLES-1) hp_state<=H_END;
                    else begin payload_hold<=rdata;data_toggle<=~data_toggle;hp_state<=H_ACK;end
                end
                H_ACK: if(abort_pending||cancel_sync2) hp_state<=H_END;
                    else if(ack_sync2==data_toggle) begin
                        if(block_index==255) hp_state<=H_END;
                        else begin block_index<=block_index+1'b1;araddr<=araddr+32'd64;hp_state<=H_REQUEST;end
                    end
                H_END: hp_state<=H_IDLE;
                default: hp_state<=H_IDLE;
            endcase
        end
    end
    localparam G_WAIT=0,G_LATCH=1,G_SEND=2;
    reg [1:0] gp_state;
    reg [511:0] payload_gp;
    reg [4:0] word_index;
    reg [12:0] sample_index;
    reg fault_seen;
    assign gp_ready=gp_resetn&&!gp_busy&&idle_sync2&&cmd_ack_sync2==cmd_toggle&&data_sync2==ack_toggle&&!cancel_hold;
    assign s_data=payload_gp[word_index*16+:16];
    assign s_valid=gp_busy&&gp_state==G_SEND&&!cancel_hold&&!gp_cancel;
    assign s_last=sample_index==8191;
    always @(posedge gp_clk) begin
        if(!gp_resetn) begin
            cmd_toggle<=0;cmd_base_hold<=0;cancel_hold<=0;ack_toggle<=0;
            gp_state<=G_WAIT;gp_busy<=0;gp_error<=0;payload_gp<=0;
            word_index<=0;sample_index<=0;fault_seen<=0;
        end else begin
            if(gp_start&&gp_ready) begin
                if(cfg_base[5:0]!=0||cfg_base>32'hffffc000) gp_error<=1;
                else begin
                    cmd_base_hold<=cfg_base;cmd_toggle<=~cmd_toggle;gp_busy<=1;gp_error<=0;
                    gp_state<=G_WAIT;sample_index<=0;word_index<=0;
                end
            end
            if(fault_sync2!=fault_seen) begin fault_seen<=fault_sync2;gp_error<=1;cancel_hold<=1;end
            if(gp_cancel&&gp_busy) cancel_hold<=1;
            if(gp_cancel) gp_error<=0;
            if(cancel_hold||gp_cancel||(fault_sync2!=fault_seen)) begin
                gp_state<=G_WAIT;ack_toggle<=data_sync2;
            end else if(gp_busy) case(gp_state)
                G_WAIT: if(data_sync2!=ack_toggle) gp_state<=G_LATCH;
                G_LATCH: begin payload_gp<=payload_hold;word_index<=0;gp_state<=G_SEND;end
                G_SEND: if(s_valid&&s_ready) begin
                    sample_index<=sample_index+1'b1;
                    if(word_index==31) begin ack_toggle<=data_sync2;gp_state<=G_WAIT;end
                    else word_index<=word_index+1'b1;
                end
                default: gp_state<=G_WAIT;
            endcase
            // 只有命令已确认、HP已停止、邮箱已消耗时才允许缓冲区复用。
            if(gp_busy&&idle_sync2&&cmd_ack_sync2==cmd_toggle&&data_sync2==ack_toggle&&gp_state==G_WAIT) begin
                gp_busy<=0;cancel_hold<=0;
            end
        end
    end
endmodule
