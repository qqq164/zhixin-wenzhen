`timescale 1ns / 1ps

// 同步数字记录器。输入和触发必须已经同步到 clk；此处不处理外部电压和跨域。
// 存储容量是综合时的资源上限，间隔和本次点数由每次装载的参数决定。
module fdw_digital_capture #(
    parameter integer SAMPLE_WIDTH = 2,
    parameter integer MAX_SAMPLES = 1024
) (
    input  wire                    clk,
    input  wire                    resetn,
    input  wire [SAMPLE_WIDTH-1:0] sample_in,
    input  wire                    arm_valid,
    output wire                    arm_ready,
    input  wire [31:0]             cfg_interval_ticks,
    input  wire [31:0]             cfg_sample_count,
    input  wire                    cfg_wait_trigger,
    input  wire                    trigger,
    input  wire                    cancel,
    output reg                     cfg_error,
    output reg                     armed,
    output reg                     busy,
    output reg                     done,
    output reg                     record_valid,
    output reg                     cancelled,
    output reg                     trigger_seen,
    output reg                     sample_taken,
    output reg [31:0]              captured_count,
    output reg [31:0]              active_interval_ticks,
    output reg [31:0]              active_sample_count,
    input  wire                    rd_request,
    input  wire [31:0]             rd_index,
    output reg                     rd_valid,
    output reg                     rd_error,
    output reg [SAMPLE_WIDTH-1:0]  rd_data
);
    localparam integer ADDR_WIDTH = (MAX_SAMPLES <= 1) ? 1 : $clog2(MAX_SAMPLES);
    reg [SAMPLE_WIDTH-1:0] sample_memory [0:MAX_SAMPLES-1];
    reg [31:0] interval_remaining;

    wire arm_accept = arm_valid && arm_ready;
    wire config_ok = cfg_interval_ticks != 0 && cfg_sample_count != 0 &&
                     cfg_sample_count <= MAX_SAMPLES;
    wire sample_fire = resetn && !cancel &&
                       ((armed && trigger) || (busy && interval_remaining == 0));

    // 只接受空闲时的装载；忙时上游保持参数，不覆盖正在记录的一帧。
    assign arm_ready = resetn && !armed && !busy && !cancel;

    // 不复位存储数组，保留块 RAM 推断条件；只有完整记录才允许读回。
    always @(posedge clk) begin
        if (sample_fire)
            sample_memory[captured_count[ADDR_WIDTH-1:0]] <= sample_in;
    end

    // 同步读响应。请求当拍必须已经有完整记录；越界或重装载返回错误及零数据。
    always @(posedge clk) begin
        if (!resetn) begin
            rd_valid <= 1'b0;
            rd_error <= 1'b0;
            rd_data  <= 0;
        end else begin
            rd_valid <= rd_request;
            rd_error <= 1'b0;
            if (rd_request) begin
                if (record_valid && !armed && !busy && !cancel &&
                    !(arm_accept && config_ok) && rd_index < captured_count) begin
                    rd_data <= sample_memory[rd_index[ADDR_WIDTH-1:0]];
                end else begin
                    rd_error <= 1'b1;
                    rd_data  <= 0;
                end
            end
        end
    end

    always @(posedge clk) begin
        if (!resetn) begin
            cfg_error             <= 1'b0;
            armed                 <= 1'b0;
            busy                  <= 1'b0;
            done                  <= 1'b0;
            record_valid          <= 1'b0;
            cancelled             <= 1'b0;
            trigger_seen          <= 1'b0;
            sample_taken          <= 1'b0;
            captured_count        <= 0;
            active_interval_ticks <= 0;
            active_sample_count   <= 0;
            interval_remaining    <= 0;
        end else begin
            cfg_error    <= 1'b0;
            cancelled    <= 1'b0;
            trigger_seen <= 1'b0;
            sample_taken <= 1'b0;

            // 取消优先于同拍触发和采样。保留已记录点数供诊断，不发布半帧。
            if (cancel) begin
                if (armed || busy) begin
                    armed              <= 1'b0;
                    busy               <= 1'b0;
                    done               <= 1'b0;
                    record_valid       <= 1'b0;
                    interval_remaining <= 0;
                    cancelled          <= 1'b1;
                end
            end else if (arm_accept) begin
                if (!config_ok) begin
                    cfg_error <= 1'b1;
                end else begin
                    active_interval_ticks <= cfg_interval_ticks;
                    active_sample_count   <= cfg_sample_count;
                    captured_count        <= 0;
                    interval_remaining    <= 0;
                    done                  <= 1'b0;
                    record_valid          <= 1'b0;
                    armed                 <= cfg_wait_trigger;
                    busy                  <= !cfg_wait_trigger;
                end
            end else if (sample_fire) begin
                // 等待触发模式在触发被接受的这一拍记录首点。
                // 立即模式在装载后的下一拍记录首点，随后严格间隔指定的时钟数。
                armed              <= 1'b0;
                trigger_seen       <= armed;
                sample_taken       <= 1'b1;
                captured_count     <= captured_count + 1'b1;
                interval_remaining <= active_interval_ticks - 1'b1;
                if (captured_count + 1'b1 == active_sample_count) begin
                    busy         <= 1'b0;
                    done         <= 1'b1;
                    record_valid <= 1'b1;
                end else begin
                    busy <= 1'b1;
                end
            end else if (busy) begin
                interval_remaining <= interval_remaining - 1'b1;
            end
        end
    end
endmodule
