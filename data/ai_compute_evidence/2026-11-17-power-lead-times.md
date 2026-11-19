# 施耐德交期：本地 Agent 核验记录

核验日：2026-11-17。仅作 power 板块定性研究证据，不强行生成定期图表。

- 官方入口：https://www.se.com/us/en/download/document/Monthly_Lead/
- 官方原表：https://download.se.com/files?p_Doc_Ref=Monthly_Lead&p_enDocType=Catalog&p_File_Name=Monthly%20Lead%20Times.pdf
- 本次取得：HTTP 200，application/pdf，406,290 bytes，7页；解析PDF正文，不以搜索摘要代替。
- 版本：November 2026，Published 11/03/26（美国入口）；不外推到中国或全球统一交期。
- 第1页：周数为规划参考，按单制造从release to manufacturing起算；并非实际下单承诺，配件、外壳和假日可使交期延长。
- 第4页：LVDO，Made to Order - Avg LT，A/B/C/D/F均50周；OEM Semi-Assembled A8/B8为45周，不能混为同一产品。
- 第5页：Medium Voltage System，SURESET 20–50周，PREMSET 28周，CBGS0 40周，GHA/MCLD >40周。范围和不等号保留，不取伪精确中点。
- 解释：产品交期差异显著。不能把所有“开关”汇成一条统一交期，不把集团积压订单或运营MW当交期代理，不把产品发布稿的提速比例当当前排期。
- 待核：同产品历版指南、实际订单承诺、发货/调试/接电阶段，以及高压变压器项目排期。当前只有一版，不能判断其最近延长或缩短。

本次结果写入 `data/ai_compute_reports/2026-11-17.json` 的power.evidence/watch，网页不新增“单期披露”或“交期快照”栏目。
