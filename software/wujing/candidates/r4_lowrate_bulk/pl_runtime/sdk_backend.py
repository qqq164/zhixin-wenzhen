"""仅PL运行端：保留已实测SDK握手，不导入CPU版IQ/FFT或离线参考。"""
import math
import struct
import time

N=8192

BASE=0x400C0000
ABI=0x20261014


def signed(value,bits):
    """将寄存器无符号返回值还原成指定宽度的有符号数。"""
    value&=(1<<bits)-1
    return value-(1<<bits) if value&(1<<(bits-1)) else value


class FdwPlDspClient:
    """传入已经打开的ICraft设备；本类不操作NPU复位、BOOT或下载器。"""
    def __init__(self,device):
        self.registers=device.defaultRegRegion()
        self.memory=device.defaultMemRegion()
        self.quarantine=[]

    def read(self,offset):
        """版本与状态均走已实测的32位SDK寄存器API。"""
        return self.registers.read(BASE+offset,False)&0xFFFFFFFF

    def write(self,offset,value):
        """配置保持完整32位，地址不再与长度混装。"""
        self.registers.write(BASE+offset,value&0xFFFFFFFF,False)

    def require_version(self):
        """在分配缓冲区和任何写操作前拒绝旧加法器位流。"""
        found=self.read(0xC0)
        if found!=ABI:raise RuntimeError(f'需要DSP ABI 0x{ABI:08x}，当前0x{found:08x}；未写寄存器或分配DDR')

    def wait_status(self,predicate,timeout=5):
        """CPU只轮询状态；逐样本IQ与FFT都在PL执行。"""
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            status=self.read(4)
            if predicate(status):return status
            time.sleep(0.001)
        raise TimeoutError('DSP状态等待超时')

    def clear_and_drain(self,timeout=5):
        """只取消自定义任务，必须排空迟到DDR响应才可复用缓冲区。"""
        self.write(0,2)
        return self.wait_status(lambda status:bool(status&1) and not(status&2),timeout)

    def release_quarantine(self):
        """排空完成后释放隔离的申请；未排空时调用者须保留设备与本对象。"""
        self.require_version();self.clear_and_drain()
        for chunk in self.quarantine:chunk.free()
        self.quarantine.clear()

    def compute(self,samples,config,frame_id,flags,fft_bins=()):
        """计算一帧；传输前由调用者验证FDW1长度/CRC和测量条件。"""
        if len(samples)!=N or any(not -2048<=v<=2047 for v in samples):raise ValueError('须为8192个有效12位码')
        bins=list(fft_bins)
        if any(not 0<=v<N for v in bins):raise ValueError('FFT下标越界')
        if len(config['inverse_q30'])!=9:raise ValueError('拟合矩阵须有9个系数')
        self.require_version()
        if self.quarantine:raise RuntimeError('上个DDR任务未排空，禁止启动或释放设备')
        self.clear_and_drain()
        chunk=self.memory.malloc(N*2+128,True,64)
        address=chunk.begin.addr()
        source=struct.pack('<8192h',*samples)+bytes([0xA5])*128
        try:
            if address%64 or not 0<=address<=0xFFFFC000 or address+len(source)>1<<32:raise ValueError('PL DDR申请不对齐或超出完整32位地址范围')
            chunk.write(0,source,len(source))
            check=bytearray(len(source));chunk.read(check,0,len(check))
            if check!=source:raise RuntimeError('CPU与PL DDR写入读回不一致，未启动')
            for offset,value in ((8,address),(0x0C,frame_id),(0x10,flags),(0x14,config['ftw']),(0x1C,config['phase_word'])):self.write(offset,value)
            for index,coefficient in enumerate(config['inverse_q30']):
                self.write(0x100+index*8,coefficient);self.write(0x104+index*8,(coefficient>>32)&15)
            started=time.monotonic();self.write(0,1)
            status=self.wait_status(lambda value:bool(value&(4|0x1F8)))
            if status&0x1F8:raise RuntimeError(f'DSP拒绝任务或报告错误，状态0x{status:08x}')
            if self.read(0x50)!=frame_id&0xFFFFFFFF:raise RuntimeError('结果帧编号不一致')
            if self.read(0x54)!=flags&0xFFFFFFFF:raise RuntimeError('结果flags不一致')
            result={'abi':ABI,'frame_id':frame_id,'flags':flags,'fit_dc_q16':signed(self.read(0x20),32),
                    'cosine_q16':signed(self.read(0x24),32),'sine_q16':signed(self.read(0x28),32),
                    'sum_x':signed(self.read(0x2C),32),'sum_xc_q16':signed(self.read(0x30)|(self.read(0x34)<<32),48),
                    'sum_xs_q16':signed(self.read(0x38)|(self.read(0x3C)<<32),48),
                    'sum_xx':self.read(0x40)|((self.read(0x44)&0xFFFF)<<32),'saturation_count':self.read(0x48),
                    'elapsed_compute_and_poll_seconds':time.monotonic()-started,'fft':[]}
            for index in bins:
                self.write(0x60,index);self.wait_status(lambda value:bool(value&512))
                if self.read(0x6C)!=index:raise RuntimeError('FFT快照下标不一致')
                result['fft'].append({'bin':index,'real_q12':signed(self.read(0x64),32),'imag_q12':signed(self.read(0x68),32)})
            chunk.read(check,0,len(check))
            if check!=source:raise RuntimeError('只读DSP改变了输入或保护块')
            result.update(scalar_features(result,config))
            return result
        finally:
            try:self.clear_and_drain()
            except Exception:
                # 未确认排空时绝不free；调用者也不得Close设备，需继续等待或执行受控总线恢复。
                self.quarantine.append(chunk)
                raise RuntimeError('DDR未确认排空，申请已隔离；保留设备和驱动对象，恢复后调用release_quarantine')
            else:chunk.free()


def scalar_features(result,config):
    """只做少量标量换算；使用PL相关量求残差，避免CPU再遍历样本解调。"""
    dc=result['fit_dc_q16']/65536;c=result['cosine_q16']/65536;s=result['sine_q16']/65536
    beta=[dc,c,s]
    rhs=[result['sum_x'],result['sum_xc_q16']/65536,result['sum_xs_q16']/65536]
    gram=config['gram_normalized']
    residual_square=result['sum_xx']-2*sum(b*r for b,r in zip(beta,rhs))+N*sum(beta[i]*gram[i][j]*beta[j] for i in range(3) for j in range(3))
    output={'dc_counts':dc,'i_counts':c,'q_counts':-s,'amplitude_counts':math.hypot(c,s),
            'phase_radians_relative_to_config':math.atan2(-s,c),'residual_rms_counts':math.sqrt(max(0,residual_square)/N),
            'fft_scale':'输出为FFT/N，Q12；原浮点FFT=复数输出*N/4096',
            'phase_reference_hardware_verified':False}
    window_sum=config['hann_sum_q17']/131072
    for item in result.get('fft',[]):
        item['fft_over_n_counts']= [item['real_q12']/4096,item['imag_q12']/4096]
        item['single_sided_amplitude_counts']=N*math.hypot(item['real_q12'],item['imag_q12'])/4096/window_sum*(1 if item['bin'] in (0,N//2) else 2)
    return output


