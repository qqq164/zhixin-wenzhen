`timescale 1ns / 1ps

// 单请求逐字诊断读回：索引到采样域，数据／错误返回控制域。
// 本模块不是 DMA，不绑定帧号；上层须串行化重新装载，或另加帧身份检查。
module fdw_cdc_readback #(
    parameter integer DATA_WIDTH = 16,
    parameter integer REQUEST_WIDTH = 32
) (
    input  wire                  control_clk,
    input  wire                  sample_clk,
    input  wire                  arstn,
    input  wire                  request_valid,
    output wire                  request_ready,
    input  wire [REQUEST_WIDTH-1:0] request_index,
    output wire                  response_valid,
    input  wire                  response_ready,
    output wire                  response_error,
    output wire [DATA_WIDTH-1:0] response_data,
    output wire                  local_rd_request,
    output wire [REQUEST_WIDTH-1:0] local_rd_index,
    input  wire                  local_rd_valid,
    input  wire                  local_rd_error,
    input  wire [DATA_WIDTH-1:0] local_rd_data
);
    (* ASYNC_REG = "TRUE", SHREG_EXTRACT = "NO" *) reg [1:0] control_reset_release;
    (* ASYNC_REG = "TRUE", SHREG_EXTRACT = "NO" *) reg [1:0] sample_reset_release;
    reg request_inflight;
    reg awaiting_local;
    reg reply_pending;
    reg [DATA_WIDTH:0] reply_hold;
    wire request_mail_ready;
    wire request_local_valid;
    wire request_local_ready;
    wire reply_mail_ready;
    wire [DATA_WIDTH:0] reply_word;

    always @(posedge control_clk or negedge arstn) begin
        if (!arstn) control_reset_release <= 0;
        else control_reset_release <= {control_reset_release[0], 1'b1};
    end
    always @(posedge sample_clk or negedge arstn) begin
        if (!arstn) sample_reset_release <= 0;
        else sample_reset_release <= {sample_reset_release[0], 1'b1};
    end

    assign request_ready = arstn && control_reset_release[1] &&
                           !request_inflight && request_mail_ready;
    assign response_error = reply_word[DATA_WIDTH];
    assign response_data = reply_word[DATA_WIDTH-1:0];
    assign request_local_ready = arstn && sample_reset_release[1] &&
                                 !awaiting_local && !reply_pending && reply_mail_ready;
    assign local_rd_request = request_local_valid && request_local_ready;

    fdw_cdc_mailbox #(.WIDTH(REQUEST_WIDTH)) requests (
        .src_clk(control_clk), .dst_clk(sample_clk), .arstn(arstn),
        .src_valid(request_valid && !request_inflight && control_reset_release[1]),
        .src_ready(request_mail_ready), .src_data(request_index),
        .dst_valid(request_local_valid), .dst_ready(request_local_ready),
        .dst_data(local_rd_index)
    );
    fdw_cdc_mailbox #(.WIDTH(DATA_WIDTH + 1)) replies (
        .src_clk(sample_clk), .dst_clk(control_clk), .arstn(arstn),
        .src_valid(reply_pending), .src_ready(reply_mail_ready), .src_data(reply_hold),
        .dst_valid(response_valid), .dst_ready(response_ready), .dst_data(reply_word)
    );

    // 直到上游实际消费响应前，不接受另一个读请求。
    always @(posedge control_clk or negedge arstn) begin
        if (!arstn) request_inflight <= 0;
        else if (!control_reset_release[1]) request_inflight <= 0;
        else begin
            if (request_valid && request_ready) request_inflight <= 1;
            if (response_valid && response_ready) request_inflight <= 0;
        end
    end

    // 本地读端必须返回一次 valid，延迟至少一拍；等待期间不会重复发请求。
    // 错误响应的字清零，不把上一份缓存数据当作本次有效读数。
    always @(posedge sample_clk or negedge arstn) begin
        if (!arstn) begin
            awaiting_local <= 0;
            reply_pending <= 0;
            reply_hold <= 0;
        end else if (!sample_reset_release[1]) begin
            awaiting_local <= 0;
            reply_pending <= 0;
        end else begin
            if (local_rd_request) awaiting_local <= 1;
            if (awaiting_local && local_rd_valid) begin
                reply_hold <= {local_rd_error, local_rd_error ? {DATA_WIDTH{1'b0}} : local_rd_data};
                reply_pending <= 1;
                awaiting_local <= 0;
            end
            if (reply_pending && reply_mail_ready) reply_pending <= 0;
        end
    end
endmodule
