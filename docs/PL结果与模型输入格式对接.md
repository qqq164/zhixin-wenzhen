# PL结果与模型输入格式对接

2026-10-26完成独立格式导出候选，Rs按实装100Ω/10kΩ定义。保留FDB4采集和现有PL计算，模型由训练端按本接口定义训练。

## 固定六项特征

| 列 | 定义 |
|---|---|
| F0、F1 | Re(C/A)、Im(C/A) |
| F2 | DC_C / abs(A) |
| F3 | residual_C_RMS / abs(A) |
| F4、F5 | Re(B/A)、Im(B/A) |

A为Rs之前，B为Rs之后，C为当前测点；A_check为重复参考。版本标识为`pl-lsq8192-dds-mean-v1`，采用现有PL整帧8192点最小二乘拟合/统计及同条件复相量平均。F3是逐帧拟合残差功率平均后开方的归一化结果。

该定义与旧`sense-periodic4-f64-v1`四周期相位网格算法不等价，不能仅更换旧模型的接口标签。训练端需要按本定义和100Ω/10kΩ条件训练，并负责条件编排、掩码、归一化及最终模型张量；六列矩阵不代表完整470维NPU输入已经实现。

## 可交接文件

- [完整对接包](../deliverables/PL结果_模型输入格式对接包_20261026.zip)：23文件，与交给队友的本地包内容相同。
- [源码及用法](../software/wujing/candidates/pl_model_export_v1/README.md)
- [FEATURE_CONTRACT.json](../software/wujing/candidates/pl_model_export_v1/FEATURE_CONTRACT.json)
- [output.schema.json](../software/wujing/candidates/pl_model_export_v1/output.schema.json)
- [model_export.py](../software/wujing/candidates/pl_model_export_v1/model_export.py)

包SHA256：`596a52a8a81e0f0cad25f54cd8aaf7f47681eaa3a7851dfd8a88c0e3ed846b74`。

输出支持JSON、CSV及逐条件小端float32：shape=[条件数,6]，每行24字节，无隐藏文件头。保留测点、频率、Rs实测值、校准和帧身份；JSON禁止NaN/Infinity。示例均明确标注为合成数据。

## 验证与尚待对接

原工程20项格式测试、单/多条件CLI及float32/JSON逐项对应检查通过；12份历史真实直连PL结果已验证可导出，缺少的A/B/C身份保持空缺。格式候选尚未部署悟净，未运行NPU或开展实物验证。

SENSE117采样编排、PL结果和本导出器还需在统一主任务中适配；弱信号、Rs压差、接触及全部网络/节点/频点覆盖仍需统一质量判定。模型训练、发布入口与真实故障验证由后续任务接入。
