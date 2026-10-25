`timescale 1ns / 1ps

// 使用官方 IOBUF 仿真模型验证两路独立波形、输入回读及高阻切换。
module tb_lxb_probe_io;
    reg clk = 0;
    always #5 clk = ~clk;
    reg resetn = 0, permit = 0;
    reg [1:0] mask = 0, external_en = 3, external_value = 1;
    reg enable_a = 0, enable_b = 0, configure = 0;
    wire [1:0] probe_io, observed;
    wire pwm_a, pwm_b;
    integer checks = 0, different = 0, i;
    assign probe_io[0] = external_en[0] ? external_value[0] : 1'bz;
    assign probe_io[1] = external_en[1] ? external_value[1] : 1'bz;
    fdw_pwm_core a (.clk(clk), .resetn(resetn), .enable(enable_a),
        .cfg_valid(configure), .cfg_ready(), .cfg_period_ticks(32'd8), .cfg_high_ticks(32'd3),
        .cfg_error(), .cfg_applied(), .pwm_out(pwm_a), .period_start(), .running(),
        .active_period_ticks(), .active_high_ticks());
    fdw_pwm_core b (.clk(clk), .resetn(resetn), .enable(enable_b),
        .cfg_valid(configure), .cfg_ready(), .cfg_period_ticks(32'd11), .cfg_high_ticks(32'd5),
        .cfg_error(), .cfg_applied(), .pwm_out(pwm_b), .period_start(), .running(),
        .active_period_ticks(), .active_high_ticks());
    fdw_lxb_probe_io dut (.resetn(resetn), .permit(permit), .output_mask(mask),
        .pwm_a(pwm_a), .pwm_b(pwm_b), .digital_in(observed), .probe_io(probe_io));
    task expect_inputs(input [1:0] value);
        begin
            #1;
            if (observed !== value) $fatal(1, "数字输入回读不一致");
            checks = checks + 1;
        end
    endtask
    initial begin
        #2; expect_inputs(2'b01);
        mask = 3; permit = 1;
        expect_inputs(2'b01);
        repeat (4) @(negedge clk);
        permit = 0; resetn = 1;
        configure = 1;
        @(negedge clk); configure = 0;
        repeat (4) @(negedge clk);
        external_en = 0;
        #1;
        if (probe_io !== 2'bzz) $fatal(1, "未许可时没有保持高阻");
        checks = checks + 1;
        mask = 1; permit = 1; enable_a = 1; enable_b = 1;
        external_en = 2; external_value = 2;
        for (i = 0; i < 32; i = i + 1) begin
            @(negedge clk); #1;
            if (observed !== {1'b1, pwm_a}) $fatal(1, "A 输出影响 B 输入");
            checks = checks + 1;
        end
        external_en = 0; mask = 2;
        for (i = 0; i < 32; i = i + 1) begin
            @(negedge clk); #1;
            if (probe_io[0] !== 1'bz || observed[1] !== pwm_b) $fatal(1, "B 独立输出或 A 高阻错误");
            checks = checks + 1;
        end
        mask = 3;
        for (i = 0; i < 100; i = i + 1) begin
            @(negedge clk); #1;
            if (observed !== {pwm_b, pwm_a}) $fatal(1, "两路 PWM 输出不一致");
            if (pwm_a != pwm_b) different = different + 1;
            checks = checks + 1;
        end
        if (different < 10) $fatal(1, "未观察到两路独立周期");
        resetn = 0;
        #1;
        if (probe_io !== 2'bzz) $fatal(1, "复位没有立即释放双向引脚");
        permit = 0; external_en = 3; external_value = 2;
        expect_inputs(2'b10);
        $display("FDW_LXB_PROBE_IO_PASS checks=%0d independent_samples=%0d", checks, different);
        $finish;
    end
endmodule
