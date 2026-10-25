`timescale 1ns/1ps
// 对照封存帧与独立定点/浮点期望，逐位核对全部8192个复数FFT频点。
module tb_fdw_iq_fft_core;
    reg clk=0;
    always #5 clk=~clk;
    reg resetn=0,clear=0,start=0;
    reg [31:0] ftw=32'd17179869,phase=0,frame_id=0,flags=8;
    reg [323:0] inverse=0;
    reg [15:0] data=0;
    reg valid=0,last=0;
    wire ready,busy,done,length_error,format_error,config_error,overflow;
    wire [31:0] result_id,result_flags,saturation;
    wire signed [31:0] dc,cosine,sine,total;
    wire signed [47:0] corr_c,corr_s;
    wire [47:0] square;
    reg read_request=0;
    reg [12:0] read_index=0;
    wire read_valid;
    wire signed [31:0] fft_real,fft_imag;
    reg [15:0] inputs[0:1199999];
    reg [63:0] spectra[0:1199999];
    reg [63:0] expected[0:4095];
    reg [323:0] matrices[0:255];
    integer cases,samples,c,index,base,waits,cycles,abort_index;
    fdw_iq_fft_core dut(
        .clk(clk),.resetn(resetn),.clear(clear),.start(start),.cfg_ftw(ftw),.cfg_phase(phase),
        .cfg_frame_id(frame_id),.cfg_flags(flags),.cfg_inverse_q30(inverse),
        .s_data(data),.s_valid(valid),.s_last(last),.s_ready(ready),.busy(busy),.result_valid(done),
        .result_frame_id(result_id),.result_flags(result_flags),.fit_dc_q16(dc),.cosine_q16(cosine),
        .sine_q16(sine),.sum_x(total),.sum_xc_q16(corr_c),.sum_xs_q16(corr_s),.sum_xx(square),
        .length_error(length_error),.format_error(format_error),.config_error(config_error),
        .overflow(overflow),.saturation_count(saturation),.fft_read_request(read_request),
        .fft_read_index(read_index),.fft_read_valid(read_valid),.fft_real_q12(fft_real),.fft_imag_q12(fft_imag)
    );
    task cancel_job;
        begin
            @(negedge clk); clear=1; valid=0; last=0; start=0; read_request=0;
            @(posedge clk); #1;
            if(done||busy||read_valid) $fatal(1,"取消后仍有旧任务/频谱结果");
            @(negedge clk); clear=0;
        end
    endtask
    task launch;
        begin
            @(negedge clk); start=1;
            @(negedge clk); start=0;
        end
    endtask
    initial begin
        if(!$value$plusargs("CASES%d",cases)||!$value$plusargs("SAMPLES%d",samples)) $fatal(1,"缺少用例参数");
        $readmemh("dsp_samples.mem",inputs,0,samples-1);
        $readmemh("dsp_fft_expected.mem",spectra,0,samples-1);
        $readmemh("dsp_iq_expected.mem",expected,0,cases*12-1);
        $readmemh("dsp_matrix.mem",matrices,0,cases-1);
        repeat(4) @(negedge clk); resetn=1;
        inverse=matrices[0];
        // 已捕获的数据、正在处理的FFT和未读完的结果均可撤销，不污染下一帧。
        launch();
        for(abort_index=0;abort_index<77;abort_index=abort_index+1) begin
            @(negedge clk); valid=1; data=16'd729;
        end
        cancel_job();
        launch();
        for(abort_index=0;abort_index<8192;abort_index=abort_index+1) begin
            @(negedge clk); valid=1; data=0; last=abort_index==8191;
        end
        @(negedge clk); valid=0; last=0;
        repeat(25000) @(posedge clk);
        if(done) $fatal(1,"FFT在预期蝶形完成前置DONE");
        cancel_job();
        // 非法频率不留下一个等待输入的FFT任务。
        ftw=0; launch();
        @(posedge clk); #1;
        if(!done||busy||!config_error||ready) $fatal(1,"非法配置处理错误");
        cancel_job();
        for(c=0;c<cases;c=c+1) begin
            base=c*12; ftw=expected[base]; phase=expected[base+1]; flags=expected[base+2];
            frame_id=c+1; inverse=matrices[c];
            launch();
            for(index=0;index<8192;index=index+1) begin
                @(negedge clk);
                if(index%29==0) begin valid=0; last=0; @(negedge clk); end
                data=inputs[c*8192+index]; valid=1; last=index==8191;
                if(!ready) $fatal(1,"完整帧中丢失输入就绪");
                if(index==71) begin
                    // 活动帧不能采用启动后的配置变化；这些端口此时已经被锁存。
                    ftw=32'd12345678; phase=32'habcd1234; frame_id=32'h98765432; inverse=0; flags=0;
                end
            end
            @(negedge clk); valid=0; last=0;
            waits=0;
            while(!done&&waits<400000) begin @(posedge clk); #1; waits=waits+1; end
            if(!done||busy||length_error||format_error||config_error||overflow) $fatal(1,"任务未正常完成 case=%0d",c);
            if(result_id!=c+1||result_flags!=expected[base+2]) $fatal(1,"任务标签不一致");
            if(dc!==expected[base+3][31:0]||cosine!==expected[base+4][31:0]||sine!==expected[base+5][31:0]||
               total!==expected[base+6][31:0]||corr_c!==expected[base+7][47:0]||corr_s!==expected[base+8][47:0]||
               square!==expected[base+9][47:0]||saturation!==expected[base+10][31:0])
                $fatal(1,"IQ逐位输出不一致 case=%0d dc=%0d cosine=%0d sine=%0d",c,dc,cosine,sine);
            for(index=0;index<8192;index=index+1) begin
                @(negedge clk); read_index=index; read_request=1;
                @(negedge clk); read_request=0;
                @(posedge clk); #1;
                if(!read_valid||{fft_real,fft_imag}!==spectra[c*8192+index])
                    $fatal(1,"FFT逐位输出不一致 case=%0d bin=%0d got=%h expected=%h",c,index,{fft_real,fft_imag},spectra[c*8192+index]);
            end
            repeat(3) @(posedge clk); #1;
            if(!done||busy||result_id!=c+1) $fatal(1,"结果未保持");
            $display("CASE_PASS case=%0d wait_cycles=%0d",c,waits);
            cancel_job();
        end
        // 短帧和第8192点缺TLAST必须拒绝，不能暴露上一任务的频谱。
        for(c=0;c<2;c=c+1) begin
            ftw=expected[0]; inverse=matrices[0]; phase=0; launch();
            for(index=0;index<(c==0?127:8192);index=index+1) begin
                @(negedge clk); valid=1; data=16'd18; last=c==0&&index==126;
            end
            @(negedge clk); valid=0; last=0;
            repeat(100) @(posedge clk); #1;
            if(!done||!length_error||busy) $fatal(1,"长度错误未被拒绝");
            read_request=1; repeat(4) @(posedge clk); #1;
            if(read_valid) $fatal(1,"错误帧仍暴露旧FFT");
            cancel_job();
        end
        $display("ALL_PASS cases=%0d fft_bins=%0d",cases,cases*8192);
        $finish;
    end
endmodule
