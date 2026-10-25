`timescale 1ns / 1ps

// 一个在途数据快照的跨时钟邮箱，用于控制参数，不用于连续 ADC 样点。
// 两域必须共用 arstn；复位丢弃在途请求，各域异步置位、同步释放。
// 综合后必须约束 src_data_hold 到 dst_data 的最大纯数据路径延迟。
module fdw_cdc_mailbox #(
    parameter integer WIDTH = 32
) (
    input  wire             src_clk,
    input  wire             dst_clk,
    input  wire             arstn,
    input  wire             src_valid,
    output wire             src_ready,
    input  wire [WIDTH-1:0] src_data,
    output reg              dst_valid,
    input  wire             dst_ready,
    output reg [WIDTH-1:0] dst_data
);
    (* ASYNC_REG = "TRUE", SHREG_EXTRACT = "NO" *) reg [1:0] src_reset_release;
    (* ASYNC_REG = "TRUE", SHREG_EXTRACT = "NO" *) reg [1:0] dst_reset_release;
    (* ASYNC_REG = "TRUE", SHREG_EXTRACT = "NO" *) reg [1:0] request_sync;
    (* ASYNC_REG = "TRUE", SHREG_EXTRACT = "NO" *) reg [1:0] acknowledge_sync;
    reg request_toggle;
    reg acknowledge_toggle;
    reg [WIDTH-1:0] src_data_hold;

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

    // 收到消费确认前，保持整份快照。上游被接受后可自由改变 src_data。
    assign src_ready = arstn && src_reset_release[1] &&
                       (request_toggle == acknowledge_sync[1]);
    always @(posedge src_clk or negedge arstn) begin
        if (!arstn) begin
            request_toggle <= 0;
            src_data_hold <= 0;
        end else if (!src_reset_release[1]) begin
            request_toggle <= 0;
        end else if (src_valid && src_ready) begin
            src_data_hold <= src_data;
            request_toggle <= !request_toggle;
        end
    end

    // 只同步请求位；数据在请求跨域期间保持不变，由目的域一次捕获。
    // dst_valid 背压期间数据保持，只有实际消费才返回确认。
    always @(posedge dst_clk or negedge arstn) begin
        if (!arstn) begin
            acknowledge_toggle <= 0;
            dst_valid <= 0;
            dst_data <= 0;
        end else if (!dst_reset_release[1]) begin
            acknowledge_toggle <= 0;
            dst_valid <= 0;
        end else if (dst_valid) begin
            if (dst_ready) begin
                acknowledge_toggle <= request_sync[1];
                dst_valid <= 0;
            end
        end else if (request_sync[1] != acknowledge_toggle) begin
            dst_data <= src_data_hold;
            dst_valid <= 1;
        end
    end
endmodule
