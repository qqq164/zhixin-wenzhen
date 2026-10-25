`timescale 1ns / 1ps

// 在旧ADC帧接收器前增加一次性触发门，不改变原码、ADC时钟或AXIS格式。
// 控制字bit0装载、bit1清理、bit2等待事件；配置随清理更新并保持到帧结束。
module fdw_adc_trigger_gate (
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 control_clk CLK" *)
    input wire control_clk,
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 control_resetn RST" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME control_resetn, POLARITY ACTIVE_LOW" *)
    input wire control_resetn,
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 sample_clk CLK" *)
    input wire sample_clk,
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 sample_resetn RST" *)
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME sample_resetn, POLARITY ACTIVE_LOW" *)
    input wire sample_resetn,
    input wire [2:0] capture_control,
    input wire trigger_event,
    output wire [1:0] legacy_control,
    output wire [3:0] trigger_status
);
    reg arm_previous, waiting, event_accepted;
    wire clear = capture_control[1];
    wire event_mode = capture_control[2];
    wire arm_rise = capture_control[0] && !arm_previous;
    wire event_ready, sample_pulse;
    wire event_valid = control_resetn && sample_resetn && !clear && event_mode &&
                       !event_accepted && (waiting || arm_rise) && trigger_event;

    // 清理同时撤销两域的在途请求；各域同步释放，停钟后恢复也不重放旧事件。
    fdw_event_cdc bridge (
        .src_clk(control_clk), .dst_clk(sample_clk),
        .arstn(control_resetn && sample_resetn && !clear),
        .src_valid(event_valid), .src_ready(event_ready), .dst_pulse(sample_pulse)
    );

    // 立即模式逐字保留旧控制通路，事件模式仅交付一个目的域完整时钟脉冲。
    assign legacy_control = {clear, event_mode ? sample_pulse : capture_control[0]};
    // 拼接到旧GPIO状态bit3~6：等待、事件已接受、桥忙、事件模式。
    assign trigger_status = {event_mode, !event_ready, event_accepted, waiting};

    always @(posedge control_clk) begin
        if (!control_resetn || !sample_resetn || clear) begin
            arm_previous <= 0;
            waiting <= 0;
            event_accepted <= 0;
        end else begin
            arm_previous <= capture_control[0];
            if (!event_mode) begin
                waiting <= 0;
                event_accepted <= 0;
            end else begin
                // 重复装载不会覆盖在途请求，一次清理后最多接受一个事件。
                if (arm_rise && !event_accepted) waiting <= 1;
                if (event_valid && event_ready) begin
                    waiting <= 0;
                    event_accepted <= 1;
                end
            end
        end
    end
endmodule
