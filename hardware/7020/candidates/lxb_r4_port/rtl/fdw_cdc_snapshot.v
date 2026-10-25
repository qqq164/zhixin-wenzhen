`timescale 1ns / 1ps

// 控制域请求一次状态快照，采样域同拍捕获整份数据，返回时保持到实际消费。
// 用于帧号、点数、状态等元数据，不逐位同步多位计数，也不是连续数据 FIFO。
module fdw_cdc_snapshot #(
    parameter integer WIDTH = 32
) (
    input  wire             control_clk,
    input  wire             sample_clk,
    input  wire             arstn,
    input  wire             request_valid,
    output wire             request_ready,
    input  wire [WIDTH-1:0] local_data,
    output reg              snapshot_taken,
    output wire             response_valid,
    input  wire             response_ready,
    output wire [WIDTH-1:0] response_data
);
    (* ASYNC_REG = "TRUE", SHREG_EXTRACT = "NO" *) reg [1:0] control_reset_release;
    (* ASYNC_REG = "TRUE", SHREG_EXTRACT = "NO" *) reg [1:0] sample_reset_release;
    reg request_inflight;
    reg reply_pending;
    reg [WIDTH-1:0] snapshot_hold;
    wire event_ready;
    wire snapshot_event;
    wire reply_ready;

    always @(posedge control_clk or negedge arstn) begin
        if (!arstn) control_reset_release <= 0;
        else control_reset_release <= {control_reset_release[0], 1'b1};
    end
    always @(posedge sample_clk or negedge arstn) begin
        if (!arstn) sample_reset_release <= 0;
        else sample_reset_release <= {sample_reset_release[0], 1'b1};
    end
    assign request_ready = arstn && control_reset_release[1] && !request_inflight && event_ready;
    fdw_event_cdc requests (
        .src_clk(control_clk), .dst_clk(sample_clk), .arstn(arstn),
        .src_valid(request_valid && !request_inflight && control_reset_release[1]),
        .src_ready(event_ready), .dst_pulse(snapshot_event)
    );
    fdw_cdc_mailbox #(.WIDTH(WIDTH)) replies (
        .src_clk(sample_clk), .dst_clk(control_clk), .arstn(arstn),
        .src_valid(reply_pending), .src_ready(reply_ready), .src_data(snapshot_hold),
        .dst_valid(response_valid), .dst_ready(response_ready), .dst_data(response_data)
    );
    always @(posedge control_clk or negedge arstn) begin
        if (!arstn) request_inflight <= 0;
        else if (!control_reset_release[1]) request_inflight <= 0;
        else begin
            if (request_valid && request_ready) request_inflight <= 1;
            if (response_valid && response_ready) request_inflight <= 0;
        end
    end
    always @(posedge sample_clk or negedge arstn) begin
        if (!arstn) begin
            reply_pending <= 0;
            snapshot_hold <= 0;
            snapshot_taken <= 0;
        end else if (!sample_reset_release[1]) begin
            reply_pending <= 0;
            snapshot_taken <= 0;
        end else begin
            snapshot_taken <= 0;
            if (reply_pending && reply_ready) reply_pending <= 0;
            if (snapshot_event) begin
                snapshot_hold <= local_data;
                reply_pending <= 1;
                snapshot_taken <= 1;
            end
        end
    end
endmodule
