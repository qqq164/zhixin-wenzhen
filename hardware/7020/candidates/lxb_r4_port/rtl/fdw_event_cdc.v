`timescale 1ns / 1ps

// 单个在途事件的跨时钟握手。共用复位异步置位、各域同步释放。
// 只传事件，不传多位参数或采样数据；目的域触发不等于源域的同时采样。
module fdw_event_cdc (
    input  wire src_clk,
    input  wire dst_clk,
    input  wire arstn,
    input  wire src_valid,
    output wire src_ready,
    output reg  dst_pulse
);
    (* ASYNC_REG = "TRUE", SHREG_EXTRACT = "NO" *) reg [1:0] src_reset_release;
    (* ASYNC_REG = "TRUE", SHREG_EXTRACT = "NO" *) reg [1:0] dst_reset_release;
    (* ASYNC_REG = "TRUE", SHREG_EXTRACT = "NO" *) reg [1:0] request_sync;
    (* ASYNC_REG = "TRUE", SHREG_EXTRACT = "NO" *) reg [1:0] acknowledge_sync;
    reg request_toggle;
    reg acknowledge_toggle;

    always @(posedge src_clk or negedge arstn) begin
        if (!arstn) src_reset_release <= 0;
        else src_reset_release <= {src_reset_release[0], 1'b1};
    end
    always @(posedge dst_clk or negedge arstn) begin
        if (!arstn) dst_reset_release <= 0;
        else dst_reset_release <= {dst_reset_release[0], 1'b1};
    end
    always @(posedge src_clk or negedge arstn) begin
        if (!arstn) acknowledge_sync <= 0;
        else acknowledge_sync <= {acknowledge_sync[0], acknowledge_toggle};
    end
    always @(posedge dst_clk or negedge arstn) begin
        if (!arstn) request_sync <= 0;
        else request_sync <= {request_sync[0], request_toggle};
    end

    // 在途请求收到目的域确认前持续背压，不能用源域窄脉冲直接喂两级同步。
    assign src_ready = arstn && src_reset_release[1] &&
                       (request_toggle == acknowledge_sync[1]);

    always @(posedge src_clk or negedge arstn) begin
        if (!arstn) request_toggle <= 0;
        else if (!src_reset_release[1]) request_toggle <= 0;
        else if (src_valid && src_ready) request_toggle <= !request_toggle;
    end

    always @(posedge dst_clk or negedge arstn) begin
        if (!arstn) begin
            acknowledge_toggle <= 0;
            dst_pulse <= 0;
        end else if (!dst_reset_release[1]) begin
            acknowledge_toggle <= 0;
            dst_pulse <= 0;
        end else begin
            dst_pulse <= 0;
            if (request_sync[1] != acknowledge_toggle) begin
                acknowledge_toggle <= request_sync[1];
                dst_pulse <= 1;
            end
        end
    end
endmodule
