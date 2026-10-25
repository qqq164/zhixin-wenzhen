`timescale 1ns / 1ps

// 独立有限周期 PWM：每次接受完整参数快照，运行中背压，结束后保持低电平。
// 不修改现有连续 PWM 核心，不接外部引脚；所有输入必须已同步到 clk。
module fdw_pwm_burst #(
    parameter integer TICKS_WIDTH = 32,
    parameter integer BURST_WIDTH = 32
) (
    input  wire                   clk,
    input  wire                   resetn,
    input  wire                   start_valid,
    output wire                   start_ready,
    input  wire [TICKS_WIDTH-1:0] cfg_period_ticks,
    input  wire [TICKS_WIDTH-1:0] cfg_high_ticks,
    input  wire [BURST_WIDTH-1:0] cfg_period_count,
    input  wire                   cancel,
    output reg                    cfg_error,
    output reg                    running,
    output reg                    pwm_out,
    output reg                    period_start,
    output reg                    done,
    output reg                    done_event,
    output reg                    cancelled,
    output reg [TICKS_WIDTH-1:0]  active_period_ticks,
    output reg [TICKS_WIDTH-1:0]  active_high_ticks,
    output reg [BURST_WIDTH-1:0]  active_period_count,
    output reg [BURST_WIDTH-1:0]  completed_periods
);
    reg [TICKS_WIDTH-1:0] counter;
    wire cycle_end = running && counter == active_period_ticks - 1'b1;
    wire parameters_ok = cfg_period_ticks != 0 && cfg_high_ticks <= cfg_period_ticks &&
                         cfg_period_count != 0;
    assign start_ready = resetn && !running && !cancel;

    always @(posedge clk) begin
        if (!resetn) begin
            counter <= 0;
            cfg_error <= 0;
            running <= 0;
            pwm_out <= 0;
            period_start <= 0;
            done <= 0;
            done_event <= 0;
            cancelled <= 0;
            active_period_ticks <= 0;
            active_high_ticks <= 0;
            active_period_count <= 0;
            completed_periods <= 0;
        end else begin
            cfg_error <= 0;
            period_start <= 0;
            done_event <= 0;
            cancelled <= 0;
            // 取消优先于首点和最后边界，不把未完成周期计入完成数。
            if (cancel) begin
                if (running) begin
                    counter <= 0;
                    running <= 0;
                    pwm_out <= 0;
                    done <= 0;
                    cancelled <= 1;
                end
            end else if (start_valid && start_ready) begin
                if (!parameters_ok) cfg_error <= 1;
                else begin
                    active_period_ticks <= cfg_period_ticks;
                    active_high_ticks <= cfg_high_ticks;
                    active_period_count <= cfg_period_count;
                    completed_periods <= 0;
                    counter <= 0;
                    running <= 1;
                    pwm_out <= cfg_high_ticks != 0;
                    period_start <= 1;
                    done <= 0;
                end
            end else if (cycle_end) begin
                counter <= 0;
                completed_periods <= completed_periods + 1'b1;
                if (completed_periods + 1'b1 == active_period_count) begin
                    // 最后周期结束时不启动第 N+1 周期，避免多一拍高电平。
                    running <= 0;
                    pwm_out <= 0;
                    done <= 1;
                    done_event <= 1;
                end else begin
                    pwm_out <= active_high_ticks != 0;
                    period_start <= 1;
                end
            end else if (running) begin
                counter <= counter + 1'b1;
                pwm_out <= counter + 1'b1 < active_high_ticks;
            end
        end
    end
endmodule
