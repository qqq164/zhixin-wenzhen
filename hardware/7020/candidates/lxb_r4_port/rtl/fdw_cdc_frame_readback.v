`timescale 1ns / 1ps

// 把帧号和索引作为同一个快照跨域；读取开始和响应时都核对帧身份。
// 帧号由上层分配且在同一复位会话内不得复用；跨重启身份仍须协议层处理。
module fdw_cdc_frame_readback #(
    parameter integer DATA_WIDTH = 16
) (
    input  wire                  control_clk,
    input  wire                  sample_clk,
    input  wire                  arstn,
    input  wire                  request_valid,
    output wire                  request_ready,
    input  wire [31:0]           request_record_id,
    input  wire [31:0]           request_index,
    output wire                  response_valid,
    input  wire                  response_ready,
    output wire                  response_error,
    output wire [DATA_WIDTH-1:0] response_data,
    input  wire [31:0]           local_record_id,
    input  wire                  local_record_valid,
    input  wire                  local_record_busy,
    output wire                  local_rd_request,
    output wire [31:0]           local_rd_index,
    input  wire                  local_rd_valid,
    input  wire                  local_rd_error,
    input  wire [DATA_WIDTH-1:0] local_rd_data
);
    (* ASYNC_REG = "TRUE", SHREG_EXTRACT = "NO" *) reg [1:0] sample_reset_release;
    wire raw_request;
    wire [63:0] raw_query;
    reg waiting_local;
    reg [31:0] accepted_record_id;
    reg checked_valid;
    reg checked_error;
    reg [DATA_WIDTH-1:0] checked_data;
    wire request_matches = sample_reset_release[1] && local_record_valid && !local_record_busy &&
                           raw_query[63:32] == local_record_id;
    wire response_matches = local_record_valid && !local_record_busy &&
                            accepted_record_id == local_record_id;

    always @(posedge sample_clk or negedge arstn) begin
        if (!arstn) sample_reset_release <= 0;
        else sample_reset_release <= {sample_reset_release[0], 1'b1};
    end
    assign local_rd_request = raw_request && request_matches;
    assign local_rd_index = raw_query[31:0];

    fdw_cdc_readback #(.DATA_WIDTH(DATA_WIDTH), .REQUEST_WIDTH(64)) bridge (
        .control_clk(control_clk), .sample_clk(sample_clk), .arstn(arstn),
        .request_valid(request_valid), .request_ready(request_ready),
        .request_index({request_record_id, request_index}),
        .response_valid(response_valid), .response_ready(response_ready),
        .response_error(response_error), .response_data(response_data),
        .local_rd_request(raw_request), .local_rd_index(raw_query),
        .local_rd_valid(checked_valid), .local_rd_error(checked_error), .local_rd_data(checked_data)
    );

    always @(posedge sample_clk or negedge arstn) begin
        if (!arstn) begin
            waiting_local <= 0;
            accepted_record_id <= 0;
            checked_valid <= 0;
            checked_error <= 0;
            checked_data <= 0;
        end else if (!sample_reset_release[1]) begin
            waiting_local <= 0;
            checked_valid <= 0;
        end else begin
            checked_valid <= 0;
            if (raw_request) begin
                if (request_matches) begin
                    waiting_local <= 1;
                    accepted_record_id <= raw_query[63:32];
                end else begin
                    checked_valid <= 1;
                    checked_error <= 1;
                    checked_data <= 0;
                end
            end
            if (waiting_local && local_rd_valid) begin
                waiting_local <= 0;
                checked_valid <= 1;
                checked_error <= local_rd_error || !response_matches;
                checked_data <= (local_rd_error || !response_matches) ? {DATA_WIDTH{1'b0}} : local_rd_data;
            end
        end
    end
endmodule
