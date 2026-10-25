`timescale 1ns/1ps
// 两个非整数比时钟，检查精确长度、字节序、反压、取消和超时迟到响应。
module tb_fdw_ddr_frame_reader;
    reg gp_clk=0,hp_clk=0,resetn=0;
    always #11 gp_clk=~gp_clk;
    always #2.7 hp_clk=~hp_clk;
    reg start=0,cancel=0;
    reg [31:0] base=32'h12340000;
    wire ready,busy,error;
    wire [15:0] data;
    wire valid,last;
    reg sample_ready=0;
    wire [31:0] araddr;
    wire arvalid,rready;
    reg arready=0,rvalid=0;
    reg [511:0] rdata=0;
    integer requests=0,responses=0,seen=0,pending_delay=-1,fixed_delay=0,j;
    reg [31:0] saved_addr=0,expected_base=0,lfsr=32'h33525214;
    reg allow_requests=1,verify_samples=1,stalled=0;
    reg [31:0] stall_address=0;
    fdw_ddr_frame_reader #(.MAX_WAIT_CYCLES(128)) dut (
        .gp_clk(gp_clk),.hp_clk(hp_clk),.resetn(resetn),.gp_start(start),.gp_cancel(cancel),
        .cfg_base(base),.gp_ready(ready),.gp_busy(busy),.gp_error(error),
        .s_data(data),.s_valid(valid),.s_ready(sample_ready),.s_last(last),
        .araddr(araddr),.arvalid(arvalid),.arready(arready),.rdata(rdata),.rvalid(rvalid),.rready(rready)
    );
    function [15:0] sample_value(input integer index);
        integer value;
        begin value=((index*37+79)%4096)-2048;sample_value=value;end
    endfunction
    always @(negedge hp_clk) begin
        lfsr={lfsr[30:0],lfsr[31]^lfsr[21]^lfsr[1]^lfsr[0]};
        arready=allow_requests&&lfsr[2:0]!=0;
    end
    always @(posedge hp_clk) if(resetn) begin
        if(stalled&&(!arvalid||araddr!==stall_address)) $fatal(1,"DDR请求反压期间地址或VALID改变");
        stalled=arvalid&&!arready;stall_address=araddr;
        if(arvalid&&arready) begin
            if(pending_delay>=0||rvalid) $fatal(1,"同时存在多个DDR在途请求");
            if(araddr!==expected_base+requests*64) $fatal(1,"完整32位DDR地址或块数错误");
            saved_addr=araddr;requests=requests+1;
            pending_delay=fixed_delay?fixed_delay:(1+lfsr[7:4]);
        end
        if(rvalid&&rready) begin rvalid<=0;responses=responses+1;end
        else if(pending_delay==0) begin
            for(j=0;j<32;j=j+1) rdata[j*16+:16]<=sample_value(((saved_addr-expected_base)/64)*32+j);
            rvalid<=1;pending_delay=-1;
        end else if(pending_delay>0) pending_delay=pending_delay-1;
    end
    always @(negedge gp_clk) sample_ready=lfsr[1:0]!=0;
    always @(posedge gp_clk) if(valid&&sample_ready&&verify_samples) begin
        if(data!==sample_value(seen)||last!==(seen==8191)) $fatal(1,"样本顺序、编码或TLAST错误 index=%0d",seen);
        seen=seen+1;
    end
    task launch(input [31:0] address);
        begin
            wait(ready);@(negedge gp_clk);base=address;expected_base=address;
            requests=0;responses=0;seen=0;start=1;
            @(negedge gp_clk);start=0;
        end
    endtask
    task abort_job;
        begin @(negedge gp_clk);cancel=1;@(negedge gp_clk);cancel=0;end
    endtask
    task finish_wait;
        integer waits;
        begin
            waits=0;
            while(!ready&&waits<30000) begin @(posedge gp_clk);#1;waits=waits+1;end
            if(!ready||busy) $fatal(1,"任务排空后未重新就绪");
        end
    endtask
    task full_frame(input [31:0] address);
        begin
            verify_samples=1;fixed_delay=0;allow_requests=1;launch(address);finish_wait();
            repeat(30) @(posedge hp_clk);
            if(requests!=256||responses!=256||seen!=8192||error) $fatal(1,"帧长度不精确 req=%0d resp=%0d samples=%0d",requests,responses,seen);
            $display("FRAME_PASS base=%h requests=%0d samples=%0d",address,requests,seen);
        end
    endtask
    initial begin
        repeat(5) @(negedge gp_clk);resetn=1;
        full_frame(32'h12340000);full_frame(32'habcd8000);
        // 请求已置VALID但READY尚未到达，取消仍保持请求直到握手，并丢弃响应。
        allow_requests=0;fixed_delay=60;verify_samples=0;launch(32'h45678000);
        wait(arvalid);abort_job();repeat(12) @(posedge gp_clk);
        if(ready||!arvalid) $fatal(1,"取消撤销了未握手请求或提前开放下一帧");
        @(negedge gp_clk);start=1;base=32'h76540000;@(negedge gp_clk);start=0;
        allow_requests=1;finish_wait();
        if(requests!=1||responses!=1) $fatal(1,"取消后的在途请求未精确排空");
        $display("CANCEL_STALLED_REQUEST_PASS");
        full_frame(32'h98764000);
        // 响应等待期间取消，迟到数据不能流入下一个任务。
        fixed_delay=75;verify_samples=0;launch(32'h23450000);wait(requests==1);abort_job();
        repeat(5) @(posedge gp_clk);if(ready) $fatal(1,"迟到响应前允许了新任务");
        finish_wait();if(requests!=1||responses!=1) $fatal(1,"取消响应检查失败");
        $display("CANCEL_LATE_RESPONSE_PASS");
        // 序列化中取消，邮箱握手须收敛。
        fixed_delay=0;launch(32'h34210000);wait(valid);repeat(5) @(posedge gp_clk);abort_job();finish_wait();
        $display("CANCEL_SERIALIZER_PASS");
        // 超时仅结束计算，读通路保持排空状态到真实响应到来。
        fixed_delay=190;launch(32'h32100000);wait(error);
        if(ready||responses!=0) $fatal(1,"超时恢复未等待迟到响应");
        finish_wait();if(requests!=1||responses!=1||!error) $fatal(1,"超时未保留错误或未排空");
        $display("TIMEOUT_DRAIN_PASS");
        full_frame(32'hffffc000);
        @(negedge gp_clk);base=32'h12340002;start=1;@(negedge gp_clk);start=0;
        @(posedge gp_clk);#1;if(!error||busy||!ready) $fatal(1,"未拒绝不对齐地址");
        if(requests!=256) $fatal(1,"非法地址仍发出了DDR请求");
        $display("ALL_PASS reader_frames=4 exact_blocks=1024 samples=32768");$finish;
    end
    initial begin #2000000;$fatal(1,"DDR仿真超过总时限");end
endmodule
