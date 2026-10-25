`timescale 1ns / 1ps

// R4保留原查询桥，增加经握手交付的空闲态抽取配置。
// bit31翻转请求；bit30配置操作，bit20:16为倍率指数，bit12:8仍为查询索引。
module fdw_phase_query_bridge(
    input wire control_clk,input wire sample_clk,
    input wire control_resetn,input wire sample_resetn,
    input wire [31:0] command_word,input wire [6:0] legacy_status,
    input wire [511:0] metadata_words,output wire [31:0] status_word,
    input wire capture_busy, output reg configure, output reg [4:0] config_log2
);
    wire arstn=control_resetn&&sample_resetn;
    reg initialized,acknowledged,pending_request,error;
    reg [1:0] state;
    reg [11:0] request_data;
    reg [15:0] value;
    wire request_ready,request_valid,response_ready,response_valid;
    wire [11:0] selected;
    wire [16:0] received;
    reg response_pending;
    reg [16:0] response_data;
    fdw_cdc_mailbox #(.WIDTH(12)) query_request(
        .src_clk(control_clk),.dst_clk(sample_clk),.arstn(arstn),
        .src_valid(state==1),.src_ready(request_ready),.src_data(request_data),
        .dst_valid(request_valid),.dst_ready(!response_pending),.dst_data(selected)
    );
    fdw_cdc_mailbox #(.WIDTH(17)) query_response(
        .src_clk(sample_clk),.dst_clk(control_clk),.arstn(arstn),
        .src_valid(response_pending),.src_ready(response_ready),.src_data(response_data),
        .dst_valid(response_valid),.dst_ready(state==2),.dst_data(received)
    );
    assign status_word={acknowledged,(state!=0),(initialized&&request_ready),error,
                        4'b0,value,1'b0,legacy_status};
    always @(posedge sample_clk or negedge arstn)begin
        if(!arstn)begin response_pending<=0;response_data<=0;configure<=0;config_log2<=0;end
        else begin
            configure<=0;
            if(response_pending&&response_ready)response_pending<=0;
            if(request_valid&&!response_pending)begin
                if(selected[11]) response_data<={1'b1,16'b0};
                else if(selected[10])begin
                    if(capture_busy || selected[9:5]>16) response_data<={1'b1,16'b0};
                    else begin
                        config_log2<=selected[9:5];configure<=1;
                        response_data<={1'b0,11'b0,selected[9:5]};
                    end
                end else response_data<={1'b0,metadata_words[selected[4:0]*16+:16]};
                response_pending<=1;
            end
        end
    end
    always @(posedge control_clk or negedge arstn)begin
        if(!arstn)begin
            initialized<=0;acknowledged<=0;pending_request<=0;error<=0;
            state<=0;request_data<=0;value<=0;
        end else if(!initialized)begin
            acknowledged<=command_word[31];initialized<=1;
        end else if(state==1)begin if(request_ready)state<=2;end
        else if(state==2)begin
            if(response_valid)begin
                acknowledged<=pending_request;error<=received[16];value<=received[15:0];state<=0;
            end
        end else if(command_word[31]!=acknowledged)begin
            request_data<={((command_word&32'h3fe0e0f8)!=0)||
                (!command_word[30]&&(|command_word[20:16]))||
                (command_word[30]&&((|command_word[12:8])||(|command_word[2:0]))),
                command_word[30],command_word[20:16],command_word[12:8]};
            pending_request<=command_word[31];state<=1;
        end
    end
endmodule
