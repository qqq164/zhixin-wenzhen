`timescale 1ns / 1ps

// 板外信号入口与数字模式控制，所有控制与记录接口在同一 100MHz 域。
// AXI GPIO 给出稳定控制字，以 bit7 翻转提出请求；状态保持对应确认和结果。
// 模式 0 停止、1 内部回环、2 双数字、3 PWM+双数字；模拟模式后续单独接入。
module fdw_probe_router (
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 clk CLK" *)
    input wire clk,
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 resetn RST" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME resetn, POLARITY ACTIVE_LOW" *)
    input wire resetn,
    input wire [31:0] control_word,
    output wire [31:0] status_word,
    input wire [1:0] digital_in,
    input wire continuous_pwm,
    input wire continuous_event,
    input wire finite_pwm,
    input wire finite_event,
    input wire pwm_active,
    input wire capture_active,
    output wire [1:0] sample_out,
    output wire trigger_out,
    output reg invalidate_capture,
    output wire pwm_out
);
    (* ASYNC_REG = "TRUE", SHREG_EXTRACT = "NO" *) reg [1:0] digital_meta;
    (* ASYNC_REG = "TRUE", SHREG_EXTRACT = "NO" *) reg [1:0] digital_sync;
    reg [1:0] digital_previous;
    reg [2:0] active_mode, trigger_select;
    reg output_permit, finite_select, acknowledged;
    reg [3:0] result_code;
    reg [2:0] settle_count;
    reg [14:0] generation;
    wire selected_pwm = finite_select ? finite_pwm : continuous_pwm;
    wire selected_event = finite_select ? finite_event : continuous_event;
    wire output_enabled = resetn && active_mode == 3 && output_permit && settle_count == 0;
    wire [1:0] rising = digital_sync & ~digital_previous;
    wire [1:0] falling = ~digital_sync & digital_previous;
    wire selected_trigger = trigger_select == 1 ? selected_event :
                            trigger_select == 2 ? rising[0] :
                            trigger_select == 3 ? falling[0] :
                            trigger_select == 4 ? rising[1] :
                            trigger_select == 5 ? falling[1] : 1'b0;

    // 停止和复位使用真正的 IO 三态；逻辑低电平不等于断开激励。
    // 模块独立综合会把推断的内部三态转换为逻辑；明确实例化管脚缓冲。
    assign pwm_out = selected_pwm;
    assign sample_out = active_mode == 1 ? {selected_event, selected_pwm} :
                        active_mode == 2 || active_mode == 3 ? digital_sync : 2'b0;
    assign trigger_out = resetn && active_mode != 0 && settle_count == 0 && selected_trigger;
    assign status_word = {generation, capture_active, pwm_active, digital_sync,
                          (settle_count != 0), result_code, acknowledged,
                          trigger_select, output_enabled, active_mode};

    always @(posedge clk) begin
        if (!resetn) begin
            digital_meta <= 0;
            digital_sync <= 0;
            digital_previous <= 0;
        end else begin
            digital_meta <= digital_in;
            digital_sync <= digital_meta;
            digital_previous <= digital_sync;
        end
    end

    always @(posedge clk) begin
        if (!resetn) begin
            active_mode <= 0;
            output_permit <= 0;
            finite_select <= 0;
            trigger_select <= 0;
            acknowledged <= 0;
            result_code <= 0;
            settle_count <= 0;
            generation <= 0;
            invalidate_capture <= 0;
        end else begin
            invalidate_capture <= 0;
            if (settle_count != 0) settle_count <= settle_count - 1'b1;
            if (control_word[7] != acknowledged) begin
                acknowledged <= control_word[7];
                result_code <= 0;
                if ((control_word & 32'hfffffe00) != 0 || control_word[6:4] > 5 ||
                    (control_word[3] && control_word[2:0] != 3)) begin
                    result_code <= 4'd4;
                end else if (control_word[2:0] > 3) begin
                    result_code <= 4'd1;
                end else if (control_word[2:0] != 0 &&
                             (capture_active || settle_count != 0)) begin
                    result_code <= 4'd2;
                end else if (control_word[2:0] != 0 && pwm_active) begin
                    result_code <= 4'd3;
                end else begin
                    active_mode <= control_word[2:0];
                    output_permit <= control_word[3];
                    trigger_select <= control_word[6:4];
                    finite_select <= control_word[8];
                    settle_count <= control_word[2:0] == 0 ? 0 : 4;
                    generation <= generation + 1'b1;
                    invalidate_capture <= 1;
                end
            end
        end
    end
endmodule
