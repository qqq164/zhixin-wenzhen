`timescale 1ns / 1ps

// 自写计数器 PWM。所有输入必须已经同步到 clk，复位为同步低有效。
// 配置通过 ready/valid 握手接收；运行中的配置在下一周期边界生效。
module fdw_pwm_core #(
    parameter integer COUNTER_WIDTH = 32
) (
    input  wire                         clk,
    input  wire                         resetn,
    input  wire                         enable,
    input  wire                         cfg_valid,
    output wire                         cfg_ready,
    input  wire [COUNTER_WIDTH-1:0]     cfg_period_ticks,
    input  wire [COUNTER_WIDTH-1:0]     cfg_high_ticks,
    output reg                          cfg_error,
    output reg                          cfg_applied,
    output reg                          pwm_out,
    output reg                          period_start,
    output reg                          running,
    output reg  [COUNTER_WIDTH-1:0]     active_period_ticks,
    output reg  [COUNTER_WIDTH-1:0]     active_high_ticks
);

    reg [COUNTER_WIDTH-1:0] counter;
    reg [COUNTER_WIDTH-1:0] pending_period_ticks;
    reg [COUNTER_WIDTH-1:0] pending_high_ticks;
    reg pending_valid;

    wire cycle_end = running && (counter == active_period_ticks - 1'b1);
    wire [COUNTER_WIDTH-1:0] next_period_ticks =
        pending_valid ? pending_period_ticks : active_period_ticks;
    wire [COUNTER_WIDTH-1:0] next_high_ticks =
        pending_valid ? pending_high_ticks : active_high_ticks;

    // 仅保留一份待生效配置；背压时上游保持配置和 valid 不变。
    assign cfg_ready = resetn && !pending_valid;

    always @(posedge clk) begin
        if (!resetn) begin
            counter              <= 0;
            pending_period_ticks <= 0;
            pending_high_ticks   <= 0;
            pending_valid        <= 1'b0;
            active_period_ticks  <= 0;
            active_high_ticks    <= 0;
            cfg_error            <= 1'b0;
            cfg_applied          <= 1'b0;
            pwm_out              <= 1'b0;
            period_start         <= 1'b0;
            running              <= 1'b0;
        end else begin
            cfg_error    <= 1'b0;
            cfg_applied  <= 1'b0;
            period_start <= 1'b0;

            // 非法请求产生一个时钟周期的错误事件，不破坏现有配置。
            if (cfg_valid && cfg_ready) begin
                if (cfg_period_ticks == 0 || cfg_high_ticks > cfg_period_ticks) begin
                    cfg_error <= 1'b1;
                end else begin
                    pending_period_ticks <= cfg_period_ticks;
                    pending_high_ticks   <= cfg_high_ticks;
                    pending_valid        <= 1'b1;
                end
            end

            // 停止时也可提交配置；启动时使用同一份完整配置。
            if (pending_valid && (!enable || !running || cycle_end)) begin
                active_period_ticks <= pending_period_ticks;
                active_high_ticks   <= pending_high_ticks;
                pending_valid       <= 1'b0;
                cfg_applied         <= 1'b1;
            end

            if (!enable) begin
                counter <= 0;
                running <= 1'b0;
                pwm_out <= 1'b0;
            end else if (!running) begin
                counter <= 0;
                if (next_period_ticks != 0) begin
                    running      <= 1'b1;
                    period_start <= 1'b1;
                    pwm_out      <= (next_high_ticks != 0);
                end else begin
                    pwm_out <= 1'b0;
                end
            end else if (cycle_end) begin
                counter      <= 0;
                period_start <= 1'b1;
                pwm_out      <= (next_high_ticks != 0);
            end else begin
                counter <= counter + 1'b1;
                pwm_out <= (counter + 1'b1 < active_high_ticks);
            end
        end
    end
endmodule
