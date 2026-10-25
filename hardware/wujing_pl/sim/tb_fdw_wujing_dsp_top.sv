`timescale 1ns/1ps
// 从CPU寄存器配置经DDR、跨域、IQ/FFT再读回，检查完整接口及结果保持。
module tb_fdw_wujing_dsp_top;
    reg gp_clk=0,hp_clk=0,resetn=0;
    always #11 gp_clk=~gp_clk;
    always #2.7 hp_clk=~hp_clk;
    reg [31:0] wa=0,wd=0,ra=0;
    reg wv=0,rv=0,rr=0;
    wire wr,ar,rdv;
    wire [31:0] rd;
    wire [31:0] da;
    wire dv,dr;
    reg dready=0,dvalid=0;
    reg [511:0] dd=0;
    wire unused_write;
    reg [15:0] samples[0:974847];
    reg [63:0] spectra[0:974847],expected[0:1427];
    reg [323:0] matrices[0:118];
    reg [31:0] base,request_address,lfsr=32'h12345678,status,value,real_value,imag_value;
    integer active_case=0,requests=0,responses=0,delay_left=-1,j,k,c,base_index,waits,case_index;
    fdw_wujing_dsp_top dut(
        .itf_ra_awaddr(wa),.itf_ra_awdata(wd),.itf_ra_awvalid(wv),.itf_ra_awready(wr),
        .itf_ra_araddr(ra),.itf_ra_arvalid(rv),.itf_ra_arready(ar),.itf_ra_rdata(rd),.itf_ra_rvalid(rdv),.itf_ra_rready(rr),
        .itf_awaddr(),.itf_awdata(),.itf_awvalid(unused_write),.itf_awready(1'b1),
        .itf_araddr(da),.itf_arvalid(dv),.itf_arready(dready),.itf_rdata(dd),.itf_rvalid(dvalid),.itf_rready(dr),
        .clk(hp_clk),.ra_clk(gp_clk),.rst_n(resetn),.ra_rst_n(resetn)
    );
    always @(negedge hp_clk) begin
        lfsr={lfsr[30:0],lfsr[31]^lfsr[21]^lfsr[1]^lfsr[0]};dready=lfsr[2:0]!=0;
    end
    always @(posedge hp_clk) if(resetn) begin
        if(unused_write) $fatal(1,"只读DSP接口意外写DDR");
        if(dv&&dready) begin
            if(da!==base+requests*64||delay_left>=0||dvalid) $fatal(1,"DDR地址或在途请求错误");
            requests=requests+1;request_address=da;delay_left=1+lfsr[6:4];
        end
        if(dvalid&&dr) begin dvalid<=0;responses=responses+1;end
        else if(delay_left==0) begin
            for(j=0;j<32;j=j+1) dd[j*16+:16]<=samples[active_case*8192+((request_address-base)/64)*32+j];
            dvalid<=1;delay_left=-1;
        end else if(delay_left>0) delay_left=delay_left-1;
    end
    task write_reg(input [17:0] offset,input [31:0] data_value);
        begin
            @(negedge gp_clk);while(!wr) @(negedge gp_clk);wa=32'h400c0000+offset;wd=data_value;wv=1;
            @(posedge gp_clk);if(!wr) $fatal(1,"寄存器写口未就绪");
            @(negedge gp_clk);wv=0;
        end
    endtask
    task read_reg(input [17:0] offset,output [31:0] result_value);
        reg [31:0] held;
        begin
            @(negedge gp_clk);while(!ar) @(negedge gp_clk);ra=32'h400c0000+offset;rv=1;rr=0;
            @(posedge gp_clk);if(!ar) $fatal(1,"寄存器读请求未就绪");
            @(negedge gp_clk);rv=0;
            if(!rdv) $fatal(1,"缺少寄存器读返回");
            held=rd;
            repeat(2) begin @(posedge gp_clk);#1;if(!rdv||rd!==held||ar) $fatal(1,"CPU反压期间读结果未保持");end
            result_value=held;
            @(negedge gp_clk);rr=1;@(negedge gp_clk);rr=0;
        end
    endtask
    task wait_ready;
        begin
            waits=0;status=0;
            while(!status[0]&&waits<200) begin read_reg(18'h004,status);waits=waits+1;end
            if(!status[0]) $fatal(1,"清除后未就绪");
        end
    endtask
    task configure(input integer fixture_index,input [31:0] address);
        begin
            active_case=fixture_index;base_index=fixture_index*12;base=address;
            requests=0;responses=0;
            write_reg(18'h008,address);write_reg(18'h00c,fixture_index+1);
            write_reg(18'h010,expected[base_index+2]);write_reg(18'h014,expected[base_index]);
            write_reg(18'h01c,expected[base_index+1]);
            for(k=0;k<9;k=k+1) begin
                write_reg(18'h100+k*8,matrices[fixture_index][k*36+:32]);
                write_reg(18'h104+k*8,{28'd0,matrices[fixture_index][k*36+32+:4]});
            end
        end
    endtask
    initial begin
        $readmemh("dsp_samples.mem",samples);$readmemh("dsp_fft_expected.mem",spectra);
        $readmemh("dsp_iq_expected.mem",expected);$readmemh("dsp_matrix.mem",matrices);
        repeat(5) @(negedge gp_clk);resetn=1;wait_ready();
        read_reg(18'h0c0,value);if(value!==32'h20261014) $fatal(1,"ABI版本错误");
        write_reg(0,1);read_reg(18'h004,status);if(!status[8]||status[1]||dv) $fatal(1,"非法配置被启动");
        write_reg(0,2);wait_ready();
        // 清除活动任务，等待DDR在途响应消失后才复用地址。
        configure(0,32'h55554000);write_reg(0,1);wait(dut.sample_valid);
        repeat(9) @(posedge gp_clk);write_reg(0,2);wait_ready();
        if(dvalid||delay_left>=0) $fatal(1,"取消任务未排空DDR");
        $display("MMIO_CANCEL_PASS");
        for(c=0;c<2;c=c+1) begin
            case_index=c==0?0:113;
            configure(case_index,c==0?32'h12340000:32'habcd8000);write_reg(0,1);
            if(c==0) begin
                wait(dut.sample_valid);write_reg(18'h008,32'h40000000);
                read_reg(18'h008,value);if(value!==base) $fatal(1,"活动帧配置被修改");
            end
            wait(dut.core_done);read_reg(18'h004,status);
            if(!status[2]||status[1]||status[7:3]!=0) $fatal(1,"完整任务状态错误 %h",status);
            if(requests!=256||responses!=256) $fatal(1,"DMA块数不精确");
            read_reg(18'h050,value);if(value!==case_index+1) $fatal(1,"结果帧编号错误");
            read_reg(18'h054,value);if(value!==expected[base_index+2][31:0]) $fatal(1,"帧flags错误");
            read_reg(18'h020,value);if(value!==expected[base_index+3][31:0]) $fatal(1,"DC系数错误");
            read_reg(18'h024,value);if(value!==expected[base_index+4][31:0]) $fatal(1,"IQ余弦系数错误");
            read_reg(18'h028,value);if(value!==expected[base_index+5][31:0]) $fatal(1,"IQ正弦系数错误");
            read_reg(18'h02c,value);if(value!==expected[base_index+6][31:0]) $fatal(1,"样本和错误");
            for(k=0;k<8192;k=k+1) begin
                write_reg(18'h060,k);status=0;waits=0;
                while(!status[9]&&waits<10) begin read_reg(18'h004,status);waits=waits+1;end
                if(!status[9]) $fatal(1,"FFT快照未有效");
                read_reg(18'h06c,value);if(value!==k) $fatal(1,"FFT频点标签错误");
                read_reg(18'h064,real_value);read_reg(18'h068,imag_value);
                if({real_value,imag_value}!==spectra[case_index*8192+k]) $fatal(1,"MMIO读FFT逐位错误 bin=%0d",k);
            end
            $display("TOP_CASE_PASS fixture=%0d blocks=%0d fft_bins=8192",case_index,requests);
            write_reg(0,2);wait_ready();
        end
        $display("ALL_PASS top_frames=2 fft_bins=16384");$finish;
    end
    initial begin #50000000;$fatal(1,"整接口仿真超时");end
endmodule
