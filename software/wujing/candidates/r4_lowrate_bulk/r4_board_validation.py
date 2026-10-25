"""悟净直接采集7020 R4并调用本机PL，真实回连验证；不导入CPU参考。"""
import hashlib
import json
import math
from pathlib import Path
import socket
import struct
import sys
import time
import traceback
from icraft import xrt,zg330backend
from r4_capture_client import command,fetch_frame,capture_job
from pl_runtime.processor import PlOnlyProcessor
from pl_runtime.phase_contract import validate_metadata
ROOT=Path(__file__).resolve().parent
HOST='192.168.126.50'
report={'passed':False,'hardware_sampled':False,'hardware_executed':False,
        'cpu_reference_executed':False,'npu_executed':False,'voltage_calibrated':False,
        'analog_phase_calibrated':False,'frames':[],'quiet_frames':[],'events':[],
        'thresholds':{'phase_spread_degrees':2.0,'amplitude_spread_percent':5.0}}
device=None;processor=None;matched=False

def save():
    (ROOT/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def event(kind,**values):
    report['events'].append(dict(kind=kind,monotonic=time.monotonic(),**values));save()
def run(text):
    result=command(text,HOST)
    if not text.startswith('ADC_READ '):event('command',command=text,response=result)
    return result
def json_any(text):
    with socket.create_connection((HOST,5001),timeout=3) as connection:
        connection.sendall((text+'\n').encode('ascii'));raw=bytearray()
        while b'\n' not in raw:
            block=connection.recv(2048)
            if not block:raise RuntimeError('查询被断开')
            raw.extend(block)
    return json.loads(raw)
def quiet(name):
    record=run('ADC_CAPTURE 8192')['record_id'];decoded,packet=fetch_frame(record,HOST)
    if decoded['flags']!=8 or decoded['overrange_count'] or decoded['metadata']['metadata_version']!=0:
        raise ValueError('停波帧格式或过量程错误')
    (ROOT/(name+'.bin')).write_bytes(decoded['payload']);(ROOT/(name+'.packet')).write_bytes(packet)
    samples=decoded['samples'];mean=sum(samples)/8192
    rms=math.sqrt(sum((x-mean)**2 for x in samples)/8192)
    report['quiet_frames'].append(dict(name=name,record_id=record,ac_rms_counts=rms,
        input_sha256=hashlib.sha256(decoded['payload']).hexdigest()))
    save()
def legacy_compare(record,payload):
    started=time.monotonic();raw=bytearray()
    for start in range(0,8192,32):
        block=command('ADC_READ {} {} 32'.format(record,start),HOST)
        if block['record_id']!=record or block['start']!=start or block['count']!=32:
            raise ValueError('旧读取身份错误')
        raw.extend(bytes.fromhex(block['hex']))
    if bytes(raw)!=payload:raise ValueError('新旧原码接口逐点不一致')
    event('legacy_compare',record_id=record,connections=256,elapsed=time.monotonic()-started,bit_exact=True)

def abort_bulk_and_recover(record):
    with socket.create_connection((HOST,5001),timeout=3) as connection:
        connection.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,256)
        connection.sendall(('ADC_FRAME {}\n'.format(record)).encode('ascii'))
        if not connection.recv(16):raise RuntimeError('未收到整帧起始')
        connection.setsockopt(socket.SOL_SOCKET,socket.SO_LINGER,struct.pack('ii',1,0))
    time.sleep(.1)
    event('bulk_reset_sent',record_id=record)

