"""R3相位链路实测客户端；仅匹配新查询协议，不回退R2或模拟样本。"""
import hashlib
import json
from pathlib import Path
import struct
import time
from pwm_client import command
from pl_runtime.phase_contract import validate_metadata


def capture(schedule, output, host='192.168.126.50', phases=(0, 0x40000000), repeats=2):
    """每帧保存真实ADC原码、硬件相位和DAC身份，结束撤销输出。"""
    output=Path(output)
    output.mkdir(parents=True,exist_ok=True)
    if any(output.iterdir()):raise FileExistsError('相位实测必须使用新空目录')
    if schedule.get('capture_profile')!='RAW25M_PHASE':raise ValueError('本次只验证25MHz原速率相位')
    report={'passed':False,'hardware_sampled':False,'voltage_calibrated':False,
            'analog_phase_calibrated':False,'commands':[],'frames':[],'quiet_frames':[]}
    matched=False
    def run(text):
        answer=command(host,text)
        if not text.startswith('ADC_READ '):report['commands'].append({'command':text,'response':answer})
        if not answer.get('ok'):raise RuntimeError('命令失败：'+text+' '+str(answer))
        return answer
    def quiet_capture(name):
        """DAC停用时走普通ADC采集，不能伪造相位有效标记。"""
        record=run('ADC_CAPTURE 8192')['record_id'];status=run('ADC_STATUS')
        if status['record_id']!=record or status['flags']!=8 or status['overrange_count']:
            raise RuntimeError('停波ADC记录无效')
        raw=bytearray()
        for start in range(0,8192,32):
            block=run('ADC_READ {} {} 32'.format(record,start));data=bytes.fromhex(block['hex'])
            if block['record_id']!=record or block['start']!=start or block['count']!=32 or len(data)!=64:
                raise RuntimeError('停波原码块错误')
            raw.extend(data)
        values=struct.unpack('<8192h',raw)
        if not all(-2048<v<2047 for v in values):raise RuntimeError('停波ADC满量程，不能继续当作有效供电采集')
        row={'name':name,'record_id':record,'binary_file':name+'.bin',
             'input_sha256':hashlib.sha256(raw).hexdigest(),'status':status}
        (output/(name+'.bin')).write_bytes(raw);report['quiet_frames'].append(row)
        print('FDW_PHASE_QUIET '+name,flush=True)
    try:
        info=run('INFO')
        if info.get('implementation_revision')!='DIG5-R3-AMP_PHASE' or info.get('phase_metadata_version')!=1:
            raise RuntimeError('相位采集需要R3完整配套，禁止使用R2补造首点相位')
        matched=True
        run('HALT')
        quiet_capture('quiet_before')
        for index,point in enumerate(schedule['points']):
            frequency=point['requested_frequency_hz']
            if int(frequency)!=frequency or point['interval_ticks']!=1:raise ValueError('R3命令仅支持整数频率及原速率采集')
            for phase in phases:
                run('DAC_SET {} 64 2048 {} 0'.format(int(frequency),phase))
                run('DAC_ENABLE 1');time.sleep(0.05)
                for repeat in range(repeats):
                    excitation=run('DAC_STATUS')
                    if not excitation['enabled'] or excitation['frequency_word']!=point['dds_ftw']:
                        raise RuntimeError('实际激励不符')
                    record=run('ADC_CAPTURE_PHASE 8192')['record_id']
                    meta=run('ADC_PHASE_STATUS');validate_metadata(meta,point)
                    status=run('ADC_STATUS')
                    if meta['record_id']!=record or status['record_id']!=record or status['sample_count']!=8192 or status['flags']!=8 or status['overrange_count']:
                        raise RuntimeError('ADC身份、连续性或过量程检查失败')
                    if meta['dac_tag']!=excitation['tag']:raise RuntimeError('元数据属于其他激励')
                    raw=bytearray();blocks=[]
                    for start in range(0,8192,32):
                        block=run('ADC_READ {} {} 32'.format(record,start))
                        data=bytes.fromhex(block['hex'])
                        if block['record_id']!=record or block['start']!=start or block['count']!=32 or len(data)!=64:
                            raise RuntimeError('原码块身份或长度错误')
                        raw.extend(data);blocks.append({'start':start,'sha256':hashlib.sha256(data).hexdigest()})
                    values=struct.unpack('<8192h',raw)
                    if not all(-2048<v<2047 for v in values):raise RuntimeError('ADC存在满量程码或越界')
                    after=run('DAC_STATUS')
                    if not after['enabled'] or any(after[key]!=excitation[key] for key in ('tag','frequency_word','phase_word','gain_q11','offset_code')):
                        raise RuntimeError('回收期间激励改变')
                    name='frame_{:02d}'.format(len(report['frames']))
                    (output/(name+'.bin')).write_bytes(raw)
                    row={'name':name,'point_index':index,'configured_phase_word':phase,'repeat':repeat,
                         'binary_file':name+'.bin','input_sha256':hashlib.sha256(raw).hexdigest(),
                         'metadata':meta,'status':status,'excitation':excitation,'chunks':blocks,
                         'minimum_code':min(values),'maximum_code':max(values)}
                    report['frames'].append(row);report['hardware_sampled']=True
                    (output/(name+'.json')).write_text(json.dumps(row,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
                    (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
                    print('FDW_PHASE_CAPTURE {} frequency={} phase={} first_phase={}'.format(name,frequency,phase,meta['first_phase_word']),flush=True)
        report['passed']=True
    except Exception as error:
        report['error']=str(error)
        raise
    finally:
        if matched:
            try:
                run('HALT')
                if report['passed']:quiet_capture('quiet_after');run('HALT')
                report['final_dac_status']=run('DAC_STATUS')
                if report['final_dac_status']['enabled']:raise RuntimeError('结束后仍有输出许可')
            except Exception as error:
                report['cleanup_error']=str(error);report['passed']=False
        (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return report
