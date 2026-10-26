"""R4真实采集协议客户端：严格核对整帧身份、CRC和硬件相位。"""
import json
import socket
import struct
import time
import zlib

HEADER_BYTES=128
FRAME_BYTES=16384

def command(text,host='192.168.126.50',port=5001):
    """普通连接关闭不代表停止激励；仅显式HALT或任务取消停波。"""
    with socket.create_connection((host,port),timeout=3) as connection:
        connection.sendall((text+'\n').encode('ascii'))
        raw=bytearray()
        while b'\n' not in raw:
            block=connection.recv(1024)
            if not block:raise RuntimeError('JSON回复中途断开')
            raw.extend(block)
            if len(raw)>2048:raise RuntimeError('JSON回复超过限制')
    answer=json.loads(raw)
    if not answer.get('ok'):raise RuntimeError('命令失败：'+text+' '+str(answer))
    return answer

def decode_frame(packet,record):
    """传输CRC和采集连续性分别核对，不把网络完整当成模拟测量正确。"""
    if len(packet)!=HEADER_BYTES+FRAME_BYTES:raise ValueError('整帧长度错误')
    header=packet[:HEADER_BYTES];payload=packet[HEADER_BYTES:]
    if header[:8]!=b'FDB4\x01\x00\x80\x00':raise ValueError('整帧版本或魔数错误')
    u32=lambda offset:struct.unpack_from('<I',header,offset)[0]
    u64=lambda offset:struct.unpack_from('<Q',header,offset)[0]
    if u32(124)!=zlib.crc32(header[:124]) or u32(20)!=zlib.crc32(payload):
        raise ValueError('整帧CRC错误：头部{}/{}，原码{}/{}'.format(u32(124),zlib.crc32(header[:124]),u32(20),zlib.crc32(payload)))
    if u32(8)!=record or not record or u32(12)!=8192 or u32(16)!=FRAME_BYTES or u32(96)!=1:
        raise ValueError('整帧身份、样点数量或完成标志错误')
    if u32(120)!=0:raise ValueError('未知扩展字段')
    values=struct.unpack('<8192h',payload)
    if not all(-2048<=v<=2047 for v in values):raise ValueError('ADC码越界')
    meta={'metadata_version':u32(36),'generation':u32(40),'record_id':record,
          'sample_count':8192,'complete':True,'errors':u32(44),
          'dds_ftw':u32(56),'dac_tag':u32(60),'inverted':bool(u32(80)),
          'interval_ticks':u32(32),'first_tick':u64(64),'last_tick':u64(72),
          'first_phase_word':u32(48),'last_phase_word':u32(52),'raw_clock_hz':u32(28),
          'decimation_log2':u32(84),'filter_id':u32(88),'pipeline_tick_compensation':u32(116)}
    if u32(80)>1:raise ValueError('反相标记错误')
    if meta['metadata_version']==0:
        if meta['interval_ticks']!=1 or any(u32(o) for o in range(36,64,4)) or any(header[64:96]) or u32(116):
            raise ValueError('无相位帧含不一致的滤波或相位字段')
    elif meta['metadata_version']!=2 or u32(92)!=record:raise ValueError('相位版本或任务身份错误')
    return {'payload':payload,'samples':values,'metadata':meta,'flags':u32(24),
            'overrange_count':u32(100),'trigger':u32(104),'route_generation':u32(108),
            'mode':u32(112),'payload_crc32':u32(20),'header_crc32':u32(124)}

def receive_packet(connection):
    """固定16512字节接收，不在厂商SDK加载后的热路径反复扩容缓冲区。"""
    packet=bytearray(HEADER_BYTES+FRAME_BYTES)
    view=memoryview(packet);received=0
    while received<len(packet):
        count=connection.recv_into(view[received:])
        if not count:
            error=ValueError('整帧中途断开，实际{}字节'.format(received))
            error.packet=bytes(view[:received]);raise error
        received+=count
        if packet[:1]==b'{':
            response=bytes(view[:received])
            if b'\n' in response:raise RuntimeError('整帧请求被拒绝：'+response.decode('ascii'))
            if received>2048:raise ValueError('错误回复过长')
    if connection.recv(1):raise ValueError('整帧有多余字节')
    return bytes(packet)

def fetch_frame(record,host='192.168.126.50',port=5001):
    """一条连接只取一帧，固定缓冲收满后检查EOF，拒绝多余字节。"""
    with socket.create_connection((host,port),timeout=5) as connection:
        connection.sendall(('ADC_FRAME {}\n'.format(record)).encode('ascii'))
        packet=receive_packet(connection)
    try:return decode_frame(packet,record),packet
    except ValueError as error:
        # 保留失败原始字节供定位，调用者仍必须拒绝此帧。
        error.packet=packet
        raise

def capture_job(log2,host='192.168.126.50',service=None):
    """任务按实际窗口决定截止时间；等待期间持续记录状态，失败显式取消。"""
    if type(log2) is not int or not 0<=log2<=16:raise ValueError('抽取指数越界')
    info=command('INFO',host)
    if info.get('implementation_revision')!='DIG5-R4-LOWRATE-BULK':raise ValueError('需要R4整套配套')
    job=command('ADC_JOB_START 8192 '+str(log2),host)['job_id']
    deadline=time.monotonic()+(8196*(1<<log2)/25_000_000)+4
    try:
        while True:
            state=command('ADC_JOB_STATUS',host)
            if state['job_id']!=job:raise RuntimeError('任务被替换')
            if service:service(state)
            if state['state']==2:
                if state['record_id']!=job:raise RuntimeError('完成帧身份不符')
                return fetch_frame(job,host)
            if state['state']!=1:raise RuntimeError('采集任务失败：'+str(state))
            if time.monotonic()>deadline:raise TimeoutError('任务完成超时')
            time.sleep(0.05)
    except BaseException:
        try:command('ADC_JOB_CANCEL '+str(job),host)
        except Exception:command('HALT',host)
        raise