try:
    manifest=json.loads((ROOT/'upload_manifest.json').read_text(encoding='utf-8'))
    for name,digest in manifest.items():
        if hashlib.sha256((ROOT/name).read_bytes()).hexdigest()!=digest:raise ValueError('上传摘要错误：'+name)
    schedule=json.loads((ROOT/'schedule.json').read_text(encoding='utf-8'))
    info=run('INFO')
    if info.get('implementation_revision')!='DIG5-R4-LOWRATE-BULK' or info.get('phase_metadata_version')!=2:
        raise ValueError('板卡不是配套R4')
    matched=True;run('HALT');quiet('quiet_before')
    report['boot_id']=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    device=xrt.Device.Open('axi://zg330aiu?npu=0x40000000&dma=0x80000000')
    processor=PlOnlyProcessor(device,schedule)
    if processor.backend.read(4)!=1:raise ValueError('悟净PL并非空闲')
    # 先核对长任务期间仍能PING、拒绝改激励、拒绝错误取消，并可正确取消后复采。
    run('DAC_SET 10 64 2048 0 0');run('DAC_ENABLE 1')
    job=run('ADC_JOB_START 8192 14')['job_id'];run('PING')
    if json_any('DAC_SET 10000 64 2048 0 0').get('code')!=-20:raise ValueError('忙时允许修改激励')
    if json_any('ADC_JOB_CANCEL '+str(job+1)).get('code')!=-4:raise ValueError('允许错误任务取消')
    run('ADC_JOB_CANCEL '+str(job))
    canceled=run('ADC_JOB_STATUS');stopped=run('DAC_STATUS')
    if canceled['state']!=4 or stopped['enabled']:raise ValueError('取消未完成或仍在输出')
    report['cancel_recovery_verified']=True
    for index,point in enumerate(schedule['points']):
        frequency=int(point['requested_frequency_hz'])
        run('DAC_SET {} 64 2048 0 0'.format(frequency));run('DAC_ENABLE 1')
        time.sleep(max(.05,3.0/frequency))
        excitation=run('DAC_STATUS');previous=None
        if excitation['frequency_word']!=point['dds_ftw']:raise ValueError('实际DDS字不符')
        for repeat in range(2):
            name='frame_{:02d}'.format(len(report['frames']));started=time.monotonic();pings=[0]
            def service(state):
                if state['state']==1 and pings[0]<3:
                    if not command('PING',HOST)['pong']:raise ValueError('长采集无法PING')
                    pings[0]+=1
            decoded,packet=capture_job(point['decimation_log2'],HOST,service)
            elapsed=time.monotonic()-started;meta=decoded['metadata'];validate_metadata(meta,point)
            if decoded['flags']!=8 or decoded['overrange_count'] or meta['dac_tag']!=excitation['tag']:
                raise ValueError('实际采集连续性、过量程或激励身份错误')
            if min(decoded['samples'])<=-2048 or max(decoded['samples'])>=2047:raise ValueError('ADC存在满量程码')
            queried=run('ADC_PHASE_STATUS')
            for key in meta:
                if queried.get(key)!=meta[key]:raise ValueError('头部与查询相位不一致：'+key)
            status=run('ADC_STATUS')
            if status['record_id']!=meta['record_id'] or status['interval_ticks']!=point['interval_ticks']:
                raise ValueError('状态记录与实际步进不符')
            if previous:
                ticks=meta['first_tick']-previous['first_tick']
                if ticks<=0 or (meta['first_phase_word']-previous['first_phase_word'])&0xffffffff!=(ticks*point['dds_ftw'])&0xffffffff:
                    raise ValueError('跨帧首点相位与真实时钟间隔不一致')
            previous=meta
            (ROOT/(name+'.bin')).write_bytes(decoded['payload']);(ROOT/(name+'.packet')).write_bytes(packet)
            row={'name':name,'point_index':index,'repeat':repeat,'record_id':meta['record_id'],
                 'input_sha256':hashlib.sha256(decoded['payload']).hexdigest(),'metadata':meta,'status':status,
                 'elapsed_capture_and_bulk_seconds':elapsed,'ping_during_pending':pings[0],
                 'minimum_code':min(decoded['samples']),'maximum_code':max(decoded['samples'])}
            # 每帧收齐后立即调用悟净PL，随后才能开始下一帧。
            center=round(point['actual_frequency_hz']*8192/point['sample_rate_hz'])
            bins=sorted(set(max(0,min(8191,center+d)) for d in (-1,0,1)))
            row['actual']=processor.process(decoded['samples'],meta,index,decoded['flags'],bins)
            report['frames'].append(row);report['hardware_sampled']=report['hardware_executed']=True;save()
            print('FDW_R4_BOARD_FRAME {} frequency={} ratio={}'.format(name,frequency,point['interval_ticks']),flush=True)
            if frequency==10000 and repeat==0:
                legacy_compare(meta['record_id'],decoded['payload'])
                fetched_at=time.monotonic();again,_=fetch_frame(meta['record_id'],HOST)
                if again['payload']!=decoded['payload']:raise ValueError('重复整帧读取改变原码')
                event('bulk_compare',record_id=meta['record_id'],connections=1,elapsed=time.monotonic()-fetched_at,bit_exact=True)
                abort_bulk_and_recover(meta['record_id'])
            time.sleep(.037 if repeat==0 else .001)
        after=run('DAC_STATUS')
        if not after['enabled'] or after['tag']!=excitation['tag']:raise ValueError('采样或连接关闭改变了连续激励')
    report['transport_and_pl_completed']=True
    forbidden=('fdw_pl_dsp_reference','fdw_dsp_analyze','fdw_point_features','numpy')
    if any(name in sys.modules for name in forbidden):raise ValueError('悟净导入CPU参考')
    report['cpu_reference_modules_loaded']=[]
    report['passed']=True
except Exception as error:
    if getattr(error,'packet',None):(ROOT/'failure.packet').write_bytes(error.packet)
    report['error']=str(error);report['traceback']=traceback.format_exc();print(report['traceback'],flush=True)
finally:
    if matched:
        try:
            run('HALT')
            if report['passed']:quiet('quiet_after');run('HALT')
            report['final_dac_status']=run('DAC_STATUS')
            if report['final_dac_status']['enabled']:raise RuntimeError('结束未停波')
        except Exception as error:report['cleanup_error']=str(error);report['passed']=False
    if processor:
        try:report['final_pl_status']=processor.backend.clear_and_drain()
        except Exception as error:report['pl_cleanup_error']=str(error);report['passed']=False
        while processor.backend.quarantine:
            print('FDW_R4_DDR_QUARANTINE_HELD',flush=True);save()
            request=ROOT/'retry_drain.request'
            if request.exists():
                request.unlink()
                try:processor.backend.release_quarantine()
                except Exception as error:report['recovery_error']=str(error)
            time.sleep(.2)
        report['quarantined_buffers']=len(processor.backend.quarantine)
    if device:xrt.Device.Close(device);report['device_closed']=True
    save();print('FDW_R4_BOARD_END '+str(report['passed']),flush=True)
if not report['passed']:raise SystemExit(1)
