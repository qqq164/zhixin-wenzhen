`timescale 1ns / 1ps

// 使用封存原始帧及独立整数期望值，对RTL的握手、算术和异常帧行为逐项检查。
module tb_fdw_adc_stats_axis;
    reg clk = 0;
    always #5 clk = ~clk;
    reg resetn = 0, clear = 0;
    reg [31:0] frame_id = 0, frame_flags = 8;
    reg [15:0] data = 0;
    reg valid = 0, last = 0;
    wire ready, result_valid, result_ok, length_error;
    wire [31:0] result_frame_id, result_flags, count, rms_q16, saturated, invalid_codes;
    wire signed [31:0] total, dc_q16;
    wire [47:0] square_total;
    wire signed [15:0] minimum, maximum;
    wire [15:0] peak_to_peak;
    reg [15:0] samples [0:1199999];
    reg [63:0] expected [0:4095];
    integer case_count, total_samples, case_index, base, offset, index, n, mode, cycles;

    fdw_adc_stats_axis dut (
        .clk(clk), .resetn(resetn), .clear(clear), .frame_id(frame_id), .frame_flags(frame_flags),
        .s_axis_tdata(data), .s_axis_tvalid(valid), .s_axis_tlast(last), .s_axis_tready(ready),
        .result_valid(result_valid), .result_ok(result_ok), .result_frame_id(result_frame_id),
        .result_flags(result_flags), .sample_count(count), .sum_counts(total),
        .sum_square_counts(square_total), .minimum_counts(minimum), .maximum_counts(maximum),
        .peak_to_peak_counts(peak_to_peak), .dc_q16(dc_q16), .ac_rms_q16(rms_q16),
        .saturation_count(saturated), .invalid_code_count(invalid_codes), .length_error(length_error)
    );

    task clear_frame;
        begin
            @(negedge clk); clear = 1; valid = 0; last = 0;
            @(posedge clk); #1;
            if (result_valid || count != 0) $fatal(1, "清除没有复位帧状态");
            @(negedge clk); clear = 0;
        end
    endtask

    initial begin
        if (!$value$plusargs("CASES%d", case_count)) $fatal(1, "缺少用例数量");
        if (!$value$plusargs("SAMPLES%d", total_samples)) $fatal(1, "缺少样本总数");
        $readmemh("samples.mem", samples, 0, total_samples - 1);
        $readmemh("expected.mem", expected, 0, case_count * 15 - 1);
        repeat (4) @(negedge clk);
        resetn = 1;

        // 中途清除一帧后，旧样本不能混入第一份正式结果。
        repeat (137) begin
            @(negedge clk); data = 16'd731; valid = 1;
        end
        clear_frame();
        repeat (3) @(posedge clk);
        if (result_valid) $fatal(1, "被撤销的帧仍产生了结果");

        offset = 0;
        for (case_index = 0; case_index < case_count; case_index = case_index + 1) begin
            base = case_index * 15;
            n = expected[base]; mode = expected[base + 14];
            clear_frame();
            frame_flags = expected[base + 1]; frame_id = expected[base + 2];
            for (index = 0; index < n; index = index + 1) begin
                @(negedge clk);
                if (index % 17 == 0) begin
                    // 流接口的空拍不表示原始ADC时间轴丢点，空拍不应计入样本数。
                    valid = 0; last = 0;
                    @(negedge clk);
                end
                data = samples[offset + index]; valid = 1;
                last = (index == n - 1) && (mode != 1);
                @(posedge clk);
                if (!ready) $fatal(1, "用例%0d在末拍之前拒绝输入", case_index);
                if (index == 0) begin
                    #1;
                    // 帧标签只在第一拍锁存，后续配置变化不得串到这一帧。
                    frame_id = 32'hdeadbeef; frame_flags = 32'hffffffff;
                end
            end
            @(negedge clk); valid = 0; last = 0;
            cycles = 0;
            while (!result_valid && cycles < 40) begin
                @(posedge clk); #1; cycles = cycles + 1;
            end
            if (!result_valid) $fatal(1, "用例%0d计算超时", case_index);
            if (count !== expected[base][31:0] || result_flags !== expected[base+1][31:0] ||
                result_frame_id !== expected[base+2][31:0]) $fatal(1, "用例%0d帧标签或长度错误", case_index);
            if ({{32{total[31]}}, total} !== expected[base+3]) $fatal(1, "用例%0d求和错误", case_index);
            if ({16'd0, square_total} !== expected[base+4]) $fatal(1, "用例%0d平方和错误", case_index);
            if ({{48{minimum[15]}}, minimum} !== expected[base+5] ||
                {{48{maximum[15]}}, maximum} !== expected[base+6]) $fatal(1, "用例%0d最值错误", case_index);
            if ({48'd0, peak_to_peak} !== expected[base+7]) $fatal(1, "用例%0d峰峰值错误", case_index);
            if ({{32{dc_q16[31]}}, dc_q16} !== expected[base+8]) $fatal(1, "用例%0d直流定点结果错误", case_index);
            if ({32'd0, rms_q16} !== expected[base+9]) $fatal(1, "用例%0d交流RMS错误: %0d != %0d", case_index, rms_q16, expected[base+9]);
            if ({32'd0, saturated} !== expected[base+10] || {32'd0, invalid_codes} !== expected[base+11])
                $fatal(1, "用例%0d质量计数错误", case_index);
            if (length_error !== expected[base+12][0] || result_ok !== expected[base+13][0])
                $fatal(1, "用例%0d质量状态错误", case_index);
            // 无clear时结果必须保持，输入不能覆盖CPU尚未读走的帧。
            repeat (5) begin
                @(negedge clk); valid = 1; data = 16'd101; last = 1;
                @(posedge clk); #1;
                if (ready || !result_valid || count != n || result_frame_id != expected[base+2][31:0])
                    $fatal(1, "用例%0d保持状态被覆盖", case_index);
            end
            $display("CASE_PASS %0d", case_index);
            offset = offset + n;
        end

        // 计算中清除也必须撤销结果，并在下一拍重新开放输入。
        clear_frame();
        for (index = 0; index < 8192; index = index + 1) begin
            @(negedge clk); valid = 1; data = 16'd13; last = (index == 8191);
        end
        @(negedge clk); valid = 0; last = 0;
        repeat (4) @(posedge clk);
        clear_frame();
        repeat (40) @(posedge clk);
        if (result_valid || !ready) $fatal(1, "计算中清除未撤销旧结果");
        $display("ALL_PASS cases=%0d clear_during_capture=1 clear_during_sqrt=1", case_count);
        $finish;
    end

    initial begin
        #100000000;
        $fatal(1, "仿真总超时");
    end
endmodule
