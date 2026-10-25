"""电脑独立验证R4传输解析与低频相位契约，不连接板卡。"""
import cmath
import copy
import hashlib
import json
import math
from pathlib import Path
import struct
import sys
import unittest
import zlib
ROOT=Path(__file__).resolve().parents[1]
CANDIDATE=ROOT/'software/wujing/candidates/r4_lowrate_bulk'
sys.path.insert(0,str(CANDIDATE))
from r4_capture_client import decode_frame,receive_packet
from pl_runtime.phase_contract import validate_metadata,reference_phasor
from prepare_phase_sweep import plan_point
OUT=ROOT/'outputs/checkpoint_99_7020_r4_lowrate_bulk/client_test_04'
FIXTURE=ROOT/'tests/fixtures/r4_bulk_fixture.bin'
class Tests(unittest.TestCase):
    """将包损坏与采样身份错误分别注入，避免只测试正常路径。"""
    def test_native_transport_fixture(self):
        data=FIXTURE.read_bytes()
        frame=decode_frame(data,19)
        self.assertEqual(frame['samples'],tuple(i%4096-2048 for i in range(8192)))
        for changed in (data[:-1],data+b'X',b'X'+data[1:],data[:150]+b'X'+data[151:]):
            with self.assertRaises(ValueError):decode_frame(changed,19)
        with self.assertRaises(ValueError):decode_frame(data,18)
        for offset,value in ((12,8191),(16,123),(36,1),(80,2),(92,18),(96,0),(120,1)):
            bad=bytearray(data);struct.pack_into('<I',bad,offset,value)
            struct.pack_into('<I',bad,124,zlib.crc32(bad[:124]))
            with self.subTest(offset=offset),self.assertRaises(ValueError):decode_frame(bytes(bad),19)
    def test_fixed_receive_fragments(self):
        data=FIXTURE.read_bytes()
        class Fragmented:
            def __init__(self,raw,step):self.raw=raw;self.offset=0;self.step=step
            def recv_into(self,view):
                count=min(len(view),len(self.raw)-self.offset,self.step)
                view[:count]=self.raw[self.offset:self.offset+count];self.offset+=count
                return count
            def recv(self,size):
                block=self.raw[self.offset:self.offset+size];self.offset+=len(block);return block
        for step in (1,17,127,1460,4096,65536):
            self.assertEqual(receive_packet(Fragmented(data,step)),data)
        with self.assertRaises(ValueError):receive_packet(Fragmented(data[:-1],17))
        with self.assertRaises(ValueError):receive_packet(Fragmented(data+b'X',4096))
        with self.assertRaises(RuntimeError):receive_packet(Fragmented(b'{"ok":false}\n',17))
    def test_delay_normalization_and_filter(self):
        for f in (1,10,1000,10000,100000,500000):
            p=plan_point(f);ratio=p['interval_ticks'];exp=p['decimation_log2']
            self.assertLessEqual(exp,16)
            for delay in (0,25000,925000,12575000,73000000):
                first=(0xFFFFFFF0+delay*p['dds_ftw'])&0xffffffff
                meta={'metadata_version':2,'generation':1,'record_id':7,'sample_count':8192,
                      'complete':True,'errors':0,'dds_ftw':p['dds_ftw'],'dac_tag':2,'inverted':False,
                      'interval_ticks':ratio,'first_tick':0xfffffffe+delay,
                      'last_tick':0xfffffffe+delay+8191*ratio,'first_phase_word':first,
                      'last_phase_word':(first+8191*p['pl_ftw'])&0xffffffff,'raw_clock_hz':25000000,
                      'decimation_log2':exp,'filter_id':int(exp!=0),'pipeline_tick_compensation':2 if exp else 0}
                validate_metadata(meta,p)
                want=83*cmath.exp(-0.72j)
                measured=-1j*want*cmath.exp(1j*(first*2*math.pi/2**32+p['filter_phase_radians']))*p['filter_gain']
                pl={'abi':0x20261014,'frame_id':7,'cosine_q16':round(measured.real*65536),'sine_q16':-round(measured.imag*65536)}
                self.assertLess(abs(reference_phasor(pl,meta,p)-want),0.00002)
                for key,value in (('metadata_version',1),('last_tick',meta['last_tick']+1),
                                  ('last_phase_word',meta['last_phase_word']^1),('filter_id',9),
                                  ('pipeline_tick_compensation',9),('decimation_log2',17),('errors',1)):
                    bad=dict(meta);bad[key]=value
                    with self.subTest(f=f,key=key),self.assertRaises(ValueError):validate_metadata(bad,p)
    def test_runtime_has_no_cpu_reference(self):
        from pl_runtime.processor import PlOnlyProcessor
        self.assertTrue(PlOnlyProcessor)
        self.assertFalse(any(x in sys.modules for x in ('fdw_pl_dsp_reference','fdw_dsp_analyze','numpy')))
        self.assertNotIn('cpu_fallback=True',(CANDIDATE/'pl_runtime/processor.py').read_text(encoding='utf-8'))
def main():
    OUT.mkdir(parents=True,exist_ok=False)
    with (OUT/'tests.log').open('w',encoding='utf-8') as stream:
        result=unittest.TextTestRunner(stream=stream,verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Tests))
    report={'passed':result.wasSuccessful(),'tests':result.testsRun,'board_measured':False,
            'sources':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in CANDIDATE.rglob('*.py')}}
    (OUT/'verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print((OUT/'tests.log').read_text(encoding='utf-8'))
    if not result.wasSuccessful():raise SystemExit(1)
if __name__=='__main__':main()
